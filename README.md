# Schalti Termine

Öffentliche Terminbuchung für Fahrschul-Beratungen. Eigenständige Django-App,
die ohne Nextcloud läuft.

Der Kerngedanke: **jeder Tag lässt sich einzeln planen** – passend zu den
unregelmäßigen Arbeitszeiten eines Fahrlehrers – und **zusätzlich** lassen sich
wiederkehrende Rhythmen hinterlegen, aus denen die App Termine automatisch für
mehrere Wochen im Voraus bereitstellt. Gesetzliche Feiertage werden dabei
anhand des Bundeslands übersprungen.

## Was die App kann

**Für Interessenten (öffentlich, ohne Login)**

- Monatskalender mit allen freien Terminen, filterbar nach Terminart und Fahrlehrer
- Die Filterleiste erscheint nur, wo es etwas zu wählen gibt: Bei einer
  Fahrschule mit einem Fahrlehrer und einer Terminart landet der Kunde direkt
  im Kalender
- Buchung mit Name, E-Mail, Telefon, Führerscheinklasse und Nachricht
- Double-Opt-in: der Termin wird erst nach Klick auf den Link in der E-Mail verbindlich
- Bestätigungsmail mit Kalendereintrag (.ics) im Anhang
- Selbstständiges Absagen über einen persönlichen Link
- Erinnerungsmail vor dem Termin

**Für die Fahrschule (interner Bereich)**

- **Tagesplanung**: Wochenansicht, in der jeder Tag einzeln beplant wird
  („am 17.09. von 14:00 bis 17:00 Beratungen anbieten“)
- **Rhythmus-Regeln**: wiederkehrende Verfügbarkeiten, wahlweise wöchentlich
  oder in mehrwöchigem Takt, mit Gültigkeitszeitraum
- **Feiertage pro Bundesland**: werden bei der automatischen Planung ausgelassen
- **Sperrzeiten** für Urlaub und Abwesenheit
- Buchungsübersicht mit Absage-Funktion (der Kunde wird automatisch informiert)
- **Kalender-Abo** (.ics-URL) für Outlook, Google Kalender oder Apple Kalender
- Mehrere Fahrlehrer, jeder mit eigenen Regeln, eigenem Bundesland und eigenem Kalender
- Django-Admin für die Stammdaten

## Technik

| Baustein | Wahl | Warum |
|---|---|---|
| Backend | Django 5.2 | Admin, Migrationen, Auth und Formulare sind fertig dabei |
| Frontend | Serverseitige Templates + htmx | Ein Projekt statt zwei, funktioniert auch ohne JavaScript |
| Styling | Handgeschriebenes CSS (`static/css/app.css`) | Kein Node-Build-Schritt im Deployment |
| Datenbank | PostgreSQL, alternativ SQLite | SQLite reicht für eine Einzelplatz-Installation |
| Hintergrundjobs | django-q2 | Nutzt die vorhandene Datenbank, kein Redis nötig |
| Feiertage | [`holidays`](https://pypi.org/project/holidays/) | Alle 16 Bundesländer, komplett offline |
| Kalender | `icalendar` | .ics-Anhang und Abo-Feed |

htmx liegt als eine Datei unter `static/js/` – es gibt bewusst keinen
Paketmanager fürs Frontend.

## Schnellstart (Entwicklung)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # SITE_BASE_URL auf http://localhost:8000 setzen

python manage.py migrate
python manage.py beispieldaten     # Demo-Fahrschule inklusive Termine
python manage.py runserver
```

Danach erreichbar:

| Adresse | Was |
|---|---|
| <http://localhost:8000/> | Öffentliche Buchungsseite |
| <http://localhost:8000/intern/> | Interner Bereich (`admin` / `admin`) |
| <http://localhost:8000/django-admin/> | Stammdaten |

Ohne `EMAIL_HOST` in der `.env` werden alle E-Mails auf der Konsole ausgegeben –
die Bestätigungslinks lassen sich also direkt aus dem Terminal kopieren.

## Betrieb mit Docker

```bash
cp .env.example .env          # ausfüllen, besonders DJANGO_SECRET_KEY und SITE_BASE_URL
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

`docker compose` startet drei Container: die Datenbank, den Webserver
(gunicorn) und einen Worker, der die wiederkehrenden Jobs abarbeitet.
Migrationen und die Job-Einrichtung laufen beim Start des Web-Containers
automatisch mit.

Vor die App gehört ein Reverse Proxy mit TLS (nginx, Caddy, Traefik). Der
Webserver lauscht absichtlich nur auf `127.0.0.1`.

## So richtet man die Terminplanung ein

1. **Terminart anlegen** (Django-Admin → Terminarten): Bezeichnung, Dauer,
   optional ein Puffer danach und ein Ort. Die Dauer bestimmt, in welche
   Häppchen ein Zeitfenster zerlegt wird.

2. **Fahrlehrer anlegen** (Django-Admin → Fahrlehrer):
   - **Bundesland** – entscheidet über die Feiertage
   - **Planungshorizont** – wie viele Wochen im Voraus geplant wird (z. B. 4)
   - **Mindest-Vorlauf** – wie kurzfristig noch gebucht werden darf
   - optional einen Login-Benutzer verknüpfen, damit die Person die eigene
     Tagesplanung selbst pflegen kann

3. **Rhythmus-Regel anlegen** (Interner Bereich → Rhythmus-Regeln), zum Beispiel
   „Di + Do, 14:00–18:00, wöchentlich“. Bei einem mehrwöchigen Takt legt das
   Referenzdatum fest, welche Woche gemeint ist. Die Vorschau auf der
   Bearbeitungsseite zeigt sofort, welche Termine dabei herauskommen.

4. **Einzelne Tage nachpflegen** (Interner Bereich → Tagesplanung): zusätzliche
   Zeitfenster eintragen, einzelne Termine wieder löschen, Urlaub sperren.

Manuell angelegte Termine und die aus Regeln erzeugten leben friedlich
nebeneinander – der Generator fasst manuelle Termine nie an.

## Was der Generator garantiert

Der Generator (`termine/services/planung.py`) läuft täglich als Hintergrundjob
und lässt sich jederzeit von Hand anstoßen. Er hält vier Zusagen ein:

- Er fasst **nur die Zukunft** an.
- Er löscht **nie** einen gebuchten oder reservierten Termin.
- Er löscht **nie** einen von Hand angelegten Termin.
- Er ist **idempotent** – zweimal laufen lassen ändert nichts.

Wird eine Regel geändert oder deaktiviert, verschwinden die noch freien Termine
aus dieser Regel; bereits gebuchte bleiben stehen. Das ist genau das Verhalten,
das man will: eine Planänderung darf niemandem den Termin unter dem Stuhl
wegziehen.

## Kommandos

```bash
python manage.py termine_generieren                  # alle Fahrlehrer
python manage.py termine_generieren --fahrlehrer anna-berger --wochen 8
python manage.py buchungen_pflegen                   # Reservierungen, Erinnerungen, DSGVO
python manage.py jobs_einrichten                     # wiederkehrende Jobs registrieren
python manage.py qcluster                            # Worker für die Jobs
python manage.py beispieldaten                       # Demodaten
python manage.py test termine                        # Testsuite
```

Wer keinen Worker betreiben möchte, kann stattdessen `cron` benutzen:

```cron
15 3 * * *  cd /pfad/zur/app && .venv/bin/python manage.py termine_generieren
*/5 * * * * cd /pfad/zur/app && .venv/bin/python manage.py buchungen_pflegen
```

## Kalender-Abo einrichten

Jeder Fahrlehrer hat eine persönliche Abo-URL (Django-Admin → Fahrlehrer, oder
im internen Bereich auf der Übersichtsseite). Diese URL im Kalenderprogramm als
Internetkalender abonnieren – die gebuchten Termine erscheinen dann automatisch
und aktualisieren sich von selbst.

Die URL enthält ein Geheimnis und sollte nicht weitergegeben werden. Ist sie
doch einmal in falsche Hände geraten, setzt die Aktion „Kalender-Abo-Token
zurücksetzen“ im Admin sie neu.

## Datenschutz

- Buchungen werden nach `DATA_RETENTION_DAYS` (Vorgabe: 180 Tage) automatisch
  anonymisiert: Name, E-Mail, Telefon und Nachricht werden überschrieben, die
  Buchung selbst bleibt für die Statistik erhalten.
- Das Buchungsformular verlangt eine ausdrückliche Einwilligung; der Zeitpunkt
  wird gespeichert.
- Impressum und Datenschutzerklärung werden über `IMPRESSUM_URL` und
  `DATENSCHUTZ_URL` in den Seitenfuß eingebunden.

## Bekannte Grenze

Die Feiertagsauswahl arbeitet auf Ebene der **Bundesländer**. Örtlich begrenzte
Feiertage kennt sie nicht – etwa Mariä Himmelfahrt, das in Bayern nur in
überwiegend katholischen Gemeinden gilt, oder das Augsburger Friedensfest. Für
solche Tage genügt eine **Sperrzeit** über den betreffenden Tag; die
automatische Planung lässt ihn dann ebenfalls aus.

## Aufbau des Codes

```
config/            Django-Projekt (Settings, URLs, WSGI)
termine/
  models.py        Fahrlehrer, Terminart, RhythmusRegel, Sperrzeit, Termin, Buchung
  views.py         Öffentliche Buchungsseite
  staff_views.py   Interner Bereich
  forms.py         Buchungs- und Planungsformulare
  admin.py         Django-Admin
  jobs.py          Einstiegspunkte für die Hintergrundjobs
  services/
    feiertage.py       Feiertage pro Bundesland
    planung.py         Slot-Generator und manuelle Tagesplanung
    buchung.py         Buchungsablauf mit Double-Opt-in
    verfuegbarkeit.py  Abfragen für die öffentliche Seite
    ics.py             Kalendereinträge und Abo-Feed
    mail.py            E-Mail-Versand
  tests/           71 Tests für Generator, Buchung und internen Bereich
static/            CSS und htmx
```

Die Geschäftslogik liegt vollständig in `services/`. Views und Kommandos rufen
sie nur auf – dadurch ist sie ohne HTTP testbar und lässt sich später auch von
einer API aus verwenden.
