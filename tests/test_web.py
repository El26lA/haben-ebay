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
        self.assertIn("<small>Abgleich</small>", self.anmelden())
        web.SITZUNGEN.clear()
        web.sitzungen_laden()
        self.assertIn("<small>Abgleich</small>", self.op.open(self.base + "/").read().decode())

    def test_reiter_und_seiten(self):
        start = self.anmelden()
        for pfad, titel in (("/", "Dashboard"), ("/bestellungen", "Bestellungen"), ("/einstellungen", "Einstellungen"),
                            ("/kontoloeschungen", "Kontolöschungen"), ("/protokoll", "Protokoll")):
            self.assertIn(f"<a href='{pfad}' class=''>{titel}</a>" if pfad != "/" else f"<a href='/' class='an'>{titel}</a>", start)
        self.assertIn("<small>Abgleich</small><b>pausiert</b>", start)
        self.assertIn("<small>Nächster Lauf</small>", start)
        self.assertIn("<small>eBay-Konto</small><b>nicht verbunden</b>", start)
        self.assertIn("<small>Umsatzsteuer</small>", start)
        self.assertIn("<small>Letzter Lauf</small>", start)
        self.assertIn("Zuletzt übernommen", start)
        self.assertIn("title='Probelauf: zeigt", start)
        self.assertNotIn("name=intervall_minuten", start)  # Einstellungen nur im eigenen Reiter
        einst = self.op.open(self.base + "/einstellungen").read().decode()
        for feld in ("intervall_minuten", "rueckblick_tage", "nur_ab", "steuersatz", "haben_token", "ebay_client_id", "mail_server"):
            self.assertIn(f"name={feld}", einst)
        self.assertIn("eBay-Konto verbinden", einst)
        self.assertIn("<a href='/einstellungen' class='an'>", einst)
        self.assertIn("Im eBay-Entwicklerportal", self.op.open(self.base + "/kontoloeschungen").read().decode())
        self.assertIn("<h2>Protokoll</h2>", self.op.open(self.base + "/protokoll").read().decode())
        best = self.op.open(self.base + "/bestellungen").read().decode()
        for ziel in ("/auszahlungen", "/bestellungen/zuletzt", "/bestellungen.csv", "/archiv.json"):
            self.assertIn(f"href={ziel}>", best)
        self.assertIn("<a href='/bestellungen' class='an'>", self.op.open(self.base + "/auszahlungen").read().decode())

    def test_speichern_fuehrt_zurueck_zu_den_einstellungen(self):
        self.anmelden()
        csrf = next(iter(web.SITZUNGEN.values()))["csrf"]
        antwort = self.op.open(self.base + "/einstellungen", urllib.parse.urlencode(
            {"csrf": csrf, "intervall_minuten": "20", "rueckblick_tage": "30", "steuersatz": "0", "haben_url": "http://127.0.0.1:9/api/v1"}).encode())
        self.assertEqual(antwort.url, self.base + "/einstellungen")
        seite = antwort.read().decode()
        self.assertIn("<dialog open class=fenster", seite)  # Fenster mit „OK“
        self.assertIn("<form method=dialog", seite)
        self.assertIn("eben neu gelesen", seite)
        self.assertIn("<td>Intervall</td><td>20 Minuten</td>", seite)
        self.assertIn("Kleinunternehmer", seite)
        self.assertEqual(basis.lade_config()["intervall_minuten"], 20)
        self.assertNotIn("<dialog", self.op.open(self.base + "/einstellungen").read().decode())  # nur einmal
        falsch = self.op.open(self.base + "/einstellungen", urllib.parse.urlencode(
            {"csrf": csrf, "intervall_minuten": "2", "rueckblick_tage": "30", "haben_url": "http://127.0.0.1:9/api/v1"}).encode()).read().decode()
        self.assertIn("<h2 id=ft class=bad>Achtung</h2>", falsch)
        self.assertIn("Nicht gespeichert", falsch)
        self.assertEqual(basis.lade_config()["intervall_minuten"], 20)

    def test_protokoll_und_verlauf_ueberstehen_neustart(self):
        basis.LOG.clear()
        basis.VERLAUF.clear()
        basis.protokoll_dauerhaft()
        try:
            basis.log("vor dem Neustart")
            basis.verlauf("12-34567-89012", "neu", "Entwurf angelegt")
            basis.DAUERHAFT["an"] = False  # Neustart: Anzeige leer, Dateien bleiben
            basis.LOG.clear()
            basis.VERLAUF.clear()
            basis.log("beim Start")
            basis.protokoll_dauerhaft()
            self.assertTrue(basis.LOG[0].endswith("vor dem Neustart"))
            self.assertTrue(basis.LOG[-1].endswith("beim Start"))
            self.assertEqual(basis.VERLAUF[0][1:], ("12-34567-89012", "neu", "Entwurf angelegt"))
            self.anmelden()
            self.assertIn("vor dem Neustart", self.op.open(self.base + "/protokoll").read().decode())
            self.assertIn("12-34567-89012", self.op.open(self.base + "/").read().decode())
            ganz = self.op.open(self.base + "/protokoll.txt").read().decode()
            self.assertIn("vor dem Neustart", ganz)
            self.assertIn("beim Start", ganz)
        finally:
            basis.DAUERHAFT["an"] = False

    def test_thema_hell_grau_dunkel(self):
        self.anmelden()
        seite = self.op.open(self.base + "/thema?t=grau&z=%2Fprotokoll")
        self.assertEqual(seite.url, self.base + "/protokoll")
        self.assertIn("<html lang=de data-thema=grau>", seite.read().decode())
        self.assertEqual(self.op.open(self.base + "/thema?t=dunkel&z=https%3A%2F%2Fboese.example").url, self.base + "/")
        self.assertIn("<html lang=de><head>", self.op.open(self.base + "/thema?t=auto&z=%2F").read().decode())  # wieder nach System

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
