"""
eBay → Haben

Holt bezahlte eBay-Bestellungen über die Fulfillment-API (bis 2 Jahre zurück) und legt in Haben über die REST-API den
Kunden als Kontakt und einen Rechnungsentwurf an (externe Referenz = eBay-Bestellnummer, daher nie doppelt), mit
Zahlungs- und Versanddaten im Schlusstext. Die eBay-Gebühren (Finances-API, bis 5 Jahre zurück) gehen je
abgeschlossenem Monat als Eigenbeleg (PDF) unter „Belege“ an Haben. Festgeschrieben und gebucht wird nichts; das
passiert in Haben.

Mit Einstellungsseite (für „Eigene Seiten“ in Haben), Abgleich im Hintergrund, eigenem Passwort.

  python -m haben_ebay                        Webseite und Hintergrund-Abgleich starten (Container)
  python -m haben_ebay passwort               Passwort für die Seite festlegen
  python -m haben_ebay probe [--voll] [--beispiel DATEI]   einmal abgleichen, ohne zu schreiben

Nur Python-Standardbibliothek.
"""
