"""Dauerhaftes Archiv aller eBay-Daten; behält Werte, die eBay später schwärzt."""
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
from .ebay import ebay_versand


# ── Archiv: alles behalten, was eBay je geliefert hat ────────────────────────────────────────────────────────────

LEER = (None, "", [], {})


def vereinen(alt, neu):
    """neu über alt legen, aber nichts verlieren: eBay schwärzt Namen und Adressen nach einiger Zeit."""
    if isinstance(alt, dict) and isinstance(neu, dict):
        aus = dict(alt)
        for k, v in neu.items():
            aus[k] = vereinen(alt.get(k), v) if k in alt else v
        return aus
    if isinstance(alt, list) and isinstance(neu, list) and len(alt) == len(neu) and all(isinstance(x, dict) for x in alt + neu):
        return [vereinen(x, y) for x, y in zip(alt, neu)]
    if neu in LEER and alt not in LEER:
        return alt
    if isinstance(neu, str) and isinstance(alt, str) and "*" in neu and "*" not in alt and len(alt) > 0:
        return alt  # maskierter Wert
    return neu


def lade_archiv() -> dict:
    a = lade_json(ARCHIV_FILE, {})
    for k in ("bestellungen", "transaktionen", "auszahlungen"):
        a.setdefault(k, {})
    return a


def archiviere_bestellung(archiv: dict, cfg: dict, order: dict, versand_holen: bool = True) -> dict:
    """Bestellung ins Archiv übernehmen (mit Sendungen) und den vereinten Stand zurückgeben."""
    oid = order["orderId"]
    jetzt = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    e_alt = archiv["bestellungen"].get(oid) or {"erstes": jetzt, "order": {}, "sendungen": [], "rechnung": None}
    geaendert = (e_alt["order"] or {}).get("lastModifiedDate") != order.get("lastModifiedDate") or not e_alt["order"]
    e_alt["order"] = vereinen(e_alt["order"], order)
    if versand_holen and order.get("fulfillmentHrefs") and (geaendert or not e_alt.get("sendungen")):
        try:
            sendungen = ebay_versand(cfg, order)
            alt = {s.get("fulfillmentId") or s.get("shipmentTrackingNumber"): s for s in e_alt.get("sendungen") or []}
            for s_ in sendungen:
                key = s_.get("fulfillmentId") or s_.get("shipmentTrackingNumber")
                alt[key] = vereinen(alt.get(key) or {}, s_)
            e_alt["sendungen"] = list(alt.values())
        except Fehler as ex:
            log(f"Sendungen zu {oid}: {ex}")
    e_alt["aktualisiert"] = jetzt if geaendert else e_alt.get("aktualisiert", jetzt)
    archiv["bestellungen"][oid] = e_alt
    return e_alt


def archiviere_liste(archiv: dict, art: str, eintraege: list[dict], schluessel: str) -> int:
    neu = 0
    for x in eintraege:
        k = x.get(schluessel)
        if not k:
            continue
        neu += k not in archiv[art]
        archiv[art][k] = vereinen(archiv[art].get(k) or {}, x)
    return neu


def bestellstatus(order: dict) -> tuple[str, str]:
    """(Text, CSS-Klasse)"""
    if (order.get("cancelStatus") or {}).get("cancelState") == "CANCELED":
        return "storniert", "bad"
    zs = order.get("orderPaymentStatus")
    if zs == "FULLY_REFUNDED":
        return "erstattet", "bad"
    if zs == "PARTIALLY_REFUNDED":
        return "teilweise erstattet", "warn"
    if (order.get("cancelStatus") or {}).get("cancelState") in ("IN_PROGRESS", "CANCEL_REQUESTED"):
        return "Storno angefragt", "warn"
    if zs == "PAID":
        return ("versandt" if order.get("orderFulfillmentStatus") == "FULFILLED" else "bezahlt"), "ok"
    return {"PENDING": "Zahlung offen", "FAILED": "Zahlung fehlgeschlagen"}.get(zs or "", zs or "–"), "warn"
