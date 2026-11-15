"""Gemeinsames für die Tests: Datenordner leeren, Beispieldaten, nachgebautes Haben und ein Mini-SMTP-Server."""
import base64
import copy
import email.parser
import json
import os
import pathlib
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from haben_ebay import basis

BEISPIEL = pathlib.Path(__file__).with_name("beispiel.json")


def beispiel() -> dict:
    return json.loads(BEISPIEL.read_text(encoding="utf-8"))


def datenordner_leeren() -> None:
    for name in os.listdir(basis.DATA_DIR):
        pfad = os.path.join(basis.DATA_DIR, name)
        if os.path.isfile(pfad):
            os.remove(pfad)
    basis.VERLAUF.clear()
    basis.LOG.clear()


class FakeHaben:
    """Antwortet wie die REST-API von Haben auf Kontakte, Rechnungen und Belege; merkt sich jede Anfrage."""

    def __init__(self, steuer: str = "kleinunternehmer"):
        self.aufrufe: list[tuple] = []
        self.rechnungen: dict[str, dict] = {}
        self.kontakte: dict[str, dict] = {}
        self.belege: dict[str, dict] = {}
        self.steuer = steuer
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def senden(self, status, obj=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def lesen(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n)

            def do_GET(self):
                fake.aufrufe.append(("GET", self.path, None))
                pfad, _, query = self.path.partition("?")
                q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                if pfad.endswith("/rechnungen"):
                    treffer = [r for r in fake.rechnungen.values() if r["externalId"] == q.get("externalId", r["externalId"])]
                    return self.senden(200, {"data": treffer, "total": len(treffer)})
                if pfad.endswith("/kontakte"):
                    return self.senden(200, {"data": list(fake.kontakte.values()), "total": len(fake.kontakte)})
                if "/kontakte/" in pfad:
                    return self.senden(200, fake.kontakte[pfad.rsplit("/", 1)[1]])
                self.senden(404, {"error": {"message": "nicht gefunden"}})

            def do_POST(self):
                body = self.lesen()
                if self.path.endswith("/belege"):
                    msg = email.parser.BytesParser().parsebytes(
                        b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
                    teile = {p.get_param("name", header="content-disposition"): p for p in msg.get_payload()}
                    eid = teile["externalId"].get_payload()
                    fake.aufrufe.append(("POST", self.path, {"externalId": eid, "pdf": teile["datei"].get_payload(decode=True)}))
                    vorhanden = eid in fake.belege
                    fake.belege[eid] = {"id": "b-" + eid}
                    return self.senden(200 if vorhanden else 201, fake.belege[eid])
                daten = json.loads(body)
                fake.aufrufe.append(("POST", self.path, daten))
                if self.path.endswith("/kontakte"):
                    k = {"id": f"k{len(fake.kontakte) + 1}", **daten}
                    fake.kontakte[k["id"]] = k
                    return self.senden(201, k)
                r = {"id": f"r{len(fake.rechnungen) + 1}", "status": "draft", "number": None, "url": "http://haben/r",
                     "taxTreatment": fake.steuer, "gross": sum(l["unitPrice"] * l["quantity"] // 1000 for l in daten["lines"]), **daten}
                fake.rechnungen[r["id"]] = r
                self.senden(201, r)

            def do_PATCH(self):
                daten = json.loads(self.lesen())
                fake.aufrufe.append(("PATCH", self.path, daten))
                ziel = fake.kontakte if "/kontakte/" in self.path else fake.rechnungen
                obj = ziel[self.path.rsplit("/", 1)[1]]
                obj.update(daten)
                self.senden(200, obj)

            def do_DELETE(self):
                fake.aufrufe.append(("DELETE", self.path, None))
                fake.rechnungen.pop(self.path.rsplit("/", 1)[1], None)
                self.senden(204)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/v1"

    def schliessen(self):
        self.server.shutdown()

    def anfragen(self, methode: str, endung: str) -> list:
        return [a for a in self.aufrufe if a[0] == methode and a[1].split("?")[0].endswith(endung)]


class FakeSMTP:
    """Mini-SMTP-Server ohne Verschlüsselung; Anmeldung gelingt nur mit dem Passwort „richtig“."""

    def __init__(self):
        self.mails: list[dict] = []
        fake = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                w = lambda t: self.wfile.write((t + "\r\n").encode())  # noqa: E731
                w("220 test")
                daten, im_data, nutzer = [], False, None
                while True:
                    z = self.rfile.readline()
                    if not z:
                        return
                    z = z.decode(errors="replace").rstrip("\r\n")
                    if im_data:
                        if z == ".":
                            im_data = False
                            fake.mails.append({"nutzer": nutzer, "text": "\n".join(daten)})
                            daten = []
                            w("250 ok")
                        else:
                            daten.append(z)
                        continue
                    c = z.upper()
                    if c.startswith("EHLO"):
                        w("250-test")
                        w("250 AUTH PLAIN LOGIN")
                    elif c.startswith("AUTH PLAIN"):
                        teile = base64.b64decode(z.split()[2]).split(b"\0")
                        if teile[2] == b"richtig":
                            nutzer = teile[1].decode()
                            w("235 ok")
                        else:
                            w("535 falsch")
                    elif c == "DATA":
                        im_data = True
                        w("354 los")
                    elif c == "QUIT":
                        w("221 tschuess")
                        return
                    else:
                        w("250 ok")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def schliessen(self):
        self.server.shutdown()


def tief(obj):
    return copy.deepcopy(obj)
