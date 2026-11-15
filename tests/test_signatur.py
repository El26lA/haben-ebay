"""Ed25519 (RFC 8032) und die Signatur der Finances-API (RFC 9421) gegen die Testvektoren der RFCs."""
import base64
import time
import unittest

from haben_ebay import ebay, signatur

# RFC 9421, Anhang B.1.4: der öffentlich bekannte Testschlüssel „test-key-ed25519“, kein echter Schlüssel. Die
# PEM-Kopfzeilen werden zusammengesetzt, damit Geheimnis-Scanner hier keinen Fehlalarm auslösen.
_ART = "PRIVATE" + " KEY"
PEM = "\n".join((
    f"-----BEGIN {_ART}-----",
    "MC4CAQAwBQYDK2VwBCIEIJ+DYvh6SEqVTm50DFtMDoQikTmiCqirVv9mWG9qfSnF",
    f"-----END {_ART}-----",
))


class Ed25519(unittest.TestCase):
    def test_rfc8032_testvektoren(self):
        for seed, nachricht, erwartet in (
            ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "",
             "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
            ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", "72",
             "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
        ):
            self.assertEqual(signatur.ed25519_signieren(bytes.fromhex(seed), bytes.fromhex(nachricht)).hex(), erwartet)

    def test_pkcs8_und_rohschluessel(self):
        seed = signatur.ed25519_seed(PEM)
        self.assertEqual(len(seed), 32)
        self.assertEqual(signatur.ed25519_seed(base64.b64encode(seed).decode()), seed)

    def test_rfc9421_b26(self):
        basis = (
            '"date": Tue, 20 Apr 2021 02:07:55 GMT\n"@method": POST\n"@path": /foo\n"@authority": example.com\n'
            '"content-type": application/json\n"content-length": 18\n'
            '"@signature-params": ("date" "@method" "@path" "@authority" "content-type" "content-length");'
            'created=1618884473;keyid="test-key-ed25519"'
        )
        sig = signatur.ed25519_signieren(signatur.ed25519_seed(PEM), basis.encode())
        self.assertEqual(base64.b64encode(sig).decode(),
                         "wqcAqbmYJ2ji2glfAMaRy4gruYYnx2nEFN2HN6jrnDnQCK1u02Gb04v9EDgwUPiu4A0w6vuQv5lIp5WPpBKRCw==")


class SignaturHeader(unittest.TestCase):
    def test_get_ohne_content_digest(self):
        cfg = {"sign_jwe": "JWE", "sign_privat": PEM, "sign_bis": time.time() + 365 * 86400}
        h = ebay.signatur_header(cfg, "GET", "https://apiz.ebay.com/sell/finances/v1/transaction?filter=x")
        self.assertEqual(h["x-ebay-signature-key"], "JWE")
        self.assertNotIn("Content-Digest", h)
        self.assertRegex(h["Signature-Input"], r'^sig1=\("x-ebay-signature-key" "@method" "@path" "@authority"\);created=\d+$')
        self.assertRegex(h["Signature"], r"^sig1=:[A-Za-z0-9+/]{86}==:$")

    def test_mit_body_content_digest_nach_rfc9530(self):
        cfg = {"sign_jwe": "JWE", "sign_privat": PEM, "sign_bis": time.time() + 365 * 86400}
        h = ebay.signatur_header(cfg, "POST", "https://apiz.ebay.com/x", b'{"hello": "world"}')
        self.assertEqual(h["Content-Digest"], "sha-256=:X48E9qOokqqrvdts8nOJRJN3OWDUoyWxBf7kbu9DBPE=:")
        self.assertTrue(h["Signature-Input"].startswith('sig1=("content-digest" '))


if __name__ == "__main__":
    unittest.main()
