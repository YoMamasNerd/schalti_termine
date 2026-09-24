"""Einstellungen: global, SMTP, FSM, Logs, SSO, Feed-Token, Sperrzeiten."""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import (
    BenutzerProfilForm,
    FahrlehrerEinstellungenForm,
    GlobaleEinstellungenForm,
    SmtpEinstellungenForm,
)
from ..models import (
    Fahrlehrer,
    FahrschulEinstellungen,
    Fuehrerscheinklasse,
    Sperrzeit,
    Terminart,
    neuer_token,
)
from ..services.fsm_client import FsmClient, FsmError
from ..services.planung import (
    generiere_alle,
)
from ..services.smtp import sende_test_email, teste_smtp_authentifizierung
from .common import _erlaubte_fahrlehrer, _gewaehlter_fahrlehrer, _sicheres_ziel, inhaber, mitarbeiter


@mitarbeiter
def einstellungen(request):
    """Was früher nur im Django-Admin einzustellen war.

    Die Auswahl des Fahrlehrers läuft wie in der Tagesplanung über die
    Adresse; das Formular schickt sie deshalb im `action` wieder mit, sonst
    landete ein Inhaber nach dem Speichern beim ersten Fahrlehrer statt bei
    dem, den er gerade bearbeitet hat.
    """
    fahrlehrer, erlaubt = _gewaehlter_fahrlehrer(request, auch_inaktive=True)
    ist_inhaber = request.user.is_staff

    if fahrlehrer is None:
        return render(
            request,
            "staff/einstellungen.html",
            {"fahrlehrer": None, "alle_fahrlehrer": erlaubt, "ist_inhaber": ist_inhaber},
        )

    globale_einst = FahrschulEinstellungen.get_solo()
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")

    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm

    class _PasswortAendernForm(PasswordChangeForm):
        """Ohne Browser-Autofill: Sonst füllt der Browser die Passwortfelder
        auf der Einstellungsseite vor und scrollt auf dem Handy dorthin.
        Django setzt auf old_password zudem autofocus=True – das explizit
        entfernen, genau das verursacht das Sprung-Verhalten."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["old_password"].widget.attrs["autofocus"] = False
            self.fields["old_password"].widget.attrs.pop("autofocus", None)
            self.fields["old_password"].widget.attrs["autocomplete"] = "off"
            self.fields["new_password1"].widget.attrs["autocomplete"] = "new-password"
            self.fields["new_password2"].widget.attrs["autocomplete"] = "new-password"

    if request.method == "POST":
        form_art = request.POST.get("form_art")
        if form_art == "user_profile":
            user_form = BenutzerProfilForm(request.POST, instance=request.user)
            if user_form.is_valid():
                user_form.save()
                if is_ajax:
                    return JsonResponse({"ok": True, "nachricht": "Persönliche Profildaten gespeichert."})
                messages.success(request, "Persönliche Daten erfolgreich gespeichert.")
                return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}#tab-mein-profil")
            if is_ajax:
                return JsonResponse({"ok": False, "fehler": user_form.errors.as_text()}, status=400)
            form = FahrlehrerEinstellungenForm(instance=fahrlehrer, inhaber=ist_inhaber)
            globale_form = GlobaleEinstellungenForm(instance=globale_einst)
            smtp_form = SmtpEinstellungenForm(instance=globale_einst)
            password_form = _PasswortAendernForm(user=request.user)
        elif form_art == "password_change":
            password_form = _PasswortAendernForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                if is_ajax:
                    return JsonResponse({"ok": True, "nachricht": "Passwort erfolgreich geändert."})
                messages.success(request, "Passwort erfolgreich geändert.")
                return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}#tab-mein-profil")
            if is_ajax:
                return JsonResponse({"ok": False, "fehler": password_form.errors.as_text()}, status=400)
            form = FahrlehrerEinstellungenForm(instance=fahrlehrer, inhaber=ist_inhaber)
            globale_form = GlobaleEinstellungenForm(instance=globale_einst)
            smtp_form = SmtpEinstellungenForm(instance=globale_einst)
            user_form = BenutzerProfilForm(instance=request.user)
        elif form_art == "global":
            if not ist_inhaber:
                raise PermissionDenied("Nur Inhaber und Büro dürfen globale Buchungsregeln ändern.")
            alter_horizont = globale_einst.horizont_wochen
            globale_form = GlobaleEinstellungenForm(request.POST, instance=globale_einst)
            if globale_form.is_valid():
                globale_einst = globale_form.save()
                if globale_einst.horizont_wochen != alter_horizont:
                    _globaler_horizont_nachziehen(request, alter_horizont)
                if is_ajax:
                    return JsonResponse({"ok": True, "nachricht": "Globale Einstellungen automatisch gespeichert."})
                messages.success(request, "Buchungsfenster (global) gespeichert.")
                return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}#tab-buchung")
            if is_ajax:
                return JsonResponse({"ok": False, "fehler": globale_form.errors.as_text()}, status=400)
            form = FahrlehrerEinstellungenForm(instance=fahrlehrer, inhaber=ist_inhaber)
            smtp_form = SmtpEinstellungenForm(instance=globale_einst)
            user_form = BenutzerProfilForm(instance=request.user)
            password_form = _PasswortAendernForm(user=request.user)
        elif form_art == "smtp":
            if not ist_inhaber:
                raise PermissionDenied("Nur Inhaber und Büro dürfen SMTP-Einstellungen ändern.")
            smtp_form = SmtpEinstellungenForm(request.POST, instance=globale_einst)
            if smtp_form.is_valid():
                globale_einst = smtp_form.save()
                if is_ajax:
                    return JsonResponse({"ok": True, "nachricht": "SMTP-Einstellungen automatisch gespeichert."})
                messages.success(request, "SMTP-Einstellungen gespeichert.")
                return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}#tab-system")
            if is_ajax:
                return JsonResponse({"ok": False, "fehler": smtp_form.errors.as_text()}, status=400)
            form = FahrlehrerEinstellungenForm(instance=fahrlehrer, inhaber=ist_inhaber)
            globale_form = GlobaleEinstellungenForm(instance=globale_einst)
            user_form = BenutzerProfilForm(instance=request.user)
            password_form = _PasswortAendernForm(user=request.user)
        else:
            globale_form = GlobaleEinstellungenForm(instance=globale_einst)
            smtp_form = SmtpEinstellungenForm(instance=globale_einst)
            user_form = BenutzerProfilForm(instance=request.user)
            password_form = _PasswortAendernForm(user=request.user)
            form = FahrlehrerEinstellungenForm(
                request.POST, instance=fahrlehrer, inhaber=ist_inhaber
            )
            if form.is_valid():
                fahrlehrer = form.save()
                if is_ajax:
                    return JsonResponse({"ok": True, "nachricht": "Fahrlehrer-Einstellungen automatisch gespeichert."})
                messages.success(request, "Einstellungen gespeichert.")
                return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}#tab-fahrlehrer")
            if is_ajax:
                return JsonResponse({"ok": False, "fehler": form.errors.as_text()}, status=400)
    else:
        form = FahrlehrerEinstellungenForm(instance=fahrlehrer, inhaber=ist_inhaber)
        globale_form = GlobaleEinstellungenForm(instance=globale_einst)
        smtp_form = SmtpEinstellungenForm(instance=globale_einst)
        user_form = BenutzerProfilForm(instance=request.user)
        password_form = _PasswortAendernForm(user=request.user)

    jetzt = timezone.now()
    alle_sperren = list(
        Sperrzeit.objects.filter(fahrlehrer=fahrlehrer, ende__gte=jetzt).order_by("beginn")
    )
    manuelle_sperren = [s for s in alle_sperren if not s.fsm_id]
    fsm_sperren_count = sum(1 for s in alle_sperren if s.fsm_id)
    alle_lehrer_sperren = list(
        Sperrzeit.objects.filter(fahrlehrer__in=erlaubt, ende__gte=jetzt, fsm_id="")
        .select_related("fahrlehrer")
        .order_by("beginn")
    )

    from allauth.socialaccount.models import SocialAccount
    social_account = SocialAccount.objects.filter(user=request.user).first()

    return render(
        request,
        "staff/einstellungen.html",
        {
            "fahrlehrer": fahrlehrer,
            "alle_fahrlehrer": erlaubt,
            "ist_inhaber": ist_inhaber,
            "form": form,
            "globale_form": globale_form,
            "smtp_form": smtp_form,
            "globale_einstellungen": globale_einst,
            "buchbar_bis": timezone.localtime(fahrlehrer.spaetester_start()).date(),
            "sperrzeiten": manuelle_sperren,
            "alle_sperrzeiten": alle_sperren,
            "alle_lehrer_sperren": alle_lehrer_sperren,
            "fsm_sperren_count": fsm_sperren_count,
            "terminarten": Terminart.objects.all(),
            "klassen": Fuehrerscheinklasse.objects.all(),
            "social_account": social_account,
            "user_form": user_form,
            "password_form": password_form,
        },
    )

@mitarbeiter
@require_POST
def disconnect_sso(request):
    """Trennt die Verknüpfung mit VoidAuth SSO."""
    from allauth.socialaccount.models import SocialAccount

    account = SocialAccount.objects.filter(user=request.user).first()
    if not account:
        messages.info(request, "Keine aktive SSO-Verknüpfung vorhanden.")
        return redirect("termine:einstellungen")

    if not request.user.has_usable_password():
        messages.error(
            request,
            "Du hast noch kein lokales Passwort festgelegt. Bitte setze zuerst über 'Passwort vergessen' "
            "ein Passwort, bevor du die SSO-Verknüpfung trennst, um dich nicht auszusperren."
        )
        return redirect("termine:einstellungen")

    account.delete()
    messages.success(request, "Die Verknüpfung mit VoidAuth wurde erfolgreich aufgehoben.")
    return redirect("termine:einstellungen")

@mitarbeiter
@require_POST
def smtp_test_ajax(request):
    """Führt einen Live-Test der SMTP-Authentifizierung oder einen Test-Mail-Versand durch."""
    if not request.user.is_staff:
        raise PermissionDenied("Nur Inhaber dürfen SMTP-Einstellungen testen.")

    globale_einst = FahrschulEinstellungen.get_solo()
    cfg = globale_einst.get_effective_email_config()

    host = request.POST.get("email_host", "").strip() or cfg.get("host") or ""
    port_val = request.POST.get("email_port", "").strip() or cfg.get("port") or 587
    user = request.POST.get("email_user", "").strip()
    if not user and (not host or host == cfg.get("host")):
        user = cfg.get("user") or ""

    password = request.POST.get("email_password", "")
    if not password and (not host or host == cfg.get("host")):
        password = cfg.get("password") or ""

    if "email_use_tls" in request.POST:
        use_tls = request.POST.get("email_use_tls") in ["true", "True", "1", "on"]
    else:
        use_tls = cfg.get("use_tls", True)

    if "email_use_ssl" in request.POST:
        use_ssl = request.POST.get("email_use_ssl") in ["true", "True", "1", "on"]
    else:
        use_ssl = cfg.get("use_ssl", False)

    from_email = request.POST.get("email_from", "").strip() or cfg.get("from_email") or ""
    aktion = request.POST.get("aktion", "auth")

    if aktion == "mail":
        empfaenger = request.POST.get("test_empfaenger", "").strip() or request.user.email or from_email
        ergebnis = sende_test_email(
            empfaenger=empfaenger,
            host=host,
            port=port_val,
            user=user,
            password=password,
            use_tls=use_tls,
            use_ssl=use_ssl,
            from_email=from_email,
        )
    else:
        ergebnis = teste_smtp_authentifizierung(
            host=host,
            port=port_val,
            user=user,
            password=password,
            use_tls=use_tls,
            use_ssl=use_ssl,
        )

    return JsonResponse({
        "ok": ergebnis.ok,
        "meldung": ergebnis.meldung,
        "details": ergebnis.details,
    })

def _globaler_horizont_nachziehen(request, alter_horizont: int) -> None:
    """Nach einer Änderung des globalen Horizonts die Termine aller Fahrlehrer anpassen."""
    globale_einst = FahrschulEinstellungen.get_solo()
    gesamt_bericht = generiere_alle()
    messages.success(
        request,
        f"Planungshorizont auf {globale_einst.horizont_wochen} Wochen geändert: {gesamt_bericht.als_text()}.",
    )

@mitarbeiter
@require_POST
def feed_token_neu(request):
    """Setzt das Kalender-Abo zurück – etwa wenn die Abo-URL abhandengekommen ist."""
    fahrlehrer, _ = _gewaehlter_fahrlehrer(request, auch_inaktive=True)
    if fahrlehrer is None:
        messages.error(request, "Kein Fahrlehrer ausgewählt.")
        return redirect("termine:einstellungen")

    fahrlehrer.feed_token = neuer_token()
    fahrlehrer.save(update_fields=["feed_token"])
    messages.warning(
        request,
        "Neue Abo-URL erzeugt. Das alte Abo liefert ab sofort nichts mehr – "
        "bitte in allen Kalenderprogrammen austauschen.",
    )
    return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}#tab-profil")

@mitarbeiter
@require_POST
def sperrzeit_loeschen(request, pk: int):
    """Hebt eine Sperrzeit wieder auf.

    Die freien Termine, die beim Eintragen entfernt wurden, kommen dadurch
    nicht von selbst zurück – der Generator holt sie beim nächsten Lauf, und
    „Jetzt vorausplanen" tut es sofort. Das steht so auch im Hinweis.
    """
    sperre = get_object_or_404(
        Sperrzeit, pk=pk, fahrlehrer__in=_erlaubte_fahrlehrer(request.user, auch_inaktive=True)
    )
    if sperre.fsm_id:
        from ..services import fsm_sync

        fsm_ids = [fid.strip() for fid in sperre.fsm_id.split(",") if fid.strip()]
        transaction.on_commit(lambda: fsm_sync.async_loesche_fsm_termine(fsm_ids))

    sperre.delete()
    messages.success(
        request,
        "Sperrzeit aufgehoben. Termine in diesem Zeitraum entstehen beim nächsten "
        "Planungslauf neu – über „Jetzt vorausplanen“ sofort.",
    )
    # Sperrzeiten stehen an zwei Stellen: in der Tagesplanung neben der Woche
    # und in den Einstellungen als Liste. Zurück geht es dorthin, wo geklickt
    # wurde – der Umweg über die jeweils andere Seite kostete nur die Ansicht.
    return redirect(_sicheres_ziel(request, "termine:einstellungen"))

@inhaber
def fsm_einstellungen(request):
    """Verwaltung der Verknüpfungen zwischen lokalen Fahrlehrern und dem Fahrschulmanager (FSM)."""
    if not getattr(settings, "FSM_SYNC_ENABLED", False):
        raise Http404("FSM-Integration ist in dieser Installation nicht aktiviert.")

    ist_inhaber = request.user.is_staff
    fahrlehrer_liste = Fahrlehrer.objects.all().order_by("reihenfolge", "name")

    fsm_client = FsmClient()
    fsm_lehrer_liste = []
    fsm_fehler = None

    from django.core.cache import cache

    cache_key = "fsm_lehrer_liste"
    fsm_lehrer_liste = cache.get(cache_key)
    if fsm_lehrer_liste is None:
        try:
            fsm_lehrer_liste = fsm_client.get_fahrlehrer() or []
            try:
                cache.set(cache_key, fsm_lehrer_liste, timeout=300)
            except Exception:
                pass
        except FsmError as exc:
            fsm_fehler = str(exc)
            fsm_lehrer_liste = []

    # Namen einheitlich als voller_name und name bereitstellen
    for fl_item in fsm_lehrer_liste:
        if isinstance(fl_item, dict):
            if "voller_name" not in fl_item:
                v = str(fl_item.get("vorname") or "").strip()
                n = str(fl_item.get("nachname") or "").strip()
                fl_item["voller_name"] = f"{v} {n}".strip() or str(fl_item.get("displayName") or fl_item.get("name") or "Unbekannt")
            if "name" not in fl_item:
                fl_item["name"] = fl_item["voller_name"]

    if request.method == "POST":
        aktion = request.POST.get("aktion")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")

        if aktion == "sync":
            from ..services.fsm_sync import sync_alle_fahrlehrer

            try:
                cache.delete(cache_key)
                ergebnisse = sync_alle_fahrlehrer(client=fsm_client)
                gesamt = sum(ergebnisse.values())
                nachricht = f"Synchronisation erfolgreich: {gesamt} Sperrzeiten für {len(ergebnisse)} Fahrlehrer abgeglichen."
                if is_ajax:
                    return JsonResponse({"ok": True, "nachricht": nachricht, "gesamt": gesamt})
                messages.success(request, nachricht)
            except Exception as exc:
                if is_ajax:
                    return JsonResponse({"ok": False, "fehler": str(exc)}, status=500)
                messages.error(request, f"Fehler bei Synchronisation: {exc}")
            return redirect("termine:fsm_einstellungen")

        if aktion == "import_fahrlehrer":
            from ..services.fsm_sync import importiere_fahrlehrer_aus_fsm, sync_alle_fahrlehrer

            try:
                cache.delete(cache_key)
                neu, aktualisiert = importiere_fahrlehrer_aus_fsm(client=fsm_client)
                sync_alle_fahrlehrer(client=fsm_client)
                nachricht = f"Fahrlehrer-Import erfolgreich: {len(neu)} neu angelegt, {len(aktualisiert)} verknüpft/aktualisiert."
                if is_ajax:
                    return JsonResponse({
                        "ok": True,
                        "nachricht": nachricht,
                        "neu_count": len(neu),
                        "aktualisiert_count": len(aktualisiert),
                    })
                messages.success(request, nachricht)
            except Exception as exc:
                if is_ajax:
                    return JsonResponse({"ok": False, "fehler": str(exc)}, status=500)
                messages.error(request, f"Fehler beim Import: {exc}")
            return redirect("termine:fsm_einstellungen")

        # Globale FSM-Optionen speichern
        globale_einst = FahrschulEinstellungen.get_solo()
        globale_einst.fsm_theorie_blockiert_beratung = "fsm_theorie_blockiert_beratung" in request.POST
        if "fsm_sync_intervall_minuten" in request.POST:
            try:
                globale_einst.fsm_sync_intervall_minuten = int(request.POST.get("fsm_sync_intervall_minuten", 15))
            except ValueError:
                globale_einst.fsm_sync_intervall_minuten = 15
        globale_einst.save(update_fields=["fsm_theorie_blockiert_beratung", "fsm_sync_intervall_minuten"])

        from ..services.fsm_sync import aktualisiere_fsm_schedule
        aktualisiere_fsm_schedule(globale_einst.fsm_sync_intervall_minuten)

        # Zuordnungen speichern
        for fahrlehrer in fahrlehrer_liste:
            key_id = f"fsm_id_{fahrlehrer.pk}"
            key_aktiv = f"fsm_sync_aktiv_{fahrlehrer.pk}"

            if key_id in request.POST:
                fahrlehrer.fsm_id = request.POST.get(key_id, "").strip()
                fahrlehrer.fsm_sync_aktiv = key_aktiv in request.POST
                fahrlehrer.save(update_fields=["fsm_id", "fsm_sync_aktiv"])

        if "sync_nach_speichern" in request.POST or request.POST.get("sync_nach_speichern") == "1":
            from ..services.fsm_sync import sync_alle_fahrlehrer

            ergebnisse = sync_alle_fahrlehrer(client=fsm_client)
            gesamt = sum(ergebnisse.values())
            nachricht = f"Einstellungen gespeichert & {gesamt} Sperrzeiten synchronisiert."
            if is_ajax:
                return JsonResponse({"ok": True, "nachricht": nachricht, "gesamt": gesamt})
            messages.success(request, nachricht)
            return redirect("termine:fsm_einstellungen")

        if is_ajax:
            intervall_txt = f"alle {globale_einst.fsm_sync_intervall_minuten} Minuten" if globale_einst.fsm_sync_intervall_minuten > 0 else "deaktiviert (nur manuell)"
            return JsonResponse({
                "ok": True,
                "nachricht": "Einstellungen automatisch gespeichert.",
                "intervall": globale_einst.fsm_sync_intervall_minuten,
                "intervall_text": intervall_txt,
            })

        messages.success(request, "FSM-Einstellungen gespeichert.")
        return redirect("termine:fsm_einstellungen")

    jetzt = timezone.now()
    fahrlehrer_daten = []
    for fl in fahrlehrer_liste:
        anzahl_sperren = (
            Sperrzeit.objects.filter(fahrlehrer=fl, beginn__gte=jetzt)
            .exclude(fsm_id="")
            .count()
        )
        fahrlehrer_daten.append(
            {
                "fahrlehrer": fl,
                "fsm_sperren_count": anzahl_sperren,
            }
        )

    return render(
        request,
        "staff/fsm_einstellungen.html",
        {
            "fahrlehrer_daten": fahrlehrer_daten,
            "fsm_lehrer_liste": fsm_lehrer_liste,
            "fsm_fehler": fsm_fehler,
            "fsm_gateway_url": getattr(settings, "FSM_GATEWAY_URL", ""),
            "fsm_leistungsart_id": getattr(settings, "FSM_LEISTUNGSART_ID", ""),
            "fsm_api_key_configured": bool(getattr(settings, "FSM_GATEWAY_API_KEY", "")),
            "globale_einst": FahrschulEinstellungen.get_solo(),
            "ist_inhaber": ist_inhaber,
        },
    )

@inhaber
def system_logs(request):
    """Zeigt die System- & Audit-Logs an, filterbar nach Art/Kategorie, Level, Zeitraum und Suche."""
    from datetime import timedelta

    from ..models.logging import LogKategorie, LogLevel, SystemLog

    qs = SystemLog.objects.select_related("benutzer").order_by("-created_at")

    selected_kategorie = request.GET.get("kategorie", "").strip().lower()
    if selected_kategorie and selected_kategorie in LogKategorie.values:
        qs = qs.filter(kategorie=selected_kategorie)

    selected_level = request.GET.get("level", "").strip().upper()
    if selected_level and selected_level in LogLevel.values:
        qs = qs.filter(level=selected_level)

    selected_zeitraum = request.GET.get("zeitraum", "7tage").strip().lower()
    now = timezone.now()
    if selected_zeitraum == "heute":
        qs = qs.filter(created_at__date=now.date())
    elif selected_zeitraum == "7tage":
        qs = qs.filter(created_at__gte=now - timedelta(days=7))
    elif selected_zeitraum == "30tage":
        qs = qs.filter(created_at__gte=now - timedelta(days=30))

    query = request.GET.get("q", "").strip()
    if query:
        qs = qs.filter(
            Q(titel__icontains=query)
            | Q(nachricht__icontains=query)
            | Q(aktion__icontains=query)
            | Q(benutzer__username__icontains=query)
            | Q(benutzer__first_name__icontains=query)
            | Q(benutzer__last_name__icontains=query)
        )

    total_logs = SystemLog.objects.count()
    letzte_24h = now - timedelta(hours=24)
    errors_24h = SystemLog.objects.filter(level__in=[LogLevel.ERROR, LogLevel.CRITICAL], created_at__gte=letzte_24h).count()
    warnings_24h = SystemLog.objects.filter(level=LogLevel.WARNING, created_at__gte=letzte_24h).count()

    paginator = Paginator(qs, 50)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "staff/system_logs.html",
        {
            "page_obj": page_obj,
            "logs": page_obj.object_list,
            "selected_kategorie": selected_kategorie,
            "selected_level": selected_level,
            "selected_zeitraum": selected_zeitraum,
            "query": query,
            "kategorien": LogKategorie.choices,
            "level_choices": LogLevel.choices,
            "total_logs": total_logs,
            "errors_24h": errors_24h,
            "warnings_24h": warnings_24h,
            "ist_inhaber": request.user.is_staff,
        },
    )

@inhaber
@require_POST
def cleanup_logs_action(request):
    """Manuelle Bereinigung alter System-Logs (> 30 Tage)."""
    from ..services.logging import cleanup_old_logs

    res = cleanup_old_logs(max_days=30, max_records=10000)
    messages.success(
        request,
        f"Bereinigung abgeschlossen: {res['gesamt_geloescht']} alte Einträge entfernt ({res['verbleibend']} verbleibend).",
    )
    return redirect("termine:system_logs")
