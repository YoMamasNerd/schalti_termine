# Projektgedächtnis: Schalti Termine

Diese Datei hält die bereits getroffenen Entscheidungen fest. **Sie sind
beantwortet und werden nicht erneut zur Diskussion gestellt.** Wer hier eine
Antwort findet, fragt nicht nach, sondern arbeitet damit weiter. Gefragt wird
nur, wenn eine neue Aufgabe eine Entscheidung verlangt, die hier fehlt – und
dann einzeln und knapp, nicht als Fragenkatalog.

## Was die App ist

Öffentliche Terminbuchung für Fahrschul-Beratungen. Eigenständige Django-App,
die ohne Nextcloud läuft (die Nextcloud-Abhängigkeit wurde bewusst ersetzt).

Kerngedanke: **zwei Planungswege nebeneinander** – jeder Tag lässt sich einzeln
beplanen (unregelmäßige Arbeitszeiten eines Fahrlehrers), und zusätzlich rollen
Rhythmus-Regeln Termine mehrere Wochen im Voraus aus.

## Entschieden – nicht neu aufrollen

### Technik

| Frage | Antwort |
| --- | --- |
| Backend | Django 5.2 |
| Frontend | serverseitige Templates + htmx (eine Datei unter `static/js/`) |
| Styling | handgeschriebenes CSS in `static/css/app.css`, **kein** Node-Build |
| Datenbank | PostgreSQL, SQLite genügt für Einzelplatz |
| Hintergrundjobs | django-q2 (kein Redis), alternativ `cron` |
| Feiertage | `holidays`, offline, alle 16 Bundesländer |
| Kalender | `icalendar` für `.ics`-Anhang und Abo-Feed |
| Sprache | Deutsch – Oberfläche, Code-Kommentare, Commit-Nachrichten. Keine Übersetzungsdateien. |

### Datum und Uhrzeit

- Immer `SHORT_DATE_FORMAT` statt handgeschriebener Formatangaben; `LANGUAGE_CODE`
  steht auf `de`, damit stimmt Tag.Monat.Jahr mit führender Null. Nie `j.m.Y` –
  das mischt Tag ohne und Monat mit führender Null.
- 24-Stunden-Uhr (`H:i`), nirgends AM/PM.
- `<input type="date">`/`<input type="time">` zeichnet der **Browser** in seiner
  Sprache. Das ist nicht umstellbar und **kein Fehler der App**. Die App liefert
  das `value` in ISO – alles andere lässt das Feld leer. Kein selbstgebauter
  Kalender als Ersatz (kostet die Handy-Tastaturauswahl, bringt JavaScript).

### Oberfläche

- **Farben von fahrschule-schaltwerk.de**: Grund `#ebefe7`, Marke/Schrift
  `#2b5883`, Akzent `#c72e2e`, Pastelltöne. Die Buchung wird von dort verlinkt.
- Markenfarbe = Knöpfe, Links, aktiver Punkt, gewählter Tag. **Grün bleibt die
  Bedeutungsfarbe** für frei/erfolgreich – im Kalender nicht durch die Marke
  ersetzen.
- Schrift auf gefüllten Flächen über `--auf-akzent`, nie festes `#fff`: In der
  dunklen Fassung ist der Akzent hell.
- Nachgeschärft gegenüber der Vorlage: `#5b7995` → `#4d6b87` (Kontrast),
  `#3673b9` ungenutzt (4,2:1 zu wenig für Text).
- Öffentliche Seite: Überschriften als gesperrte Versalien mit doppeltem rotem
  Strich (`body.oeffentlich`). Interner Bereich (`body.intern`) ohne das.
- Kopfzeile klebt beim Scrollen, milchig über dem Inhalt; Navigationspunkte als
  Pillen mit eingefärbtem aktivem Bereich (`aria-current="page"`).
- Unter 760 px klappt die Navigation hinter einen Schalter – als `<details>`,
  **ohne JavaScript**. Kein Skript für Aufklappmenüs nachrüsten.
- Der Kopf darf breiter spannen (1240 px) als der Inhalt (1040 px); sonst passt
  der Name der Fahrschule nicht neben die fünf Punkte.
- Die öffentliche Seite ist auf das Handy optimiert, der interne Bereich auf den
  Schreibtisch. Das ist eine Entscheidung, kein Versehen.

### Einbettung

- Die Terminauswahl gibt es unter `/einbetten/` ohne Kopf und Fuß.
- Wer einrahmen darf, steht in `EMBED_ORIGINS`; leer = niemand. Umgesetzt über
  `frame-ancestors` plus `xframe_options_exempt`, weil `X-Frame-Options` nur
  eine Adresse kennt und die Fahrschule zwei hat (mit und ohne www).
- **Das Buchungsformular bleibt außerhalb des Rahmens** (`target="_blank"`):
  Es braucht Cookies, die Browser in fremden Rahmen blockieren. Nur der
  Kalender liest und kommt ohne aus. Ausnahme wäre eine Unterdomain derselben
  Domain – dann ginge auch das Formular im Rahmen.

### Zugriff und Login

- **Kein Kundenkonto.** Interessenten buchen ohne Registrierung.
- Zugang zur eigenen Buchung über geheimen Token-Link aus der E-Mail
  (`secrets.token_urlsafe(32)`).
- Passwort nur für die Fahrschule: Fahrlehrer sehen ausschließlich die eigenen
  Daten, `is_staff` = Inhaber sieht alle plus Django-Admin.
- Durchgesetzt an genau **zwei** Stellen (Türsteher + `_erlaubte_fahrlehrer`),
  nicht verstreut über die Views. Fremdzugriff liefert **404, nicht 403**.
- Kalender-Abo pro Fahrlehrer über eigenen Token, im Admin zurücksetzbar.

### Buchungsablauf

- **Double-Opt-in**: verbindlich erst nach Klick auf den Mail-Link.
- Unbestätigte Reservierung verfällt nach **30 Minuten**.
- Bestätigungsmail an Kunde und Fahrlehrer, je mit `.ics`-Anhang.
- Selbst-Storno über denselben Link; Erinnerungsmail vor dem Termin.
- Honigtopf-Feld gegen Bots.
- Doppelbuchung wird auf drei Ebenen verhindert: Zeilensperre, Statusprüfung in
  derselben Transaktion, partieller Unique-Index.

### Terminplanung

- Termine sind **konkrete Zeilen in der Datenbank**, keine bei jedem Aufruf
  durchgerechneten Regeln.
- Der Generator (`services/planung.py`) hält vier Zusagen, jede durch Tests
  abgesichert: nur Zukunft, nie gebuchte/reservierte löschen, nie manuelle
  Termine löschen, idempotent.
- Feiertage auf **Bundesland-Ebene**; örtliche Feiertage über eine Sperrzeit.
- Manuelle und generierte Termine leben friedlich nebeneinander.

### Datenschutz

- Anonymisierung nach `DATA_RETENTION_DAYS` (Vorgabe 180 Tage), Buchung bleibt
  für die Statistik erhalten.
- Ausdrückliche Einwilligung im Formular mit Zeitstempel.
- Impressum/Datenschutz über `IMPRESSUM_URL` / `DATENSCHUTZ_URL`.

### Bewusst **nicht** gebaut

Diese Punkte sind entschieden. Nicht erneut anbieten, außer der Nutzer fragt
ausdrücklich danach:

- Kalender nur in eine Richtung (Abo raus, kein CalDAV rein – Ersatz: Sperrzeit)
- Gruppenberatung (ein Termin = eine Person)
- Zahlung / Anzahlung
- Mehrsprachigkeit
- Django-Signale (der Buchungsablauf steht explizit in `services/buchung.py`)

## Architekturregeln

- Geschäftslogik **vollständig** in `termine/services/`. Views, Kommandos und
  Jobs rufen sie nur auf – dadurch ohne HTTP testbar.
- `apps.py` lädt nur die Systemprüfungen in `ready()`, sonst nichts.
- `checks.py` fängt Fehlkonfigurationen ab, die still unbrauchbar machen:
  `SITE_BASE_URL` auf localhost, Konsolen-Mailbackend im Betrieb, Beispielserver
  als `EMAIL_HOST`, unveränderte Geheimnisse aus `.env.example`. Alle mit
  `deploy=True` registriert, weil Djangos Testrunner `DEBUG=False` setzt.
- `docker-compose` ruft `check --deploy` **vor** der Migration auf.
- `docker-compose.yml` bleibt proxy-agnostisch (nur `127.0.0.1:8000`). TLS ist
  eine **Ergänzung**: `docker-compose.tls.yml` + `deploy/Caddyfile`. Wer auf dem
  Host schon nginx betreibt, benutzt sie nicht.
- Beide Container prüfen sich selbst: `web` über `GET /healthz` (Datenbank),
  `worker` über `manage.py jobs_pruefen` (Fahrplan statt Prozess). Der Worker
  wartet auf einen gesunden `web`, weil dort die Migration läuft.
- `/healthz` braucht zwei Ausnahmen, damit der Container sich selbst erreicht:
  Loopback in `ALLOWED_HOSTS` und `SECURE_REDIRECT_EXEMPT`. Beide nicht entfernen.
- **Keine personenbezogenen Daten ins Log** – dort greift das Löschkonzept nicht.
  Immer die Buchungsreferenz protokollieren, nie Name oder E-Mail.

## Arbeitsweise

```bash
python manage.py test termine     # muss grün bleiben
coverage run manage.py test termine && coverage report
```

- `main` trägt den stabilen Stand. Entwickelt wird auf einem eigenen Branch,
  der erst nach grüner Testsuite dorthin zurückfließt.
- Stand: 227 Tests, 97 % Zeilenabdeckung. Neue Funktionen kommen mit Tests –
  der Schwerpunkt liegt dort, wo ein Fehler unbemerkt bliebe (Jobs, Kommandos,
  Ausfallpfade des Mailversands).
- Commit-Nachrichten auf Deutsch, im Stil der bestehenden Historie: erst was
  das Problem war, dann was die Änderung tut.
- Screenshots für die README liegen als Dateien unter `docs/bilder/` – GitHub
  blockiert `data:`-URIs in Markdown.

## Offene Kleinigkeit

Bei einem einzelnen Fahrlehrer bleiben die Auswahlfelder in den **internen**
Formularen sichtbar; auf der öffentlichen Seite entfallen sie bereits.
