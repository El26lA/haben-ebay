"""Zahlungen an Haben: die Geldbewegungen und Auszahlungen aus dem Archiv als Lieferung nach dem Importvertrag
(Haben #306) an POST /api/v1/bankimporte. Haben legt daraus die Umsätze im eBay-Konto an; zugeordnet wird dort im
Bankabgleich. Liefern geht nur in eine Quelle, die in Haben am eBay-Konto freigegeben ist (Bank › Konto › Externe
Quelle hinzufügen: Anbieter „haben-ebay“, Kennung wie unter Einstellungen).

Eine eBay-Geldbewegung hat den Nettobetrag (`amount`) und die einbehaltene Gebühr (`totalFeeAmount`). Daraus werden
zwei Bewegungen: `main` mit dem Bruttobetrag und `fee` mit der Gebühr; zusammen ergeben sie das Netto. Bei einem
Verkauf ist das Brutto der Erlös und die Gebühr negativ, bei einer Erstattung das Brutto negativ und die Gebühr, die
eBay zurückgibt, positiv. Auszahlungen auf die Bank kommen als `payout`."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

from .basis import *  # noqa: F403
from .haben import haben, haben_ok

ANBIETER = "haben-ebay"
ZAHLUNG_FILE = os.path.join(DATA_DIR, "zahlungen.json")  # Prüfwert der zuletzt gelieferten Bewegungen
TEIL_MAX = 1000  # höchstens so viele Bewegungen je Lieferung (Haben)

ART = {
    "SALE": "payment",
    "REFUND": "refund",
    "DISPUTE": "chargeback",
    "NON_SALE_CHARGE": "fee",
    "SHIPPING_LABEL": "fee",
}


def tag(zeit: str | None) -> str:
    return (zeit or "")[:10]


def bewegungen(transaktionen: list[dict], auszahlungen: list[dict]) -> list[dict]:
    """Bewegungen nach dem Importvertrag, nach Tag sortiert. FAILED und Fremdwährung gehen mit, Haben weist sie aus."""
    verkauf = {}
    for t in transaktionen:
        if t.get("transactionType") == "SALE" and t.get("orderId"):
            verkauf.setdefault(t["orderId"], []).append(t["transactionId"])
    liste = []
    for t in transaktionen:
        if t.get("transactionStatus") == "FAILED" or not t.get("transactionId"):
            continue
        s = 1 if t.get("bookingEntry") == "CREDIT" else -1
        typ = t.get("transactionType") or ""
        art = ART.get(typ, "adjustment")
        betrag = (t.get("amount") or {})
        waehrung = betrag.get("currency", "EUR")
        netto = s * cent(betrag)
        gebuehr = cent(t.get("totalFeeAmount")) if typ in ("SALE", "REFUND") else 0
        refs = {"eventCode": typ}
        if t.get("orderId"):
            refs["orderId"] = t["orderId"]
        if t.get("payoutId"):
            refs["payoutId"] = t["payoutId"]
        bezug = verkauf.get(t.get("orderId") or "")
        if art in ("refund", "chargeback") and bezug and len(bezug) == 1:
            refs["relatedEventId"] = bezug[0]
        gemeinsam = {
            "sourceEventId": t["transactionId"],
            "kind": art,
            "bookingDate": tag(t.get("transactionDate")),
            "currency": waehrung,
            "references": refs,
        }
        if t.get("transactionDate"):
            gemeinsam["sourceTimestamp"] = t["transactionDate"]
        kaeufer = (t.get("buyer") or {}).get("username")
        text = t.get("transactionMemo") or (f"Bestellung {t['orderId']}" if t.get("orderId") else typ)
        haupt = {**gemeinsam, "component": "main", "amount": netto + s * gebuehr, "purpose": text}
        if kaeufer:
            haupt["counterpartyName"] = kaeufer
        liste.append(haupt)
        if gebuehr:
            liste.append({**gemeinsam, "component": "fee", "amount": -s * gebuehr, "counterpartyName": "eBay", "purpose": f"Gebühren zu {text}"})
    for p in auszahlungen:
        if p.get("payoutStatus") != "SUCCEEDED" or not p.get("payoutId"):
            continue
        betrag = p.get("amount") or {}
        liste.append(
            {
                "sourceEventId": p["payoutId"],
                "component": "main",
                "kind": "payout",
                "bookingDate": tag(p.get("payoutDate")),
                "amount": -cent(betrag),
                "currency": betrag.get("currency", "EUR"),
                "counterpartyName": "eBay",
                "purpose": "Auszahlung auf das Bankkonto",
                "references": {"payoutId": p["payoutId"]},
            }
        )
    return sorted(liste, key=lambda m: (m["bookingDate"], m["sourceEventId"], m["component"]))


def lieferungen(liste: list[dict], kennung: str, von: str, bis: str, jetzt: str) -> list[dict]:
    """Die Bewegungen in Teile zu höchstens TEIL_MAX, als ein Abruf (gleiche fetchId, fortlaufender Index)."""
    pruef = hashlib.sha256(json.dumps(liste, sort_keys=True).encode()).hexdigest()[:16]
    teile = [liste[i : i + TEIL_MAX] for i in range(0, len(liste), TEIL_MAX)] or [[]]
    abruf = f"ebay-{von}-{bis}-{pruef}"
    return [
        {
            "contractVersion": 1,
            "provider": ANBIETER,
            "externalAccountId": kennung,
            "idempotencyKey": f"{abruf}-{i}",
            "fetchedAt": jetzt,
            "coverage": {"from": von, "to": bis},
            "part": {"fetchId": abruf, "index": i, "last": i == len(teile) - 1},
            "movements": teil,
        }
        for i, teil in enumerate(teile)
    ]


def zahlungen_abgleich(cfg: dict, schreiben: bool, transaktionen: list[dict] | None, auszahlungen: list[dict]) -> str:
    if not cfg.get("zahlungen_an"):
        return "Zahlungen: aus (Einstellungen › Zahlungen an Haben liefern)"
    if transaktionen is None:
        return "Zahlungen: keine Finanzdaten von eBay"
    kennung = cfg.get("zahlungen_kennung") or "ebay"
    status, quellen = haben(cfg, "GET", "/bankquellen")
    if status == 403:
        return "Zahlungen: dem Haben-Token fehlt das Recht „bank:importieren“"
    quellen = haben_ok(status, quellen, "Quellen lesen")
    quelle = next((q for q in quellen.get("data", []) if q["provider"] == ANBIETER and q["externalAccountId"] == kennung), None)
    if not quelle:
        return f"Zahlungen: In Haben ist keine Quelle „{ANBIETER}“ / „{kennung}“ freigegeben (Bank › eBay-Konto › Externe Quelle hinzufügen)"
    if quelle.get("accountKind") != "psp":
        return (f"Zahlungen: Die Quelle ist am Konto „{quelle.get('accountName')}“ freigegeben, das ist kein Konto eines "
                "Zahlungsdienstleisters. eBay-Zahlungen gehören in ein eigenes eBay-Konto (Bank › Konto hinzufügen › Zahlungsdienstleister).")
    if quelle.get("openConflicts"):
        return "Zahlungen: In Haben ist ein Konflikt offen; bitte unter Bank am eBay-Konto klären"
    stichtag = quelle.get("cutoverDate") or "2000-01-01"
    heute = dt.date.today().isoformat()
    liste = [m for m in bewegungen(transaktionen, auszahlungen) if stichtag <= m["bookingDate"] <= heute]
    pruef = hashlib.sha256(json.dumps(liste, sort_keys=True).encode()).hexdigest()
    stand = lade_json(ZAHLUNG_FILE, {})
    if stand.get("pruef") == pruef and stand.get("stichtag") == stichtag:
        return f"Zahlungen: nichts Neues ({len(liste)} Bewegungen schon in Haben)"
    if not schreiben:
        return f"Zahlungen: würde {len(liste)} Bewegungen ab {stichtag} an Haben liefern"
    neu = bekannt = 0
    hinweise = []
    jetzt = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    for teil in lieferungen(liste, kennung, stichtag, heute, jetzt):
        status, antwort = haben(cfg, "POST", "/bankimporte", teil)
        if status == 409:
            err = (antwort or {}).get("error", {}) if isinstance(antwort, dict) else {}
            raise Fehler(f"Haben meldet einen Konflikt: {err.get('message', antwort)}")
        antwort = haben_ok(status, antwort, "Zahlungen liefern")
        neu += antwort.get("added", 0)
        bekannt += antwort.get("skipped", 0)
        hinweise += antwort.get("warnings", [])
    speichere_json(ZAHLUNG_FILE, {"pruef": pruef, "stichtag": stichtag, "zeit": jetzt, "anzahl": len(liste)})
    if hinweise:
        log("Zahlungen, Hinweise von Haben: " + " | ".join(hinweise[:10]))
    return f"Zahlungen: {neu} neu in Haben, {bekannt} schon bekannt" + (f", {len(hinweise)} Hinweise" if hinweise else "")
