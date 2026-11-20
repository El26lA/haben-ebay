"""Zahlungen an Haben: eBay-Geldbewegungen als Bewegungen nach dem Importvertrag (erfundene Daten)."""
import os
import tempfile
import unittest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from haben_ebay.zahlungen import TEIL_MAX, bewegungen, lieferungen  # noqa: E402


def geld(wert: str) -> dict:
    return {"value": wert, "currency": "EUR"}


VERKAUF = {
    "transactionId": "T-1",
    "transactionType": "SALE",
    "bookingEntry": "CREDIT",
    "transactionStatus": "PAYOUT",
    "transactionDate": "2026-10-03T10:00:00.000Z",
    "amount": geld("39.93"),
    "totalFeeAmount": geld("10.06"),
    "orderId": "01-1",
    "payoutId": "P-1",
    "buyer": {"username": "kaeufer_1"},
}
ERSTATTUNG = {
    "transactionId": "T-2",
    "transactionType": "REFUND",
    "bookingEntry": "DEBIT",
    "transactionStatus": "PAYOUT",
    "transactionDate": "2026-10-05T10:00:00.000Z",
    "amount": geld("7.39"),
    "totalFeeAmount": geld("1.60"),
    "orderId": "01-1",
}
ABO = {
    "transactionId": "T-3",
    "transactionType": "NON_SALE_CHARGE",
    "bookingEntry": "DEBIT",
    "transactionStatus": "PAYOUT",
    "transactionDate": "2026-10-06T10:00:00.000Z",
    "amount": geld("39.95"),
    "transactionMemo": "Shop-Abo",
}
AUSZAHLUNG = {"payoutId": "P-1", "payoutStatus": "SUCCEEDED", "payoutDate": "2026-10-07T08:00:00.000Z", "amount": geld("12.20")}


class Zahlungen(unittest.TestCase):
    def test_brutto_und_gebuehr_ergeben_das_netto(self):
        b = bewegungen([VERKAUF, ERSTATTUNG, ABO], [AUSZAHLUNG])
        werte = [(m["sourceEventId"], m["component"], m["kind"], m["amount"]) for m in b]
        self.assertEqual(
            werte,
            [
                ("T-1", "fee", "payment", -1006),
                ("T-1", "main", "payment", 4999),
                ("T-2", "fee", "refund", 160),
                ("T-2", "main", "refund", -899),
                ("T-3", "main", "fee", -3995),
                ("P-1", "main", "payout", -1220),
            ],
        )
        # Haupt und Gebühr zusammen = das Netto, das eBay bucht
        self.assertEqual(sum(m["amount"] for m in b if m["sourceEventId"] == "T-1"), 3993)
        self.assertEqual(sum(m["amount"] for m in b if m["sourceEventId"] == "T-2"), -739)

    def test_erstattung_nennt_den_verkauf_und_die_bestellung(self):
        haupt = next(m for m in bewegungen([VERKAUF, ERSTATTUNG], []) if m["sourceEventId"] == "T-2" and m["component"] == "main")
        self.assertEqual(haupt["references"], {"eventCode": "REFUND", "orderId": "01-1", "relatedEventId": "T-1"})

    def test_fehlgeschlagene_auslassen(self):
        self.assertEqual(bewegungen([{**VERKAUF, "transactionStatus": "FAILED"}], [{**AUSZAHLUNG, "payoutStatus": "FAILED"}]), [])

    def test_teile_eines_abrufs(self):
        liste = bewegungen([VERKAUF], []) * (TEIL_MAX // 2 + 1)
        teile = lieferungen(liste, "ebay", "2026-10-01", "2026-10-31", "2026-11-01T06:00:00+01:00")
        self.assertEqual([len(t["movements"]) for t in teile], [TEIL_MAX, len(liste) - TEIL_MAX])
        self.assertEqual([t["part"]["last"] for t in teile], [False, True])
        self.assertEqual(len({t["part"]["fetchId"] for t in teile}), 1)
        self.assertEqual(teile[0]["provider"], "haben-ebay")


if __name__ == "__main__":
    unittest.main()
