"""Regressionsskript fuer tactics.fuss_passung (D12: Fuss passt zur Rolle).

Rein synthetisch, nur tactics – ohne FM24, ohne DB, ohne Savegame-Dateien.
Sichert ab: beim Inversen Aussenstuermer (aml/amr) gehoert der starke Fuss nach
innen (links Rechtsfuss, rechts Linksfuss); bei den Innenverteidigern (ivl/ivr)
ist die Seite nur ein Hinweis und nie "gegen die Rolle"; Slots ohne Regel und
unbekannter Fuss liefern None. Der Fuss geht NICHT in Fit oder Score ein.

Ausfuehren wie test_meldeliste.py, kein pytest (Arbeitsordner = Projektordner):
    .venv\\Scripts\\python.exe test_fuss.py
"""
import math
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fmcompanion import tactics

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


def urteil(foot, slot):
    r = tactics.fuss_passung(foot, slot)
    return None if r is None else (r["passung"], r["deutlich"])


FUESSE = ["Rechts", "Links", "Nur Rechts", "Nur Links", "Beide"]
P, G, N = "passt", "gegen die Rolle", "neutral"

# Erwartung je Fuss und Slot: (passung, deutlich)
SOLL = {
    "aml": {"Rechts": (P, False), "Links": (G, False), "Nur Rechts": (P, False),
            "Nur Links": (G, True), "Beide": (P, False)},
    "amr": {"Rechts": (G, False), "Links": (P, False), "Nur Rechts": (G, True),
            "Nur Links": (P, False), "Beide": (P, False)},
    "ivl": {"Rechts": (N, False), "Links": (P, False), "Nur Rechts": (N, False),
            "Nur Links": (P, False), "Beide": (P, False)},
    "ivr": {"Rechts": (P, False), "Links": (N, False), "Nur Rechts": (P, False),
            "Nur Links": (N, False), "Beide": (P, False)},
}


def main():
    print("== Alle fuenf Fuesse x vier Regel-Slots ==")
    for slot, tabelle in SOLL.items():
        for foot in FUESSE:
            pruefe(f"{foot!r} auf {slot}", urteil(foot, slot), tabelle[foot])

    print("== Pflichtfaelle ==")
    pruefe("Links auf aml: gegen die Rolle", urteil("Links", "aml"), (G, False))
    pruefe("Links auf amr: passt", urteil("Links", "amr"), (P, False))
    pruefe("Rechts auf aml: passt", urteil("Rechts", "aml"), (P, False))
    pruefe("Rechts auf amr: gegen die Rolle", urteil("Rechts", "amr"), (G, False))
    pruefe("Nur Links auf aml: gegen die Rolle, deutlich", urteil("Nur Links", "aml"), (G, True))
    pruefe("Rechts auf ivl: neutral", urteil("Rechts", "ivl"), (N, False))
    pruefe("Beide passt ueberall",
           [urteil("Beide", s)[0] for s in SOLL], [P] * 4)
    pruefe("Hinweis-Slots kennen nie 'gegen die Rolle'",
           [s for s in ("ivl", "ivr") for f in FUESSE if urteil(f, s)[0] == G], [])

    print("== Texte ==")
    pruefe("passt", tactics.fuss_passung("Rechts", "aml")["text"], "Fuß passt zur Rolle")
    pruefe("gegen die Rolle", tactics.fuss_passung("Links", "aml")["text"], "Fuß gegen die Rolle")
    pruefe("gegen die Rolle, deutlich",
           tactics.fuss_passung("Nur Links", "aml")["text"], "Fuß gegen die Rolle (nur Links)")
    pruefe("gegen die Rolle, deutlich (rechts)",
           tactics.fuss_passung("Nur Rechts", "amr")["text"], "Fuß gegen die Rolle (nur Rechts)")
    pruefe("neutral", tactics.fuss_passung("Rechts", "ivl")["text"], "Fuß neutral")

    print("== Keine Aussage ==")
    for slot in ("st", "tw", "lv", "rv", "dml", "dmr", "amc", "ohne_slot"):
        pruefe(f"Slot ohne Regel ({slot})", tactics.fuss_passung("Links", slot), None)
    for foot in (None, "", "  "):
        pruefe(f"foot {foot!r} auf aml", tactics.fuss_passung(foot, "aml"), None)

    print()
    print(f"{_bestanden} von {_gesamt} Prüfungen bestanden")
    if _bestanden != _gesamt:
        sys.exit(1)


if __name__ == "__main__":
    main()
