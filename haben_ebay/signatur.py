"""Ed25519 nach RFC 8032 ohne Fremdbibliothek, für die digitale Signatur der eBay-Finances-API (RFC 9421)."""
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


# ── Digitale Signatur (Ed25519, RFC 8032) für die Finances-API ───────────────────────────────────────────────────

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    return _P - x if x % 2 else x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_xrecover(_BY), _BY, 1, _xrecover(_BY) * _BY % _P)


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    A = (y1 - x1) * (y2 - x2) % _P
    B_ = (y1 + x1) * (y2 + x2) % _P
    C = t1 * 2 * _D * t2 % _P
    D_ = z1 * 2 * z2 % _P
    E, F, G, H = B_ - A, D_ - C, D_ + C, B_ + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(pkt, n: int):
    q = (0, 1, 1, 0)
    while n:
        if n & 1:
            q = _add(q, pkt)
        pkt = _add(pkt, pkt)
        n >>= 1
    return q


def _enc(pkt) -> bytes:
    x, y, z, _ = pkt
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def ed25519_signieren(seed: bytes, nachricht: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    oeff = _enc(_mul(_B, a))
    r = int.from_bytes(hashlib.sha512(h[32:] + nachricht).digest(), "little") % _L
    R = _enc(_mul(_B, r))
    k = int.from_bytes(hashlib.sha512(R + oeff + nachricht).digest(), "little") % _L
    S = (r + k * a) % _L
    return R + S.to_bytes(32, "little")


def ed25519_seed(privat: str) -> bytes:
    """Private Key von eBay: Base64 (auch als PEM) eines PKCS#8-Schlüssels oder der 32 Byte selbst."""
    roh = "".join(z for z in privat.strip().splitlines() if not z.startswith("-----"))
    der = base64.b64decode(roh)
    if len(der) == 32:
        return der
    i = der.find(bytes.fromhex("0420"), len(der) - 34)
    if len(der) >= 48 and i >= 0:
        return der[i + 2: i + 34]
    raise Fehler("Signaturschlüssel von eBay hat ein unbekanntes Format")
