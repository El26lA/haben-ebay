"""Neutrale und negative Bewertungen: erster Lauf ohne Mail, danach jede neue sofort (erfundene Daten)."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from haben_ebay import bewertungen as bw  # noqa: E402
from haben_ebay.basis import Fehler  # noqa: E402

from .hilfen import datenordner_leeren  # noqa: E402

ARCHIV = {"bestellungen": {"01-1": {"order": {
    "orderId": "01-1", "creationDate": "2026-11-02T10:00:00.000Z", "sellerId": "mein_laden",
    "buyer": {"username": "kaeufer_1", "buyerRegistrationAddress": {"fullName": "Erika Muster"}},
    "lineItems": [{"lineItemId": "L-1", "title": "Programm 2026"}]}}}}


def eintrag(fid, art="NEGATIVE", tag="2026-11-20"):
    return {"feedbackId": fid, "commentType": art, "feedbackEnteredDate": f"{tag}T08:00:00.000Z",
            "feedbackComment": {"commentText": "Kam spät an"},
            "providerUserDetail": {"userId": "kaeufer_1", "role": "BUYER"},
            "orderLineItemSummary": {"orderLineItemId": "L-1", "listingTitle": "Programm 2026", "listingPrice": {"value": 19.99, "currency": "EUR"}}}


class Bewertungen(unittest.TestCase):
    def setUp(self):
        datenordner_leeren()
        self.cfg = {"ebay_scopes": bw.SCOPE_BEWERTUNG, "mail_aktiv": True}
        self.ebay = {"NEGATIVE": [eintrag("F-1")], "NEUTRAL": []}
        self.anfragen = []

        def ebay_get(cfg, url, was):
            self.anfragen.append(url)
            art = "NEUTRAL" if "NEUTRAL" in url else "NEGATIVE"
            return {"feedbackEntries": self.ebay[art], "pagination": {"total": len(self.ebay[art])}}

        self.mails = []
        p1 = mock.patch.object(bw, "ebay_get", ebay_get)
        p2 = mock.patch.object(bw, "mail_senden", lambda cfg, b, t: self.mails.append((b, t)))
        p1.start(), p2.start()
        self.addCleanup(mock.patch.stopall)

    def test_erster_lauf_ohne_mail_danach_sofort(self):
        self.assertIn("1 vorhandene", bw.bewertungen_abgleich(self.cfg, ARCHIV, True))
        self.assertEqual(self.mails, [])
        self.assertIn("user_id=mein_laden", self.anfragen[0])
        self.assertIn("FEEDBACK_RECEIVED", self.anfragen[0])
        self.ebay["NEUTRAL"] = [eintrag("F-2", "NEUTRAL", "2026-11-23")]
        self.assertIn("1 gemeldet", bw.bewertungen_abgleich(self.cfg, ARCHIV, True))
        betreff, text = self.mails[0]
        self.assertEqual(betreff, "Neutrale Bewertung von kaeufer_1")
        for teil in ("Erika Muster", "23.11.2026", "Kam spät an", "Programm 2026", "Bestellung: 01-1"):
            self.assertIn(teil, text)
        bw.bewertungen_abgleich(self.cfg, ARCHIV, True)
        self.assertEqual(len(self.mails), 1)  # nicht doppelt

    def test_ohne_bewertungen_zaehlt_der_erste_lauf_trotzdem(self):
        self.ebay["NEGATIVE"] = []
        bw.bewertungen_abgleich(self.cfg, ARCHIV, True)
        self.ebay["NEGATIVE"] = [eintrag("F-1")]
        bw.bewertungen_abgleich(self.cfg, ARCHIV, True)
        self.assertEqual(len(self.mails), 1)

    def test_mail_fehlgeschlagen_wird_wiederholt(self):
        bw.bewertungen_abgleich(self.cfg, ARCHIV, True)
        self.ebay["NEGATIVE"].append(eintrag("F-3"))

        def fehler(*a):
            raise Fehler("SMTP-Server nicht erreichbar")

        with mock.patch.object(bw, "mail_senden", fehler):
            self.assertIn("0 gemeldet", bw.bewertungen_abgleich(self.cfg, ARCHIV, True))
        self.assertIn("1 gemeldet", bw.bewertungen_abgleich(self.cfg, ARCHIV, True))

    def test_ohne_recht_kein_abruf(self):
        self.assertIn("neu verbinden", bw.bewertungen_abgleich({"ebay_scopes": "x"}, ARCHIV, True))
        self.assertEqual(self.anfragen, [])


class TokenMitAltenRechten(unittest.TestCase):
    def test_refresh_nur_mit_zugestimmten_rechten(self):
        """Nach der Erweiterung von SCOPES läuft der Abgleich bis zum Neu-Verbinden mit den alten Rechten weiter."""
        from haben_ebay import ebay
        gesendet = {}
        alt = "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly"
        with mock.patch.object(ebay, "ebay_token_post", lambda cfg, form: gesendet.update(form) or {"access_token": "a"}):
            ebay._access.update(token="", bis=0.0)
            ebay.ebay_access_token({"ebay_refresh_token": "r-alt", "ebay_scopes": alt})
        self.assertEqual(gesendet["scope"], alt)

    def test_zusaetzliche_rechte_beim_verbinden(self):
        from haben_ebay import ebay
        url = ebay.ebay_consent_url({"ebay_client_id": "id", "ebay_runame": "rn", "ebay_scopes_zusatz": "https://api.ebay.com/oauth/api_scope/x"}, "s")
        self.assertIn("commerce.feedback", url)
        self.assertIn("api_scope%2Fx", url)


if __name__ == "__main__":
    unittest.main()
