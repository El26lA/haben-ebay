"""Grundlagen: Einstellungen, Dateien im Datenordner, Protokoll, Fehler, HTTP-Client, Formatierung."""
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


DATA_DIR = os.environ.get("DATA_DIR", "/data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
PORT = int(os.environ.get("PORT", "8080"))

EBAY_API = "https://api.ebay.com"
EBAY_FIN = "https://apiz.ebay.com"
MARKTPLATZ = "EBAY_DE"
EBAY_AUTH = "https://auth.ebay.com/oauth2/authorize"
SCOPES = ("https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly https://api.ebay.com/oauth/api_scope/sell.finances"
          " https://api.ebay.com/oauth/api_scope/commerce.feedback")
BESTELL_TAGE_MAX = 729  # getOrders: höchstens zwei Jahre zurück
GEBUEHR_MONATE_MAX = 59  # getTransactions: höchstens fünf Jahre zurück, je Abfrage höchstens 36 Monate
QUELLE_GEBUEHR = "ebay-gebuehren"
QUELLE = "ebay"

DEFAULTS = {
    "aktiv": False,
    "haben_url": os.environ.get("HABEN_URL", "http://app:3000/api/v1"),
    # Öffentliche Adressen: Haben (für frame-ancestors und Links), diese Seite (für die Rücksprungadresse bei eBay)
    "haben_adresse": os.environ.get("HABEN_ADRESSE", ""),
    "seite_adresse": os.environ.get("SEITE_ADRESSE", ""),
    "ebay_marktplatz": os.environ.get("EBAY_MARKTPLATZ", "EBAY_DE"),
    "haben_token": "",
    "ebay_client_id": "",
    "ebay_client_secret": "",
    "ebay_runame": "",
    "ebay_refresh_token": "",
    "ebay_refresh_bis": 0,
    "ebay_scopes": "",  # mit welchen Rechten verbunden wurde
    "ebay_scopes_zusatz": "",  # weitere Rechte (durch Leerzeichen getrennt) für Dienste, die diese Verbindung mitnutzen
    "letzter_abruf": "",
    # Signaturschlüssel für die Finances-API (eBay verlangt bei EU-Verkäufern signierte Anfragen, RFC 9421)
    "sign_jwe": "",
    "sign_privat": "",
    "sign_id": "",
    "sign_bis": 0,  # Beginn des letzten erfolgreichen Abrufs (UTC), danach nur Geändertes holen
    "steuersatz": 1900,
    "intervall_minuten": 15,
    "rueckblick_tage": 30,
    "nur_ab": "",
    "zahlungen_an": False,  # Geldbewegungen und Auszahlungen an Haben liefern (REST-Import, Haben ab 0.12)
    "zahlungen_kennung": "ebay",  # Kennung der Quelle in Haben (externalAccountId)
    "passwort_hash": "",
    "cookie_secure": True,
    # Meldestelle für eBays Marketplace Account Deletion: einzige von außen erreichbare Adresse
    "hook_url": os.environ.get("EBAY_HOOK_URL", ""),
    "hook_token": "",
    # E-Mail-Benachrichtigung (SMTP)
    "mail_aktiv": False,
    "mail_server": "",
    "mail_port": 587,
    "mail_sicherheit": "starttls",  # starttls, ssl oder keine
    "mail_benutzer": "",
    "mail_passwort": "",
    "mail_absender": "",
    "mail_empfaenger": "",
    "mail_ordner": "",  # IMAP-Ordner, in den jede gesendete Mail kommt (leer = aus)
    "mail_imap_server": "",  # leer = SMTP-Server mit imap. statt smtp.
}

LOCK = threading.RLock()
KAEUFER_FILE = os.path.join(DATA_DIR, "kaeufer.json")  # eBay-Nutzername → Name, Bestellungen, Kontakt in Haben
LOESCH_FILE = os.path.join(DATA_DIR, "kontoloeschungen.json")  # Meldungen von eBay, neueste zuerst
ARCHIV_FILE = os.path.join(DATA_DIR, "archiv.json")  # alles, was eBay je geliefert hat: Bestellungen, Sendungen, Geldbewegungen, Auszahlungen
SITZUNG_FILE = os.path.join(DATA_DIR, "sitzungen.json")  # SHA-256 der Sitzungs-ID → {"csrf", "bis"}
GEBUEHR_FILE = os.path.join(DATA_DIR, "gebuehren.json")
MELDUNG_FILE = os.path.join(DATA_DIR, "meldungen.json")  # Schlüssel → Zeitpunkt der letzten Mail (nicht zu oft melden)  # "JJJJ-MM" → {"beleg", "summe", "posten"}: schon an Haben
HOOK_PFAD = "/ebay/konto-loeschung"
LOESCH_MAX = 2000  # die Meldestelle ist öffentlich: Liste begrenzen
LOG = collections.deque(maxlen=1000)  # Anzeige; mit protokoll_dauerhaft() steht alles in PROTOKOLL_FILE
VERLAUF = collections.deque(maxlen=300)  # (zeit, orderId, ergebnis, text), neueste zuerst
PROTOKOLL_FILE = os.path.join(DATA_DIR, "protokoll.log")  # jede Protokollzeile, nur anhängen, nie gekürzt
VERLAUF_FILE = os.path.join(DATA_DIR, "verlauf.log")  # „Zuletzt übernommen“, je Zeile ein JSON-Eintrag, nur anhängen
DAUERHAFT = {"an": False}  # nur der Dienst selbst schreibt die Dateien, nicht Tests oder Dienste, die das Paket mitnutzen
_DATEI_LOCK = threading.Lock()
STATUS = {"letzter_lauf": None, "letztes_ergebnis": "noch nicht gelaufen", "laeuft": False}
WECKER = threading.Event()



# ── Konfiguration ────────────────────────────────────────────────────────────────────────────────────────────────


def lade_config() -> dict:
    with LOCK:
        cfg = dict(DEFAULTS)
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except FileNotFoundError:
            pass
        return cfg


def speichere_config(cfg: dict) -> None:
    with LOCK:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG_FILE)


def lade_json(pfad: str, leer):
    with LOCK:
        try:
            with open(pfad, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return leer


def speichere_json(pfad: str, daten) -> None:
    with LOCK:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(daten, f, indent=1, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, pfad)



def _anhaengen(pfad: str, zeile: str) -> None:
    if not DAUERHAFT["an"]:
        return
    try:
        with _DATEI_LOCK, open(pfad, "a", encoding="utf-8") as f:
            f.write(zeile.replace("\n", " ") + "\n")
    except OSError:
        pass  # das Protokoll darf den Dienst nie anhalten


def letzte_zeilen(pfad: str, n: int) -> list[str]:
    try:
        with open(pfad, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 600 * n))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []


def protokoll_dauerhaft() -> None:
    """Beim Start des Dienstes: Protokoll und Verlauf ab jetzt auch in Dateien; die letzten Einträge zurück in die Anzeige"""
    alt_log, alt_verlauf = list(LOG), list(VERLAUF)
    LOG.clear()
    LOG.extend(letzte_zeilen(PROTOKOLL_FILE, LOG.maxlen))
    VERLAUF.clear()
    for zeile in letzte_zeilen(VERLAUF_FILE, VERLAUF.maxlen):
        try:
            VERLAUF.appendleft(tuple(json.loads(zeile)))
        except (ValueError, TypeError):
            pass
    DAUERHAFT["an"] = True
    for zeile in alt_log:
        LOG.append(zeile)
        _anhaengen(PROTOKOLL_FILE, zeile)
    for eintrag in reversed(alt_verlauf):
        VERLAUF.appendleft(eintrag)
        _anhaengen(VERLAUF_FILE, json.dumps(list(eintrag), ensure_ascii=False))


def verlauf(bezug: str, art: str, text: str) -> None:
    """Eintrag für „Zuletzt übernommen“"""
    eintrag = (dt.datetime.now().strftime("%d.%m.%Y %H:%M"), bezug, art, text)
    VERLAUF.appendleft(eintrag)
    _anhaengen(VERLAUF_FILE, json.dumps(list(eintrag), ensure_ascii=False))


def log(msg: str) -> None:
    zeile = f"{dt.datetime.now().strftime('%d.%m.%Y %H:%M:%S')} {msg}"
    LOG.append(zeile)
    _anhaengen(PROTOKOLL_FILE, zeile)
    try:
        print(zeile, flush=True)
    except UnicodeEncodeError:  # Konsole ohne UTF-8 (etwa Windows): Zeichen ersetzen statt abbrechen
        kodierung = getattr(sys.stdout, "encoding", None) or "ascii"
        print(zeile.encode(kodierung, "replace").decode(kodierung), flush=True)


class Fehler(Exception):
    pass


class Abbruch(Fehler):
    """Grundsätzliches Problem: ganzen Lauf anhalten statt Bestellung für Bestellung Falsches anzulegen"""


# ── HTTP-Client ──────────────────────────────────────────────────────────────────────────────────────────────────


def http(method: str, url: str, headers: dict | None = None, body: bytes | None = None, versuche: int = 4):
    for versuch in range(versuche):
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                data = json.loads(raw) if raw else None
            except ValueError:
                data = {"raw": raw[:300].decode("utf-8", "replace")}
            if e.code == 429 or e.code >= 500:
                warte = int(e.headers.get("Retry-After") or 0) or 10 * (versuch + 1)
                time.sleep(min(warte, 90))
                continue
            return e.code, data
        except (urllib.error.URLError, TimeoutError) as e:
            if versuch == versuche - 1:
                raise Fehler(f"{url.split('?')[0]} nicht erreichbar: {getattr(e, 'reason', e)}") from e
            time.sleep(10 * (versuch + 1))
    raise Fehler(f"{url.split('?')[0]}: keine Antwort nach {versuche} Versuchen")




def cent(betrag: dict | None) -> int:
    if not betrag or betrag.get("value") in (None, ""):
        return 0
    return round(float(betrag["value"]) * 100)


def kurz(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"



def datum_de(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y")
    except ValueError:
        return iso[:10]


ZAHLART = {"CREDIT_CARD": "Kreditkarte", "PAYPAL": "PayPal", "APPLE_PAY": "Apple Pay", "GOOGLE_PAY": "Google Pay",
           "DIRECT_DEBIT": "Lastschrift", "BANK_TRANSFER": "Überweisung", "KLARNA": "Klarna", "EBAY": "eBay",
           "CASH_ON_PICKUP": "Barzahlung bei Abholung"}


def euro(cent_betrag: int) -> str:
    s = f"{abs(cent_betrag) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if cent_betrag < 0 else "") + s + " €"
