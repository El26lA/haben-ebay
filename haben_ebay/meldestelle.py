"""Meldungen über Kontolöschungen verarbeiten, ob sie am offenen Port ankommen oder beim Cloudflare Worker abgeholt werden."""
from __future__ import annotations

import datetime as dt
import json

from .basis import *  # noqa: F403
from .kontoloeschung import kontoloeschung_merken
from .mail import melden

ARTEN = {"port": "Offener Port am NAS", "cloudflare": "Cloudflare Worker (kein offener Port)"}


def meldung_verarbeiten(nachricht: dict) -> dict | None:
    """Meldung ablegen, protokollieren und bei eigenen Kunden per Mail melden. Gelöscht wird nichts."""
    eintrag = kontoloeschung_merken(nachricht) if isinstance(nachricht, dict) else None
    if not eintrag:
        return None
    wer = eintrag["bekannt"]["name"] if eintrag["bekannt"] else "unbekannt (keine übernommene Bestellung)"
    log(f"KONTOLÖSCHUNG gemeldet: eBay-Nutzer {eintrag['username'] or eintrag['userId']} – {wer}")
    if eintrag["bekannt"]:
        b = eintrag["bekannt"]
        melden(f"loeschung_{eintrag['username'] or eintrag['userId']}", f"Kontolöschung: {b['name']}",
               f"eBay meldet, dass der Käufer {eintrag['username'] or eintrag['userId']} ({b['name']}) sein eBay-Konto gelöscht hat.\n"
               f"Bestellungen: {', '.join(b.get('bestellungen') or [])}\n\n"
               "Der Abgleich löscht nichts. Rechnungen musst du wegen der Aufbewahrungspflicht ohnehin behalten; "
               "ob du den Kontakt in Haben änderst, entscheidest du.", 24 * 365)
    return eintrag


def endpunkt(cfg: dict) -> str:
    """Die Adresse, die bei eBay als Meldestelle eingetragen sein muss"""
    return cfg["cf_url"] if cfg.get("meldestelle") == "cloudflare" else cfg["hook_url"]


def meldestelle_abholen(cfg: dict) -> str:
    """Beim Cloudflare Worker liegende Meldungen holen, ablegen und dort löschen lassen (erst nach dem Speichern)."""
    if cfg.get("meldestelle") != "cloudflare":
        return ""
    if not cfg.get("cf_url") or not cfg.get("cf_schluessel"):
        raise Fehler("Adresse des Workers fehlt (Einstellungen › Anbindung an eBay)")
    basis_url = cfg["cf_url"].rstrip("/").removesuffix(HOOK_PFAD) + "/abholen"
    kopf = {"Authorization": f"Bearer {cfg['cf_schluessel']}", "Accept": "application/json", "User-Agent": "haben-ebay"}
    neu = abgeholt = 0
    for _ in range(20):  # je Abruf höchstens 500; mehr kommt praktisch nie
        status, daten = http("GET", basis_url, kopf)
        if status == 404:
            raise Fehler("Worker lehnt den Abholschlüssel ab oder ist nicht eingerichtet (404)")
        if status != 200 or not isinstance(daten, dict):
            raise Fehler(f"Worker antwortet mit {status}")
        meldungen = daten.get("meldungen") or []
        if not meldungen:
            break
        for m in meldungen:
            neu += meldung_verarbeiten(m.get("nachricht")) is not None
        ids = [m.get("id") for m in meldungen if m.get("id")]
        status, _ = http("POST", basis_url, {**kopf, "Content-Type": "application/json"}, json.dumps({"erledigt": ids}).encode())
        if status != 200:
            raise Fehler(f"Worker konnte abgeholte Meldungen nicht löschen ({status}); sie kommen beim nächsten Mal wieder, doppelt wird nichts")
        abgeholt += len(meldungen)
        if not daten.get("weitere"):
            break
    STATUS["meldestelle"] = {"zeit": dt.datetime.now().strftime("%d.%m.%Y %H:%M"), "abgeholt": abgeholt}
    return f"Kontolöschungen: {abgeholt} abgeholt, {neu} neu" if abgeholt else "Kontolöschungen: nichts Neues"
