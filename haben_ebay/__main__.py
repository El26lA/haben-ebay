"""Start: python -m haben_ebay [passwort | probe [--voll] [--beispiel DATEI]]"""
from __future__ import annotations

import base64
import collections
import datetime as dt
import getpass
import hashlib
import hmac
import html
import json
import os
import secrets
import smtplib
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .basis import *  # noqa: F403
from .abgleich import abgleich, worker
from .web import Handler, passwort_hash, sitzungen_laden, sitzungen_speichern


def selbst_neustart() -> None:
    """Ändert sich eine Datei des Pakets (neue Version eingespielt), beendet sich der Dienst nach dem laufenden Abgleich;
    Docker startet ihn mit restart: unless-stopped neu. So braucht ein Update weder sudo noch einen Neustart von Hand."""
    ordner = os.path.dirname(os.path.abspath(__file__))

    def dateien():
        return sorted(os.path.join(ordner, n) for n in os.listdir(ordner) if n.endswith(".py"))

    def stand():
        h = hashlib.sha256()
        try:
            for pfad in dateien():
                with open(pfad, "rb") as f:
                    h.update(pfad.encode() + b"\0" + f.read())
        except OSError:
            return ""
        return h.hexdigest()

    start = stand()
    while True:
        time.sleep(15)
        jetzt = stand()
        if not jetzt or jetzt == start:
            continue
        time.sleep(10)  # fertig geschrieben?
        if stand() != jetzt:
            continue
        try:
            for pfad in dateien():
                with open(pfad, encoding="utf-8") as f:
                    compile(f.read(), pfad, "exec")
        except (SyntaxError, ValueError, OSError) as ex:
            log(f"Neue Version fehlerhaft, bleibe bei der alten: {ex}")
            start = jetzt
            continue
        while STATUS["laeuft"]:
            time.sleep(5)
        log("Neue Version erkannt – Neustart")
        sitzungen_speichern()
        os._exit(0)



# ── Start ────────────────────────────────────────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    beispiel = None
    if "--beispiel" in argv:
        i = argv.index("--beispiel")
        beispiel = argv[i + 1]
        del argv[i : i + 2]
    cmd = argv[1] if len(argv) > 1 else "serve"
    if cmd == "passwort":
        pw = getpass.getpass("Neues Passwort für die eBay-Seite: ")
        if len(pw) < 10:
            print("Bitte mindestens 10 Zeichen.")
            return 1
        if getpass.getpass("Wiederholen: ") != pw:
            print("Stimmt nicht überein.")
            return 1
        cfg = lade_config()
        cfg["passwort_hash"] = passwort_hash(pw)
        speichere_config(cfg)
        speichere_json(SITZUNG_FILE, {})  # alle gespeicherten Anmeldungen ungültig
        print("Passwort gespeichert. Bestehende Anmeldungen gelten bis zum Neustart des Containers weiter.")
        return 0
    if cmd == "probe":
        try:
            print(abgleich(False, beispiel, voll="--voll" in argv))
        except Fehler as ex:
            print(f"FEHLER: {ex}")
            return 1
        return 0
    if cmd == "serve":
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(CONFIG_FILE):
            speichere_config(lade_config())
        cfg = lade_config()
        if not cfg["hook_token"]:
            # Verification token für eBay: 32 bis 80 Zeichen aus Buchstaben, Ziffern, _ und -
            cfg["hook_token"] = secrets.token_urlsafe(48)
            speichere_config(cfg)
        if not cfg["cf_schluessel"]:
            cfg["cf_schluessel"] = secrets.token_urlsafe(48)  # zum Abholen beim Cloudflare Worker
            speichere_config(cfg)
        protokoll_dauerhaft()
        sitzungen_laden()
        threading.Thread(target=worker, daemon=True).start()
        threading.Thread(target=selbst_neustart, daemon=True).start()
        log(f"eBay → Haben läuft auf Port {PORT}")
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
        return 0
    print(__doc__)
    return 2





if __name__ == "__main__":
    sys.exit(main(sys.argv))
