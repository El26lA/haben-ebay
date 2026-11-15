"""REST-Client für Haben."""
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


# ── Haben ────────────────────────────────────────────────────────────────────────────────────────────────────────


def haben(cfg: dict, method: str, pfad: str, body: dict | None = None):
    if not cfg["haben_token"]:
        raise Fehler("Haben-API-Token fehlt")
    headers = {"Authorization": "Bearer " + cfg["haben_token"], "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    return http(method, cfg["haben_url"].rstrip("/") + pfad, headers, data)


def haben_ok(status: int, data, was: str):
    if status not in (200, 201):
        err = (data or {}).get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            err = err.get("message", err)
        raise Fehler(f"Haben {was} ({status}): {err}")
    return data
