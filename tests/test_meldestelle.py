"""Meldestelle beim Cloudflare Worker: abholen, ablegen, dort löschen; der offene Port ist dann abgeschaltet."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from haben_ebay import basis, meldestelle, web

from .hilfen import datenordner_leeren

SCHLUESSEL = "s" * 48


def meldung(nid: str, nutzer: str) -> dict:
    return {"metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
            "notification": {"notificationId": nid, "data": {"username": nutzer, "userId": "u-" + nutzer}}}


class FalscherWorker(BaseHTTPRequestHandler):
    """Nachgebauter Worker: GET /abholen liefert, POST /abholen löscht"""
    ablage: dict = {}
    geloescht: list = []

    def log_message(self, *a):
        pass

    def antworten(self, status, daten=None):
        body = json.dumps(daten).encode() if daten is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def erlaubt(self):
        return self.path == "/abholen" and self.headers.get("Authorization") == f"Bearer {SCHLUESSEL}"

    def do_GET(self):
        if not self.erlaubt():
            return self.antworten(404)
        self.antworten(200, {"meldungen": [{"id": k, "nachricht": v} for k, v in sorted(self.ablage.items())], "weitere": False})

    def do_POST(self):
        if not self.erlaubt():
            return self.antworten(404)
        ids = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["erledigt"]
        for i in ids:
            self.ablage.pop(i, None)
        self.geloescht.extend(ids)
        self.antworten(200, {"geloescht": len(ids)})


class Abholen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FalscherWorker)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/ebay/konto-loeschung"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        datenordner_leeren()
        FalscherWorker.ablage = {"m:1": meldung("n1", "kaeufer_a"), "m:2": meldung("n2", "kaeufer_b")}
        FalscherWorker.geloescht = []
        self.cfg = basis.lade_config()
        self.cfg.update(meldestelle="cloudflare", cf_url=self.url, cf_schluessel=SCHLUESSEL)

    def test_abholen_ablegen_und_dort_loeschen(self):
        basis.speichere_json(basis.KAEUFER_FILE, {"kaeufer_a": {"name": "Erika Muster", "bestellungen": ["12-34567-89012"], "kontakt": "k1"}})
        self.assertEqual(meldestelle.meldestelle_abholen(self.cfg), "Kontolöschungen: 2 abgeholt, 2 neu")
        liste = basis.lade_json(basis.LOESCH_FILE, [])
        self.assertEqual({x["username"] for x in liste}, {"kaeufer_a", "kaeufer_b"})
        self.assertEqual(next(x for x in liste if x["username"] == "kaeufer_a")["bekannt"]["name"], "Erika Muster")
        self.assertEqual(sorted(FalscherWorker.geloescht), ["m:1", "m:2"])
        self.assertEqual(meldestelle.meldestelle_abholen(self.cfg), "Kontolöschungen: nichts Neues")

    def test_wiederholte_meldung_nicht_doppelt(self):
        meldestelle.meldestelle_abholen(self.cfg)
        FalscherWorker.ablage = {"m:3": meldung("n1", "kaeufer_a")}  # eBay wiederholt
        self.assertEqual(meldestelle.meldestelle_abholen(self.cfg), "Kontolöschungen: 1 abgeholt, 0 neu")
        self.assertEqual(len(basis.lade_json(basis.LOESCH_FILE, [])), 2)
        self.assertEqual(FalscherWorker.ablage, {})

    def test_falscher_schluessel_meldet_fehler_und_loescht_nichts(self):
        self.cfg["cf_schluessel"] = "falsch" * 8
        with self.assertRaises(basis.Fehler):
            meldestelle.meldestelle_abholen(self.cfg)
        self.assertEqual(len(FalscherWorker.ablage), 2)

    def test_offener_port_abholen_tut_nichts(self):
        self.cfg["meldestelle"] = "port"
        self.assertEqual(meldestelle.meldestelle_abholen(self.cfg), "")
        self.assertEqual(len(FalscherWorker.ablage), 2)


class PortAbgeschaltet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = basis.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_bei_cloudflare_antwortet_der_port_nicht(self):
        datenordner_leeren()
        cfg = basis.lade_config()
        cfg.update(hook_url="https://hook.example.de/ebay/konto-loeschung", hook_token="t" * 40, meldestelle="cloudflare",
                   cf_url="https://x.workers.dev/ebay/konto-loeschung")
        basis.speichere_config(cfg)
        for daten in (None, json.dumps(meldung("n9", "kaeufer_x")).encode()):
            req = urllib.request.Request(self.base + "/ebay/konto-loeschung?challenge_code=abc", data=daten,
                                         headers={"Host": "hook.example.de", "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as fehler:
                urllib.request.urlopen(req)
            self.assertEqual(fehler.exception.code, 404)
        self.assertEqual(basis.lade_json(basis.LOESCH_FILE, []), [])
        self.assertEqual(meldestelle.endpunkt(cfg), "https://x.workers.dev/ebay/konto-loeschung")


if __name__ == "__main__":
    unittest.main()
