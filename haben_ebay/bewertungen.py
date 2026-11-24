"""Neutrale und negative Bewertungen: abrufen (Feedback API), für immer speichern, bei neuen sofort eine Mail."""
from __future__ import annotations

import datetime as dt
import os
import urllib.parse

from .basis import *  # noqa: F403
from .ebay import ebay_get
from .mail import mail_senden

BEWERTUNG_FILE = os.path.join(DATA_DIR, "bewertungen.json")  # {"seit": erster Abruf, "eintraege": feedbackId → Eintrag von eBay + „gemeldet“}
SCOPE_BEWERTUNG = "https://api.ebay.com/oauth/api_scope/commerce.feedback"
ARTEN = {"NEGATIVE": "Negative", "NEUTRAL": "Neutrale"}


def eigener_nutzer(archiv: dict) -> str:
    """Eigener eBay-Nutzername aus der neuesten Bestellung im Archiv (Feld sellerId)."""
    for b in sorted(archiv["bestellungen"].values(), key=lambda b: (b.get("order") or {}).get("creationDate", ""), reverse=True):
        if (b.get("order") or {}).get("sellerId"):
            return b["order"]["sellerId"]
    return ""


def ebay_bewertungen(cfg: dict, nutzer: str, art: str) -> list[dict]:
    """Erhaltene Bewertungen einer Art seit Beginn des Kontos (neutrale und negative sind wenige)."""
    flt = f"commentType:{art}"
    offset, alle = 0, []
    while True:
        q = urllib.parse.urlencode({"user_id": nutzer, "feedback_type": "FEEDBACK_RECEIVED", "filter": flt,
                                    "sort": "TIME", "limit": 200, "offset": offset})
        data = ebay_get(cfg, f"{EBAY_API}/commerce/feedback/v1/feedback?{q}", "Bewertungen")
        teil = data.get("feedbackEntries") or []
        alle += teil
        offset += len(teil)
        if not teil or offset >= int((data.get("pagination") or {}).get("total") or 0):
            return alle


def bestellung_zur_position(archiv: dict, position: str) -> dict:
    for b in archiv["bestellungen"].values():
        o = b.get("order") or {}
        if any(li.get("lineItemId") == position for li in o.get("lineItems") or []):
            return o
    return {}


def mail_text(e: dict, archiv: dict, nutzer: str) -> tuple[str, str]:
    li = e.get("orderLineItemSummary") or {}
    o = bestellung_zur_position(archiv, li.get("orderLineItemId") or "") if li.get("orderLineItemId") else {}
    kaeufer = (e.get("providerUserDetail") or {}).get("userId") or (o.get("buyer") or {}).get("username") or "?"
    name = ((o.get("buyer") or {}).get("buyerRegistrationAddress") or {}).get("fullName") or ""
    preis = li.get("listingPrice") or {}
    tag = (e.get("feedbackEnteredDate") or "")[:10]
    art = ARTEN.get(e.get("commentType"), e.get("commentType") or "?")
    zeilen = [
        f"{art} Bewertung von {kaeufer}{f' ({name})' if name else ''} am {'.'.join(reversed(tag.split('-'))) if tag else '?'}:",
        "",
        f"„{(e.get('feedbackComment') or {}).get('commentText') or '(ohne Kommentar)'}“",
        "",
        f"Artikel: {li.get('listingTitle') or '?'}" + (f" ({preis.get('value')} {preis.get('currency', '')})".rstrip() if preis.get("value") else ""),
    ]
    if o.get("orderId"):
        zeilen.append(f"Bestellung: {o['orderId']} vom {o.get('creationDate', '')[:10]}")
    for r in e.get("feedbackRatings") or []:
        zeilen.append(f"Bewertung {r.get('ratingType')}: {r.get('value')} von 5")
    zeilen += ["", "Antworten oder eine Änderung anfragen: eBay › Mein eBay › Bewertungen",
               f"https://www.ebay.de/fdbk/feedback_profile/{urllib.parse.quote(nutzer)}"]
    return f"{art} Bewertung von {kaeufer}", "\n".join(zeilen)


def bewertungen_abgleich(cfg: dict, archiv: dict, schreiben: bool) -> str:
    """Erster Lauf: alle vorhandenen neutralen/negativen speichern, ohne Mail. Danach: jede neue sofort melden.
    Eine Bewertung gilt erst als gemeldet, wenn die Mail raus ist; sonst wird es im nächsten Lauf wieder versucht."""
    if SCOPE_BEWERTUNG not in (cfg.get("ebay_scopes") or "").split():
        return "Bewertungen: für die Meldung bitte eBay neu verbinden"
    nutzer = eigener_nutzer(archiv)
    if not nutzer:
        return "Bewertungen: eigener eBay-Name noch unbekannt (keine Bestellung im Archiv)"
    with LOCK:
        stand = lade_json(BEWERTUNG_FILE, {})
    erstmal, bekannt = "seit" not in stand, stand.get("eintraege") or {}
    eintraege = [e for art in ARTEN for e in ebay_bewertungen(cfg, nutzer, art)]
    neu = [e for e in eintraege if e.get("feedbackId") and e["feedbackId"] not in bekannt]
    if not schreiben:
        return f"Bewertungen: {len(neu)} neue neutrale/negative"
    gemeldet = 0
    for e in sorted(neu, key=lambda e: e.get("feedbackEnteredDate") or ""):
        if not erstmal and cfg.get("mail_aktiv"):
            betreff, text = mail_text(e, archiv, nutzer)
            try:
                mail_senden(cfg, betreff, text)
            except Fehler as ex:
                log(f"Mail zur Bewertung {e['feedbackId']} nicht gesendet: {ex}")
                continue
            gemeldet += 1
            log(f"Mail gesendet: {betreff}")
        with LOCK:
            stand = lade_json(BEWERTUNG_FILE, {})
            stand.setdefault("eintraege", {})[e["feedbackId"]] = {**e, "gemeldet": not erstmal and bool(cfg.get("mail_aktiv"))}
            speichere_json(BEWERTUNG_FILE, stand)
    if erstmal:
        with LOCK:
            stand = lade_json(BEWERTUNG_FILE, {})
            stand["seit"] = dt.datetime.now(dt.timezone.utc).isoformat()
            speichere_json(BEWERTUNG_FILE, stand)
        return f"Bewertungen: {len(neu)} vorhandene neutrale/negative übernommen (ohne Mail)"
    return f"Bewertungen: {len(neu)} neue neutrale/negative, {gemeldet} gemeldet"
