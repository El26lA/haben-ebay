"""eBay-Gebühren je abgeschlossenem Monat als Eigenbeleg (PDF) an Haben."""
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
from .ebay import ebay_transaktionen
from .haben import haben_ok


# ── Gebühren → Eigenbeleg je Monat ───────────────────────────────────────────────────────────────────────────────

GEBUEHRART = {
    "FINAL_VALUE_FEE": "Verkaufsprovision",
    "FINAL_VALUE_FEE_FIXED_PER_ORDER": "Fixgebühr je Bestellung",
    "FINAL_VALUE_SHIPPING_FEE": "Provision auf Versand",
    "INTERNATIONAL_FEE": "Gebühr für internationale Verkäufe",
    "AD_FEE": "Anzeigengebühr (Promoted Listings)",
    "PROMOTED_LISTINGS_FEE": "Anzeigengebühr (Promoted Listings)",
    "INSERTION_FEE": "Angebotsgebühr",
    "REGULATORY_OPERATING_FEE": "Regulierungsgebühr",
    "SUBSCRIPTION_FEE": "Shop-Abo",
    "STORE_SUBSCRIPTION_FEE": "Shop-Abo",
    "BELOW_STANDARD_FEE": "Zuschlag „unter Standard“",
    "HIGH_ITEM_NOT_AS_DESCRIBED_FEE": "Zuschlag Artikel nicht wie beschrieben",
    "PAYMENT_DISPUTE_FEE": "Gebühr Zahlungskonflikt",
    "CHARITY_DONATION": "Spende",
    "OTHER_FEES": "sonstige Gebühr",
}
TXART = {"SALE": "Verkauf", "REFUND": "Erstattung", "CREDIT": "Gutschrift", "DISPUTE": "Zahlungskonflikt",
         "NON_SALE_CHARGE": "Gebühr", "SHIPPING_LABEL": "Versandetikett", "TRANSFER": "Überweisung",
         "ADJUSTMENT": "Korrektur", "WITHDRAWAL": "Auszahlung", "PURCHASE": "Kauf", "LOAN_REPAYMENT": "Darlehen"}


def gebuehr_text(typ: str | None) -> str:
    return GEBUEHRART.get(typ or "", (typ or "Gebühr").replace("_", " ").capitalize())


def monat_von(tx: dict) -> str:
    t = dt.datetime.fromisoformat(tx["transactionDate"].replace("Z", "+00:00")).astimezone()
    return t.strftime("%Y-%m")


def gebuehr_posten(tx: dict) -> list[tuple[str, str, int]]:
    """(Art, Bezug, Betrag in Cent; positiv = Kosten, negativ = Gutschrift) je Geldbewegung."""
    typ = tx.get("transactionType")
    bezug = tx.get("orderId") or tx.get("transactionMemo") or tx.get("transactionId") or ""
    posten = []
    if typ in ("SALE", "REFUND", "DISPUTE"):
        vorz = 1 if typ == "SALE" else -1  # bei Erstattung bekommt man Gebühren zurück
        einzeln = 0
        for li in tx.get("orderLineItems") or []:
            for fee in li.get("marketplaceFees") or []:
                b = cent(fee.get("amount"))
                if b:
                    posten.append(((vorz < 0 and "Zurück: " or "") + gebuehr_text(fee.get("feeType")), bezug, vorz * b))
                    einzeln += b
            for fee in li.get("donations") or []:
                b = cent(fee.get("amount"))
                if b:
                    posten.append((gebuehr_text("CHARITY_DONATION"), bezug, vorz * b))
                    einzeln += b
        rest = cent(tx.get("totalFeeAmount")) - einzeln
        if rest:
            posten.append(("Gebühren", bezug, vorz * rest))
    elif typ in ("NON_SALE_CHARGE", "SHIPPING_LABEL"):
        b = cent(tx.get("amount"))
        vorz = -1 if tx.get("bookingEntry") == "CREDIT" else 1
        art = gebuehr_text(tx.get("feeType")) if typ == "NON_SALE_CHARGE" else "Versandetikett"
        if b:
            posten.append((art, bezug if typ == "SHIPPING_LABEL" else (tx.get("transactionMemo") or bezug), vorz * b))
    return posten


def monatsuebersicht(transaktionen: list[dict]) -> dict[str, dict]:
    """JJJJ-MM → {"posten": [(datum, art, bezug, cent)], "summe", "verkauf", "erstattet", "ausgezahlt", "anzahl"}"""
    monate: dict[str, dict] = {}
    for tx in transaktionen:
        if not tx.get("transactionDate"):
            continue
        w = (tx.get("amount") or {}).get("currency", "EUR")
        m = monate.setdefault(monat_von(tx), {"posten": [], "summe": 0, "verkauf": 0, "erstattet": 0, "anzahl": 0, "fremd": 0})
        if w != "EUR":
            m["fremd"] += 1
            continue
        m["anzahl"] += 1
        typ = tx.get("transactionType")
        if typ == "SALE":
            m["verkauf"] += cent(tx.get("totalFeeBasisAmount")) or cent(tx.get("amount")) + cent(tx.get("totalFeeAmount"))
        elif typ == "REFUND":
            m["erstattet"] += cent(tx.get("amount"))
        for art, bezug, b in gebuehr_posten(tx):
            m["posten"].append((datum_de(tx["transactionDate"]), art, bezug, b))
            m["summe"] += b
    for m in monate.values():
        m["posten"].sort(key=lambda x: (x[0][6:], x[0][3:5], x[0][:2]))
    return monate


def pdf_text(titel: str, zeilen: list[str]) -> bytes:
    """Schlichtes A4-PDF (Courier) ohne Fremdbibliothek."""
    def esc(t: str) -> bytes:
        b = t.encode("cp1252", "replace")
        return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

    je_seite = 68
    seiten = [zeilen[i : i + je_seite] for i in range(0, len(zeilen), je_seite)] or [[]]
    objs: list[bytes] = []
    n_seiten = len(seiten)
    # 1 Katalog, 2 Seiten, 3 Schrift, 4 Schrift fett, dann je Seite: Seite + Inhalt
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n_seiten))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_seiten} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    for i, sz in enumerate(seiten):
        inhalt = b"BT /F2 12 Tf 50 800 Td (" + esc(titel) + b") Tj ET\n"
        inhalt += b"BT /F1 8 Tf 50 780 Td 10.6 TL\n"
        for z in sz:
            inhalt += b"(" + esc(z) + b") '\n"
        inhalt += b"ET\n"
        inhalt += b"BT /F1 7 Tf 50 30 Td (" + esc(f"Seite {i + 1} von {n_seiten}") + b") Tj ET\n"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + 2 * i} 0 R >>".encode())
        objs.append(f"<< /Length {len(inhalt)} >>\nstream\n".encode() + inhalt + b"endstream")
    out = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    pos = []
    for i, o in enumerate(objs, 1):
        pos.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{p:010d} 00000 n \n".encode() for p in pos)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def gebuehrenbeleg(monat: str, m: dict, verkaeufer: str = "") -> tuple[bytes, str]:
    jahr, mon = monat.split("-")
    titel = f"eBay-Gebühren {mon}/{jahr} – Eigenbeleg"
    z = [
        "Zusammenstellung aus den Finanzdaten des eBay-Verkäuferkontos (eBay Finances API),",
        f"erzeugt am {dt.date.today().strftime('%d.%m.%Y')} vom eBay-Abgleich. Ersetzt nicht die Gebührenrechnung von eBay.",
        "",
        f"{'Datum':<11}{'Art':<38}{'Bezug':<32}{'Betrag':>14}",
        "-" * 95,
    ]
    for datum, art, bezug, b in m["posten"]:
        z.append(f"{datum:<11}{kurz(art, 37):<38}{kurz(bezug, 31):<32}{euro(b):>14}")
    z += ["-" * 95, f"{'Summe Gebühren und Kosten (brutto, wie von eBay abgezogen)':<81}{euro(m['summe']):>14}", ""]
    z.append(f"Zum Vergleich: Verkäufe im Monat {euro(m['verkauf'])}, Erstattungen {euro(m['erstattet'])}, "
             f"{m['anzahl']} Geldbewegungen.")
    if m.get("fremd"):
        z.append(f"{m['fremd']} Geldbewegungen in Fremdwährung sind nicht enthalten.")
    notiz = (f"Eigenbeleg aus eBay-Finanzdaten {mon}/{jahr}: {len(m['posten'])} Posten, Summe {euro(m['summe'])}. "
             "Gebucht wird erst nach Prüfung" + ("." if int(lade_config()["steuersatz"]) == 0 else
             "; für den Vorsteuerabzug zählt die Gebührenrechnung von eBay."))
    return pdf_text(titel, z), notiz


def beleg_hochladen(cfg: dict, dateiname: str, inhalt: bytes, extern_id: str, notiz: str):
    grenze = "----ebayhaben" + secrets.token_hex(12)
    teile = []
    for name, wert in (("externalSource", QUELLE_GEBUEHR), ("externalId", extern_id), ("note", notiz)):
        teile.append(f'--{grenze}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{wert}\r\n'.encode())
    teile.append(f'--{grenze}\r\nContent-Disposition: form-data; name="datei"; filename="{dateiname}"\r\n'
                 "Content-Type: application/pdf\r\n\r\n".encode() + inhalt + b"\r\n")
    teile.append(f"--{grenze}--\r\n".encode())
    headers = {"Authorization": "Bearer " + cfg["haben_token"], "Accept": "application/json",
               "Content-Type": f"multipart/form-data; boundary={grenze}"}
    status, data = http("POST", cfg["haben_url"].rstrip("/") + "/belege", headers, b"".join(teile))
    return haben_ok(status, data, "Beleg hochladen"), status


def gebuehren_abgleich(cfg: dict, schreiben: bool, voll: bool, transaktionen: list[dict] | None = None) -> str:
    if transaktionen is None:
        return "Gebühren: keine Finanzdaten von eBay (siehe oben)"
    erledigt = lade_json(GEBUEHR_FILE, {})
    jetzt = dt.datetime.now().astimezone()
    erster_dieses_monats = jetzt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    frueheste = (erster_dieses_monats - dt.timedelta(days=GEBUEHR_MONATE_MAX * 31)).replace(day=1)
    frueheste = max(frueheste, jetzt - dt.timedelta(days=5 * 365 - 2))
    nur_ab = cfg.get("nur_ab")
    if nur_ab:
        frueheste = max(frueheste, dt.datetime.fromisoformat(nur_ab).astimezone().replace(day=1))
    monate = []
    t = frueheste.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while t < erster_dieses_monats:
        monate.append(t.strftime("%Y-%m"))
        t = (t + dt.timedelta(days=32)).replace(day=1)
    offen = [m for m in monate if m not in erledigt]
    if not offen:
        return "Gebühren: alle abgeschlossenen Monate schon in Haben"
    if transaktionen is None:
        von = dt.datetime.strptime(offen[0] + "-01", "%Y-%m-%d").astimezone()
        von = max(von, jetzt - dt.timedelta(days=5 * 365 - 2))
        transaktionen = ebay_transaktionen(cfg, von, erster_dieses_monats)
    uebersicht = monatsuebersicht(transaktionen)
    neu = leer = 0
    for monat in offen:
        m = uebersicht.get(monat)
        if not m or not m["posten"]:
            leer += 1
            if schreiben:
                erledigt[monat] = {"beleg": None, "summe": 0, "posten": 0}
            continue
        pdf, notiz = gebuehrenbeleg(monat, m)
        text = f"{len(m['posten'])} Posten, Summe {euro(m['summe'])}"
        if not schreiben:
            VERLAUF.appendleft((dt.datetime.now().strftime("%d.%m. %H:%M"), f"Gebühren {monat}", "probe", "würde als Beleg anlegen: " + text))
            neu += 1
            continue
        beleg, status = beleg_hochladen(cfg, f"ebay-gebuehren-{monat}.pdf", pdf, monat, notiz)
        erledigt[monat] = {"beleg": beleg.get("id"), "summe": m["summe"], "posten": len(m["posten"])}
        speichere_json(GEBUEHR_FILE, erledigt)
        VERLAUF.appendleft((dt.datetime.now().strftime("%d.%m. %H:%M"), f"Gebühren {monat}", "neu" if status == 201 else "vorhanden", text))
        log(f"Gebührenbeleg {monat}: {text}")
        neu += 1
    if schreiben:
        speichere_json(GEBUEHR_FILE, erledigt)
    return f"Gebühren: {neu} Monatsbelege {'würden angelegt' if not schreiben else 'an Haben'}, {leer} Monate ohne Gebühren"
