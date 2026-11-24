# Hinweise für KI-Agenten: eBay → Haben installieren und betreiben

Diese Datei ist für Coding-Agenten (Claude Code, Codex, Cursor, Copilot …), die einem Nutzer helfen, den Dienst einzurichten, zu aktualisieren oder Fehler zu finden. Was der Dienst tut und seine Grenzen stehen in der [README](README.md); hier steht der Ablauf. Mit dem Nutzer in seiner Sprache sprechen, im Zweifel Deutsch.

## Grundregeln

- **Geheimnisse tippt der Nutzer selbst:** eBay Cert ID, Haben-API-Token, SMTP-Passwort, das Passwort der Seite, sudo-Passwörter, die Anmeldung bei eBay. Nicht in Formulare eintragen, nicht in Dateien schreiben, nicht im Chat wiederholen. Der Agent bereitet alles andere vor und sagt genau, wo der Nutzer was eingibt.
- **Zustimmungen gibt der Nutzer selbst:** OAuth-Freigabe bei eBay, Nutzungsbedingungen, Sicherheitseinstellungen von NAS, Router und Firewall (Portfreigaben, Zugriffsprofile). Der Agent erklärt, was nötig ist und warum.
- **Vor jeder Änderung an fremden Dateien eine Sicherung** (z. B. `compose.yaml.vor-ebay`). Nie den Datenordner von Haben oder dessen Datenbank anfassen.
- **Erst prüfen, dann schreiben:** vor „Alles Alte holen“ immer „Alles Alte – Probelauf“ und danach **einen** Entwurf in Haben mit dem Nutzer ansehen (Betrag, Steuer, Datum). Ein falscher Steuersatz erzeugt sonst Hunderte falsche Entwürfe.
- `sudo` nicht umgehen; dauert eine Abfrage zu lange, läuft sie ab und muss neu gestartet werden.

## 1. Lage klären

Beim Nutzer erfragen oder nachsehen, nicht raten:

| Frage | Warum |
| --- | --- |
| Wo läuft Haben (Synology, Unraid, Linux-Server mit Docker, …), mit welcher Compose-Datei und welchem Projektnamen? | Der Dienst kommt am einfachsten **in dasselbe Compose-Projekt** wie Haben, als Dienst `ebay` neben `app`. |
| Haben-Version (mindestens 0.8.2) und ist unter Einstellungen › KI-Assistenten und API der Zugang eingeschaltet? | REST-API mit Belegen und externer Referenz |
| Ist die Firma in Haben **Kleinunternehmer** (§ 19 UStG) oder regelbesteuert? | Die Einstellung „Umsatzsteuer“ muss dazu passen. |
| Welche Domains gibt es, wie kommt HTTPS zustande (Reverse Proxy, Zertifikat)? | Zwei Adressen werden gebraucht, siehe unten. |
| Ist Haben nur im Heimnetz erreichbar? | Die Einstellungsseite darf intern bleiben, die Meldestelle **muss** öffentlich sein. |
| Benutzer- und Gruppen-ID für den Container (`id` auf dem Host) | Der Datenordner muss diesem Benutzer gehören (Synology: meist `1026:100`). |
| Gibt es schon ein eBay-Entwicklerkonto? | Freischaltung des Keysets dauert, siehe Schritt 5. |

## 2. Adressen

| Adresse | Beispiel | Erreichbar | Ziel im Reverse Proxy |
| --- | --- | --- | --- |
| Einstellungsseite | `https://ebay.example.de` | am besten nur intern/VPN | `http://127.0.0.1:8140` |
| Meldestelle für Kontolöschungen | `https://ebay-hook.example.de/ebay/konto-loeschung` | **öffentlich** (eBay ruft sie auf) | `http://127.0.0.1:8140` |

Der Dienst unterscheidet am Hostnamen: Über den Namen der Meldestelle antwortet er nur auf `/ebay/konto-loeschung`, alles andere ist 404. Deshalb dürfen beide Namen auf denselben Port zeigen. Für die Meldestelle muss Port 443 aus dem Internet zum Reverse Proxy durchgehen; das richtet der Nutzer am Router ein.

## 3. Dateien und Compose

1. Den Ordner `haben_ebay` aus diesem Repository neben die Compose-Datei von Haben kopieren (z. B. nach `<haben>/ebay/haben_ebay`) und einen Datenordner anlegen (`<haben>/ebay/data`), Besitzer = Benutzer des Containers:
   `sudo mkdir -p ebay/data && sudo chown <uid>:<gid> ebay/data && sudo chmod 700 ebay/data`
2. Den Dienst aus [`compose.yaml`](compose.yaml) in die Compose-Datei von Haben übernehmen, Pfade und `user` anpassen, die drei Adressen unter `environment` setzen. `HABEN_URL` bleibt `http://app:3000/api/v1`, solange der Dienst im selben Projekt wie Haben läuft und dessen Dienst `app` heißt.
3. Läuft der Dienst **in einem eigenen Projekt**, muss er ins Netzwerk von Haben:
   ```yaml
   services:
     ebay:
       networks: [haben]
   networks:
     haben:
       external: true
       name: <projekt>_default   # docker network ls zeigt den Namen
   ```
4. Starten: `docker compose up -d ebay` (bei Synology mit `sudo /usr/local/bin/docker compose …` oder im Container Manager über das Projekt). Prüfen: `curl -s http://127.0.0.1:8140/health` → `ok`.
5. Passwort der Seite setzt der Nutzer: `docker compose exec ebay python -m haben_ebay passwort`.

**Synology (DSM 7, Container Manager):**
- Container Manager bringt Docker Compose 2.20 mit; die Compose-Datei dieses Dienstes braucht nichts Neueres.
- Reverse Proxy: Systemsteuerung › Anmeldeportal › Erweitert › Reverse Proxy, je Adresse eine Regel (Quelle HTTPS 443, Ziel HTTP localhost 8140). Für die Einstellungsseite ein Zugriffsprofil, das nur das Heimnetz erlaubt; für die Meldestelle **kein** solches Profil.
- Zertifikat (z. B. Let's Encrypt, auch als Wildcard über DDNS) unter Systemsteuerung › Sicherheit › Zertifikat den Regeln zuordnen.
- Docker braucht sudo. Dateien lassen sich als normaler Benutzer in den Projektordner schreiben, wenn er ihm gehört; `scp` ist auf manchen DSM-Versionen gesperrt, dann `ssh nas 'cat > ziel' < datei`.
- Im Heimnetz lösen die öffentlichen Namen oft nicht auf die interne Adresse auf (Router ohne Hairpin-NAT, Zugriffsprofil sieht die öffentliche IP). Abhilfe: lokaler DNS (z. B. AdGuard/Pi-hole) mit Umschreibung der Domains auf die NAS-IP.

**Andere Systeme:** Reverse Proxy nach Wahl (Caddy, Traefik, Nginx Proxy Manager, SWAG); wichtig sind nur HTTPS, die zwei Hostnamen und dass die Meldestelle öffentlich ist.

## 4. Haben vorbereiten (Nutzer klickt, Agent sagt wo)

- Einstellungen › KI-Assistenten und API: Zugang einschalten, Token anlegen mit **Kontakte lesen, anlegen und ändern**, **Rechnungen lesen, Entwürfe anlegen, ändern und löschen**, **Belege lesen und hochladen**; den Haken bei MCP herausnehmen; Gültigkeit 1 Jahr. Das Token wird nur einmal angezeigt.
- Für Zahlungen (Haben ab 0.12): Bank › Konto hinzufügen, Art **Zahlungsdienstleister** (nie ein Bankkonto, die Quelle lässt sich nicht entfernen); dort **Externe Quelle hinzufügen** mit Anbieter `haben-ebay`, Kennung `ebay` und Stichtag; dem Token zusätzlich **Bankumsätze in freigegebene Quellen liefern** geben. Auf der Einstellungsseite „Zahlungen an Haben liefern“ erst nach einem Probelauf einschalten (README, „Zahlungen an Haben“).
- Einstellungen › Eigene Seiten: die Adresse der Einstellungsseite eintragen (Menüpunkt z. B. „eBay“).
- Einstellungen › Firma: Kleinunternehmer ja/nein merken.

## 5. eBay-Entwicklerkonto (Nutzer meldet sich an, Agent führt)

Auf [developer.ebay.com/my/keys](https://developer.ebay.com/my/keys):

1. **Production-Keyset** anlegen. eBay schaltet es erst frei, wenn die Meldestelle eingerichtet ist („Non Compliant“).
2. **Alerts & Notifications** › Marketplace Account Deletion: E-Mail-Adresse, als **Endpoint** die öffentliche Adresse der Meldestelle, als **Verification Token** den Wert, den die Einstellungsseite im Abschnitt „Kontolöschungen“ zeigt (32–80 Zeichen, erzeugt der Dienst). Speichern, dann **Send Test Notification**; eBay meldet Erfolg, und auf der Seite erscheint die Testmeldung.
   - Ruft eBay die Meldestelle nicht ab: öffentlich prüfen mit `curl "https://<meldestelle>/ebay/konto-loeschung?challenge_code=test"` → JSON mit `challengeResponse`.
3. **User Tokens** › „Get a Token from eBay via Your Application“: RuName anlegen (eBay fragt Kontaktdaten ab), Token-Typ **OAuth**, „Auth accepted URL“ = `https://<einstellungsseite>/ebay/callback`, „Auth declined URL“ = `https://<einstellungsseite>/`, OAuth einschalten, speichern.
4. App ID (Client ID), Cert ID (Client Secret) und den RuName-Namen notieren; die Cert ID tippt der Nutzer später selbst ein.

## 6. Einstellungsseite

1. Anmelden (Haken „angemeldet bleiben“).
2. Einstellungen: Haben-Token (Nutzer), App ID, Cert ID (Nutzer), RuName, Adressen, Marktplatz (`EBAY_DE` für Deutschland), **Umsatzsteuer** passend zu Haben, „Nur Bestellungen ab“ leer für alles. Speichern: oben muss „Verbindung zu Haben steht“ erscheinen.
3. **Mit eBay verbinden** → Zwischenseite → „Weiter zu eBay“ (neuer Tab). Der Nutzer meldet sich mit dem **Verkäuferkonto** an und stimmt zu (Bestellungen lesen, Finanzdaten). Danach: „eBay-Konto verbunden (gültig … Tage)“.
4. Optional E-Mail-Benachrichtigung: SMTP-Server, Port, Verschlüsselung, Benutzer, Passwort (Nutzer), Empfänger; optional „Ordner für gesendete Mails“ (IMAP, Port 993, gleiche Anmeldung; IMAP-Server leer = `imap.` statt `smtp.`); „Speichern und Testmail senden“. Die Testmail muss dann auch im Ordner liegen, sonst steht der Grund im Protokoll („nicht im Ordner … abgelegt“).
5. **„Alles Alte – Probelauf“**, Protokoll lesen. Der erste Lauf mit Finanzdaten legt den Signaturschlüssel bei eBay an („eBay-Signaturschlüssel angelegt“).
6. **„Alles Alte holen“**, nach den ersten Einträgen im Protokoll einen Entwurf in Haben öffnen und mit dem Nutzer prüfen. Erst dann **Aktivieren** (Abgleich alle 15 Minuten).

## 7. Prüfen, dass alles läuft

- `curl -s http://127.0.0.1:8140/health` → `ok`; Container `healthy`.
- Seite: Status „Abgleich aktiv“, „eBay-Konto verbunden“, letzter Lauf mit „0 Fehler“.
- Meldestelle öffentlich (siehe oben), jeder andere Pfad auf diesem Namen 404.
- In Haben: Entwürfe mit Quelle eBay, Belege `ebay-gebuehren-JJJJ-MM.pdf` „Zu prüfen“.

## 8. Fehlerbilder

| Meldung oder Zeichen | Ursache | Abhilfe |
| --- | --- | --- |
| „Haben antwortet mit 401“ beim Speichern | Token falsch, abgelaufen oder widerrufen; oft hat der Browser ein gespeichertes Passwort ins Token-Feld gefüllt | Token neu kopieren und einfügen (beginnt mit `hb_`) |
| Klick auf „Mit eBay verbinden“ tut nichts / kein Tab | Popup im eingebetteten Fenster blockiert | Seite direkt im Browser öffnen, Link „Weiter zu eBay“ selbst anklicken |
| „Ungültiger oder abgelaufener Aufruf“ nach eBay | zwischen Klick und Rückkehr mehr als 15 Minuten oder Dienst neu gestartet | erneut verbinden |
| „Abgebrochen: Haben ist auf Kleinunternehmer … eingestellt“ | Einstellung „Umsatzsteuer“ passt nicht zu Haben | Einstellung korrigieren; der falsche Entwurf ist schon wieder gelöscht |
| Finanzdaten: `Missing x-ebay-signature-key` (215001) o. ä. | Signaturschlüssel fehlt oder ungültig | Dienst neu starten; der Schlüssel wird beim nächsten Lauf angelegt. Bleibt der Fehler: Protokoll und Uhrzeit des Hosts prüfen (Signatur enthält den Zeitpunkt) |
| „eBay-Anmeldung abgelehnt“ oder „eBay-Konto noch nicht verbunden“ | Freigabe abgelaufen (18 Monate; die Seite warnt 30 Tage vorher), widerrufen oder Cert ID geändert | „Neu verbinden“ |
| „Bewertungen: für die Meldung bitte eBay neu verbinden“ | Verbindung stammt von vor der Bewertungs-Meldung (ohne Recht `commerce.feedback`); der übrige Abgleich läuft mit den alten Rechten weiter | „Neu verbinden“ |
| Ein weiterer Dienst soll dieselbe eBay-Verbindung nutzen | eBay erlaubt je App nur einen RuName mit OAuth; ein zweiter würde diesen hier abschalten | keinen zweiten RuName mit OAuth anlegen. Seine Rechte in `data/config.json` unter `ebay_scopes_zusatz` (durch Leerzeichen getrennt) eintragen, „Neu verbinden“; der andere Dienst liest `ebay_refresh_token` und fragt beim Token-Abruf nur seine Rechte an |
| „Neu verbinden“ endet bei eBay mit einem Fehler zu den Rechten (scope) | dem Keyset fehlt ein Recht | developer.ebay.com › Application Keysets › OAuth Scopes prüfen; die alte Verbindung bleibt bis dahin gültig |
| SMTP „nicht erreichbar“ oder „Verschlüsselung fehlgeschlagen“ | Port und Verschlüsselung passen nicht (587 = STARTTLS, 465 = SSL/TLS) oder ausgehende Verbindungen gesperrt | Kombination ändern, Testmail |
| Sehr langsame Läufe | Haben erlaubt 120 API-Aufrufe je Minute und Token; beim ersten vollen Lauf normal | abwarten; nicht parallel weitere Werkzeuge mit demselben Token |

Protokoll: auf der Seite unten oder `docker compose logs ebay`.

## 9. Aktualisieren

Neue Dateien nach `haben_ebay/` kopieren und **an Ort und Stelle überschreiben**. Der Dienst erkennt die Änderung nach etwa 30 Sekunden, wartet einen laufenden Abgleich ab und beendet sich; Docker startet ihn neu (`restart: unless-stopped`). Erst prüfen: `python -m unittest` im Repository. Der Datenordner bleibt unverändert; neue Einstellungen bekommen Vorgaben.

## 10. Für Änderungen am Code

- Nur Python-Standardbibliothek, Python ab 3.11; Texte, Kommentare und Commits auf Deutsch, Bezeichner wie im Bestand.
- Beträge in ganzen Cent, nie Gleitkomma für Geld.
- Tests: `python -m unittest -v`. `tests/hilfen.py` enthält ein nachgebautes Haben und einen Mini-SMTP-Server; neue Fälle dort ergänzen. `tests/test_namen.py` prüft, dass jeder benutzte Name aufgelöst wird.
- eBay-Eigenheiten, die man leicht falsch macht: `paymentSummary.payments.amount` und `paymentSummary.refunds.amount` sind Beträge **nach** Gebühren; für die Rechnung zählen `pricingSummary.total` und `lineItems[].refunds`. Finanzdaten über `apiz.ebay.com` mit Header `X-EBAY-C-MARKETPLACE-ID` und Signatur; leere Zeiträume liefern 204.
