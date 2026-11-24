"""Einstellungsseite, Verlauf, Anmeldung und Meldestelle (HTTP-Handler)."""
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
from .abgleich import *  # noqa: F403
from .abgleich import _auftrag  # noqa: F401
from .archiv import *  # noqa: F403
from .bestellung import *  # noqa: F403
from .ebay import *  # noqa: F403
from .ebay import _access
from .gebuehren import *  # noqa: F403
from .haben import haben
from .kontoloeschung import *  # noqa: F403
from .mail import *  # noqa: F403


# ── Anmeldung ────────────────────────────────────────────────────────────────────────────────────────────────────


def passwort_hash(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 300_000)
    return f"pbkdf2${salt.hex()}${h.hex()}"


def passwort_ok(pw: str, gespeichert: str) -> bool:
    try:
        _, salt, h = gespeichert.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 300_000).hex(), h)


SITZUNGEN: dict[str, dict] = {}  # SHA-256(id) → {"csrf", "bis", "dauerhaft"}
DAUERHAFT_TAGE = 365


def hk(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def sitzungen_laden() -> None:
    jetzt = time.time()
    SITZUNGEN.update({k: v for k, v in lade_json(SITZUNG_FILE, {}).items() if v.get("bis", 0) > jetzt})


def sitzungen_speichern() -> None:
    jetzt = time.time()
    speichere_json(SITZUNG_FILE, {k: {"csrf": v["csrf"], "bis": v["bis"], "dauerhaft": True}
                                  for k, v in SITZUNGEN.items() if v.get("dauerhaft") and v["bis"] > jetzt})
FEHLVERSUCHE: dict[str, list[float]] = {}
OAUTH_STATES: dict[str, float] = {}
SITZUNG_STUNDEN = 12


# ── Webseite ─────────────────────────────────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1b1f24;--muted:#5f6b7a;--line:#dde2e8;--accent:#1f6feb;--ok:#1a7f37;--bad:#cf222e;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--text:#e6e9ee;--muted:#9aa5b1;--line:#2a313a;--accent:#4c8dff;--ok:#3fb950;--bad:#f85149;--warn:#d29922}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:920px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}
.sub{color:var(--muted);margin:0 0 20px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin:0 0 16px}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}label{display:block;font-size:13px;color:var(--muted);margin:0 0 4px}
input[type=text],input[type=password],input[type=number],input[type=date],select{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--text);font:inherit}
.hint{font-size:12px;color:var(--muted);margin:4px 0 0}button,.btn{display:inline-block;padding:8px 14px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--text);font:inherit;cursor:pointer;text-decoration:none}
button.primary,.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:500}
pre{white-space:pre-wrap;font-size:12px;max-height:320px;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;margin:0}
.flash{padding:10px 14px;border-radius:8px;margin:0 0 16px;border:1px solid var(--line);background:var(--card)}.check{display:flex;gap:8px;align-items:center}
.stat{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}.stat div b{display:block;font-size:13px;color:var(--muted);font-weight:500}
"""


def e(x) -> str:
    return html.escape(str(x), quote=True)


def maskiert(wert: str) -> str:
    return "nicht gesetzt" if not wert else f"gesetzt (…{e(wert[-4:])})"


def seite(titel: str, inhalt: str) -> bytes:
    return (
        f"<!doctype html><html lang=de><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{e(titel)}</title><style>{CSS}</style></head><body><main>{inhalt}</main></body></html>"
    ).encode()


def login_seite(meldung: str = "") -> bytes:
    cfg = lade_config()
    if not cfg["passwort_hash"]:
        return seite("eBay → Haben", "<h1>eBay → Haben</h1><div class=card>Noch kein Passwort festgelegt. Auf dem NAS "
                     "<code>docker compose exec ebay python -m haben_ebay passwort</code> ausführen.</div>")
    fl = f"<div class='flash bad'>{e(meldung)}</div>" if meldung else ""
    return seite("Anmelden – eBay → Haben", f"""<h1>eBay → Haben</h1><p class=sub>Anmelden</p>{fl}
<form class=card method=post action=/login style='max-width:380px'><label for=pw>Passwort</label>
<input id=pw type=password name=passwort autofocus required autocomplete=current-password>
<label class=check style='margin-top:12px'><input type=checkbox name=bleiben value=1 checked> Auf diesem Gerät angemeldet bleiben (1 Jahr)</label>
<div class=actions><button class=primary>Anmelden</button></div></form>""")


def loesch_abschnitt(cfg: dict) -> str:
    """Kontolöschungen, die eBay gemeldet hat, und die Angaben für die Meldestelle im eBay-Entwicklerportal"""
    liste = lade_json(LOESCH_FILE, [])
    zeilen = "".join(
        f"<tr><td>{e(x['zeit'][:16].replace('T', ' '))}</td><td>{e(x['username'] or x['userId'] or '–')}</td><td>"
        + (
            f"{e(x['bekannt']['name'])} – Bestellungen {e(', '.join(x['bekannt']['bestellungen']))}"
            if x.get("bekannt")
            else "<span class=hint>keine übernommene Bestellung, nichts zu tun</span>"
        )
        + "</td></tr>"
        for x in liste[:50]
    ) or "<tr><td colspan=3 class=hint>Keine Meldungen.</td></tr>"
    bekannte = sum(1 for x in liste if x.get("bekannt"))
    return f"""<section class=card><h2>Kontolöschungen bei eBay</h2>
<p class=hint>Löscht ein Käufer sein eBay-Konto, meldet eBay das hier. Haben löscht nichts: Rechnungen sind 10 Jahre aufzubewahren
(§ 147 AO, § 14b UStG). Du entscheidest selbst, ob du Kontaktdaten außerhalb der Rechnungen anpasst.
{f"<strong class=warn>{bekannte} Meldung(en) betreffen eigene Kunden.</strong>" if bekannte else ""}</p>
<table><tr><th>Gemeldet</th><th>eBay-Nutzer</th><th>Betrifft</th></tr>{zeilen}</table>
<h2 style='margin-top:18px'>Meldestelle im eBay-Entwicklerportal</h2>
<p class=hint>developer.ebay.com › Alerts &amp; Notifications › Marketplace Account Deletion, beide Werte genau so eintragen:</p>
<div class=row><div><label for=hu2>Marketplace account deletion notification endpoint</label>
<input id=hu2 type=text readonly value='{e(cfg['hook_url'])}'></div>
<div><label for=ht2>Verification token</label><input id=ht2 type=text readonly value='{e(cfg['hook_token'])}'></div></div>
</section>"""


def gebuehr_info() -> str:
    erledigt = lade_json(GEBUEHR_FILE, {})
    belege = {m: d for m, d in erledigt.items() if d.get("beleg")}
    if not erledigt:
        return ""
    summe = sum(d.get("summe", 0) for d in belege.values())
    return (f"<p class=hint>Gebühren in Haben: {len(belege)} Monatsbelege ({min(erledigt)} bis {max(erledigt)}), "
            f"zusammen {e(euro(summe))}.</p>")


def mail_abschnitt(cfg: dict, hidden: str) -> str:
    sich = "".join(f"<option value={v}{' selected' if cfg.get('mail_sicherheit') == v else ''}>{t}</option>"
                   for v, t in (("starttls", "STARTTLS (meist Port 587)"), ("ssl", "SSL/TLS (meist Port 465)"), ("keine", "keine (nicht empfohlen)")))
    return f"""<form class=card method=post action=/mail>{hidden}<h2>E-Mail-Benachrichtigung</h2>
<p class=hint>Mail bei: eBay-Verbindung läuft in 30 Tagen ab (wöchentlich, in der letzten Woche täglich), drei Läufe in Folge mit Fehlern
(höchstens einmal am Tag), Kontolöschung eines Käufers, von dem es eine Rechnung gibt, sofort bei neutraler oder negativer Bewertung.</p>
<label class=check style='margin:8px 0 12px'><input type=checkbox name=mail_aktiv value=1{' checked' if cfg.get('mail_aktiv') else ''}> Benachrichtigungen einschalten</label>
<div class=row>
<div><label for=ms>SMTP-Server</label><input id=ms type=text name=mail_server value='{e(cfg.get('mail_server', ''))}' autocomplete=off>
<p class=hint>Name des Postausgangsservers deines Mail-Anbieters</p></div>
<div><label for=mp>Port</label><input id=mp type=number min=1 max=65535 name=mail_port value='{e(cfg.get('mail_port', 587))}'></div>
<div><label for=mx>Verschlüsselung</label><select id=mx name=mail_sicherheit>{sich}</select></div>
<div><label for=mb>Benutzer</label><input id=mb type=text name=mail_benutzer value='{e(cfg.get('mail_benutzer', ''))}' autocomplete=off>
<p class=hint>Meist die volle E-Mail-Adresse</p></div>
<div><label for=mw>Passwort</label><input id=mw type=password name=mail_passwort placeholder='{maskiert(cfg.get('mail_passwort', ''))}' autocomplete=new-password>
<p class=hint>Leer lassen = unverändert</p></div>
<div><label for=ma>Absender</label><input id=ma type=text name=mail_absender value='{e(cfg.get('mail_absender', ''))}' autocomplete=off>
<p class=hint>Leer = Benutzer. Bei 1&amp;1 muss es ein Postfach oder Alias des Kontos sein.</p></div>
<div><label for=me>Empfänger</label><input id=me type=text name=mail_empfaenger value='{e(cfg.get('mail_empfaenger', ''))}' autocomplete=off></div>
<div><label for=mo>Ordner für gesendete Mails</label><input id=mo type=text name=mail_ordner value='{e(cfg.get('mail_ordner', ''))}' autocomplete=off>
<p class=hint>Jede gesendete Mail kommt zusätzlich in diesen Ordner deines Postfachs (IMAP, Port 993); er wird bei Bedarf angelegt. Leer = aus.</p></div>
<div><label for=mi>IMAP-Server</label><input id=mi type=text name=mail_imap_server value='{e(cfg.get('mail_imap_server', ''))}' autocomplete=off>
<p class=hint>Leer = SMTP-Server mit „imap.“ statt „smtp.“</p></div>
</div>
<div class=actions><button class=primary>Speichern</button><button formaction=/mail/test>Speichern und Testmail senden</button></div></form>"""


def haupt_seite(sid: str, flash: str = "") -> bytes:
    cfg = lade_config()
    csrf = SITZUNGEN[hk(sid)]["csrf"]
    tage = int((cfg["ebay_refresh_bis"] - time.time()) // 86400) if cfg["ebay_refresh_bis"] else None
    if not cfg["ebay_refresh_token"]:
        ebay_status = "<span class=bad>nicht verbunden</span>"
    elif tage is not None and tage < 30:
        ebay_status = f"<span class=warn>verbunden, läuft in {tage} Tagen ab</span>"
    else:
        ebay_status = f"<span class=ok>verbunden</span> <span class=hint>(noch {tage} Tage)</span>"
    lauf = STATUS["letzter_lauf"].strftime("%d.%m.%Y %H:%M") if STATUS["letzter_lauf"] else "–"
    if STATUS["laeuft"]:
        lauf += f" <span class=pill>läuft gerade{(' – ' + e(STATUS.get('fortschritt'))) if STATUS.get('fortschritt') else ''}</span>"
    aktiv = "<span class=ok>aktiv</span>" if cfg["aktiv"] else "<span class=warn>pausiert</span>"
    naechster = ""
    if cfg["aktiv"] and STATUS["letzter_lauf"]:
        naechster = (STATUS["letzter_lauf"] + dt.timedelta(minutes=int(cfg["intervall_minuten"]))).strftime("%H:%M")
    fl = f"<div class=flash>{flash}</div>" if flash else ""
    verlauf = "".join(
        f"<tr><td>{e(z)}</td><td>{e(o)}</td><td class={'ok' if a == 'neu' else 'bad' if a in ('fehler', 'achtung') else ''}>"
        f"{ {'neu': 'angelegt', 'probe': 'Probe', 'fehler': 'Fehler', 'achtung': 'Achtung'}.get(a, e(a)) }</td><td>{e(t)}</td></tr>"
        for z, o, a, t in list(VERLAUF)[:150]
    ) or "<tr><td colspan=4 class=hint>Noch keine Bestellungen übernommen.</td></tr>"
    logtext = e("\n".join(list(LOG)[-80:][::-1])) or "–"
    steuer_opts = "".join(
        f"<option value={v}{' selected' if int(cfg['steuersatz']) == v else ''}>{t}</option>"
        for v, t in ((0, "Kleinunternehmer nach § 19 UStG (ohne Umsatzsteuer)"), (1900, "regelbesteuert, 19 %"), (700, "regelbesteuert, 7 %"))
    )
    hidden = f"<input type=hidden name=csrf value='{e(csrf)}'>"
    verbinden_ok = cfg["ebay_client_id"] and cfg["ebay_client_secret"] and cfg["ebay_runame"]
    return seite("eBay → Haben", f"""
<h1>eBay → Haben</h1><p class=sub>Bezahlte eBay-Bestellungen werden als Kontakt und Rechnungsentwurf in Haben angelegt.
Festschreiben und Versenden machst du in Haben.</p>{fl}
<section class=card><h2>Status</h2><div class=stat>
<div><b>Abgleich</b>{aktiv}</div><div><b>eBay-Konto</b>{ebay_status}</div>
<div><b>Umsatzsteuer</b>{'Kleinunternehmer (§ 19 UStG)' if int(cfg['steuersatz']) == 0 else f"regelbesteuert, {int(cfg['steuersatz']) // 100} %"}</div>
<div><b>Letzter Lauf</b>{lauf}</div><div><b>Nächster Lauf</b>{naechster or '–'}</div></div>
<p class=hint style='margin-top:10px'>{e(STATUS['letztes_ergebnis'])}</p>
<p class=hint>„Alles Alte holen“: Bestellungen der letzten 2 Jahre und Gebühren der letzten 5 Jahre (so weit eBay zurück liefert),
ab „Nur Bestellungen ab“, falls gesetzt. Gebühren kommen je abgeschlossenem Monat als Eigenbeleg unter Belege. Schon Vorhandenes wird übersprungen.</p>
{gebuehr_info()}
<form method=post class=actions>{hidden}
<button class=primary formaction=/jetzt>Jetzt abgleichen</button>
<button formaction=/probe>Probelauf (schreibt nichts)</button>
<button formaction=/vollprobe>Alles Alte – Probelauf</button>
<button formaction=/voll>Alles Alte holen</button>
<button formaction=/umschalten>{'Pausieren' if cfg['aktiv'] else 'Aktivieren'}</button></form></section>

<section class=card><h2>Alle eBay-Bestellungen</h2><p class=hint>{archiv_info()}</p>
<div class=actions><a class='btn primary' href=/bestellungen>Verlauf aller Bestellungen</a><a class=btn href=/auszahlungen>Geldbewegungen und Auszahlungen</a>
<a class=btn href=/bestellungen.csv>Bestellungen als CSV</a><a class=btn href=/archiv.json>Alles als JSON</a></div></section>

<section class=card><h2>Zuletzt übernommen</h2><table><tr><th>Zeit</th><th>Bestellung</th><th>Ergebnis</th><th>Details</th></tr>{verlauf}</table></section>

<form class=card method=post action=/einstellungen>{hidden}<h2>Einstellungen</h2>
<div class=row>
<div><label for=iv>Intervall (Minuten)</label><input id=iv type=number min=5 max=1440 name=intervall_minuten value='{e(cfg['intervall_minuten'])}'></div>
<div><label for=rb>Rückblick (Tage)</label><input id=rb type=number min=1 max=729 name=rueckblick_tage value='{e(cfg['rueckblick_tage'])}'>
<p class=hint>So weit zurück werden Bestellungen bei jedem Lauf geprüft. Doppelte entstehen nicht.</p></div>
<div><label for=na>Nur Bestellungen ab</label><input id=na type=date name=nur_ab value='{e(cfg['nur_ab'])}'>
<p class=hint>Ältere nie übernehmen, z. B. Tag der Umstellung auf Haben.</p></div>
<div><label for=st>Umsatzsteuer</label><select id=st name=steuersatz>{steuer_opts}</select>
<p class=hint>Muss zu Haben › Einstellungen › Firma passen. Haben hält den Lauf an, wenn es nicht passt.</p></div>
</div>
<h2 style='margin-top:18px'>Haben</h2><div class=row>
<div><label for=ht>API-Token</label><input id=ht type=password name=haben_token placeholder='{maskiert(cfg['haben_token'])}' autocomplete=new-password>
<p class=hint>Haben › Einstellungen › KI-Assistenten und API. Rechte: Kontakte schreiben, Rechnungen Entwürfe, Belege anlegen, für die Zahlungen auch Bankumsätze liefern (bank:importieren). Leer lassen = unverändert.</p></div>
<div><label><input type=checkbox name=zahlungen_an value=1{' checked' if cfg.get('zahlungen_an') else ''}> Zahlungen an Haben liefern</label>
<label for=zk>Kennung der Quelle</label><input id=zk type=text name=zahlungen_kennung value='{e(cfg.get('zahlungen_kennung') or 'ebay')}'>
<p class=hint>Haben ab 0.12: Geldbewegungen und Auszahlungen landen als Umsätze im eBay-Konto. Vorher in Haben unter Bank › eBay-Konto › Externe Quelle hinzufügen: Anbieter „haben-ebay“, Kennung wie hier.</p></div>
<div><label for=hu>API-Adresse</label><input id=hu type=text name=haben_url value='{e(cfg['haben_url'])}'>
<p class=hint>Im Container: http://app:3000/api/v1</p></div>
<div><label for=ha>Adresse von Haben</label><input id=ha type=text name=haben_adresse value='{e(cfg['haben_adresse'])}' placeholder='https://haben.example.de'>
<p class=hint>Wie im Browser. Erlaubt, diese Seite in Haben unter „Eigene Seiten“ einzubetten.</p></div>
<div><label for=sa>Adresse dieser Seite</label><input id=sa type=text name=seite_adresse value='{e(cfg['seite_adresse'])}' placeholder='https://ebay.example.de'>
<p class=hint>Für die Rücksprungadresse bei eBay und den Link in Benachrichtigungen.</p></div></div>
<h2 style='margin-top:18px'>eBay-Anwendung</h2><div class=row>
<div><label for=ci>App ID (Client ID)</label><input id=ci type=text name=ebay_client_id value='{e(cfg['ebay_client_id'])}' autocomplete=off></div>
<div><label for=cs>Cert ID (Client Secret)</label><input id=cs type=password name=ebay_client_secret placeholder='{maskiert(cfg['ebay_client_secret'])}' autocomplete=new-password></div>
<div><label for=rn>RuName (eBay Redirect URL name)</label><input id=rn type=text name=ebay_runame value='{e(cfg['ebay_runame'])}' autocomplete=off></div>
<div><label for=mk>Marktplatz</label><input id=mk type=text name=ebay_marktplatz value='{e(cfg['ebay_marktplatz'])}'>
<p class=hint>Für die Finanzdaten, z. B. EBAY_DE</p></div>
<div><label for=hk>Öffentliche Adresse der Meldestelle</label><input id=hk type=text name=hook_url value='{e(cfg['hook_url'])}' placeholder='https://ebay-hook.example.de/ebay/konto-loeschung'>
<p class=hint>Für Kontolöschungen bei eBay; muss aus dem Internet erreichbar sein und auf <code>/ebay/konto-loeschung</code> enden.</p></div></div>
<p class=hint>developer.ebay.com › Application Keysets (Production). Beim RuName als „Auth accepted URL“ eintragen:
<code>{e((cfg['seite_adresse'] or 'https://…').rstrip('/'))}/ebay/callback</code></p>
<div class=actions><button class=primary>Speichern</button></div></form>

<form class=card method=post action=/ebay/verbinden>{hidden}<h2>eBay-Konto verbinden</h2>
<p class=hint>Öffnet eBay in einem neuen Tab. Dort mit dem Verkäuferkonto anmelden und zustimmen. Die Freigabe gilt 18 Monate.
Rechte: Bestellungen lesen, Finanzdaten (für die Gebühren) und Bewertungen (Mail bei neutraler oder negativer).</p>
{'' if not cfg['ebay_refresh_token'] or 'commerce.feedback' in (cfg.get('ebay_scopes') or '') else '<p class=bad>Für die Meldung neutraler und negativer Bewertungen bitte neu verbinden.</p>'}
<div class=actions><button class=primary {'disabled' if not verbinden_ok else ''}>{'Neu verbinden' if cfg['ebay_refresh_token'] else 'Mit eBay verbinden'}</button>
{'' if verbinden_ok else '<span class=hint>Erst App ID, Cert ID und RuName speichern.</span>'}</div></form>

{mail_abschnitt(cfg, hidden)}

{loesch_abschnitt(cfg)}

<section class=card><h2>Protokoll</h2><pre>{logtext}</pre></section>
<form method=post action=/logout class=actions>{hidden}<button>Abmelden</button></form>""")


# ── Verlauf aller Bestellungen ───────────────────────────────────────────────────────────────────────────────────


def archiv_info() -> str:
    a = lade_archiv()
    b = a["bestellungen"]
    if not b:
        return "Noch nichts abgerufen. Ein Lauf (auch ein Probelauf) speichert alle Bestellungen hier dauerhaft."
    daten = sorted((x["order"].get("creationDate") or "")[:10] for x in b.values())
    storno = sum(1 for x in b.values() if bestellstatus(x["order"])[0] in ("storniert", "erstattet"))
    return (f"{len(b)} Bestellungen gespeichert ({datum_de(daten[0])} bis {datum_de(daten[-1])}), davon {storno} storniert oder erstattet; "
            f"{len(a['transaktionen'])} Geldbewegungen, {len(a['auszahlungen'])} Auszahlungen. Alles bleibt hier gespeichert, "
            "auch wenn eBay es nicht mehr liefert oder Namen und Adressen schwärzt.")


def kaeufer_text(o: dict) -> tuple[str, str]:
    k = kontakt_aus_bestellung(o)
    user = (o.get("buyer") or {}).get("username") or ""
    name = k["name"] if k["name"] != user else ""
    return (name or user), " ".join(x for x in (k["plz"], k["ort"]) if x) + (f" ({k['land']})" if k["land"] and k["land"] != "DE" else "")


def bestell_zeile(x: dict) -> dict:
    o = x["order"]
    st, kl = bestellstatus(o)
    wer, wo = kaeufer_text(o)
    return {
        "datum": (o.get("creationDate") or "")[:10], "id": o.get("orderId", ""), "kaeufer": wer, "ort": wo,
        "nutzer": (o.get("buyer") or {}).get("username") or "",
        "artikel": "; ".join(f"{li.get('quantity', 1)}× {li.get('title', '')}" for li in o.get("lineItems") or []),
        "summe": cent((o.get("pricingSummary") or {}).get("total")),
        "versandkosten": cent((o.get("pricingSummary") or {}).get("deliveryCost")),
        "gebuehr": cent(o.get("totalMarketplaceFee")),
        "erstattet": sum(b for _, b in kunden_erstattungen(o)),
        "status": st, "klasse": kl,
        "zahlung": "; ".join(f"{datum_de(p.get('paymentDate'))} {ZAHLART.get(p.get('paymentMethod'), p.get('paymentMethod') or '')} {p.get('paymentReferenceId') or ''}".strip()
                             for p in (o.get("paymentSummary") or {}).get("payments") or []),
        "sendung": "; ".join(f"{sx.get('shippingCarrierCode') or ''} {sx.get('shipmentTrackingNumber') or ''}".strip() for sx in x.get("sendungen") or []),
        "rechnung": x.get("rechnung") or {},
    }


def verlauf_seite(q: dict) -> bytes:
    a = lade_archiv()
    such = (q.get("q") or [""])[0].strip().lower()
    jahr = (q.get("jahr") or [""])[0]
    nur = (q.get("status") or [""])[0]
    zeilen = sorted((bestell_zeile(x) for x in a["bestellungen"].values()), key=lambda z: z["datum"], reverse=True)
    jahre = sorted({z["datum"][:4] for z in zeilen if z["datum"]}, reverse=True)
    if jahr:
        zeilen = [z for z in zeilen if z["datum"].startswith(jahr)]
    if nur == "storno":
        zeilen = [z for z in zeilen if z["status"] in ("storniert", "erstattet", "teilweise erstattet", "Storno angefragt")]
    elif nur == "ohne":
        zeilen = [z for z in zeilen if not z["rechnung"]]
    if such:
        zeilen = [z for z in zeilen if such in " ".join(str(v) for v in z.values() if not isinstance(v, dict)).lower()]
    summe = sum(z["summe"] for z in zeilen if z["status"] not in ("storniert", "erstattet"))
    gebuehr = sum(z["gebuehr"] for z in zeilen)
    jahr_opts = "".join(f"<option{' selected' if j == jahr else ''}>{j}</option>" for j in jahre)
    st_opts = "".join(f"<option value={v}{' selected' if v == nur else ''}>{t}</option>"
                      for v, t in (("", "alle"), ("storno", "storniert / erstattet"), ("ohne", "ohne Rechnung in Haben")))
    rows = "".join(
        f"<tr><td>{e(datum_de(z['datum']))}</td><td><a href='/bestellung?id={e(urllib.parse.quote(z['id']))}'>{e(z['id'])}</a></td>"
        f"<td>{e(z['kaeufer'])}<br><span class=hint>{e(z['ort'])}</span></td><td>{e(kurz(z['artikel'], 90))}</td>"
        f"<td style='text-align:right;white-space:nowrap'>{e(euro(z['summe']))}"
        f"{'<br><span class=hint>Gebühr ' + e(euro(z['gebuehr'])) + '</span>' if z['gebuehr'] else ''}</td>"
        f"<td class={z['klasse']}>{e(z['status'])}{'<br><span class=hint>' + e(kurz(z['sendung'], 40)) + '</span>' if z['sendung'] else ''}</td>"
        f"<td>{('<a href=' + chr(39) + e(z['rechnung'].get('url') or '') + chr(39) + ' target=_top>' + e(z['rechnung'].get('nummer') or 'Entwurf') + '</a>') if z['rechnung'] else '–'}</td></tr>"
        for z in zeilen
    ) or "<tr><td colspan=7 class=hint>Keine Bestellungen.</td></tr>"
    return seite("Bestellungen – eBay → Haben", f"""
<p><a href=/>← zurück</a></p><h1>Alle eBay-Bestellungen</h1><p class=sub>{e(archiv_info())}</p>
<form class=card method=get action=/bestellungen><div class=row>
<div><label for=q>Suche (Name, Nutzer, Ort, Artikel, Bestellnummer, Sendung)</label><input id=q type=text name=q value='{e(such)}'></div>
<div><label for=j>Jahr</label><select id=j name=jahr><option value=''>alle</option>{jahr_opts}</select></div>
<div><label for=s>Status</label><select id=s name=status>{st_opts}</select></div></div>
<div class=actions><button class=primary>Filtern</button><a class=btn href=/bestellungen>Zurücksetzen</a>
<a class=btn href='/bestellungen.csv?{e(urllib.parse.urlencode({"q": such, "jahr": jahr, "status": nur}))}'>Als CSV</a></div></form>
<section class=card><p class=hint>{len(zeilen)} Bestellungen, Umsatz ohne Stornos {e(euro(summe))}, eBay-Gebühren {e(euro(gebuehr))}</p>
<div style='overflow-x:auto'><table><tr><th>Datum</th><th>Bestellung</th><th>Käufer</th><th>Artikel</th><th>Betrag</th><th>Status</th><th>Haben</th></tr>{rows}</table></div></section>""")


def verlauf_csv(q: dict) -> bytes:
    a = lade_archiv()
    zeilen = sorted((bestell_zeile(x) for x in a["bestellungen"].values()), key=lambda z: z["datum"])
    jahr = (q.get("jahr") or [""])[0]
    if jahr:
        zeilen = [z for z in zeilen if z["datum"].startswith(jahr)]
    kopf = ["Datum", "Bestellnummer", "Käufer", "eBay-Nutzer", "PLZ/Ort", "Artikel", "Summe", "Versandkosten", "eBay-Gebühr",
            "Erstattet", "Status", "Zahlung", "Sendung", "Rechnung in Haben"]

    def zelle(v) -> str:
        v = str(v).replace('"', '""')
        return f'"{v}"'

    def betrag(c: int) -> str:
        return f"{c / 100:.2f}".replace(".", ",")

    out = [";".join(zelle(k) for k in kopf)]
    for z in zeilen:
        out.append(";".join(zelle(v) for v in (
            datum_de(z["datum"]), z["id"], z["kaeufer"], z["nutzer"], z["ort"], z["artikel"], betrag(z["summe"]),
            betrag(z["versandkosten"]), betrag(z["gebuehr"]), betrag(z["erstattet"]), z["status"], z["zahlung"], z["sendung"],
            z["rechnung"].get("nummer") or ("Entwurf" if z["rechnung"] else ""))))
    return ("﻿" + "\r\n".join(out) + "\r\n").encode("utf-8")


def json_block(daten) -> str:
    return f"<pre style='max-height:none'>{e(json.dumps(daten, indent=1, ensure_ascii=False))}</pre>"


def detail_seite(oid: str) -> bytes | None:
    a = lade_archiv()
    x = a["bestellungen"].get(oid)
    if not x:
        return None
    z = bestell_zeile(x)
    o = x["order"]
    k = kontakt_aus_bestellung(o)
    tx = [t for t in a["transaktionen"].values() if t.get("orderId") == oid]
    tx.sort(key=lambda t: t.get("transactionDate") or "")
    tx_rows = "".join(
        f"<tr><td>{e(datum_de(t.get('transactionDate')))}</td><td>{e(TXART.get(t.get('transactionType'), t.get('transactionType')))}</td>"
        f"<td style='text-align:right'>{e(euro(cent(t.get('amount')) * (-1 if t.get('bookingEntry') == 'DEBIT' else 1)))}</td>"
        f"<td style='text-align:right'>{e(euro(cent(t.get('totalFeeAmount'))))}</td><td>{e(t.get('transactionStatus') or '')}</td>"
        f"<td>{e(t.get('payoutId') or '')}</td></tr>" for t in tx
    ) or "<tr><td colspan=6 class=hint>Keine Geldbewegungen gespeichert.</td></tr>"
    pos = "".join(
        f"<tr><td>{e(li.get('quantity'))}</td><td>{e(li.get('title'))}<br><span class=hint>Artikel {e(li.get('legacyItemId') or '')} {e(li.get('sku') or '')}</span></td>"
        f"<td style='text-align:right'>{e(euro(cent(li.get('lineItemCost'))))}</td><td>{e(li.get('lineItemFulfillmentStatus') or '')}</td></tr>"
        for li in o.get("lineItems") or [])
    rechnung = (f"<a href='{e(z['rechnung'].get('url') or '')}' target=_top>{e(z['rechnung'].get('nummer') or 'Entwurf')}</a>"
                if z["rechnung"] else "noch keine")
    zusatz = "<br>".join(e(t) for t in zusatzdaten(o, x.get("sendungen") or []))
    return seite(f"{oid} – eBay → Haben", f"""
<p><a href=/bestellungen>← alle Bestellungen</a></p><h1>Bestellung {e(oid)}</h1>
<p class=sub><span class={z['klasse']}>{e(z['status'])}</span> · {e(datum_de(o.get('creationDate')))} · Rechnung in Haben: {rechnung}</p>
<section class=card><h2>Käufer</h2><p>{e(k['name'])}<br>{e(k['strasse'])}<br>{e(k['plz'])} {e(k['ort'])} {e(k['land'])}<br>
{e(k['email'])} {e(k['telefon'])}<br><span class=hint>eBay-Nutzer {e(z['nutzer'])}</span></p></section>
<section class=card><h2>Artikel</h2><table><tr><th>Menge</th><th>Artikel</th><th>Betrag</th><th>Status</th></tr>{pos}</table>
<p class=hint>Summe {e(euro(z['summe']))}, davon Versand {e(euro(z['versandkosten']))}; eBay-Gebühr {e(euro(z['gebuehr']))}; erstattet {e(euro(z['erstattet']))}</p></section>
<section class=card><h2>Zahlung und Versand</h2><p>{zusatz}</p></section>
<section class=card><h2>Geldbewegungen bei eBay</h2><table><tr><th>Datum</th><th>Art</th><th>Betrag</th><th>Gebühren</th><th>Status</th><th>Auszahlung</th></tr>{tx_rows}</table></section>
<section class=card><h2>Alle Daten von eBay</h2><p class=hint>Erstmals gespeichert {e(x.get('erstes', ''))}, zuletzt geändert {e(x.get('aktualisiert', ''))}</p>
<h2 style='margin-top:12px'>Bestellung</h2>{json_block(o)}<h2 style='margin-top:12px'>Sendungen</h2>{json_block(x.get('sendungen') or [])}
<h2 style='margin-top:12px'>Geldbewegungen</h2>{json_block(tx)}</section>""")


def geld_seite(q: dict) -> bytes:
    a = lade_archiv()
    tx = sorted(a["transaktionen"].values(), key=lambda t: t.get("transactionDate") or "", reverse=True)
    px = sorted(a["auszahlungen"].values(), key=lambda p: p.get("payoutDate") or "", reverse=True)
    t_rows = "".join(
        f"<tr><td>{e(datum_de(t.get('transactionDate')))}</td><td>{e(TXART.get(t.get('transactionType'), t.get('transactionType')))}"
        f"{'<br><span class=hint>' + e(gebuehr_text(t.get('feeType'))) + '</span>' if t.get('feeType') else ''}</td>"
        f"<td>{('<a href=' + chr(39) + '/bestellung?id=' + e(urllib.parse.quote(t['orderId'])) + chr(39) + '>' + e(t['orderId']) + '</a>') if t.get('orderId') in a['bestellungen'] else e(t.get('orderId') or t.get('transactionMemo') or '')}</td>"
        f"<td style='text-align:right'>{e(euro(cent(t.get('amount')) * (-1 if t.get('bookingEntry') == 'DEBIT' else 1)))}</td>"
        f"<td style='text-align:right'>{e(euro(cent(t.get('totalFeeAmount'))))}</td><td>{e(t.get('transactionStatus') or '')}</td></tr>"
        for t in tx[:2000]) or "<tr><td colspan=6 class=hint>Noch keine Geldbewegungen.</td></tr>"
    p_rows = "".join(
        f"<tr><td>{e(datum_de(p.get('payoutDate')))}</td><td>{e(p.get('payoutId'))}</td><td style='text-align:right'>{e(euro(cent(p.get('amount'))))}</td>"
        f"<td>{e(p.get('payoutStatus') or '')}</td><td>{e(((p.get('payoutInstrument') or {}).get('nickname') or '') + ' ' + ((p.get('payoutInstrument') or {}).get('accountLastFourDigits') or ''))}</td>"
        f"<td>{e(p.get('transactionCount') or '')}</td></tr>"
        for p in px) or "<tr><td colspan=6 class=hint>Noch keine Auszahlungen.</td></tr>"
    return seite("Geldbewegungen – eBay → Haben", f"""
<p><a href=/>← zurück</a></p><h1>Geldbewegungen und Auszahlungen</h1><p class=sub>Bis fünf Jahre zurück, wie eBay sie liefert; danach bleiben sie hier gespeichert.</p>
<section class=card><h2>Auszahlungen aufs Bankkonto</h2><div style='overflow-x:auto'><table><tr><th>Datum</th><th>Auszahlung</th><th>Betrag</th><th>Status</th><th>Konto</th><th>Buchungen</th></tr>{p_rows}</table></div></section>
<section class=card><h2>Geldbewegungen</h2><div style='overflow-x:auto'><table><tr><th>Datum</th><th>Art</th><th>Bezug</th><th>Betrag</th><th>Gebühren</th><th>Status</th></tr>{t_rows}</table></div>
{'<p class=hint>Nur die neuesten 2000 angezeigt; alle stehen im JSON.</p>' if len(tx) > 2000 else ''}</section>""")


class Handler(BaseHTTPRequestHandler):
    server_version = "ebay-haben"

    def log_message(self, fmt, *args):  # kein Zugriffsprotokoll mit Codes in URLs
        pass

    def client_ip(self) -> str:
        return (self.headers.get("X-Real-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0] or self.client_address[0]).strip()

    def antwort(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
        cfg = lade_config()
        self.send_response(status)
        if "Content-Type" not in (headers or {}):
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://auth.ebay.com; "
            f"frame-ancestors 'self' {urllib.parse.urlparse(cfg['haben_adresse']).netloc and cfg['haben_adresse'].rstrip('/')}; base-uri 'none'",
        )
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def weiter(self, ziel: str, extra: dict | None = None) -> None:
        self.antwort(303, b"", {"Location": ziel, **(extra or {})})

    def cookie(self, wert: str, max_age: int) -> str:
        sicher = "; Secure; SameSite=None; Partitioned" if lade_config()["cookie_secure"] else "; SameSite=Lax"
        return f"sid={wert}; Path=/; HttpOnly; Max-Age={max_age}{sicher}"

    def sitzung(self) -> str | None:
        for teil in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = teil.strip().partition("=")
            if k == "sid" and v and hk(v) in SITZUNGEN and SITZUNGEN[hk(v)]["bis"] > time.time():
                return v
        return None

    def formular(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20000:
            return {}
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode(), keep_blank_values=True).items()}

    def oeffentlich(self) -> bool:
        """Aufruf über den öffentlichen Namen der Meldestelle: dort gibt es nur den Pfad der Meldestelle"""
        hook_host = urllib.parse.urlparse(lade_config()["hook_url"]).hostname or ""
        return bool(hook_host) and (self.headers.get("Host") or "").split(":")[0].lower() == hook_host.lower()

    def json_antwort(self, status: int, daten: dict | None = None) -> None:
        body = json.dumps(daten).encode() if daten is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def hook_get(self, q: dict) -> None:
        cfg = lade_config()
        code = (q.get("challenge_code") or [""])[0]
        if not code or not cfg["hook_token"] or not cfg["hook_url"]:
            return self.json_antwort(400, {"error": "challenge_code fehlt oder Meldestelle nicht eingerichtet"})
        self.json_antwort(200, {"challengeResponse": challenge_antwort(code, cfg["hook_token"], cfg["hook_url"])})

    def hook_post(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 65536:
            return self.json_antwort(400)
        try:
            nachricht = json.loads(self.rfile.read(n))
        except ValueError:
            return self.json_antwort(400)
        eintrag = kontoloeschung_merken(nachricht) if isinstance(nachricht, dict) else None
        if eintrag:
            wer = eintrag["bekannt"]["name"] if eintrag["bekannt"] else "unbekannt (keine übernommene Bestellung)"
            log(f"KONTOLÖSCHUNG gemeldet: eBay-Nutzer {eintrag['username'] or eintrag['userId']} – {wer}")
            if eintrag["bekannt"]:
                b = eintrag["bekannt"]
                melden(f"loeschung_{eintrag['username'] or eintrag['userId']}", f"Kontolöschung: {b['name']}",
                       f"eBay meldet, dass der Käufer {eintrag['username'] or eintrag['userId']} ({b['name']}) sein eBay-Konto gelöscht hat.\n"
                       f"Bestellungen: {', '.join(b.get('bestellungen') or [])}\n\n"
                       "Der Abgleich löscht nichts. Rechnungen musst du wegen der Aufbewahrungspflicht ohnehin behalten; "
                       "ob du den Kontakt in Haben änderst, entscheidest du.", 24 * 365)
        # eBay erwartet 200/201/202/204, sonst wiederholt es die Meldung
        self.json_antwort(204)

    # GET
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == HOOK_PFAD:
            return self.hook_get(urllib.parse.parse_qs(url.query))
        if self.oeffentlich():
            return self.json_antwort(404)
        if url.path == "/health":
            return self.antwort(200, b"ok", {"Content-Type": "text/plain"})
        if url.path == "/ebay/callback":
            return self.callback(urllib.parse.parse_qs(url.query))
        seiten = ("/", "/bestellungen", "/bestellung", "/auszahlungen", "/bestellungen.csv", "/archiv.json")
        if url.path not in seiten:
            return self.antwort(404, seite("Nicht gefunden", "<h1>Nicht gefunden</h1>"))
        sid = self.sitzung()
        if not sid:
            return self.antwort(200, login_seite()) if url.path == "/" else self.weiter("/")
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/bestellungen":
            return self.antwort(200, verlauf_seite(q))
        if url.path == "/auszahlungen":
            return self.antwort(200, geld_seite(q))
        if url.path == "/bestellung":
            inhalt = detail_seite((q.get("id") or [""])[0])
            return self.antwort(200 if inhalt else 404, inhalt or seite("Nicht gefunden", "<h1>Bestellung nicht gefunden</h1>"))
        if url.path == "/bestellungen.csv":
            return self.antwort(200, verlauf_csv(q), {"Content-Type": "text/csv; charset=utf-8",
                                                      "Content-Disposition": "attachment; filename=ebay-bestellungen.csv"})
        if url.path == "/archiv.json":
            body = json.dumps(lade_archiv(), indent=1, ensure_ascii=False).encode()
            return self.antwort(200, body, {"Content-Type": "application/json; charset=utf-8",
                                            "Content-Disposition": "attachment; filename=ebay-archiv.json"})
        flash = SITZUNGEN[hk(sid)].pop("flash", "")
        self.antwort(200, haupt_seite(sid, flash))

    def callback(self, q: dict) -> None:
        state = (q.get("state") or [""])[0]
        code = (q.get("code") or [""])[0]
        gueltig = OAUTH_STATES.pop(state, 0) > time.time() if state else False
        if not gueltig or not code:
            return self.antwort(400, seite("eBay", "<h1>eBay-Verbindung</h1><div class='card bad'>Ungültiger oder abgelaufener "
                                            "Aufruf. Bitte auf der Seite erneut „Mit eBay verbinden“ klicken.</div>"))
        try:
            tage = ebay_code_einloesen(code)
            log(f"eBay-Konto verbunden, gültig {tage} Tage")
            text = f"<div class='card ok'>eBay-Konto verbunden (gültig {tage} Tage). Du kannst diesen Tab schließen.</div>"
            anstossen("sync") if lade_config()["aktiv"] else None
        except Fehler as ex:
            log(f"eBay-Verbindung fehlgeschlagen: {ex}")
            text = f"<div class='card bad'>{e(ex)}</div>"
        self.antwort(200, seite("eBay", "<h1>eBay-Verbindung</h1>" + text))

    # POST
    def do_POST(self):
        pfad = urllib.parse.urlparse(self.path).path
        if pfad == HOOK_PFAD:
            return self.hook_post()
        if self.oeffentlich():
            return self.json_antwort(404)
        if pfad == "/login":
            return self.login()
        sid = self.sitzung()
        f = self.formular()
        if not sid or not hmac.compare_digest(f.get("csrf", ""), SITZUNGEN[hk(sid)]["csrf"]):
            return self.weiter("/")
        cfg = lade_config()
        if pfad == "/logout":
            SITZUNGEN.pop(hk(sid), None)
            sitzungen_speichern()
            return self.weiter("/", {"Set-Cookie": self.cookie("", 0)})
        if pfad == "/einstellungen":
            fl = self.einstellungen(cfg, f)
        elif pfad == "/jetzt":
            anstossen("sync")
            fl = "Abgleich gestartet. Seite in ein paar Sekunden neu laden."
        elif pfad == "/probe":
            anstossen("probe")
            fl = "Probelauf gestartet. Ergebnis erscheint unter „Zuletzt übernommen“ und im Protokoll."
        elif pfad == "/vollprobe":
            anstossen("vollprobe")
            fl = "Probelauf über alles Alte gestartet (schreibt nichts). Das kann einige Minuten dauern."
        elif pfad == "/voll":
            anstossen("voll")
            fl = "Alles Alte wird geholt. Bei vielen Bestellungen dauert das (Haben erlaubt 120 Aufrufe je Minute)."
        elif pfad in ("/mail", "/mail/test"):
            fl = self.mail_einstellungen(cfg, f, pfad == "/mail/test")
        elif pfad == "/umschalten":
            cfg["aktiv"] = not cfg["aktiv"]
            speichere_config(cfg)
            log("Abgleich " + ("aktiviert" if cfg["aktiv"] else "pausiert"))
            if cfg["aktiv"]:
                anstossen("sync")
            fl = "Abgleich " + ("aktiviert." if cfg["aktiv"] else "pausiert.")
        elif pfad == "/ebay/verbinden":
            if not (cfg["ebay_client_id"] and cfg["ebay_client_secret"] and cfg["ebay_runame"]):
                return self.weiter("/")
            jetzt = time.time()
            for s, b in list(OAUTH_STATES.items()):
                if b < jetzt:
                    OAUTH_STATES.pop(s, None)
            state = secrets.token_urlsafe(24)
            OAUTH_STATES[state] = jetzt + 900
            ziel = e(ebay_consent_url(cfg, state))
            # Kein Redirect: form-action der CSP würde die Weiterleitungskette bei eBay blockieren
            return self.antwort(200, seite("eBay verbinden", "<h1>eBay-Konto verbinden</h1><section class=card>"
                f"<p>Weiter zu eBay: dort mit dem Verkäuferkonto anmelden und zustimmen. Danach kommst du hierher zurück.</p>"
                f"<div class=actions><a class='btn primary' href='{ziel}' target=_blank rel=noopener>Weiter zu eBay</a>"
                f"<a class=btn href=/>Abbrechen</a></div></section>"))
        else:
            return self.antwort(404, seite("Nicht gefunden", "<h1>Nicht gefunden</h1>"))
        SITZUNGEN[hk(sid)]["flash"] = fl
        self.weiter("/")

    def login(self) -> None:
        ip = self.client_ip()
        jetzt = time.time()
        versuche = [t for t in FEHLVERSUCHE.get(ip, []) if t > jetzt - 900]
        FEHLVERSUCHE[ip] = versuche
        if len(versuche) >= 5:
            return self.antwort(429, login_seite("Zu viele Fehlversuche. Bitte 15 Minuten warten."))
        f = self.formular()
        cfg = lade_config()
        if not cfg["passwort_hash"] or not passwort_ok(f.get("passwort", ""), cfg["passwort_hash"]):
            versuche.append(jetzt)
            log(f"Fehlgeschlagene Anmeldung von {ip}")
            time.sleep(1)
            return self.antwort(401, login_seite("Passwort falsch."))
        FEHLVERSUCHE.pop(ip, None)
        for s, d in list(SITZUNGEN.items()):
            if d["bis"] < jetzt:
                SITZUNGEN.pop(s, None)
        sid = secrets.token_urlsafe(32)
        dauerhaft = f.get("bleiben") == "1"
        dauer = DAUERHAFT_TAGE * 86400 if dauerhaft else SITZUNG_STUNDEN * 3600
        SITZUNGEN[hk(sid)] = {"csrf": secrets.token_urlsafe(24), "bis": jetzt + dauer, "dauerhaft": dauerhaft}
        sitzungen_speichern()
        self.weiter("/", {"Set-Cookie": self.cookie(sid, dauer)})

    def mail_einstellungen(self, cfg: dict, f: dict, test: bool) -> str:
        fehler = []
        cfg["mail_aktiv"] = f.get("mail_aktiv") == "1"
        for feld in ("mail_server", "mail_benutzer", "mail_absender", "mail_empfaenger", "mail_ordner", "mail_imap_server"):
            cfg[feld] = f.get(feld, "").strip()
        if f.get("mail_passwort", ""):
            cfg["mail_passwort"] = f["mail_passwort"]
        if f.get("mail_sicherheit") in ("starttls", "ssl", "keine"):
            cfg["mail_sicherheit"] = f["mail_sicherheit"]
        try:
            port = int(f.get("mail_port", ""))
            if not 1 <= port <= 65535:
                raise ValueError
            cfg["mail_port"] = port
        except ValueError:
            fehler.append("Port muss eine Zahl zwischen 1 und 65535 sein")
        for feld, name in (("mail_empfaenger", "Empfänger"), ("mail_absender", "Absender")):
            if cfg[feld] and ("@" not in cfg[feld] or any(c in cfg[feld] for c in " ,;<>\r\n")):
                fehler.append(f"{name} ist keine gültige E-Mail-Adresse")
        if any(c in cfg["mail_server"] for c in " /:@") :
            fehler.append("SMTP-Server nur als Name, z. B. smtp.example.de")
        if not cfg["mail_ordner"].isascii() or any(c in cfg["mail_ordner"] for c in '"\\\r\n'):
            fehler.append("Ordnername ohne Umlaute, Anführungszeichen und Backslash")
        if any(c in cfg["mail_imap_server"] for c in " /:@"):
            fehler.append("IMAP-Server nur als Name, z. B. imap.example.de")
        if (cfg["mail_aktiv"] or test) and not (cfg["mail_server"] and cfg["mail_empfaenger"]):
            fehler.append("SMTP-Server und Empfänger angeben")
        if fehler:
            return "<span class=bad>Nicht gespeichert: " + e("; ".join(fehler)) + "</span>"
        speichere_config(cfg)
        log("Mail-Einstellungen gespeichert")
        if not test:
            return "Mail-Einstellungen gespeichert" + ("" if cfg["mail_aktiv"] else " (Benachrichtigungen aus)") + "."
        try:
            mail_senden(cfg, "Testmail", "Diese Testmail zeigt, dass die Benachrichtigungen des eBay-Abgleichs ankommen.\n\n"
                        f"Server {cfg['mail_server']}:{cfg['mail_port']} ({cfg['mail_sicherheit']}), Benutzer {cfg['mail_benutzer'] or '–'}.")
            log(f"Testmail an {cfg['mail_empfaenger']} gesendet")
            return f"<span class=ok>Gespeichert. Testmail an {e(cfg['mail_empfaenger'])} gesendet – bitte Postfach (und Spam-Ordner) prüfen.</span>"
        except Fehler as ex:
            log(f"Testmail fehlgeschlagen: {ex}")
            return f"<span class=bad>Gespeichert, aber Testmail fehlgeschlagen: {e(ex)}</span>"

    def einstellungen(self, cfg: dict, f: dict) -> str:
        fehler = []

        def zahl(name, lo, hi):
            try:
                v = int(f.get(name, cfg[name]))
                if not lo <= v <= hi:
                    raise ValueError
                cfg[name] = v
            except ValueError:
                fehler.append(f"{name} muss zwischen {lo} und {hi} liegen")

        zahl("intervall_minuten", 5, 1440)
        zahl("rueckblick_tage", 1, 729)
        if f.get("steuersatz") in ("1900", "700", "0"):
            cfg["steuersatz"] = int(f["steuersatz"])
        na = f.get("nur_ab", "").strip()
        if na:
            try:
                dt.date.fromisoformat(na)
                cfg["nur_ab"] = na
            except ValueError:
                fehler.append("Datum „Nur ab“ ungültig")
        else:
            cfg["nur_ab"] = ""
        for feld in ("haben_url", "ebay_client_id", "ebay_runame", "haben_adresse", "seite_adresse", "hook_url", "ebay_marktplatz"):
            if feld in f:
                cfg[feld] = f[feld].strip()
        for feld, name in (("haben_adresse", "Adresse von Haben"), ("seite_adresse", "Adresse dieser Seite"), ("hook_url", "Adresse der Meldestelle")):
            if cfg[feld] and not cfg[feld].startswith("https://"):
                fehler.append(f"{name} muss mit https:// beginnen")
        if cfg["hook_url"] and not cfg["hook_url"].rstrip("/").endswith(HOOK_PFAD):
            fehler.append(f"Adresse der Meldestelle muss auf {HOOK_PFAD} enden")
        if "zahlungen_kennung" in f:
            cfg["zahlungen_an"] = f.get("zahlungen_an") == "1"
            cfg["zahlungen_kennung"] = f["zahlungen_kennung"].strip() or "ebay"
        for feld in ("haben_token", "ebay_client_secret"):
            if f.get(feld, "").strip():
                cfg[feld] = f[feld].strip()
        if not cfg["haben_url"].startswith(("http://", "https://")):
            fehler.append("API-Adresse muss mit http:// oder https:// beginnen")
        if fehler:
            return "<span class=bad>Nicht gespeichert: " + e("; ".join(fehler)) + "</span>"
        speichere_config(cfg)
        _access.update(token="", bis=0.0)
        log("Einstellungen gespeichert")
        # Verbindung zu Haben gleich prüfen
        try:
            status, data = haben(cfg, "GET", "/rechnungen?" + urllib.parse.urlencode({"externalSource": QUELLE, "limit": 1}))
            if status == 200:
                return f"Gespeichert. Verbindung zu Haben steht ({data.get('total', 0)} Rechnungen aus eBay)."
            meldung = (data or {}).get("error", {}).get("message", data) if isinstance(data, dict) else data
            return f"<span class=bad>Gespeichert, aber Haben antwortet mit {status}: {e(meldung)}</span>"
        except Fehler as ex:
            return "Gespeichert. Haben: " + e(ex)
