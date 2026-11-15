"""Tests nur mit der Standardbibliothek: python -m unittest. Der Datenordner ist ein frischer temporärer Ordner."""
import os
import tempfile

# Vor dem ersten Import des Pakets setzen: die Dateipfade werden beim Import festgelegt
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="haben-ebay-test-")
for name in ("HABEN_ADRESSE", "SEITE_ADRESSE", "EBAY_HOOK_URL", "HABEN_URL", "EBAY_MARKTPLATZ"):
    os.environ.pop(name, None)
