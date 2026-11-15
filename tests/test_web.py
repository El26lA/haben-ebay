"""Webseite, Anmeldung, Meldestelle und E-Mail über echte HTTP- und SMTP-Verbindungen auf localhost."""
import hashlib
import http.cookiejar
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from haben_ebay import archiv, basis, mail, web

from .hilfen import FakeSMTP, beispiel, datenordner_leeren

PASSWORT = "geheim-fuer-tests"
HOOK = "https://hook.example.de/ebay/konto-loeschung"


class Web(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = basis.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        datenordner_leeren()
        web.SITZUNGEN.clear()
        web.FEHLVERSUCHE.clear()
        cfg = basis.lade_config()
        cfg.update(passwort_hash=web.passwort_hash(PASSWORT), cookie_secure=False, hook_url=HOOK, hook_token="t" * 40)
        basis.speichere_config(cfg)
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def anmelden(self, bleiben="1"):
        return self.op.open(self.base + "/login", urllib.parse.urlencode({"passwort": PASSWORT, "bleiben": bleiben}).encode()).read().decode()

    def test_falsches_passwort(self):
        with self.assertRaises(urllib.error.HTTPError) as fehler:
            self.op.open(self.base + "/login", urllib.parse.urlencode({"passwort": "falsch"}).encode())
        self.assertEqual(fehler.exception.code, 401)

    def test_angemeldet_bleiben_ueberlebt_neustart(self):
        self.assertIn("Alle eBay-Bestellungen", self.anmelden())
        web.SITZUNGEN.clear()
        web.sitzungen_laden()
        self.assertIn("Alle eBay-Bestellungen", self.op.open(self.base + "/").read().decode())

    def test_ohne_bleiben_nicht_gespeichert(self):
        self.anmelden(bleiben="")
        self.assertEqual(basis.lade_json(basis.SITZUNG_FILE, {}), {})

    def test_post_ohne_csrf_wird_abgewiesen(self):
        self.anmelden()
        self.op.open(self.base + "/umschalten", b"csrf=falsch").read()
        self.assertFalse(basis.lade_config()["aktiv"])

    def test_verlauf_detail_csv_json(self):
        a = archiv.lade_archiv()
        archiv.archiviere_bestellung(a, {}, beispiel()["orders"][0], versand_holen=False)
        basis.speichere_json(basis.ARCHIV_FILE, a)
        self.anmelden()
        self.assertIn("Erika Muster", self.op.open(self.base + "/bestellungen").read().decode())
        self.assertIn("12-34567-89012", self.op.open(self.base + "/bestellung?id=12-34567-89012").read().decode())
        csv = self.op.open(self.base + "/bestellungen.csv")
        self.assertEqual(csv.headers["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn('"54,90"', csv.read().decode("utf-8-sig"))
        self.assertIn("12-34567-89012", json.loads(self.op.open(self.base + "/archiv.json").read())["bestellungen"])

    def test_meldestelle_challenge_und_nur_dieser_pfad(self):
        def holen(pfad, host="hook.example.de"):
            return urllib.request.urlopen(urllib.request.Request(self.base + pfad, headers={"Host": host}))

        antwort = json.loads(holen("/ebay/konto-loeschung?challenge_code=abc").read())
        erwartet = hashlib.sha256(("abc" + "t" * 40 + HOOK).encode()).hexdigest()
        self.assertEqual(antwort["challengeResponse"], erwartet)
        with self.assertRaises(urllib.error.HTTPError) as fehler:
            holen("/")
        self.assertEqual(fehler.exception.code, 404)

    def test_meldung_wird_abgelegt_nichts_geloescht(self):
        nachricht = {"metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
                     "notification": {"notificationId": "n1", "data": {"username": "kaeufer_x", "userId": "u1"}}}
        req = urllib.request.Request(self.base + "/ebay/konto-loeschung", data=json.dumps(nachricht).encode(),
                                     headers={"Host": "hook.example.de", "Content-Type": "application/json"}, method="POST")
        self.assertEqual(urllib.request.urlopen(req).status, 204)
        self.assertEqual(basis.lade_json(basis.LOESCH_FILE, [])[0]["username"], "kaeufer_x")


class Mail(unittest.TestCase):
    def setUp(self):
        datenordner_leeren()
        self.smtp = FakeSMTP()
        cfg = basis.lade_config()
        cfg.update(mail_aktiv=True, mail_server="127.0.0.1", mail_port=self.smtp.port, mail_sicherheit="keine",
                   mail_benutzer="ich@example.de", mail_passwort="richtig", mail_empfaenger="ziel@example.de")
        basis.speichere_config(cfg)

    def tearDown(self):
        self.smtp.schliessen()

    def warten(self, anzahl):
        for _ in range(50):
            if len(self.smtp.mails) >= anzahl:
                return
            time.sleep(0.05)

    def test_testmail_und_falsches_passwort(self):
        mail.mail_senden(basis.lade_config(), "Testmail", "Hallo")
        self.assertIn("Subject:", self.smtp.mails[0]["text"])
        cfg = basis.lade_config()
        cfg["mail_passwort"] = "falsch"
        with self.assertRaises(basis.Fehler) as fehler:
            mail.mail_senden(cfg, "Testmail", "Hallo")
        self.assertIn("Anmeldung am SMTP-Server abgelehnt", str(fehler.exception))

    def test_ablauf_der_ebay_verbindung_einmal_gemeldet(self):
        cfg = basis.lade_config()
        cfg["ebay_refresh_bis"] = time.time() + 10 * 86400
        basis.speichere_config(cfg)
        mail.lauf_pruefen("0 Fehler", False)
        self.warten(1)
        mail.lauf_pruefen("0 Fehler", False)
        time.sleep(0.5)
        self.assertEqual(len(self.smtp.mails), 1)

    def test_drei_fehllaeufe_ergeben_eine_mail(self):
        basis.STATUS["fehler_in_folge"] = 0
        for _ in range(4):
            mail.lauf_pruefen("Fehler: Haben (401)", True)
        self.warten(1)
        time.sleep(0.3)
        self.assertEqual(len(self.smtp.mails), 1)


if __name__ == "__main__":
    unittest.main()
