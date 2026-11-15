"""E-Mail-Benachrichtigung über SMTP, gedrosselt je Anlass."""
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


# ── E-Mail-Benachrichtigung ──────────────────────────────────────────────────────────────────────────────────────


def mail_senden(cfg: dict, betreff: str, text: str) -> None:
    """Schickt eine Mail über den eingestellten SMTP-Server; wirft Fehler mit verständlicher Meldung."""
    if not (cfg.get("mail_server") and cfg.get("mail_empfaenger")):
        raise Fehler("SMTP-Server und Empfänger fehlen")
    nachricht = EmailMessage()
    absender = cfg.get("mail_absender") or cfg.get("mail_benutzer") or cfg["mail_empfaenger"]
    nachricht["From"] = f"eBay → Haben <{absender}>"
    nachricht["To"] = cfg["mail_empfaenger"]
    nachricht["Subject"] = f"[eBay → Haben] {betreff}"
    nachricht["Date"] = formatdate(localtime=True)
    nachricht["Message-ID"] = make_msgid(domain=absender.split("@")[-1] if "@" in absender else None)
    seite_url = cfg.get("seite_adresse") or ""
    nachricht.set_content(f"{text}\n\n--\neBay → Haben auf dem NAS\n{seite_url}\n")
    port = int(cfg.get("mail_port") or 587)
    art = cfg.get("mail_sicherheit") or "starttls"
    kontext = ssl.create_default_context()
    try:
        if art == "ssl":
            verbindung = smtplib.SMTP_SSL(cfg["mail_server"], port, timeout=30, context=kontext)
        else:
            verbindung = smtplib.SMTP(cfg["mail_server"], port, timeout=30)
        with verbindung as smtp:
            smtp.ehlo()
            if art == "starttls":
                smtp.starttls(context=kontext)
                smtp.ehlo()
            if cfg.get("mail_benutzer"):
                smtp.login(cfg["mail_benutzer"], cfg.get("mail_passwort") or "")
            smtp.send_message(nachricht)
    except smtplib.SMTPAuthenticationError as ex:
        raise Fehler(f"Anmeldung am SMTP-Server abgelehnt ({ex.smtp_code}): Benutzer oder Passwort prüfen") from ex
    except smtplib.SMTPException as ex:
        raise Fehler(f"SMTP-Fehler: {ex}") from ex
    except ssl.SSLError as ex:
        raise Fehler(f"Verschlüsselung fehlgeschlagen ({ex.reason}): Port und Verschlüsselung passen nicht zusammen?") from ex
    except OSError as ex:
        raise Fehler(f"SMTP-Server {cfg['mail_server']}:{port} nicht erreichbar: {ex}") from ex


def melden(schluessel: str, betreff: str, text: str, abstand_stunden: float = 24) -> None:
    """Benachrichtigung per Mail, je Schlüssel höchstens alle abstand_stunden; läuft im Hintergrund."""
    cfg = lade_config()
    if not cfg.get("mail_aktiv"):
        return
    with LOCK:
        zuletzt = lade_json(MELDUNG_FILE, {})
        if time.time() - zuletzt.get(schluessel, 0) < abstand_stunden * 3600:
            return
        zuletzt[schluessel] = time.time()
        speichere_json(MELDUNG_FILE, zuletzt)

    def senden():
        try:
            mail_senden(cfg, betreff, text)
            log(f"Mail gesendet: {betreff}")
        except Fehler as ex:
            log(f"Mail „{betreff}“ nicht gesendet: {ex}")
            with LOCK:
                z = lade_json(MELDUNG_FILE, {})
                z.pop(schluessel, None)  # beim nächsten Mal erneut versuchen
                speichere_json(MELDUNG_FILE, z)

    threading.Thread(target=senden, daemon=True).start()


def lauf_pruefen(ergebnis: str, fehlgeschlagen: bool) -> None:
    """Nach jedem Lauf: dauerhafte Fehler und ablaufende Freigaben melden."""
    cfg = lade_config()
    if fehlgeschlagen or "Fehler:" in ergebnis or "Abgebrochen" in ergebnis:
        STATUS["fehler_in_folge"] = STATUS.get("fehler_in_folge", 0) + 1
    else:
        STATUS["fehler_in_folge"] = 0
    if STATUS["fehler_in_folge"] >= 3:
        melden("fehler", "Abgleich schlägt fehl",
               f"Die letzten {STATUS['fehler_in_folge']} Läufe des eBay-Abgleichs hatten Fehler.\n\nLetztes Ergebnis:\n{ergebnis}\n\n"
               "Häufige Ursachen: Haben-API-Token abgelaufen oder widerrufen, eBay-Verbindung abgelaufen, NAS ohne Internet.\n"
               "Details im Protokoll auf der eBay-Seite.", 24)
    bis = cfg.get("ebay_refresh_bis") or 0
    rest = (bis - time.time()) / 86400 if bis else None
    if rest is not None and rest < 30:
        tage = max(0, int(rest))
        melden("ebay_ablauf", f"eBay-Verbindung läuft in {tage} Tagen ab",
               f"Die Freigabe für den eBay-Abgleich läuft in {tage} Tagen ab "
               f"({dt.datetime.fromtimestamp(bis).strftime('%d.%m.%Y')}).\n\n"
               "Bitte auf der eBay-Seite „Neu verbinden“ klicken und bei eBay zustimmen. Danach gilt sie wieder 18 Monate.\n"
               "Ohne neue Verbindung kommen keine Bestellungen mehr an, und Namen und Adressen gehen nach etwa 3 Monaten bei eBay verloren.",
               24 if rest < 7 else 24 * 7)
