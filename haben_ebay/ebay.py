"""eBay-APIs: OAuth, Bestellungen, Sendungen, Geldbewegungen, Auszahlungen, signierte Anfragen."""
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
from .signatur import ed25519_seed, ed25519_signieren


# ── eBay ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def ebay_token_post(cfg: dict, form: dict) -> dict:
    if not (cfg["ebay_client_id"] and cfg["ebay_client_secret"]):
        raise Fehler("eBay App-ID und Cert-ID fehlen")
    basic = base64.b64encode(f"{cfg['ebay_client_id']}:{cfg['ebay_client_secret']}".encode()).decode()
    status, data = http(
        "POST",
        f"{EBAY_API}/identity/v1/oauth2/token",
        {"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"},
        urllib.parse.urlencode(form).encode(),
    )
    if status != 200 or not data or "access_token" not in data:
        meldung = (data or {}).get("error_description") or (data or {}).get("error") or data
        raise Fehler(f"eBay-Anmeldung abgelehnt ({status}): {meldung}")
    return data


_access = {"token": "", "bis": 0.0, "refresh": ""}


def ebay_access_token(cfg: dict) -> str:
    if not cfg["ebay_refresh_token"]:
        raise Fehler("eBay-Konto noch nicht verbunden")
    if _access["token"] and _access["refresh"] == cfg["ebay_refresh_token"] and time.time() < _access["bis"] - 120:
        return _access["token"]
    data = ebay_token_post(cfg, {"grant_type": "refresh_token", "refresh_token": cfg["ebay_refresh_token"], "scope": cfg.get("ebay_scopes") or SCOPES})
    _access.update(token=data["access_token"], bis=time.time() + int(data.get("expires_in", 7200)), refresh=cfg["ebay_refresh_token"])
    return _access["token"]


def gewuenschte_scopes(cfg: dict) -> str:
    """Eigene Rechte plus „ebay_scopes_zusatz“ aus der Config (für andere Dienste, die diese Verbindung mitnutzen)."""
    return " ".join(dict.fromkeys((SCOPES + " " + (cfg.get("ebay_scopes_zusatz") or "")).split()))


def ebay_consent_url(cfg: dict, state: str) -> str:
    return EBAY_AUTH + "?" + urllib.parse.urlencode(
        {"client_id": cfg["ebay_client_id"], "redirect_uri": cfg["ebay_runame"], "response_type": "code", "scope": gewuenschte_scopes(cfg), "state": state}
    )


def ebay_code_einloesen(code: str) -> int:
    cfg = lade_config()
    data = ebay_token_post(cfg, {"grant_type": "authorization_code", "code": code, "redirect_uri": cfg["ebay_runame"]})
    if "refresh_token" not in data:
        raise Fehler("eBay hat kein Refresh-Token geliefert")
    cfg["ebay_refresh_token"] = data["refresh_token"]
    cfg["ebay_scopes"] = gewuenschte_scopes(cfg)
    cfg["ebay_refresh_bis"] = int(time.time() + int(data.get("refresh_token_expires_in", 47304000)))
    speichere_config(cfg)
    _access.update(token="", bis=0.0)
    return (cfg["ebay_refresh_bis"] - int(time.time())) // 86400


def ebay_bestellungen(cfg: dict, seit: dt.datetime, feld: str = "creationdate") -> list[dict]:
    flt = feld + ":[" + seit.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") + "..]"
    offset, alle = 0, []
    while True:
        q = urllib.parse.urlencode({"filter": flt, "limit": 200, "offset": offset})
        status, data = http(
            "GET",
            f"{EBAY_API}/sell/fulfillment/v1/order?{q}",
            {"Authorization": "Bearer " + ebay_access_token(cfg), "Accept": "application/json"},
        )
        if status != 200:
            raise Fehler(f"eBay-Bestellungen ({status}): {data}")
        orders = (data or {}).get("orders", [])
        alle += orders
        offset += len(orders)
        if not orders or offset >= int(data.get("total", 0)):
            return alle


def ebay_get(cfg: dict, url: str, was: str, marktplatz: bool = False) -> dict:
    headers = {"Authorization": "Bearer " + ebay_access_token(cfg), "Accept": "application/json"}
    if marktplatz:
        headers["X-EBAY-C-MARKETPLACE-ID"] = cfg.get("ebay_marktplatz") or MARKTPLATZ
        headers.update(signatur_header(cfg, "GET", url))
    status, data = http("GET", url, headers)
    if status in (204, 404):  # keine Einträge im Zeitraum
        return {}
    if status != 200:
        raise Fehler(f"eBay {was} ({status}): {data}")
    return data or {}


def ebay_versand(cfg: dict, order: dict) -> list[dict]:
    """Sendungen einer Bestellung: Sendungsnummer, Versanddienst, Versanddatum."""
    if "_sendungen" in order:  # Testdaten
        return order["_sendungen"]
    if not order.get("fulfillmentHrefs"):
        return []
    oid = urllib.parse.quote(order["orderId"], safe="")
    data = ebay_get(cfg, f"{EBAY_API}/sell/fulfillment/v1/order/{oid}/shipping_fulfillment", "Sendungen")
    return data.get("fulfillments") or []


def utc_text(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ebay_transaktionen(cfg: dict, von: dt.datetime, bis: dt.datetime) -> list[dict]:
    """Geldbewegungen (Verkäufe, Gebühren, Erstattungen, Versandetiketten …) zwischen von und bis."""
    alle, start = [], von
    while start < bis:
        ende = min(bis, start + dt.timedelta(days=1060))  # unter 36 Monaten je Abfrage
        flt = f"transactionDate:[{utc_text(start)}..{utc_text(ende)}]"
        offset = 0
        while True:
            q = urllib.parse.urlencode({"filter": flt, "limit": 1000, "offset": offset})
            data = ebay_get(cfg, f"{EBAY_FIN}/sell/finances/v1/transaction?{q}", "Finanzdaten", marktplatz=True)
            tx = data.get("transactions") or []
            alle += tx
            offset += len(tx)
            if not tx or offset >= int(data.get("total", 0)):
                break
        start = ende
    return alle


def ebay_auszahlungen(cfg: dict, von: dt.datetime, bis: dt.datetime) -> list[dict]:
    alle, start = [], von
    while start < bis:
        ende = min(bis, start + dt.timedelta(days=1060))
        flt = f"payoutDate:[{utc_text(start)}..{utc_text(ende)}]"
        offset = 0
        while True:
            q = urllib.parse.urlencode({"filter": flt, "limit": 200, "offset": offset})
            data = ebay_get(cfg, f"{EBAY_FIN}/sell/finances/v1/payout?{q}", "Auszahlungen", marktplatz=True)
            px = data.get("payouts") or []
            alle += px
            offset += len(px)
            if not px or offset >= int(data.get("total", 0)):
                break
        start = ende
    return alle



def ebay_app_token(cfg: dict) -> str:
    data = ebay_token_post(cfg, {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"})
    return data["access_token"]


def signaturschluessel(cfg: dict) -> dict:
    """Schlüsselpaar bei eBay anlegen, wenn keins da ist oder es in 30 Tagen abläuft (gilt 3 Jahre)."""
    if cfg.get("sign_jwe") and cfg.get("sign_privat") and cfg.get("sign_bis", 0) > time.time() + 30 * 86400:
        return cfg
    status, data = http(
        "POST", f"{EBAY_FIN}/developer/key_management/v1/signing_key",
        {"Authorization": "Bearer " + ebay_app_token(cfg), "Content-Type": "application/json", "Accept": "application/json"},
        json.dumps({"signingKeyCipher": "ED25519"}).encode())
    if status not in (200, 201) or not data or not data.get("privateKey"):
        raise Fehler(f"eBay-Signaturschlüssel anlegen ({status}): {data}")
    ed25519_seed(data["privateKey"])  # Format prüfen, bevor gespeichert wird
    frisch = lade_config()
    frisch.update(sign_jwe=data["jwe"], sign_privat=data["privateKey"], sign_id=data.get("signingKeyId", ""),
                  sign_bis=int(data.get("expirationTime") or time.time() + 3 * 365 * 86400))
    speichere_config(frisch)
    log(f"eBay-Signaturschlüssel angelegt (gültig bis {dt.datetime.fromtimestamp(frisch['sign_bis']).strftime('%d.%m.%Y')})")
    cfg.update({k: frisch[k] for k in ("sign_jwe", "sign_privat", "sign_id", "sign_bis")})
    return cfg


def signatur_header(cfg: dict, methode: str, url: str, body: bytes | None = None) -> dict:
    cfg = signaturschluessel(cfg)
    u = urllib.parse.urlsplit(url)
    teile = ['"x-ebay-signature-key"', '"@method"', '"@path"', '"@authority"']
    werte = [cfg["sign_jwe"], methode.upper(), u.path, u.netloc.lower()]
    h = {"x-ebay-signature-key": cfg["sign_jwe"]}
    if body:
        h["Content-Digest"] = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
        teile.insert(0, '"content-digest"')
        werte.insert(0, h["Content-Digest"])
    params = "(" + " ".join(teile) + f");created={int(time.time())}"
    basis = "\n".join(f"{t}: {w}" for t, w in zip(teile, werte)) + f'\n"@signature-params": {params}'
    sig = ed25519_signieren(ed25519_seed(cfg["sign_privat"]), basis.encode())
    h["Signature-Input"] = "sig1=" + params
    h["Signature"] = "sig1=:" + base64.b64encode(sig).decode() + ":"
    return h
