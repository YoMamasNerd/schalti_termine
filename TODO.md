# 📋 FSM Proxy Gateway – Architektur- & Implementierungsplan

Dieser Plan fasst alle analysierten FSM-Schnittstellen aus `schalti_termine`, `django_rechn`, `django_diacard` und `portal_fsmanage` zusammen und dient als Blueprint für den Bau des zentralen **FSM-Proxy Microservices** (`fsm_gateway`).

---

## 🎯 1. Ziel & Vision
Ein zentraler, leichtgewichtiger **FastAPI-Microservice** auf DocMan (`78.47.122.27`), der als Single Source of Truth die gesamte Kommunikation mit der Fahrschulmanager-API übernimmt:
* **Zentraler Login & Session-Pooling**: Nur 1 aktiver Token / Re-Login, kein gegenseitiges Session-Abmelden der Apps.
* **Smart Caching & Rate-Limiting**: Pufferung statischer Daten (Fahrlehrer, Stammdaten, Klassen) zur Schonung von FSM.
* **Instant Hotfixing**: Ändert FSM ein Schema, wird nur der Gateway-Container gepatcht. Alle 3 Django-Apps (`schalti_termine`, `django_rechn`, `django_diacard`) laufen ohne Codeänderung oder Rebuild weiter.
* **OpenAPI / Swagger Doku**: Interaktive Dokumentation aller FSM-Endpunkte unter `http://fsm-proxy:8000/docs`.

---

## 🔍 2. Vollständige Übersicht aller FSM-Endpunkte (Konsolidiert)

Aus der Analyse der Repositories wurden folgende 10 Kern-Endpunkte identifiziert:

### A. Authentifizierung & Session (Alle Apps)
* `POST /v1/auth/login` (E-Mail/Passwort $\rightarrow$ Bearer Token)
* `POST /v1/auth/sso` (SSO Login & API-Key Extraction)
* *Gateway-Route:* `GET /auth/status` (Überprüft Token-Gültigkeit, erneuert automatisch im Hintergrund)

### B. Fahrlehrer & Kalender (`schalti_termine`)
* `GET /v1/lehrer/fahrlehrer?onlyActive=true`
  * *Gateway-Route:* `GET /fahrlehrer` (Cached für 5 Min, liefert saubere JSON-Liste mit vollen Namen, UUIDs, Status)
* `GET /v1/termine/kalender/woche/{fahrlehrer_fsm_id}?startDatum={YYYY-MM-DD}&endDatum={YYYY-MM-DD}`
  * *Gateway-Route:* `GET /kalender/{fahrlehrer_id}?von=...&bis=...` (Liefert normalisierte Events: Fahrstunden, Theorieunterricht `PT`/`TH`, Pausen, Urlaube `ST`, Privat `PP`)
* `POST /v1/termine/termin`
  * *Gateway-Route:* `POST /termine` (Erzeugt Termine/Blocker; payload-validiert, zerlegt automatisch Blöcke > 600 Min)
* `DELETE /v1/termine/termin/{termin_id}`
  * *Gateway-Route:* `DELETE /termine/{termin_id}`

### C. Schüler & Kartei (`django_rechn`, `django_diacard`)
* `POST /v2/schueler/suche`
  * *Gateway-Route:* `POST /schueler/suche` (Suchquery, Filter, Pagination, Schüler-Listen)
* `GET /v1/schueler/kartei/{student_uuid}`
  * *Gateway-Route:* `GET /schueler/{student_uuid}` (Vollständige Schülerkartei, Kontaktdaten, Ausbildungsstatus, FEK)

### D. Fahrstunden, Leistungen & Finanzen (`django_rechn`, SumUp)
* `GET /v2/fahrstunden/kunde/{student_uuid}`
  * *Gateway-Route:* `GET /schueler/{student_uuid}/fahrstunden` (Gefahrene Stunden, unbezahlte Einheiten, Kategorien)
* `GET /v2/leistungen/{student_uuid}`
  * *Gateway-Route:* `GET /schueler/{student_uuid}/leistungen` (Gebuchte Leistungen, Grundbetrag, Prüfungsgebühren, Zahlungen)
* `POST /v2/leistungen/zahlung` (Zahlung für Schüler erfassen)
  * *Gateway-Route:* `POST /schueler/{student_uuid}/zahlung` (Betrag, Datum, Zahlungsart z. B. SumUp/Kartenzahlung, Belegnummer)

### E. Optionale SumUp-Zahlungs-Integration (Zukunft)
* *Gateway-Route:* `POST /webhooks/sumup`
  * Empfängt SumUp Webhook Events (`transaction.created`/`successful`)
  * Extrahiert Schüler-Referenz/Name und bucht die Zahlung automatisch via `POST /schueler/{student_uuid}/zahlung` in FSM ein.

---

## 🏗️ 3. Geplante Gateway-Architektur

```
                                  ┌────────────────────────┐
                                  │   Fahrschulmanager     │
                                  │   api.fahrschulmanager │
                                  └───────────▲────────────┘
                                              │ HTTPS (Auth/Bearer)
                                  ┌───────────┴────────────┐
                                  │   FSM-Gateway          │
                                  │   (FastAPI Microservice│
                                  │    auf DocMan:8090)    │
                                  └─▲───────▲──────▲─────▲─┘
                                    │       │      │     │
             HTTP / Internal Docker │       │      │     └──────────────────────────┐
       ┌────────────────────────────┘       │      └──────────────┐                 │
       │                                    │                     │                 │
┌──────┴──────────────┐      ┌──────────────┴───────┐   ┌─────────┴─────────┐ ┌─────┴──────────┐
│  schalti_termine    │      │    django_rechn      │   │  django_diacard   │ │ SumUp Webhooks │
│  (Terminbuchung &   │      │ (Rechnungen &        │   │ (Schülerkarten &  │ │ (Optionale     │
│   Tagesplanung)     │      │  Leistungsabgleich)  │   │  Prüfungsman.)    │ │  Kartenzahlung)│
└─────────────────────┘      └──────────────────────┘   └───────────────────┘ └────────────────┘
```

---

## 📝 4. Roadmap zur Umsetzung

- [ ] **Schritt 1: FSM-Gateway Repo anlegen (`fsm_gateway`)**
  - FastAPI mit Pydantic v2 Models für alle Requests & Responses
  - Zentraler `FSMClient` mit Token-Cache & Auto-Login bei `401`
  - Dockerfile & `docker-compose.yml` (Port `8090` auf DocMan)
- [ ] **Schritt 2: Endpunkte implementieren & testen**
  - `/auth`, `/fahrlehrer`, `/kalender`, `/termine`
  - `/schueler/suche`, `/schueler/{id}`, `/schueler/{id}/fahrstunden`, `/schueler/{id}/leistungen`, `/schueler/{id}/zahlung`
  - Unit-Tests & Mock-Tests für alle FSM-Payloads
- [ ] **Schritt 3: Deployment auf DocMan**
  - Docker-Container in das gemeinsame interne Docker-Netzwerk einbinden
  - Healthcheck & Auto-Restart konfigurieren
- [ ] **Schritt 4: Anbindung der 3 Clients**
  - `schalti_termine`: `fsm_client.py` auf `FSM_GATEWAY_URL=http://fsm-gateway:8000` umstellen
  - `django_rechn`: `fsm_importer.py` auf das Gateway umstellen
  - `django_diacard`: Schüler-Import direkt über das Gateway anbinden
- [ ] **Schritt 5: SumUp Webhook-Handler (optional)**
  - Webhook-Endpunkt `/webhooks/sumup` für automatisches Einbuchen von Kartenzahlungen aktivieren
