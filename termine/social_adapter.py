"""Social account adapter for VoidAuth SSO integration in Schalti Termine."""

import logging
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


class VoidAuthSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Adapter for VoidAuth SSO logins in Schalti Termine.
    - Allows automatic user creation for users authenticated via VoidAuth.
    - Automatically links social login to existing Django user accounts matching email or username.
    - Synchronizes roles based on VoidAuth groups (Büro/Admin vs. Fahrlehrer).
    - Automatically matches and links Fahrlehrer profile by email or name.
    """

    def is_open_for_signup(self, request, sociallogin):
        return True

    def is_auto_signup_allowed(self, request, sociallogin):
        return True

    def get_connect_redirect_url(self, request, socialaccount):
        from django.urls import reverse
        return reverse("termine:einstellungen")

    def _sync_user_profile_and_groups(self, user, sociallogin):
        """Synchronisiert Büro/Staff-Rolle und verknüpft Fahrlehrer-Datensätze."""
        if not user or not user.pk:
            return

        extra = sociallogin.account.extra_data or {}
        raw_groups = extra.get("groups", [])
        if isinstance(raw_groups, str):
            raw_groups = [g.strip() for g in raw_groups.split(",") if g.strip()]
        user_groups = [str(g).lower().strip() for g in raw_groups if g]

        buero_groups = [
            str(g).lower().strip()
            for g in getattr(
                settings,
                "VOIDAUTH_BUERO_GROUPS",
                ["buero", "admin", "office", "leitung"],
            )
        ]
        lehrer_groups = [
            str(g).lower().strip()
            for g in getattr(
                settings,
                "VOIDAUTH_FAHRLEHRER_GROUPS",
                ["fahrlehrer", "instructor", "lehrer"],
            )
        ]

        hat_buero_gruppe = any(bg in user_groups for bg in buero_groups)
        hat_lehrer_gruppe = any(lg in user_groups for lg in lehrer_groups)

        # Wenn Gruppen im Token vorhanden sind, steuern sie is_staff:
        if user_groups:
            neuer_staff_status = hat_buero_gruppe or user.is_superuser
            if user.is_staff != neuer_staff_status and not user.is_superuser:
                user.is_staff = neuer_staff_status
                user.save(update_fields=["is_staff"])
                logger.info(
                    "Aktualisiere is_staff=%s für User %s anhand VoidAuth-Gruppen %s",
                    neuer_staff_status,
                    user.username,
                    user_groups,
                )

        # Fahrlehrer-Datensatz automatisch verknüpfen falls vorhanden
        try:
            from .models import Fahrlehrer

            fl = getattr(user, "fahrlehrer", None)
            if not fl:
                # 1. Nach E-Mail suchen
                if user.email:
                    fl = Fahrlehrer.objects.filter(email__iexact=user.email).first()

                # 2. Nach Vor- und Nachname oder Vollname suchen
                if not fl:
                    voller_name = f"{user.first_name} {user.last_name}".strip()
                    if voller_name:
                        fl = Fahrlehrer.objects.filter(name__iexact=voller_name).first()

                # 3. Nach Name aus Token suchen
                if not fl and extra.get("name"):
                    fl = Fahrlehrer.objects.filter(name__iexact=extra.get("name").strip()).first()

                # 4. Nach Slug / Username suchen
                if not fl and user.username:
                    fl = Fahrlehrer.objects.filter(slug__iexact=user.username).first()

                if fl and fl.benutzer_id != user.id:
                    # Wenn dieser Fahrlehrer noch keinem anderen User fest zugeordnet ist
                    if fl.benutzer is None:
                        fl.benutzer = user
                        fl.save(update_fields=["benutzer"])
                        logger.info(
                            "Fahrlehrer '%s' automatisch mit Benutzer '%s' verknüpft.",
                            fl.name,
                            user.username,
                        )
        except Exception:
            logger.exception("Fehler beim Verknüpfen des Fahrlehrers für User %s", user.username)

    def pre_social_login(self, request, sociallogin):
        # Wenn der Social-Account bereits existiert, bestehenden Nutzer synchronisieren
        if sociallogin.is_existing and sociallogin.user:
            self._sync_user_profile_and_groups(sociallogin.user, sociallogin)
            return

        if request.user and request.user.is_authenticated:
            return

        extra = sociallogin.account.extra_data or {}

        # 1. Try matching by email
        email = extra.get("email")
        if not email and sociallogin.email_addresses:
            email = sociallogin.email_addresses[0].email
        if not email and hasattr(sociallogin.user, "email"):
            email = sociallogin.user.email

        matched_user = None
        if email:
            try:
                matched_user = User.objects.get(email__iexact=email)
            except (User.DoesNotExist, User.MultipleObjectsReturned):
                matched_user = None

        # 2. Try matching by username / preferred_username
        if not matched_user:
            username = extra.get("preferred_username") or extra.get("username")
            if username:
                try:
                    matched_user = User.objects.get(username__iexact=username)
                except (User.DoesNotExist, User.MultipleObjectsReturned):
                    matched_user = None

        # 3. Connect to existing user if found
        if matched_user:
            logger.info(
                "Auto-connecting VoidAuth login to existing user: %s (id=%s)",
                matched_user.username,
                matched_user.id,
            )
            sociallogin.connect(request, matched_user)
            self._sync_user_profile_and_groups(matched_user, sociallogin)

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data or {}

        if not user.email and extra.get("email"):
            user.email = extra.get("email")

        if not user.first_name and extra.get("name"):
            parts = extra.get("name", "").split(" ", 1)
            user.first_name = parts[0]
            if len(parts) > 1 and not user.last_name:
                user.last_name = parts[1]

        if not user.username:
            if extra.get("preferred_username"):
                user.username = extra.get("preferred_username")
            elif user.email:
                user.username = user.email.split("@")[0]

        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        self._sync_user_profile_and_groups(user, sociallogin)
        return user

