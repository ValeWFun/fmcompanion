"""Regressionsskript fuer Importer und Ranglisten (laeuft ohne FM24 und ohne DB).

Sichert die Fallen aus CLAUDE.md ab:
- Zahlenformat der HTML-Exporte (`_dezimal`, `_num`, `_eins`, `parse_money`):
  deutsche und englische Zahlen muessen beide gelesen werden, Marktwerte
  mit Nachkommastelle duerfen nicht um den Faktor 10 danebenliegen.
- Ranglisten (`moneyball.rank`): Spieler ohne Wert fallen raus, kein
  TypeError ueber None.
- Schraegstrich-Positionen (`moneyball.pos_mask_from_string`): "V/FV (R)" usw.

Ausfuehren wie test_pipeline.py, kein pytest:
    .venv\\Scripts\\python.exe test_importer.py

Importiert nur importer und moneyball - kein db, kein scanner, kein Datei-I/O.
"""
import math
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fmcompanion import importer, moneyball

_gesamt = 0
_bestanden = 0


def pruefe(titel, ist, soll):
    """Vergleicht ist mit soll (Floats per isclose, None per 'is None')."""
    global _gesamt, _bestanden
    _gesamt += 1
    if soll is None:
        ok = ist is None
    elif isinstance(soll, float):
        ok = isinstance(ist, (int, float)) and math.isclose(ist, soll)
    else:
        ok = ist == soll
    if ok:
        _bestanden += 1
        print(f"OK      {titel}")
    else:
        print(f"FEHLER: {titel}: ist {ist!r} soll {soll!r}")


print("== importer._dezimal ==")
for s, soll in [("2,198", 2198.0), ("2.198", 2198.0), ("6.54", 6.54),
                ("6,54", 6.54), ("1,234,567", 1234567.0), ("-0.35", -0.35)]:
    pruefe(f"_dezimal({s!r})", importer._dezimal(s), soll)

print("== importer._num ==")
for s, soll in [("", None), ("-", None), ("N/A", None), ("<b>6.54</b>", 6.54)]:
    pruefe(f"_num({s!r})", importer._num(s), soll)
wert = importer._num("2,198", int)
pruefe('_num("2,198", int)', wert, 2198)
pruefe('_num("2,198", int) Typ ist int', type(wert) is int, True)

print("== importer._eins ==")
for s, soll in [("30 (2)", 32), ("14", 14), ("-", None)]:
    pruefe(f"_eins({s!r})", importer._eins(s), soll)

print("== importer.parse_money ==")
for s, soll in [("€17.5Mio", 17500000), ("€17,5Mio", 17500000),
                ("€157Mio - €189Mio", 173000000), ("€115.000/W.", 115000),
                ("€115,000/W.", 115000), ("€850Tsd", 850000),
                ("€1.2Mrd", 1200000000), ("€900K", 900000), ("-", None)]:
    pruefe(f"parse_money({s!r})", importer.parse_money(s), soll)

print("== moneyball.pos_mask_from_string ==")
for s, soll in [("TW", 1), ("V (Z)", 16), ("V (R)", 4), ("V/FV (R)", 4),
                ("M/OM (R)", 4352), ("M/OM (Z)", 5120), ("V/FV/M (L)", 260),
                ("M (Z), OM (RLZ), ST (Z)", 21504), ("DM", 128),
                ("", 0), (None, 0)]:
    pruefe(f"pos_mask_from_string({s!r})", moneyball.pos_mask_from_string(s), soll)

print("== moneyball.rank ==")
spieler = [
    {"name": "A", "minutes": 900, "value_score": 3.0},
    {"name": "B", "minutes": 900, "value_score": None},
    {"name": "C", "minutes": 900, "value_score": 5.0},
    {"name": "D", "minutes": 30, "value_score": 9.0},
]
try:
    namen = [p["name"] for p in moneyball.rank(spieler, "value_score")]
except TypeError as e:
    namen = f"TypeError: {e}"
pruefe("rank absteigend (None und <45 Min fliegen raus)", namen, ["C", "A"])
try:
    namen = [p["name"] for p in moneyball.rank(spieler, "value_score", descending=False)]
except TypeError as e:
    namen = f"TypeError: {e}"
pruefe("rank aufsteigend", namen, ["A", "C"])

print()
print(f"{_bestanden} von {_gesamt} Prüfungen bestanden")
if _bestanden != _gesamt:
    sys.exit(1)
