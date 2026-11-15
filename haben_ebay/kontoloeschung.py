"""Meldestelle für eBays Marketplace Account Deletion: Challenge und Ablage der Meldungen; gelöscht wird nie."""
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


def merke_kaeufer(username: str, name: str, oid: str, kontakt_id: str) -> None:
    """Zu jeder übernommenen Bestellung den eBay-Nutzernamen, damit eine Kontolöschung zugeordnet werden kann."""
    if not username:
        return
    with LOCK:
        alle = lade_json(KAEUFER_FILE, {})
        k = alle.setdefault(username, {"name": name, "bestellungen": [], "kontakt": kontakt_id})
        k["name"], k["kontakt"] = name, kontakt_id
        if oid not in k["bestellungen"]:
            k["bestellungen"].append(oid)
        speichere_json(KAEUFER_FILE, alle)


def challenge_antwort(challenge_code: str, token: str, endpoint: str) -> str:
    """eBay prüft die Meldestelle mit SHA-256 über challengeCode + verificationToken + endpoint (in dieser Reihenfolge)."""
    return hashlib.sha256((challenge_code + token + endpoint).encode()).hexdigest()


def kontoloeschung_merken(nachricht: dict) -> dict | None:
    """Meldung MARKETPLACE_ACCOUNT_DELETION ablegen. Nichts wird gelöscht; der Betreiber entscheidet selbst (Aufbewahrungspflicht)."""
    if (nachricht.get("metadata") or {}).get("topic") != "MARKETPLACE_ACCOUNT_DELETION":
        return None
    n = nachricht.get("notification") or {}
    d = n.get("data") or {}
    username = str(d.get("username") or "")[:100]
    with LOCK:
        liste = lade_json(LOESCH_FILE, [])
        nid = str(n.get("notificationId") or "")[:100]
        if nid and any(x.get("notificationId") == nid for x in liste):
            return None  # eBay wiederholt Meldungen
        bekannt = lade_json(KAEUFER_FILE, {}).get(username) if username else None
        eintrag = {
            "zeit": dt.datetime.now().isoformat(timespec="seconds"),
            "ereignis": str(n.get("eventDate") or "")[:40],
            "notificationId": nid,
            "username": username,
            "userId": str(d.get("userId") or "")[:100],
            "bekannt": bekannt,
        }
        liste.insert(0, eintrag)
        speichere_json(LOESCH_FILE, liste[:LOESCH_MAX])
    return eintrag
