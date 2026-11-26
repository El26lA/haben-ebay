"""Ein Lauf: Bestellungen, Archiv, Entwürfe, Finanzdaten, Gebühren; Hintergrund-Schleife."""
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
from .archiv import *  # noqa: F403
from .bestellung import *  # noqa: F403
from .ebay import *  # noqa: F403
from .gebuehren import gebuehren_abgleich
from .zahlungen import zahlungen_abgleich
from .mail import fristen_pruefen, lauf_pruefen
from .bewertungen import bewertungen_abgleich
from .meldestelle import meldestelle_abholen


def abgleich(schreiben: bool = True, beispiel: str | None = None, voll: bool = False) -> str:
    cfg = lade_config()
    transaktionen = None
    meldestelle = ""
    if schreiben and not beispiel:  # zuerst, damit Kontolöschungen auch ankommen, wenn eBay gerade nicht antwortet
        try:
            meldestelle = meldestelle_abholen(cfg)
        except Fehler as e:
            meldestelle = f"Kontolöschungen: Fehler: {e}"
            log(f"FEHLER Meldestelle (Cloudflare): {e}")
    if beispiel:
        with open(beispiel, encoding="utf-8") as f:
            raw = json.load(f)
        orders = raw.get("orders", raw) if isinstance(raw, dict) else raw
        transaktionen = raw.get("transactions", []) if isinstance(raw, dict) else []
    else:
        start = dt.datetime.now(dt.timezone.utc)
        letzter = cfg.get("letzter_abruf")
        if voll or not letzter:
            seit = start - dt.timedelta(days=BESTELL_TAGE_MAX)
            orders = ebay_bestellungen(cfg, seit)
        else:
            # alles, was sich seit dem letzten Abruf geändert hat (neu, bezahlt, versandt, storniert, erstattet)
            seit = max(dt.datetime.fromisoformat(letzter) - dt.timedelta(days=2), start - dt.timedelta(days=BESTELL_TAGE_MAX))
            orders = ebay_bestellungen(cfg, seit, "lastmodifieddate")
    archiv = lade_archiv()
    neu = fehler = 0
    for i, o in enumerate(sorted(orders, key=lambda o: o.get("creationDate", "")), 1):
        oid = o.get("orderId", "?")
        STATUS["fortschritt"] = f"Bestellung {i} von {len(orders)}"
        try:
            vorher = (archiv["bestellungen"].get(oid) or {}).get("order", {}).get("lastModifiedDate")
            eintrag = archiviere_bestellung(archiv, cfg, o, versand_holen=not beispiel) if not beispiel else \
                {"order": o, "sendungen": o.get("_sendungen", []), "rechnung": None}
            if voll or (eintrag.get("rechnung") and vorher and vorher != o.get("lastModifiedDate")):
                eintrag["nachfuehren"] = True  # beim vollen Lauf alle Entwürfe neu berechnen
            art, text = verarbeite(cfg, eintrag["order"], schreiben, eintrag)
        except Abbruch as e:
            log(f"ABBRUCH: {e}")
            fehler += 1
            STATUS["fortschritt"] = ""
            if not beispiel:
                speichere_json(ARCHIV_FILE, archiv)
            return f"Abgebrochen: {e}"
        except Fehler as e:
            art, text = "fehler", str(e)
        if not beispiel and i % 25 == 0:
            speichere_json(ARCHIV_FILE, archiv)
        if art in ("neu", "probe", "fehler", "aktualisiert", "achtung"):
            log(f"{'Probe ' if not schreiben else ''}{oid}: {text}")
            verlauf(oid, art, text)
        neu += art in ("neu", "probe")
        fehler += art == "fehler"
    ergebnis = f"{len(orders)} Bestellungen geprüft, {neu} {'würden angelegt' if not schreiben else 'neu angelegt'}, {fehler} Fehler"
    if not beispiel:
        speichere_json(ARCHIV_FILE, archiv)
    # Geldbewegungen und Auszahlungen: beim ersten und beim vollen Lauf fünf Jahre, sonst die letzten 60 Tage
    if transaktionen is None and "sell.finances" in (cfg.get("ebay_scopes") or ""):
        try:
            jetzt = dt.datetime.now(dt.timezone.utc)
            erstmal = voll or not archiv["transaktionen"]
            von = jetzt - dt.timedelta(days=5 * 365 - 2 if erstmal else 60)
            tx = ebay_transaktionen(cfg, von, jetzt)
            n_tx = archiviere_liste(archiv, "transaktionen", tx, "transactionId")
            px = ebay_auszahlungen(cfg, von, jetzt)
            n_px = archiviere_liste(archiv, "auszahlungen", px, "payoutId")
            speichere_json(ARCHIV_FILE, archiv)
            ergebnis += f" · {len(tx)} Geldbewegungen ({n_tx} neu), {len(px)} Auszahlungen ({n_px} neu)"
            transaktionen = list(archiv["transaktionen"].values())
        except Fehler as e:
            ergebnis += f" · Finanzdaten: Fehler: {e}"
            log(f"FEHLER Finanzdaten: {e}")
    try:
        ergebnis += " · " + gebuehren_abgleich(cfg, schreiben, voll, transaktionen)
    except Fehler as e:
        ergebnis += f" · Gebühren: Fehler: {e}"
        log(f"FEHLER Gebühren: {e}")
    try:
        ergebnis += " · " + zahlungen_abgleich(cfg, schreiben, transaktionen, list(archiv["auszahlungen"].values()))
    except Fehler as e:
        ergebnis += f" · Zahlungen: Fehler: {e}"
        log(f"FEHLER Zahlungen: {e}")
    if not beispiel:
        try:
            ergebnis += " · " + bewertungen_abgleich(cfg, archiv, schreiben)
        except Fehler as e:
            ergebnis += f" · Bewertungen: Fehler: {e}"
            log(f"FEHLER Bewertungen: {e}")
    if meldestelle:
        ergebnis += " · " + meldestelle
    if not beispiel and fehler == 0:
        cfg = lade_config()
        cfg["letzter_abruf"] = start.isoformat()
        speichere_config(cfg)
    log(("Probelauf: " if not schreiben else "Abgleich: ") + ergebnis)
    return ergebnis



# ── Hintergrund ──────────────────────────────────────────────────────────────────────────────────────────────────

_auftrag = {"art": None}


def worker() -> None:
    erinnert = None
    while True:
        cfg = lade_config()
        art = _auftrag["art"]
        _auftrag["art"] = None
        if art or cfg["aktiv"]:
            STATUS["laeuft"] = True
            fehlgeschlagen = False
            try:
                STATUS["letztes_ergebnis"] = abgleich(schreiben=art not in ("probe", "vollprobe"), voll=art in ("voll", "vollprobe"))
                fehlgeschlagen = " 0 Fehler" not in STATUS["letztes_ergebnis"]
            except Fehler as e:
                STATUS["letztes_ergebnis"] = f"Fehler: {e}"
                log(f"FEHLER: {e}")
                fehlgeschlagen = True
            except Exception as e:
                STATUS["letztes_ergebnis"] = f"Fehler: {type(e).__name__}: {e}"
                log(f"UNERWARTETER FEHLER: {type(e).__name__}: {e}")
                fehlgeschlagen = True
            try:
                lauf_pruefen(STATUS["letztes_ergebnis"], fehlgeschlagen)
            except Exception as e:
                log(f"Benachrichtigung: {type(e).__name__}: {e}")
            STATUS["laeuft"] = False
            STATUS["fortschritt"] = ""
            STATUS["letzter_lauf"] = dt.datetime.now()
        else:
            pausiert(cfg)
        bis = cfg.get("ebay_refresh_bis") or 0
        if bis and (bis - time.time()) < 30 * 86400 and erinnert != dt.date.today():
            log(f"ACHTUNG: eBay-Verbindung läuft in {int((bis - time.time()) // 86400)} Tagen ab – neu verbinden")
            erinnert = dt.date.today()
        WECKER.wait(max(1, int(cfg["intervall_minuten"])) * 60)
        WECKER.clear()



def pausiert(cfg: dict) -> None:
    """Auch bei pausiertem Abgleich: Kontolöschungen beim Worker abholen (sonst wächst seine Datenbank) und Fristen melden."""
    try:
        text = meldestelle_abholen(cfg)
        if text and text != "Kontolöschungen: nichts Neues":
            log(f"Abgleich pausiert, nur abgeholt: {text}")
    except Fehler as e:
        log(f"FEHLER Meldestelle (Cloudflare): {e}")
    try:
        fristen_pruefen(cfg)
    except Exception as e:
        log(f"Benachrichtigung: {type(e).__name__}: {e}")


def anstossen(art: str) -> None:
    _auftrag["art"] = art
    WECKER.set()
