"""Jeder globale Name, den eine Funktion benutzt, muss im Modul existieren (Ersatz für einen Linter, nur Standardbibliothek)."""
import builtins
import importlib
import os
import pathlib
import symtable
import tempfile
import unittest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
PAKET = pathlib.Path(__file__).resolve().parent.parent / "haben_ebay"


def globale_namen(tabelle):
    for kind in tabelle.get_children():
        for sym in kind.get_symbols():
            if sym.is_global() and sym.is_referenced():
                yield kind.get_name(), sym.get_name()
        yield from globale_namen(kind)


class Namen(unittest.TestCase):
    def test_alle_globalen_namen_aufloesbar(self):
        fehlend = []
        for datei in sorted(PAKET.glob("*.py")):
            modul = importlib.import_module(f"haben_ebay.{datei.stem}")
            tabelle = symtable.symtable(datei.read_text(encoding="utf-8"), str(datei), "exec")
            for funktion, name in globale_namen(tabelle):
                if not hasattr(modul, name) and not hasattr(builtins, name):
                    fehlend.append(f"{datei.name}: {funktion} → {name}")
        self.assertEqual(fehlend, [])


if __name__ == "__main__":
    unittest.main()
