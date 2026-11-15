"""Reine Datenlogik: Positionen, Schlusstext, Erstattungen, Archiv, Gebühren, PDF."""
import unittest

from haben_ebay import archiv, bestellung, gebuehren

from .hilfen import beispiel, tief


class Bestellung(unittest.TestCase):
    def setUp(self):
        self.o = beispiel()["orders"][0]

    def test_positionen_ergeben_bestellwert(self):
        lines, hinweise = bestellung.positionen(self.o, 0)
        self.assertEqual(sum(l["unitPrice"] * l["quantity"] // 1000 for l in lines), 5490)
        self.assertEqual([l["taxRate"] for l in lines], [0, 0])
        self.assertEqual(lines[0]["quantity"], 2000)
        self.assertFalse([h for h in hinweise if "≠" in h])

    def test_schlusstext_nennt_betrag_des_kaeufers_nicht_den_nettobetrag(self):
        text = "\n".join(bestellung.zusatzdaten(self.o, self.o["_sendungen"]))
        self.assertIn("Bezahlt am 10.03.2025 über eBay (54,90 €)", text)
        self.assertNotIn("48,17", text)
        self.assertNotIn("000000_X", text)
        self.assertIn("Versand am 11.03.2025 mit DHL, Sendungsnummer 00340434161234567890", text)

    def test_erstattung_aus_den_positionen(self):
        o = tief(self.o)
        o["paymentSummary"]["refunds"] = [{"refundDate": "2025-03-12T10:00:00Z", "amount": {"value": "8.80"}}]
        o["lineItems"][0]["refunds"] = [{"refundDate": "2025-03-12T10:00:00Z", "amount": {"value": "10.00"}}]
        self.assertEqual(bestellung.kunden_erstattungen(o), [("2025-03-12T10:00:00Z", 1000)])
        self.assertIn("Erstattet am 12.03.2025: 10,00 €.", bestellung.zusatzdaten(o, []))

    def test_unbekannter_versanddienst_entfaellt(self):
        s = [{"shippedDate": "2025-03-11T00:00:00Z", "shippingCarrierCode": "Other (No Carrier Match)", "shipmentTrackingNumber": "AU1"}]
        self.assertIn("Versand am 11.03.2025, Sendungsnummer AU1.", bestellung.zusatzdaten(self.o, s))

    def test_rechnungsdatum_ist_bestelltag_in_ortszeit(self):
        self.assertEqual(bestellung.bestelldatum(self.o), "2025-03-10")


class Archiv(unittest.TestCase):
    def test_geschwaerzte_daten_bleiben_erhalten(self):
        o = beispiel()["orders"][0]
        spaeter = tief(o)
        spaeter["lastModifiedDate"] = "2025-06-01T00:00:00.000Z"
        spaeter["fulfillmentStartInstructions"] = [{"shippingStep": {"shipTo": {"contactAddress": {"postalCode": "12345", "city": "Musterstadt", "countryCode": "DE"}}}}]
        spaeter["buyer"]["buyerRegistrationAddress"] = {"contactAddress": {"postalCode": "12345", "city": "Musterstadt", "countryCode": "DE"}}
        vereint = archiv.vereinen(o, spaeter)
        k = bestellung.kontakt_aus_bestellung(vereint)
        self.assertEqual((k["name"], k["strasse"], k["email"]), ("Erika Muster", "Weg 1", "abc123@members.ebay.com"))
        self.assertEqual(vereint["lastModifiedDate"], "2025-06-01T00:00:00.000Z")

    def test_status(self):
        o = beispiel()["orders"][0]
        self.assertEqual(archiv.bestellstatus(o), ("versandt", "ok"))
        o["cancelStatus"] = {"cancelState": "CANCELED"}
        self.assertEqual(archiv.bestellstatus(o)[0], "storniert")


class Gebuehren(unittest.TestCase):
    def test_monate_und_vorzeichen(self):
        u = gebuehren.monatsuebersicht(beispiel()["transactions"])
        self.assertEqual(u["2025-03"]["summe"], 673 + 310)
        # April: Versandetikett 5,49 €, bei der Erstattung gutgeschriebene Provision −1,20 €
        self.assertEqual(u["2025-04"]["summe"], 549 - 120)
        self.assertTrue(any(art.startswith("Zurück:") for _, art, _, b in u["2025-04"]["posten"] if b < 0))

    def test_pdf_ist_gueltig_aufgebaut(self):
        u = gebuehren.monatsuebersicht(beispiel()["transactions"])
        pdf, notiz = gebuehren.gebuehrenbeleg("2025-03", u["2025-03"])
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        xref = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
        self.assertTrue(pdf[xref:].startswith(b"xref"))
        self.assertIn("9,83 €", notiz)


if __name__ == "__main__":
    unittest.main()
