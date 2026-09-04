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
- Unter 960 px klappt die Navigation hinter einen Schalter – als `<details>`,
  **ohne JavaScript**. Kein Skript für Aufklappmenüs nachrüsten. Die Grenze
  richtet sich nach der ausgeklappten Leiste: Marke, sechs Punkte und die
  Abmeldung stehen auf einer Zeile und brechen nicht um. Kommt ein Punkt
  hinzu, muss die Zahl mitwachsen – sonst schiebt der klebende Kopf die ganze
  Seite seitlich hinaus.
- **Nichts darf die Seite breiter machen als das Fenster.** Grid-Elemente
  schrumpfen von sich aus nicht unter ihren Inhalt: `.raster-zwei` benutzt
  deshalb `minmax(0, …)` in beiden Fassungen und setzt `min-width: 0` an den
  Spalten. Ohne das zieht ein einziges nicht umbrechendes Wort – die
  Abo-Adresse des Kalenders – die Seite auf Schreibtischbreite. Ein
  `overflow-x` am Kasten selbst greift dagegen nicht.
- Der Kopf darf breiter spannen (1240 px) als der Inhalt (1040 px); sonst passt
  der Name der Fahrschule nicht neben die fünf Punkte.
- Die öffentliche Seite ist auf das Handy optimiert, der interne Bereich auf den
  Schreibtisch. Das ist eine Entscheidung, kein Versehen.

### E-Mails

- Jede Mail geht **zweigestaltig** raus: Text und HTML im Bild der Seite
  (`EmailMultiAlternatives`). Die Textfassung bleibt **vollwertig** – kein
  „siehe HTML-Fassung“.
- `_senden(template=…)` bekommt den Namen **ohne Endung**; `mail/<name>.txt`
  und `mail/<name>.html` gehören zusammen.
- Gemeinsames Gerüst: `mail/_geruest.html`, dazu `_karte_termin`, `_knopf`,
  `_hinweis`. Farben aus `app.css`, aber **ausgeschrieben** – Postfächer kennen
  kein `var()`.
- Tabellen statt Grid/Flex, tragende Stile **am Element**. `<style>` nur für
  dunkle Fassung und schmale Geräte; fällt es weg, steht die helle Fassung.
- **Kein Bild im Kopf** (Gmail blockt `data:`, externe Bilder werden geblockt),
  unter jedem Knopf die Adresse zum Abtippen.
- Mails an die Fahrschule leeren den Block `ueberschrift_schmuck` und bleiben
  damit sachlich – wie der interne Bereich.
- Mails entstehen **ohne Request**, also ohne Kontextprozessor: Was der Fuß
  braucht, steht in `settings` (`IMPRESSUM_URL`, `DATENSCHUTZ_URL`) und wird in
  `_kontext()` dazugelegt.
- Im Betrieb geht jede Mail über die Warteschlange, und der Worker **lädt die
  Buchung anhand ihrer Nummer neu**. Wer eine Mail über einen Zustand
  verschickt, den dieselbe Transaktion gerade zerstört, muss deshalb direkt
  verschicken: `storno_kunde(…, direkt=True)` beim Löschen auf Kundenwunsch.
  Im Testlauf (`IM_TESTLAUF`) fällt der Umweg weg – ein Test, der diesen Pfad
  meint, muss ihn ausdrücklich einschalten.

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
- Durchgesetzt an genau **drei** Stellen (`mitarbeiter`, `inhaber`,
  `_erlaubte_fahrlehrer`), nicht verstreut über die Views. Fremdzugriff liefert
  **404, nicht 403**; die fehlende Stufe (kein Zugang, keine Inhaberrechte)
  dagegen **403**.
- **Alles Interne liegt unter `/intern/`.** `test_zugriff.py` geht die
  URL-Konfiguration durch statt einer gepflegten Liste – jede neue Adresse
  fällt von selbst in die Prüfung – und hält umgekehrt fest, dass keine
  Ansicht aus `staff_views` außerhalb von `/intern/` hängt.
- Kalender-Abo pro Fahrlehrer über eigenen Token, im internen Bereich
  zurücksetzbar.

### Buchungsablauf

- **Double-Opt-in**: Der Mail-Link führt auf eine Seite mit Knopf, gebucht
  wird per **POST**. Ein GET darf nie buchen – Postfach-Scanner rufen Links
  vorab ab und hätten sonst für den Kunden bestätigt.
- Unbestätigte Reservierung verfällt nach **30 Minuten**.
- Bestätigungsmail an Kunde und Fahrlehrer, je mit `.ics`-Anhang.
- Selbst-Storno über denselben Link; Erinnerungsmail vor dem Termin.
- Honigtopf-Feld gegen Bots.
- Doppelbuchung wird auf drei Ebenen verhindert: Zeilensperre, Statusprüfung in
  derselben Transaktion, partieller Unique-Index.

### Was die Fahrschule selbst pflegt

Der Django-Admin ist **nicht** mehr der Ort für den täglichen Bedarf. Im
internen Bereich liegen:

- **Terminarten** (`/intern/terminarten/`) – Stammdaten der ganzen Fahrschule,
  aus dem Django-Admin herausgelöst, aber weiterhin **nur für den Inhaber**:
  Eine Terminart wirkt auf das öffentliche Angebot aller Fahrlehrer, nicht auf
  den eigenen Kalender – dieselbe Grenze wie bei `aktiv`/`reihenfolge`.
  `test_zugriff.py` hält das fest. Das URL-Kürzel steht **nicht** im Formular: Es steckt in verteilten Links (`?art=…`) und
  überlebt eine Umbenennung. Die `farbe` fehlt, weil sie nirgends angezeigt
  wird – kein Bedienfeld ohne Wirkung.
- **Einstellungen** (`/intern/einstellungen/`) – Kontakt, Beschreibung,
  Bundesland, Mindest-Vorlauf, Planungshorizont; dazu Kalender-Abo neu
  erzeugen, Sperrzeiten aufheben, Terminarten im Überblick.
- **Fahrlehrer anlegen** – nur `is_staff`. Das *Login* bleibt Sache des
  Django-Admins (Benutzerverwaltung + Verknüpfung).
- `aktiv`/`reihenfolge` sieht nur der Inhaber: Sie wirken auf das öffentliche
  Angebot aller, nicht auf die eigene Planung.
- Die Einstellungsseite arbeitet als einzige mit `auch_inaktive=True`. Sonst
  wäre das Wegnehmen von „Aktiv" eine Einbahnstraße, aus der nur der
  Django-Admin herausführt.

Im Admin bleibt, was darüber hinausgeht: Benutzerkonten, die Verknüpfung von
Login und Fahrlehrer, der Blick in einzelne Datensätze.

### Terminplanung

- Termine sind **konkrete Zeilen in der Datenbank**, keine bei jedem Aufruf
  durchgerechneten Regeln.
- `horizont_wochen` ist **eine** Zahl für beides: wie weit der Generator plant
  und wie weit Kunden buchen können. Und zwar **eine fahrschulweite** Zahl,
  aus `FahrschulEinstellungen`. `Fahrlehrer.horizont_wochen` gibt es noch,
  wird aber nirgends gelesen – nur der Mindest-Vorlauf ist pro Fahrlehrer
  einstellbar (und dort nur nach oben, siehe `fruehester_start`). Wer die Zahl
  irgendwo anzeigt, nimmt den globalen Wert; das Fahrlehrer-Feld nennte sonst
  eine Zahl, nach der niemand plant.
  Durchgesetzt wird sie an denselben drei Stellen wie der Mindest-Vorlauf –
  `Termin.objects.buchbar()`, die `buchen`-View und `buchung.reservieren()`. Ohne die letzten beiden wäre sie
  nur eine Empfehlung an den Generator, und ein direkter Aufruf käme daran
  vorbei.
- Der Horizont **verkürzt** nichts rückwirkend: Schon erzeugte Termine
  dahinter bleiben stehen (der Generator räumt nur in seinem Fenster auf, und
  von Hand angelegte fasst er nie an), werden aber nicht mehr angeboten. Die
  Einstellungsseite sagt das ausdrücklich statt still zu löschen.
- Der Generator (`services/planung.py`) hält vier Zusagen, jede durch Tests
  abgesichert: nur Zukunft, nie gebuchte/reservierte löschen, nie manuelle
  Termine löschen, idempotent. **Nur Zukunft gilt auch für die Handplanung.**
- Feiertage auf **Bundesland-Ebene**; örtliche Feiertage über eine Sperrzeit.
- Manuelle und generierte Termine leben friedlich nebeneinander.
- **Termine nie direkt löschen**, immer über `planung.termine_entfernen()`:
  `Buchung.termin` steht auf PROTECT, ein Termin mit Buchungshistorie wird
  deshalb auf `ENTFALLEN` gesetzt statt gelöscht. Direktes `.delete()` endet
  im ProtectedError, sobald jemand einmal storniert hat.
- Deckt eine Regel einen entfallenen Zeitpunkt wieder ab, wird der Termin
  wiederbelebt – ein zweiter zur selben Uhrzeit ginge wegen des Unique-Index
  ohnehin nicht.
- Der Haken **„Aktiv“ an der Terminart** ist keine Empfehlung an die Anzeige:
  `buchbar()`, die `buchen`-View **und** `reservieren()` blenden abgeschaltete
  Terminarten aus. Sonst käme man über einen alten Link am Angebot vorbei.
- **Kollisionsprüfung nur über Angebots-Regeln.** Eine Sperr-Regel soll den
  Kalender blockieren; sie meldete sich sonst als Konflikt mit genau der
  Sperrzeit, die sie selbst erzeugt hat.

### Datenschutz

- Anonymisierung nach `DATA_RETENTION_DAYS` (Vorgabe 180 Tage), Buchung bleibt
  für die Statistik erhalten.
- Ausdrückliche Einwilligung im Formular mit Zeitstempel.
- Impressum/Datenschutz über `IMPRESSUM_URL` / `DATENSCHUTZ_URL`.
- **Löschen auf Kundenwunsch** über denselben Token-Link (`daten_loeschen()`),
  nur per POST. Nachweis ist der Token – derselbe wie fürs Ansehen und
  Absagen, ein zweiter Weg wäre eine zweite Angriffsfläche.
- Löschen gibt den Termin frei; der Hinweis darauf steht **vor** dem Klick auf
  der Seite. Die Absagemail geht noch raus, danach ist die Adresse weg. Ein
  vergangener Termin bleibt gebucht (Historie).
- Danach liefert der Link **404** – eine gelöschte Buchung gibt sich nicht mehr
  als vorhanden zu erkennen.
- **Nicht angefasst** (ausdrücklich entschieden): personenbezogene Daten im
  ICS-Feed, Logs des Reverse Proxy, Aufbewahrung der Sicherungen.

### Fehlerseiten

- `templates/_fehler.html` ist das gemeinsame Gerüst, `400/403/404.html` füllen
  es. Die **500 ist eigenständig mit eingebettetem CSS**: Django rendert sie
  ohne Request und ohne Kontextprozessoren – ein `{% url %}` oder `{{ SITE_NAME }}`
  darin wäre ein Fehler beim Anzeigen des Fehlers.
- Überschrift und Reiter (`titel`) stehen in **zwei getrennten Blöcken**; eine
  Variable aus dem einen ist im anderen nicht sichtbar. Jede Seite setzt beides.

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
- Stand: 409 Tests, 81 % Zeilenabdeckung. Der Kern (Buchung, Planung, Views,
  Modelle) liegt über 90 %; die Lücke steckt fast ganz in den später
  dazugekommenen Teilen: `services/fsm_sync.py` (62 %), `social_adapter.py`
  (44 %) und `management/commands/import_alt_kalender.py` (0 %). Neue
  Funktionen kommen mit Tests – der Schwerpunkt liegt dort, wo ein Fehler
  unbemerkt bliebe (Jobs, Kommandos, Ausfallpfade des Mailversands).
- Commit-Nachrichten auf Deutsch, im Stil der bestehenden Historie: erst was
  das Problem war, dann was die Änderung tut.
- Screenshots für die README liegen als Dateien unter `docs/bilder/` – GitHub
  blockiert `data:`-URIs in Markdown.

## Offene Kleinigkeit

Bei einem einzelnen Fahrlehrer bleiben die Auswahlfelder in den **internen**
Formularen sichtbar; auf der öffentlichen Seite entfallen sie bereits.
