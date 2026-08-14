# Einrichtung mit Docker

Diese Anleitung führt von einem leeren Server zu einer laufenden Terminbuchung.
Sie ist für den Fall geschrieben, dass jemand das zum ersten Mal macht – wer
Docker kennt, springt zu [Die `.env` im Einzelnen](#die-env-im-einzelnen).

---

## Inhalt

- [Was der Server mitbringen muss](#was-der-server-mitbringen-muss)
- [Der kurze Weg](#der-kurze-weg)
- [Die `.env` im Einzelnen](#die-env-im-einzelnen)
- [Reverse Proxy mit TLS](#reverse-proxy-mit-tls)
- [Die ersten Schritte in der App](#die-ersten-schritte-in-der-app)
- [Läuft alles?](#läuft-alles)
- [Im Betrieb](#im-betrieb)
- [Wenn der Start abbricht](#wenn-der-start-abbricht)
- [Wenn die Seite läuft, aber etwas fehlt](#wenn-die-seite-läuft-aber-etwas-fehlt)

---

## Was der Server mitbringen muss

- Docker mit dem Plugin `compose` (`docker compose version` muss antworten)
- Einen Namen im DNS, der auf den Server zeigt, z. B. `termine.meine-fahrschule.de`
- Einen Reverse Proxy mit TLS davor. Entweder bringt der Stack ihn selbst mit
  (`docker-compose.tls.yml` mit Caddy), oder auf dem Host läuft schon einer –
  siehe [Reverse Proxy mit TLS](#reverse-proxy-mit-tls)
- Zugangsdaten zu einem SMTP-Postfach für den Mailversand
- Ungefähr 1 GB freien Arbeitsspeicher

Die App bringt drei Container mit: die Datenbank (`db`), den Webserver (`web`)
und einen Worker (`worker`) für die wiederkehrenden Aufgaben.

---

## Der kurze Weg

```bash
git clone https://github.com/YoMamasNerd/schalti_termine.git
cd schalti_termine

cp .env.example .env
nano .env                    # siehe nächster Abschnitt – ohne das startet nichts

docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Danach ist die App unter `http://127.0.0.1:8000` erreichbar – **nur dort**. Der
Webserver lauscht absichtlich auf der Loopback-Adresse; nach außen kommt er erst
durch den Reverse Proxy.

Beim ersten Start passiert der Reihe nach: Konfiguration prüfen, Datenbank
migrieren, Hintergrundjobs eintragen, Webserver starten. Stimmt an der
Konfiguration etwas nicht, **bricht der Container ab, statt loszulaufen**. Das
ist Absicht – siehe [Wenn der Start abbricht](#wenn-der-start-abbricht).

---

## Die `.env` im Einzelnen

`cp .env.example .env` legt eine Vorlage an. Alle Felder, die dort auf
`bitte-aendern` stehen, müssen ersetzt werden; sonst startet die App nicht.

> **Die `.env` gehört nicht ins Git-Repository.** Sie steht in `.gitignore`, weil
> sie Passwörter enthält. Für die Sicherung siehe [Im Betrieb](#im-betrieb).

### Pflicht

| Feld | Was hinein muss |
| --- | --- |
| `DJANGO_SECRET_KEY` | Ein langer Zufallswert. Erzeugen mit:<br>`python3 -c 'import secrets; print(secrets.token_urlsafe(50))'` |
| `DJANGO_DEBUG` | `false`. Im Betrieb niemals `true` – das zeigt Fremden Innereien der App. |
| `DJANGO_ALLOWED_HOSTS` | Der Name, unter dem die Seite erreichbar ist, z. B. `termine.meine-fahrschule.de`. Mehrere durch Komma getrennt. Steht hier der falsche Name, antwortet die App auf **jede** Anfrage mit `400`. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Dieselbe Adresse, aber vollständig: `https://termine.meine-fahrschule.de`. Ohne das scheitert jedes Absenden eines Formulars. |
| `SITE_BASE_URL` | Ebenfalls die vollständige öffentliche Adresse. Daraus entstehen die Bestätigungs- und Storno-Links in den E-Mails. Diese entstehen in Hintergrundjobs, wo es keine Anfrage gibt, aus der sich der Name ableiten ließe – deshalb muss er hier stehen. |
| `SITE_NAME` | Der Name der Fahrschule. Steht in der Kopfzeile und in den E-Mails. |

### Datenbank

| Feld | Was hinein muss |
| --- | --- |
| `POSTGRES_DB` | Name der Datenbank, z. B. `termine`. |
| `POSTGRES_USER` | Benutzer, z. B. `termine`. |
| `POSTGRES_PASSWORD` | Ein selbst gewähltes, langes Passwort. Es wird nirgends von Hand eingetippt – nimm einen Zufallswert. |
| `POSTGRES_HOST` | `db` – so heißt der Datenbank-Container. Nicht ändern. |
| `POSTGRES_PORT` | `5432`. Nicht ändern. |

> **Wichtig:** Fehlt `POSTGRES_DB`, fällt die App still auf SQLite **innerhalb des
> Containers** zurück. Das funktioniert – bis der Container neu gebaut wird, denn
> dann sind alle Buchungen weg. Für den Betrieb mit Docker gehört `POSTGRES_DB`
> immer in die `.env`.

### E-Mail

Ohne funktionierenden Versand kommt **keine einzige Buchung zustande**: Der
Kunde bestätigt seinen Termin über einen Link in der E-Mail. Bekommt er die Mail
nicht, verfällt die Reservierung nach 30 Minuten.

| Feld | Was hinein muss |
| --- | --- |
| `EMAIL_HOST` | Der SMTP-Server des Anbieters, z. B. `smtp.strato.de`. |
| `EMAIL_PORT` | Meist `587` (STARTTLS) oder `465` (SSL). |
| `EMAIL_HOST_USER` | Der Postfachname, meist die vollständige Adresse. |
| `EMAIL_HOST_PASSWORD` | Das Postfach-Passwort. |
| `EMAIL_USE_TLS` | `true` bei Port 587. |
| `EMAIL_USE_SSL` | `true` **statt** `EMAIL_USE_TLS`, wenn der Anbieter Port 465 verlangt. Nie beide auf `true`. |
| `DEFAULT_FROM_EMAIL` | Die Absenderadresse, z. B. `Fahrschule Muster <termine@meine-fahrschule.de>`. Nimm eine Adresse eurer eigenen Domain – viele Mailserver verwerfen Nachrichten mit fremder Absenderdomain. |

### Buchungsregeln

Alle vier haben brauchbare Vorgaben und können erst einmal so bleiben.

| Feld | Vorgabe | Bedeutung |
| --- | --- | --- |
| `RESERVATION_MINUTES` | `30` | Wie lange ein Termin nach dem Absenden des Formulars reserviert bleibt, bis der Bestätigungslink geklickt sein muss. Unter 5 Minuten warnt die App: So schnell ruft kaum jemand seine Mails ab. |
| `DEFAULT_HORIZON_WEEKS` | `4` | Wie viele Wochen im Voraus die Automatik plant, wenn beim Fahrlehrer nichts anderes hinterlegt ist. |
| `REMINDER_HOURS_BEFORE` | `24` | Wie viele Stunden vor dem Termin die Erinnerungsmail rausgeht. |
| `DATA_RETENTION_DAYS` | `180` | Nach dieser Frist werden Name, E-Mail, Telefon und Nachricht überschrieben. Die Buchung selbst bleibt für die Statistik erhalten. |

### Sicherheit

| Feld | Empfehlung | Bedeutung |
| --- | --- | --- |
| `SECURE_SSL_REDIRECT` | `true` | Leitet http auf https um. Setzt voraus, dass der Reverse Proxy `X-Forwarded-Proto` mitschickt – siehe unten. |
| `SECURE_HSTS_SECONDS` | erst `0` | Sagt Browsern, dass sie die Seite künftig nur noch über https aufrufen sollen. **Erst einschalten, wenn https zuverlässig läuft** – ein zu früh gesetzter Wert sperrt Besucher für die eingetragene Dauer aus. Wenn alles steht: `31536000` (ein Jahr). |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `false` | Nur einschalten, wenn wirklich **alle** Unterdomains https können. |
| `SECURE_HSTS_PRELOAD` | `false` | Nur für Fortgeschrittene; der Eintrag in die Browser-Listen ist schwer rückgängig zu machen. |
| `SESSION_COOKIE_SECURE` | `true` (Vorgabe) | Sitzungs-Cookie nur über https. |
| `CSRF_COOKIE_SECURE` | `true` (Vorgabe) | Formular-Token nur über https. |

### Sonstiges

| Feld | Bedeutung |
| --- | --- |
| `TIME_ZONE` | `Europe/Berlin`. Bestimmt auch die Sommerzeitumstellung. |
| `LOG_LEVEL` | `INFO` ist richtig. `WARNING` macht das Protokoll ruhiger, `DEBUG` sehr geschwätzig. |
| `TLS_DOMAIN` | Nur für `docker-compose.tls.yml`: der Name, für den Caddy das Zertifikat holt. Muss mit `SITE_BASE_URL` übereinstimmen. |
| `IMPRESSUM_URL` | Adresse eures Impressums. Erscheint im Seitenfuß. |
| `DATENSCHUTZ_URL` | Adresse der Datenschutzerklärung. Erscheint im Seitenfuß. Die Einwilligung im Buchungsformular ist ein eigenes Pflichtfeld und verlinkt sie nicht – wer das möchte, verlinkt sie über den Seitenfuß. |

---

## Reverse Proxy mit TLS

Die App bringt kein TLS mit und lauscht nur auf `127.0.0.1:8000`. Davor gehört
ein Proxy, der das Zertifikat verwaltet. Es gibt zwei Wege – nimm den ersten,
wenn auf dem Server sonst nichts läuft.

Wichtig ist in allen Fällen der Kopf `X-Forwarded-Proto`. Ohne ihn weiß die App
nicht, dass die Anfrage verschlüsselt ankam – mit `SECURE_SSL_REDIRECT=true`
entsteht dann eine endlose Umleitungsschleife.

### Weg A: Caddy im Stack (nichts am Host einzurichten)

`docker-compose.tls.yml` ergänzt den Stack um Caddy. Das Zertifikat holt es
selbst, die Weiterleitungs-Köpfe setzt es selbst.

```bash
# TLS_DOMAIN in der .env eintragen – der Name muss im DNS auf diesen Server
# zeigen und mit SITE_BASE_URL übereinstimmen.
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
```

Voraussetzung: Die Ports **80 und 443** sind von außen erreichbar. Port 80 wird
gebraucht, auch wenn nachher alles über https läuft – darüber weist Caddy nach,
dass ihm der Name gehört.

Der Befehl ist bei jedem Start derselbe; beide Dateien müssen jedes Mal
angegeben werden. Wer sich das sparen will, setzt einmalig:

```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.tls.yml
```

Zwei Dinge zum Wissen:

- Die Zertifikate liegen in einem eigenen Volume. Wird das gelöscht, holt Caddy
  neue – **Let's Encrypt begrenzt das auf fünf pro Woche und Name**, danach
  steht die Seite ohne TLS da.
- `127.0.0.1:8000` bleibt zusätzlich offen. Das ist Absicht: So funktionieren
  `curl http://127.0.0.1:8000/healthz` und die Fehlersuche weiterhin, ohne dass
  die Adresse nach außen erreichbar wäre.

Von außen gesperrt ist `/healthz` – die Container prüfen sich über `127.0.0.1`,
von außen braucht die Adresse niemand. Wer sie doch abfragen möchte, löscht den
`handle`-Block in `deploy/Caddyfile`.

### Weg B: vorhandener Proxy auf dem Host

Läuft auf dem Server schon nginx oder Traefik, bleibt `docker-compose.tls.yml`
ungenutzt und der vorhandene Proxy zeigt auf `127.0.0.1:8000`.

#### nginx als Beispiel

```nginx
server {
    listen 443 ssl;
    server_name termine.meine-fahrschule.de;

    ssl_certificate     /etc/letsencrypt/live/termine.meine-fahrschule.de/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/termine.meine-fahrschule.de/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # unbedingt setzen
    }
}

server {
    listen 80;
    server_name termine.meine-fahrschule.de;
    return 301 https://$host$request_uri;
}
```

Wer die Zustandsseite nicht aus dem Netz erreichbar haben möchte, sperrt sie im
Proxy – die Container prüfen sich selbst und brauchen sie von außen nicht:

```nginx
    location = /healthz { return 404; }
```

---

## Die ersten Schritte in der App

Nach dem Start ist die Installation leer. Die Seiten sagen das auch: Solange
kein Verwaltungskonto existiert, steht auf der Anmeldeseite und auf der
öffentlichen Buchungsseite ein Hinweis mit dem passenden Befehl.

### 1 · Verwaltungskonto anlegen

```bash
docker compose exec web python manage.py createsuperuser
```

### 2 · Terminart anlegen

Im Browser unter `/django-admin/` anmelden, dann **Terminarten → Hinzufügen**:

| Feld | Bedeutung |
| --- | --- |
| Bezeichnung | z. B. „Erstberatung" |
| Dauer | in Minuten. Bestimmt, in welche Häppchen ein Zeitfenster zerlegt wird. |
| Puffer danach | optionale Pause zwischen zwei Terminen |
| Ort | erscheint in der Bestätigungsmail und im Kalendereintrag |

### 3 · Fahrlehrer anlegen

**Fahrlehrer → Hinzufügen**:

| Feld | Bedeutung |
| --- | --- |
| Name | erscheint auf der öffentlichen Seite |
| Bundesland | entscheidet, welche Feiertage die Automatik auslässt |
| Planungshorizont | wie viele Wochen im Voraus geplant wird |
| Mindest-Vorlauf | wie kurzfristig noch gebucht werden darf, in Stunden |
| Benutzer | optional ein Login, damit die Person ihre eigene Planung selbst pflegt |
| Beschreibung | erscheint auf der Buchungsseite, wenn nur ein Fahrlehrer aktiv ist |

Ein Fahrlehrer-Login sieht ausschließlich die eigenen Daten. Nur ein Konto mit
der Kennzeichnung „Mitarbeiter" (`is_staff`) sieht alle und darf in den
Django-Admin.

### 4 · Termine anbieten

Zwei Wege, die nebeneinander funktionieren:

- **Rhythmus-Regeln** (interner Bereich → Rhythmus-Regeln): wiederkehrende
  Verfügbarkeiten wie „Di + Do, 14:00–18:00, wöchentlich". Die Vorschau zeigt
  sofort, welche Termine dabei herauskommen.
- **Tagesplanung** (interner Bereich → Tagesplanung): einzelne Tage von Hand
  beplanen, einzelne Termine löschen, Urlaub sperren.

Die Automatik läuft danach täglich von selbst weiter.

### Zum Ausprobieren: Demodaten

```bash
docker compose exec web python manage.py beispieldaten
```

Legt zwei Fahrlehrer, zwei Terminarten, Regeln und Termine an – praktisch, um
die Oberfläche einmal gefüllt zu sehen.

> **Nicht auf einer echten Installation ausführen.** Der Befehl legt nebenbei
> einen Superuser `admin` mit dem Passwort `admin` an. Auf einem Server, der aus
> dem Netz erreichbar ist, ist das eine offene Tür. Wenn es doch passiert ist:
> sofort `docker compose exec web python manage.py changepassword admin` – oder
> das Konto im Django-Admin löschen.

---

## Läuft alles?

```bash
docker compose ps
```

In der Spalte `STATUS` muss bei allen drei Containern `healthy` stehen. Das
kann beim ersten Start eine Minute dauern – der Worker startet erst, wenn der
Webserver gesund ist, weil dort die Migration läuft.

| Container | Was geprüft wird |
| --- | --- |
| `db` | `pg_isready` |
| `web` | `GET /healthz` – antwortet der Webserver, **und** ist die Datenbank erreichbar? |
| `worker` | Läuft der Fahrplan der Hintergrundjobs weiter? |

Beim Worker genügt kein Prozess-Check: Er kann laufen und trotzdem nichts
abarbeiten. Geprüft wird deshalb, ob die geplanten Jobs weiterrücken. Bleibt
einer länger als eine Viertelstunde liegen, gilt der Container als krank.

Von Hand nachsehen:

```bash
docker compose exec web  python manage.py check --deploy   # Konfiguration
docker compose exec worker python manage.py jobs_pruefen   # Fahrplan (0 = gut)
curl -i http://127.0.0.1:8000/healthz                      # Webserver
```

---

## Im Betrieb

### Protokoll ansehen

```bash
docker compose logs -f web       # Zugriffe und Ereignisse
docker compose logs -f worker    # Hintergrundjobs
```

Im Protokoll stehen Buchungsreferenzen, aber **keine Namen und keine
E-Mail-Adressen** – das Löschkonzept greift dort nicht, deshalb landen
personenbezogene Daten gar nicht erst im Log.

### Sicherung

Zwei Dinge sind zu sichern: die Datenbank und die `.env`.

```bash
# Datenbank sichern – Benutzer und Name kommen aus der Umgebung des Containers,
# damit die Befehle auch nach einer Umbenennung in der .env noch stimmen.
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > sicherung-$(date +%F).sql

# Datenbank zurückspielen
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  < sicherung-2026-08-14.sql
```

Die `.env` von Hand an einen sicheren Ort kopieren – sie enthält die Passwörter
und den Schlüssel, mit dem Sitzungen signiert werden.

### Aktualisieren

```bash
git pull
docker compose up -d --build
```

Migrationen laufen beim Start des Web-Containers automatisch mit. Sichere
vorher die Datenbank.

### Anhalten und starten

```bash
docker compose stop     # anhalten, Daten bleiben
docker compose start    # weiter
docker compose down     # Container entfernen, Daten bleiben im Volume
```

`docker compose down -v` löscht **auch das Volume und damit alle Buchungen**.

---

## Wenn der Start abbricht

Der Web-Container prüft seine Konfiguration, bevor er loslegt. Findet er einen
Fehler, bricht er ab – eine falsch konfigurierte Installation soll gar nicht
erst hochfahren, statt später tote Links zu verschicken. Die Meldung steht im
Protokoll:

```bash
docker compose logs web
```

| Meldung | Was zu tun ist |
| --- | --- |
| `POSTGRES_PASSWORD fehlt in der .env` | Die `.env` fehlt ganz oder das Feld ist leer. |
| `termine.E001` – SITE_BASE_URL zeigt auf den lokalen Rechner | Die öffentliche Adresse eintragen, nicht `localhost`. |
| `termine.E002` – E-Mails werden auf die Konsole geschrieben | `EMAIL_HOST` und die Zugangsdaten eintragen. |
| `termine.E003` – EMAIL_HOST steht noch auf einem Beispielserver | Den echten SMTP-Server des Anbieters eintragen. |
| `termine.E004` – DJANGO_SECRET_KEY steht noch auf dem Wert aus der Beispieldatei | Neuen Schlüssel erzeugen (Befehl oben in der Tabelle). |
| `termine.E005` – Unverändert aus der Beispieldatei übernommen | Die genannten Passwörter ersetzen. |
| `termine.W001` – SITE_BASE_URL ohne https | Nur eine Warnung. Die Links enthalten den Zugangs-Token einer Buchung; ohne TLS wandert der im Klartext durchs Netz. |
| `termine.W002` – DEFAULT_FROM_EMAIL auf einer Beispieladresse | Nur eine Warnung, aber viele Mailserver verwerfen solche Nachrichten. |
| `termine.W003` – RESERVATION_MINUTES sehr kurz | Nur eine Warnung. Empfohlen sind 15 bis 60 Minuten. |

Warnungen halten den Start nicht auf, Fehler schon.

---

## Wenn die Seite läuft, aber etwas fehlt

| Beobachtung | Ursache |
| --- | --- |
| Jede Seite antwortet mit `400` | `DJANGO_ALLOWED_HOSTS` enthält den Namen nicht, unter dem die Seite aufgerufen wird. |
| Formulare lassen sich nicht absenden, Meldung zu CSRF | `DJANGO_CSRF_TRUSTED_ORIGINS` fehlt oder steht ohne `https://` davor. |
| Endlose Umleitung, der Browser bricht ab | `SECURE_SSL_REDIRECT=true`, aber der Proxy setzt `X-Forwarded-Proto` nicht. |
| Der Kalender ist leer | Es gibt noch keine Terminart, keinen Fahrlehrer oder keine Regel. Zum Ausprobieren gibt es `beispieldaten` – siehe die Warnung unten. |
| Niemand bekommt eine Bestätigungsmail | Zugangsdaten prüfen. Häufig: Port 465 verlangt `EMAIL_USE_SSL=true` **statt** `EMAIL_USE_TLS`. |
| Buchungen kommen an, aber es werden keine neuen Termine erzeugt | Der Worker steht. `docker compose exec worker python manage.py jobs_pruefen` fragen und `docker compose logs worker` ansehen. |
| Die Auswahlfelder für Datum und Uhrzeit zeigen `08/13/2026` und `02:00 PM` | Kein Fehler der App: Diese Felder zeichnet der Browser in **seiner** Sprache. Ein Browser mit deutscher Oberfläche zeigt dort `13.08.2026` und `14:00`. |
| Ein Kalender-Abo ist in falsche Hände geraten | Im Django-Admin die Aktion „Kalender-Abo-Token zurücksetzen" auf dem Fahrlehrer ausführen. Das alte Abo wird damit ungültig. |

---

## Ohne Docker

Für eine Einzelplatz-Installation genügt SQLite und ein `cron`-Eintrag statt des
Workers. Der [Schnellstart in der README](../README.md#schnellstart-entwicklung)
beschreibt den Weg; die Felder der `.env` sind dieselben.
