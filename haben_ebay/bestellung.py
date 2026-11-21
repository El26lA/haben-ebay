"""Bestellung → Kontakt und Rechnungsentwurf in Haben."""
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
from .archiv import bestellstatus
from .ebay import ebay_versand
from .haben import haben, haben_ok
from .kontoloeschung import merke_kaeufer
from .mail import melden


def bestelldatum(order: dict) -> str:
    t = dt.datetime.fromisoformat(order["creationDate"].replace("Z", "+00:00"))
    return t.astimezone().date().isoformat()


def bezahlt(order: dict) -> dict:
    """Zahlungsart „Bereits bezahlt“ über eBay mit dem Tag der ersten Zahlung (Haben ab v0.10.0, #1056)."""
    p = ((order.get("paymentSummary") or {}).get("payments") or [{}])[0]
    if not p.get("paymentDate"):
        return {}
    t = dt.datetime.fromisoformat(p["paymentDate"].replace("Z", "+00:00"))
    return {"paymentMethod": "bereits_bezahlt", "paidVia": "eBay", "paidOn": t.astimezone().date().isoformat()}


def kontakt_aus_bestellung(order: dict) -> dict:
    ship = {}
    for fsi in order.get("fulfillmentStartInstructions") or []:
        ship = (fsi.get("shippingStep") or {}).get("shipTo") or {}
        if ship:
            break
    buyer = order.get("buyer") or {}
    reg = buyer.get("buyerRegistrationAddress") or {}
    adr = ship.get("contactAddress") or reg.get("contactAddress") or {}
    name = ship.get("fullName") or reg.get("fullName") or buyer.get("username") or "eBay-Käufer"
    strasse = ", ".join(x for x in (adr.get("addressLine1"), adr.get("addressLine2")) if x)
    return {
        "name": kurz(name, 200),
        "strasse": kurz(strasse, 200),
        "plz": (adr.get("postalCode") or "").strip(),
        "ort": (adr.get("city") or "").strip(),
        "land": (adr.get("countryCode") or "DE").upper(),
        "email": (ship.get("email") or reg.get("email") or "").strip(),
        "telefon": ((ship.get("primaryPhone") or reg.get("primaryPhone") or {}).get("phoneNumber") or "").strip(),
    }


def positionen(order: dict, steuersatz: int) -> tuple[list[dict], list[str]]:
    def pos(beschreibung, menge, preis, nummer="", detail=""):
        p = {"description": beschreibung, "quantity": menge, "unit": "Stk.", "unitPrice": preis, "taxRate": steuersatz}
        if nummer:
            p["itemNumber"] = nummer
        if detail:
            p["detail"] = detail
        return p

    lines, hinweise, pos_rabatt = [], [], 0
    for li in order.get("lineItems") or []:
        menge = int(li.get("quantity") or 1)
        gesamt = cent(li.get("lineItemCost"))
        rabatt = sum(cent(p.get("discountAmount")) for p in li.get("appliedPromotions") or [])
        titel = kurz(li.get("title") or "Artikel", 200)
        nummer = kurz(li.get("sku") or li.get("legacyItemId") or "", 50)
        detail = f"eBay-Artikel {li['legacyItemId']}" if li.get("legacyItemId") else ""
        if gesamt % menge == 0:
            lines.append(pos(titel, menge * 1000, gesamt // menge, nummer, detail))
        else:
            lines.append(pos(f"{menge} × {titel}", 1000, gesamt, nummer, detail))
        if rabatt:
            lines.append(pos(f"Rabatt: {titel}", 1000, -rabatt))
            pos_rabatt += rabatt
    ps = order.get("pricingSummary") or {}
    versand = cent(ps.get("deliveryCost")) - abs(cent(ps.get("deliveryDiscount")))
    if versand:
        lines.append(pos("Versand", 1000, versand))
    rest = abs(cent(ps.get("priceDiscount"))) - pos_rabatt
    if rest > 0:
        lines.append(pos("Rabatt", 1000, -rest))
    ebay_steuer = sum(cent(t.get("amount")) for li in order.get("lineItems") or [] for t in li.get("ebayCollectAndRemitTaxes") or [])
    summe = sum(l["unitPrice"] * l["quantity"] // 1000 for l in lines)
    erwartet = cent(ps.get("total")) - ebay_steuer
    if ps.get("total") and summe != erwartet:
        hinweise.append(f"Positionen {summe / 100:.2f} € ≠ Bestellwert {erwartet / 100:.2f} € – Entwurf prüfen")
    if ebay_steuer:
        hinweise.append(f"{ebay_steuer / 100:.2f} € Steuer hat eBay selbst abgeführt")
    return lines, hinweise



def kunden_erstattungen(order: dict) -> list[tuple[str, int]]:
    """(Datum, Betrag in Cent), den der Käufer zurückbekommen hat. paymentSummary.refunds nennt dagegen, was eBay
    dem Verkäufer abzieht (Erstattung abzüglich gutgeschriebener Gebühr) – das gehört nicht auf die Rechnung."""
    je_tag: dict[str, int] = collections.defaultdict(int)
    for li in order.get("lineItems") or []:
        for r in li.get("refunds") or []:
            je_tag[r.get("refundDate") or ""] += cent(r.get("amount"))
    if not je_tag:  # ältere Bestellungen ohne Angabe je Position
        for r in (order.get("paymentSummary") or {}).get("refunds") or []:
            je_tag[r.get("refundDate") or ""] += 0
    return sorted(je_tag.items())


# Versanddienste, die eBay ohne Zuordnung meldet: auf der Rechnung weglassen
VERSAND_UNBEKANNT = ("other", "no carrier", "unknown")


def zusatzdaten(order: dict, sendungen: list[dict]) -> list[str]:
    """Bestellung, Zahlung, Erstattung und Versand als Zeilen für den Schlusstext der Rechnung.

    Beträge immer aus Sicht des Käufers: payments.amount ist bei eBay der Betrag nach Abzug der Gebühren
    (totalDueSeller), deshalb gilt für die Zahlung pricingSummary.total."""
    kaeufer = (order.get("buyer") or {}).get("username")
    z = [f"eBay-Bestellung {order['orderId']} vom {datum_de(order.get('creationDate'))}" + (f", Käufer {kaeufer}" if kaeufer else "") + "."]
    ps = order.get("paymentSummary") or {}
    gezahlt = cent((order.get("pricingSummary") or {}).get("total"))
    for p in (ps.get("payments") or [])[:1]:
        teil = f"Bezahlt am {datum_de(p['paymentDate'])}" if p.get("paymentDate") else "Bezahlt"
        teil += " über eBay"
        if gezahlt:
            teil += f" ({euro(gezahlt)})"
        z.append(teil + ".")
    for datum, betrag in kunden_erstattungen(order):
        z.append(f"Erstattet am {datum_de(datum)}" + (f": {euro(betrag)}" if betrag else "") + ".")
    for s in sendungen:
        teil = "Versand"
        if s.get("shippedDate"):
            teil += f" am {datum_de(s['shippedDate'])}"
        dienst = (s.get("shippingCarrierCode") or "").strip()
        if dienst and not any(w in dienst.lower() for w in VERSAND_UNBEKANNT):
            teil += f" mit {dienst}"
        if s.get("shipmentTrackingNumber"):
            teil += f", Sendungsnummer {s['shipmentTrackingNumber']}"
        z.append(teil + ".")
    return z


def uebernehmen(cfg: dict, order: dict) -> tuple[bool, str]:
    if (order.get("cancelStatus") or {}).get("cancelState") == "CANCELED":
        return False, "storniert"
    if order.get("orderPaymentStatus") not in ("PAID", "PARTIALLY_REFUNDED"):
        return False, f"noch nicht bezahlt ({order.get('orderPaymentStatus')})"
    w = ((order.get("pricingSummary") or {}).get("total") or {}).get("currency", "EUR")
    if w != "EUR":
        return False, f"Währung {w}"
    if cfg["nur_ab"] and bestelldatum(order) < cfg["nur_ab"]:
        return False, f"vor {cfg['nur_ab']}"
    return True, ""


def finde_kontakt(cfg: dict, k: dict) -> dict | None:
    suche = k["email"] or k["name"]
    status, data = haben(cfg, "GET", "/kontakte?" + urllib.parse.urlencode({"q": suche[:200], "limit": 50}))
    data = haben_ok(status, data, "Kontaktsuche")
    for c in data.get("data", []):
        mails = [m.strip().lower() for m in (c.get("email") or "").split(",") if m.strip()]
        if k["email"] and k["email"].lower() in mails:
            return c
        if c.get("name", "").strip().lower() == k["name"].lower() and c.get("plz", "").strip() == k["plz"]:
            return c
    return None


KONTAKT_FELDER = {"strasse": "Straße", "plz": "PLZ", "ort": "Ort", "email": "E-Mail", "telefon": "Telefon"}


def kontakt_ergaenzen(cfg: dict, kontakt_id: str, k: dict, nutzer: str, vorhanden: dict | None = None) -> list[str]:
    """Leere Felder des Kontakts in Haben mit Daten von eBay füllen; nichts überschreiben, was dort schon steht.
    Den Namen nur ersetzen, wenn er noch der eBay-Nutzername ist (Platzhalter aus einer Bestellung ohne Namen)."""
    if vorhanden is None:
        st, vorhanden = haben(cfg, "GET", f"/kontakte/{kontakt_id}")
        if st != 200 or not vorhanden:
            return []
    patch = {f: k[f] for f in KONTAKT_FELDER if k.get(f) and not (vorhanden.get(f) or "").strip()}
    if k.get("name") and nutzer and k["name"] != nutzer and (vorhanden.get("name") or "").strip() == nutzer:
        patch["name"] = k["name"]
    # Anschrift nur als Ganzes: keine Straße aus der einen, Ort aus einer anderen Bestellung mischen
    if "strasse" in patch and (vorhanden.get("plz") or vorhanden.get("ort")) and \
            ((vorhanden.get("plz") or "").strip(), (vorhanden.get("ort") or "").strip()) != (k["plz"], k["ort"]):
        patch.pop("strasse")
    if not patch:
        return []
    st, antw = haben(cfg, "PATCH", f"/kontakte/{kontakt_id}", patch)
    if st != 200:
        log(f"Kontakt {kontakt_id} nicht ergänzt ({st}): {antw}")
        return []
    return [KONTAKT_FELDER.get(f, "Name") for f in patch]


def verarbeite(cfg: dict, order: dict, schreiben: bool, eintrag: dict | None = None) -> tuple[str, str]:
    """(art, text) mit art in neu, vorhanden, uebersprungen, probe. eintrag = Archiv-Eintrag (Sendungen, Rechnung)."""
    oid = order["orderId"]
    eintrag = eintrag if eintrag is not None else {"sendungen": None, "rechnung": None}
    ok, grund = uebernehmen(cfg, order)
    if not ok:
        r = eintrag.get("rechnung")
        if r and bestellstatus(order)[0] in ("storniert", "erstattet"):
            st, d = haben(cfg, "GET", "/rechnungen?" + urllib.parse.urlencode({"externalSource": QUELLE, "externalId": oid}))
            if st == 200 and d and d.get("total", 0) > 0:  # aktueller Stand: festgeschrieben?
                x = d["data"][0]
                r = eintrag["rechnung"] = {"id": x.get("id"), "url": x.get("url"), "nummer": x.get("number"), "status": x.get("status")}
            elif st == 200:
                eintrag["rechnung"] = None  # Entwurf wurde in Haben schon gelöscht
                return "uebersprungen", grund
            # Rechnung oder Entwurf gibt es schon, die Bestellung wurde danach storniert oder ganz erstattet:
            # nichts löschen, aber deutlich melden (Entwurf löschen bzw. Rechnung stornieren macht der Nutzer in Haben)
            was = "Entwurf in Haben löschen" if r.get("status") == "draft" else "Rechnung in Haben stornieren"
            text = f"Bestellung {bestellstatus(order)[0]}, aber {'Entwurf' if r.get('status') == 'draft' else 'Rechnung'} vorhanden – {was}"
            if schreiben and not eintrag.get("storno_gemeldet"):
                eintrag["storno_gemeldet"] = True
                melden(f"storno_{oid}", f"Bestellung {oid} {bestellstatus(order)[0]}",
                       f"Die eBay-Bestellung {oid} wurde {bestellstatus(order)[0]}, in Haben gibt es dazu schon "
                       f"{'einen Entwurf' if r.get('status') == 'draft' else 'die Rechnung ' + str(r.get('nummer') or '')}.\n"
                       f"Bitte {was}: {r.get('url') or ''}", 24 * 365)
            return "achtung", text
        return "uebersprungen", grund
    if eintrag.get("rechnung") and not eintrag.get("nachfuehren"):
        return "vorhanden", "schon in Haben"
    status, data = haben(cfg, "GET", "/rechnungen?" + urllib.parse.urlencode({"externalSource": QUELLE, "externalId": oid}))
    data = haben_ok(status, data, "Rechnungssuche")
    if data.get("total", 0) > 0:
        r = data["data"][0]
        eintrag["rechnung"] = {"id": r.get("id"), "url": r.get("url"), "nummer": r.get("number"), "status": r.get("status")}
        if schreiben and eintrag.get("nachfuehren") and r.get("status") == "draft":
            sendungen = eintrag.get("sendungen") or []
            notiz = "\n".join(zusatzdaten(order, sendungen))[:2000]
            lines, _ = positionen(order, int(cfg["steuersatz"]))
            datum = bestelldatum(order)
            st, antw = haben(cfg, "PATCH", f"/rechnungen/{r['id']}",
                             {"note": notiz, "lines": lines, "priceMode": "brutto", "issueDate": datum,
                              "serviceFrom": datum, "serviceTo": datum, **bezahlt(order)})
            haben_ok(st, antw, "Entwurf nachführen")
            eintrag["nachfuehren"] = False
            brutto = (antw or {}).get("gross")
            text = "Entwurf neu berechnet" + (f" ({euro(brutto)})" if brutto is not None else "")
            if r.get("contactId"):
                ergaenzt = kontakt_ergaenzen(cfg, r["contactId"], kontakt_aus_bestellung(order), (order.get("buyer") or {}).get("username") or "")
                if ergaenzt:
                    text += " – Kontakt ergänzt: " + ", ".join(ergaenzt)
            return "aktualisiert", text
        if schreiben or r.get("status") != "draft":
            eintrag["nachfuehren"] = False
        return "vorhanden", "schon in Haben"
    k = kontakt_aus_bestellung(order)
    lines, hinweise = positionen(order, int(cfg["steuersatz"]))
    if not lines:
        return "uebersprungen", "keine Positionen"
    datum = bestelldatum(order)
    sendungen = eintrag["sendungen"] if eintrag.get("sendungen") is not None else ebay_versand(cfg, order)
    notiz = "\n".join(zusatzdaten(order, sendungen))[:2000]
    gebuehr = cent(order.get("totalMarketplaceFee"))
    if gebuehr:
        hinweise.append(f"eBay-Gebühr {euro(gebuehr)} (kommt mit dem Gebührenbeleg des Monats)")
    if (order.get("paymentSummary") or {}).get("refunds"):
        hinweise.append("teilweise erstattet – Gutschrift in Haben prüfen")
    brutto = sum(l["unitPrice"] * l["quantity"] // 1000 for l in lines) / 100
    betrag = f"{brutto:.2f}".replace(".", ",")
    text = f"{k['name']}, {k['plz']} {k['ort']} ({k['land']}), {betrag} €"
    if hinweise:
        text += " – " + "; ".join(hinweise)
    track = [s["shipmentTrackingNumber"] for s in sendungen if s.get("shipmentTrackingNumber")]
    if track:
        text += " – Sendung " + ", ".join(track)
    if not schreiben:
        return "probe", "würde anlegen: " + text
    kontakt = finde_kontakt(cfg, k)
    if kontakt is None:
        status, kontakt = haben(cfg, "POST", "/kontakte", {key: v for key, v in k.items() if v})
        kontakt = haben_ok(status, kontakt, "Kontakt anlegen")
        text += " – neuer Kontakt"
    else:
        ergaenzt = kontakt_ergaenzen(cfg, kontakt["id"], k, (order.get("buyer") or {}).get("username") or "", kontakt)
        if ergaenzt:
            text += " – Kontakt ergänzt: " + ", ".join(ergaenzt)
    entwurf = {
        "contactId": kontakt["id"],
        "externalSource": QUELLE,
        "externalId": oid,
        "buyerOrderReference": oid,
        "note": notiz,
        "priceMode": "brutto",
        "issueDate": datum,  # Rechnungsdatum = Leistungsdatum = Tag der Bestellung (Wunsch des Nutzers)
        "serviceFrom": datum,
        "serviceTo": datum,
        "lines": lines,
        **bezahlt(order),
    }
    status, rechnung = haben(cfg, "POST", "/rechnungen", entwurf)
    haben_ok(status, rechnung, "Rechnungsentwurf anlegen")
    if rechnung.get("taxTreatment") == "kleinunternehmer" and any(l["taxRate"] for l in lines):
        haben(cfg, "DELETE", f"/rechnungen/{rechnung['id']}")
        raise Abbruch("Haben ist auf Kleinunternehmer (§ 19 UStG) eingestellt, die eBay-Seite auf "
                      f"{int(cfg['steuersatz']) // 100} %. Entwurf wieder gelöscht, Lauf angehalten: Steuersatz in den Einstellungen anpassen.")
    eintrag["rechnung"] = {"id": rechnung.get("id"), "url": rechnung.get("url"), "nummer": rechnung.get("number"), "status": rechnung.get("status")}
    merke_kaeufer(str((order.get("buyer") or {}).get("username") or ""), k["name"], oid, kontakt["id"])
    return ("neu" if status == 201 else "vorhanden"), text
