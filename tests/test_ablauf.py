"""Ganzer Lauf gegen ein nachgebautes Haben: Kontakt, Entwurf, Gebührenbelege, keine Doppelten, Kleinunternehmer-Sperre."""
import json
import os
import tempfile
import unittest

from haben_ebay import abgleich, basis, bestellung

from .hilfen import FakeHaben, beispiel, datenordner_leeren, tief


class Ablauf(unittest.TestCase):
    def setUp(self):
        datenordner_leeren()
        self.haben = FakeHaben()
        cfg = basis.lade_config()
        cfg.update(haben_url=self.haben.url, haben_token="hb_test", steuersatz=0, nur_ab="")
        basis.speichere_config(cfg)
        fd, self.datei = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(beispiel(), f)

    def tearDown(self):
        self.haben.schliessen()
        os.remove(self.datei)

    def test_probe_schreibt_nichts(self):
        ergebnis = abgleich.abgleich(False, self.datei, voll=True)
        self.assertIn("1 würden angelegt", ergebnis)
        self.assertIn("2 Monatsbelege würden angelegt", ergebnis)
        self.assertEqual([a for a in self.haben.aufrufe if a[0] != "GET"], [])

    def test_echter_lauf_legt_kontakt_entwurf_und_belege_an(self):
        ergebnis = abgleich.abgleich(True, self.datei, voll=True)
        self.assertIn("1 neu angelegt, 0 Fehler", ergebnis)
        (_, _, kontakt), = self.haben.anfragen("POST", "/kontakte")
        self.assertEqual(kontakt["name"], "Erika Muster")
        self.assertEqual(kontakt["email"], "abc123@members.ebay.com")
        (_, _, entwurf), = self.haben.anfragen("POST", "/rechnungen")
        self.assertEqual(entwurf["externalSource"], "ebay")
        self.assertEqual(entwurf["externalId"], "12-34567-89012")
        self.assertEqual(entwurf["priceMode"], "brutto")
        self.assertEqual((entwurf["issueDate"], entwurf["serviceFrom"], entwurf["serviceTo"]), ("2025-03-10",) * 3)
        self.assertIn("(54,90 €)", entwurf["note"])
        self.assertEqual((entwurf["paymentMethod"], entwurf["paidVia"], entwurf["paidOn"]), ("bereits_bezahlt", "eBay", "2025-03-10"))
        belege = self.haben.anfragen("POST", "/belege")
        self.assertEqual(sorted(b[2]["externalId"] for b in belege), ["2025-03", "2025-04"])
        self.assertTrue(all(b[2]["pdf"].startswith(b"%PDF") for b in belege))

    def test_zweiter_lauf_legt_nichts_doppelt_an(self):
        abgleich.abgleich(True, self.datei, voll=True)
        abgleich.abgleich(True, self.datei, voll=False)
        self.assertEqual(len(self.haben.anfragen("POST", "/rechnungen")), 1)
        self.assertEqual(len(self.haben.anfragen("POST", "/belege")), 2)

    def test_kleinunternehmer_mit_steuersatz_haelt_den_lauf_an(self):
        cfg = basis.lade_config()
        cfg["steuersatz"] = 1900
        basis.speichere_config(cfg)
        ergebnis = abgleich.abgleich(True, self.datei, voll=True)
        self.assertTrue(ergebnis.startswith("Abgebrochen"))
        self.assertEqual(self.haben.rechnungen, {})  # wieder gelöscht
        self.assertEqual(len(self.haben.anfragen("DELETE", "/r1")), 1)

    def test_storno_nach_entwurf_meldet_statt_zu_loeschen(self):
        abgleich.abgleich(True, self.datei, voll=True)
        o = tief(beispiel()["orders"][0])
        o["orderPaymentStatus"] = "FULLY_REFUNDED"
        eintrag = {"order": o, "sendungen": [], "rechnung": {"id": "r1", "status": "draft"}}
        art, text = bestellung.verarbeite(basis.lade_config(), o, True, eintrag)
        self.assertEqual(art, "achtung")
        self.assertIn("Entwurf in Haben löschen", text)
        self.assertIn("r1", self.haben.rechnungen)


class KontaktErgaenzen(unittest.TestCase):
    def setUp(self):
        datenordner_leeren()
        self.haben = FakeHaben()
        self.cfg = basis.lade_config()
        self.cfg.update(haben_url=self.haben.url, haben_token="hb_test")
        self.k = {"name": "Erika Muster", "strasse": "Weg 1", "plz": "12345", "ort": "Musterstadt", "land": "DE",
                  "email": "abc123@members.ebay.com", "telefon": "0151"}

    def tearDown(self):
        self.haben.schliessen()

    def test_fuellt_nur_leere_felder_und_ersetzt_den_nutzernamen(self):
        self.haben.kontakte["k1"] = {"id": "k1", "name": "kaeufer_x", "strasse": "", "plz": "12345", "ort": "Musterstadt", "email": "", "telefon": "0170"}
        ergaenzt = bestellung.kontakt_ergaenzen(self.cfg, "k1", self.k, "kaeufer_x")
        self.assertEqual(sorted(ergaenzt), ["E-Mail", "Name", "Straße"])
        self.assertEqual(self.haben.kontakte["k1"]["telefon"], "0170")

    def test_mischt_keine_anschriften(self):
        self.haben.kontakte["k1"] = {"id": "k1", "name": "Erika Muster", "strasse": "", "plz": "99999", "ort": "Anderswo", "email": "x@example.de", "telefon": "1"}
        self.assertEqual(bestellung.kontakt_ergaenzen(self.cfg, "k1", self.k, "kaeufer_x"), [])


if __name__ == "__main__":
    unittest.main()
