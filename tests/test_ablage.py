"""Gesendete Mails zusätzlich in einen IMAP-Ordner legen (nachgebautes IMAP, echter SMTP-Versand auf localhost)."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from haben_ebay import basis, mail  # noqa: E402

from .hilfen import FakeSMTP, datenordner_leeren  # noqa: E402


class FakeIMAP:
    """Merkt sich Anmeldung, angelegte Ordner und abgelegte Mails; APPEND in einen fehlenden Ordner ergibt NO."""

    ordner: set = set()
    abgelegt: list = []
    server: list = []

    def __init__(self, host, port, ssl_context=None):
        FakeIMAP.server.append((host, port))
        self.sock = mock.Mock()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, benutzer, passwort):
        if passwort != "richtig":
            raise mail.imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def append(self, name, flags, zeit, inhalt):
        if name not in FakeIMAP.ordner:
            return "NO", [b"[TRYCREATE] Mailbox does not exist"]
        FakeIMAP.abgelegt.append((name, flags, inhalt))
        return "OK", [b"APPEND completed"]

    def create(self, name):
        FakeIMAP.ordner.add(name)
        return "OK", [b""]

    def subscribe(self, name):
        return "OK", [b""]


class Ablage(unittest.TestCase):
    def setUp(self):
        datenordner_leeren()
        self.smtp = FakeSMTP()
        self.addCleanup(self.smtp.schliessen)
        FakeIMAP.ordner, FakeIMAP.abgelegt, FakeIMAP.server = set(), [], []
        p = mock.patch.object(mail.imaplib, "IMAP4_SSL", FakeIMAP)
        p.start()
        self.addCleanup(p.stop)
        self.cfg = {"mail_server": "127.0.0.1", "mail_port": self.smtp.port, "mail_sicherheit": "keine",
                    "mail_benutzer": "ich@example.de", "mail_passwort": "richtig", "mail_empfaenger": "ziel@example.de",
                    "mail_ordner": "Shop Gesendet", "mail_imap_server": "imap.example.de"}

    def test_ordner_wird_angelegt_und_mail_abgelegt(self):
        mail.mail_senden(self.cfg, "Testmail", "Hallo")
        mail.mail_senden(self.cfg, "Zweite", "Hallo")
        self.assertEqual(len(self.smtp.mails), 2)
        self.assertEqual(FakeIMAP.ordner, {'"Shop Gesendet"'})
        self.assertEqual([a[0] for a in FakeIMAP.abgelegt], ['"Shop Gesendet"'] * 2)
        self.assertEqual(FakeIMAP.abgelegt[0][1], r"(\Seen)")
        self.assertIn(b"Subject: [eBay", FakeIMAP.abgelegt[0][2])
        self.assertEqual(FakeIMAP.server[0], ("imap.example.de", 993))

    def test_imap_server_aus_smtp(self):
        self.assertEqual(mail.imap_server({"mail_server": "smtp.ionos.de"}), "imap.ionos.de")

    def test_ohne_ordner_kein_imap(self):
        self.cfg["mail_ordner"] = ""
        mail.mail_senden(self.cfg, "Testmail", "Hallo")
        self.assertEqual(FakeIMAP.server, [])

    def test_ablage_fehlgeschlagen_mail_trotzdem_gesendet(self):
        self.cfg["mail_imap_server"] = "imap.example.de"
        with mock.patch.object(FakeIMAP, "login", side_effect=mail.imaplib.IMAP4.error("AUTHENTICATIONFAILED")):
            mail.mail_senden(self.cfg, "Testmail", "Hallo")
        self.assertEqual(len(self.smtp.mails), 1)
        self.assertTrue(any("nicht im Ordner" in z for z in basis.LOG))


if __name__ == "__main__":
    unittest.main()
