# eBay → Haben

Holt eBay-Verkäufe in die selbst gehostete Buchhaltung [Haben](https://github.com/firsttris/haben): Käufer als Kontakt, jede bezahlte Bestellung als Rechnungsentwurf, die eBay-Gebühren je Monat als Beleg. Ein kleiner Dienst neben Haben, nur Python-Standardbibliothek, mit eigener Einstellungsseite, die sich in Haben unter **Eigene Seiten** einbinden lässt.

Haben bucht und schreibt fest, dieser Dienst nicht: Er legt nur Entwürfe und Belege „Zu prüfen“ über die [REST-API von Haben](https://haben.app/api.html) an.

## Was er tut

- **Bestellungen** über die eBay Fulfillment API, beim ersten Lauf zwei Jahre zurück (mehr liefert eBay nicht), danach alle 15 Minuten alles, was sich geändert hat.
- **Kontakt und Rechnungsentwurf** je bezahlter Bestellung, mit externer Referenz `ebay` + Bestellnummer, daher nie doppelt. Positionen mit Artikelnummer, Versand, Rabatte; Rechnungs- und Leistungsdatum ist der Tag der Bestellung. Im Schlusstext: Bestellung, Zahlung, Erstattungen, Versand mit Sendungsnummer.
- **Kleinunternehmer oder regelbesteuert** einstellbar. Passt die Einstellung nicht zu Haben, hält der Lauf an, statt falsche Entwürfe anzulegen.
- **Gebühren** über die Finances API (fünf Jahre zurück): je abgeschlossenem Monat ein Eigenbeleg als PDF mit allen Posten (Provision, Fixgebühr, Anzeigen, Versandetiketten, gutgeschriebene Gebühren).
- **Archiv** aller eBay-Daten im Datenordner, dauerhaft: Bestellungen auch storniert oder erstattet, Sendungen, Geldbewegungen, Auszahlungen. Was eBay später schwärzt (Name, Anschrift nach etwa 90 Tagen), bleibt erhalten. Verlauf mit Suche und Filter, Export als CSV und JSON.
- **Nachführen:** Kommen Versand- oder Zahlungsdaten später, werden sie in noch nicht festgeschriebene Entwürfe nachgetragen; leere Felder im Kontakt werden ergänzt, nie überschrieben. Wird eine Bestellung nach dem Entwurf storniert oder erstattet, gibt es einen Hinweis und eine Mail; gelöscht wird nichts.
- **Bereits bezahlt** (Haben ab 0.10): Jeder Entwurf bekommt die Zahlungsart „Bereits bezahlt“ über eBay mit dem Tag der Zahlung; die Rechnung enthält dann keine Bitte um Überweisung und keinen GiroCode.
- **Zahlungen an Haben** (Haben ab 0.12, abschaltbar): Geldbewegungen und Auszahlungen gehen über den REST-Import (`POST /api/v1/bankimporte`) in ein eBay-Konto des Zahlungsdienstleisters, je Verkauf als Brutto und Gebühr, Auszahlungen als Geldtransit. Dafür in Haben am eBay-Konto die externe Quelle „haben-ebay“ freigeben und dem Token das Recht `bank:importieren` geben. An ein Bankkonto liefert der Dienst nie.
- **Kontolöschungen:** die von eBay verlangte Meldestelle (Marketplace Account Deletion). Meldungen werden angezeigt und bei bekannten Käufern per Mail gemeldet, aber nie automatisch umgesetzt: Rechnungen unterliegen der Aufbewahrungspflicht.
- **Bewertungen** über die Feedback API: Jede neue neutrale oder negative Bewertung wird beim nächsten Lauf (alle 15 Minuten) per Mail gemeldet, mit Kommentar, Artikel, Bestellung und Käufer, und für immer im Datenordner gespeichert. Beim ersten Lauf werden die vorhandenen nur übernommen, ohne Mail. Braucht das Recht `commerce.feedback` (nach einem Update einmal „Neu verbinden“).
- **E-Mail** über SMTP: eBay-Verbindung läuft bald ab, drei Läufe in Folge mit Fehlern, Kontolöschung eines bekannten Käufers, neutrale oder negative Bewertung. Auf Wunsch kommt jede gesendete Mail zusätzlich per IMAP in einen Ordner des Postfachs (Einstellung „Ordner für gesendete Mails“).

## Grenzen

- **Zahlungen zuordnen** macht Haben nicht selbst: Die Rechnung bleibt offen, bis die Zahlung im Bankabgleich am eBay-Konto zugeordnet ist. Haben schlägt sie über Bestellnummer und Betrag vor, aber nur an festgeschriebenen Rechnungen, und auf dem Konto eines Zahlungsdienstleisters einzeln.
- **Der Gebührenbeleg ist ein Eigenbeleg**, keine Rechnung von eBay. Wer regelbesteuert ist, braucht für den Vorsteuerabzug die Gebührenrechnung von eBay.
- **E-Mail-Adressen** der Käufer gibt eBay nur in den ersten Tagen nach dem Kauf heraus, und nur als Weiterleitung `…@members.ebay.com`. Bei regelmäßigem Abruf ist sie bei neuen Bestellungen dabei, bei alten fehlt sie.
- Nur Euro.

## Voraussetzungen

- Haben ab 0.8.2 mit eingeschalteter API.
- Docker, ein Reverse Proxy mit HTTPS.
- Ein **eBay-Entwicklerkonto** (kostenlos) mit eigenem Production-Keyset. Jeder Betreiber braucht sein eigenes.
- Eine **aus dem Internet erreichbare Adresse** für die Meldestelle der Kontolöschungen; ohne sie schaltet eBay das Keyset nicht frei. Die Einstellungsseite selbst muss nicht öffentlich sein.

## Einrichten

Mit einem KI-Agenten (Claude Code, Codex, Cursor …): Er findet in [AGENTS.md](AGENTS.md) den ganzen Ablauf mit Prüfschritten und Fehlerbildern, auch für Synology. Von Hand:

1. **Dateien:** den Ordner `haben_ebay` und die `compose.yaml` neben Haben legen und den Dienst `ebay` in die Compose-Datei von Haben übernehmen (gleiches Netzwerk wie `app`). Die Adressen unter `environment` anpassen. Der Datenordner muss dem Benutzer unter `user` gehören.
2. **Reverse Proxy:** die Einstellungsseite (z. B. `https://ebay.example.de` → `127.0.0.1:8140`), am besten nur aus dem eigenen Netz; die Meldestelle (z. B. `https://ebay-hook.example.de` → `127.0.0.1:8140`) öffentlich. Ruft man den Dienst über den Namen der Meldestelle auf, antwortet er nur auf `/ebay/konto-loeschung`, alles andere ist 404.
3. **Passwort** für die Seite: `docker compose exec ebay python -m haben_ebay passwort`.
4. **eBay-Entwicklerkonto** auf [developer.ebay.com](https://developer.ebay.com/my/keys):
   - Production-Keyset anlegen. App ID und Cert ID werden auf der Seite eingetragen.
   - Unter **Alerts & Notifications** „Marketplace Account Deletion“: E-Mail, als Endpoint die öffentliche Adresse der Meldestelle, als Verification Token den Wert, den die Einstellungsseite unter „Kontolöschungen“ zeigt. „Send Test Notification“ muss klappen.
   - Unter **User Tokens** einen RuName anlegen, OAuth einschalten, als „Auth accepted URL“ `https://ebay.example.de/ebay/callback`.
5. **Haben:** unter Einstellungen › KI-Assistenten und API ein Token mit den Rechten **Kontakte lesen, anlegen und ändern**, **Rechnungen lesen, Entwürfe anlegen, ändern und löschen** und **Belege lesen und hochladen**, ohne MCP, höchste Gültigkeit. Unter **Eigene Seiten** die Adresse der Einstellungsseite eintragen.
6. **Einstellungsseite:** Token, App ID, Cert ID, RuName, Adressen und **Umsatzsteuer** (wie in Haben) eintragen, speichern, **Mit eBay verbinden**. eBay fragt nach dem Zugriff auf Bestellungen und Finanzdaten.
7. **Erst prüfen:** „Alles Alte – Probelauf“ schreibt nichts. Dann einen einzelnen Entwurf in Haben ansehen, erst danach „Alles Alte holen“ und **Aktivieren**.

### Zahlungen an Haben (ab Haben 0.12)

Damit die eBay-Zahlungen als Umsätze in Haben ankommen und die Rechnungen dort zugeordnet werden können:

1. **eBay-Konto in Haben anlegen:** Bank › **Konto hinzufügen**, Art **Zahlungsdienstleister**, Anbieter eBay, Name z. B. „eBay“. Kein Bankkonto nehmen: Eine externe Quelle lässt sich in Haben nicht wieder entfernen, und das Konto bekäme dann keinen Bankabruf mehr.
2. **Quelle freigeben:** Bank › das neue eBay-Konto › **Externe Quelle hinzufügen**. Anbieter `haben-ebay`, Kennung `ebay` (oder was auf der Einstellungsseite unter „Kennung der Quelle“ steht), Stichtag der erste Tag, ab dem Zahlungen kommen sollen, z. B. der Beginn des ältesten Jahres, das noch nicht abgeschlossen ist. Vor dem Stichtag liefert der Dienst nichts.
3. **Recht für das Token:** Dem Token aus Schritt 5 zusätzlich **Bankumsätze in freigegebene Quellen liefern** (`bank:importieren`) geben. Geht das am vorhandenen Token nicht, ein neues mit allen Rechten anlegen und auf der Einstellungsseite eintragen.
4. **Einstellungsseite:** Haken bei **Zahlungen an Haben liefern** setzen, speichern und gleich den **Probelauf** starten (bei aktivem Abgleich liefert sonst der nächste Lauf): Im Protokoll steht „Zahlungen: würde … Bewegungen ab … an Haben liefern“. Steht dort, die Quelle sei an einem Konto, das kein Zahlungsdienstleister ist, liefert der Dienst nichts; dann Schritt 1 prüfen.
5. **Jetzt abgleichen:** Danach stehen die Umsätze im eBay-Konto: je Verkauf der Betrag des Käufers und die Gebühr als eigener Umsatz, dazu Erstattungen, sonstige Gebühren und Auszahlungen. Der Kontostand dort muss dem eBay-Guthaben entsprechen. Spätere Läufe liefern nur, was neu ist.
6. **Zuordnen in Haben:** Nach dem Festschreiben schlägt Haben zu jedem Verkauf die Rechnung vor (Bestellnummer und Betrag), die Auszahlungen als Geldtransit zum Bankkonto. Bestätigen musst du in Haben selbst.

Meldet Haben einen Konflikt (eine Bewegung kam mit anderem Betrag noch einmal), liefert der Dienst nichts mehr, bis er am eBay-Konto unter **Konflikt klären** gelöst ist.

Für die Finanzdaten verlangt eBay bei Verkäufern in der EU und im Vereinigten Königreich [signierte Anfragen](https://developer.ebay.com/develop/guides/digital-signatures-for-apis). Den Schlüssel legt der Dienst selbst über die Key Management API an (drei Jahre gültig, Erneuerung 30 Tage vorher).

## Umgebungsvariablen

| Variable | Bedeutung | Vorgabe |
| --- | --- | --- |
| `DATA_DIR` | Datenordner (Einstellungen, Archiv, Sitzungen) | `/data` |
| `PORT` | Port im Container | `8080` |
| `HABEN_URL` | REST-API von Haben aus Sicht des Containers | `http://app:3000/api/v1` |
| `HABEN_ADRESSE` | Adresse von Haben im Browser (Einbetten unter „Eigene Seiten“) | leer |
| `SEITE_ADRESSE` | Adresse der Einstellungsseite (Rücksprung von eBay, Links in Mails) | leer |
| `EBAY_HOOK_URL` | öffentliche Adresse der Meldestelle | leer |
| `EBAY_MARKTPLATZ` | Marktplatz für die Finanzdaten | `EBAY_DE` |

Die Werte sind nur Vorgaben für die erste Einrichtung; danach gelten die Einstellungen auf der Seite.

## Daten und Sicherheit

- Alles liegt im Datenordner, Dateien mit Rechten `0600`: `config.json` (Tokens, Cert ID, Signaturschlüssel, SMTP-Passwort, Passwort-Hash), `archiv.json` (alle eBay-Daten inklusive Käuferdaten), `sitzungen.json` (nur Hashes der Sitzungen). Den Ordner wie die Datenbank von Haben sichern und schützen.
- Der Dienst speichert personenbezogene Daten der Käufer dauerhaft, begründet mit der Aufbewahrungspflicht für Rechnungen. Das gehört in das eigene Verzeichnis der Verarbeitungstätigkeiten.
- Anmeldung mit eigenem Passwort (PBKDF2), Sperre nach fünf Fehlversuchen, CSRF-Schutz, „angemeldet bleiben“ für ein Jahr. Ein neues Passwort meldet alle Geräte ab.
- Der Container läuft schreibgeschützt, ohne Linux-Capabilities, und spricht nur mit eBay, Haben und dem SMTP-Server.

## Aktualisieren

Neue Dateien in `haben_ebay` an Ort und Stelle überschreiben. Der Dienst merkt das nach etwa 30 Sekunden, wartet einen laufenden Abgleich ab und beendet sich; Docker startet ihn mit `restart: unless-stopped` neu.

## Entwickeln

```sh
python -m unittest -v
```

Die Tests brauchen nichts außer Python ab 3.11: Sie starten ein nachgebautes Haben und einen kleinen SMTP-Server auf localhost und prüfen die Signatur gegen die Testvektoren aus RFC 8032 und RFC 9421.

## Lizenz

[AGPL-3.0](LICENSE), wie Haben.
