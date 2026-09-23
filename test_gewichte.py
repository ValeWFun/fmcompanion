"""Regressionsskript fuer das Gewichtungspaket D13 (Rauschgroessen raus,
Torwart-NüE neu kalibriert und geschrumpft). Rein synthetisch, nur moneyball
und tactics – ohne FM24, ohne DB, ohne Savegame-Dateien.

Sichert ab:
1. Gewichte summieren sich je Slot (tactics.FORMATION) und je Profil
   (moneyball.PROFILES) auf 1.
2. Die Soll-Gewichte der Slots tw, ivl/ivr, lv/rv, dml/dmr, st und der Profile
   tw, iv, st (Wert und Invertierung) – ein Vertipper faellt hier auf.
3. Entfernt bleibt entfernt: duel_pct, finishing, shot_acc, gp_shot, save_pct
   in den Slots; fin_adj, duel_pct, gp_shot, save_pct in den Profilen; err_adj
   in tw und iv; K_SOT und FIN_SHRINK_MIN gibt es nicht mehr; jede Kennzahl der
   ARCHETYPEN steht in den Gewichten ihres Slots.
4. Torwart-NüE ueber moneyball.enrich: Gerade GK_NOTE_A/B/C, Schrumpfung
   m/(m + NUE_SHRINK_MIN), keine doppelte Schrumpfung in Profil und Slot, None
   fuer Feldspieler und fuer Torhueter ohne Export-Herkunft.

Ausfuehren wie test_meldeliste.py, kein pytest (Arbeitsordner = Projektordner):
    .venv\\Scripts\\python.exe test_gewichte.py
"""
import math
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fmcompanion import moneyball, tactics

_gesamt = 0
_bestanden = 0


def pruefe(titel, ist, soll, abs_tol=None):
    """Vergleicht ist mit soll (Floats per isclose, None per 'is None')."""
    global _gesamt, _bestanden
    _gesamt += 1
    if soll is None:
        ok = ist is None
    elif isinstance(soll, float):
        ok = isinstance(ist, (int, float)) and math.isclose(
            ist, soll, abs_tol=abs_tol if abs_tol is not None else 0.0)
    else:
        ok = ist == soll
    if ok:
        _bestanden += 1
        print(f"OK      {titel}")
    else:
        print(f"FEHLER: {titel}: ist {ist!r} soll {soll!r}")


def gleich(ist, soll):
    """Dicts mit Float-Werten vergleichen (gleiche Schluessel, isclose je Wert)."""
    return (set(ist) == set(soll)
            and all(math.isclose(ist[k], soll[k], abs_tol=1e-9) for k in soll))


SLOTS = {s["key"]: s for s in tactics.FORMATION}


def profil(name):
    """Profil -> {Kennzahl: (Gewicht, invertiert)}."""
    return {m: (w, inv) for m, _, w, inv in moneyball.PROFILES[name]}


def gleich_profil(ist, soll):
    return (set(ist) == set(soll)
            and all(math.isclose(ist[k][0], soll[k][0], abs_tol=1e-9)
                    and ist[k][1] == soll[k][1] for k in soll))


def fall_1():
    print("== 1. Summen ergeben 1 ==")
    for key, s in SLOTS.items():
        pruefe(f"Slot {key}", math.isclose(sum(s["gewichte"].values()), 1.0), True)
    for name, mets in moneyball.PROFILES.items():
        pruefe(f"Profil {name}", math.isclose(sum(w for _, _, w, _ in mets), 1.0), True)


def fall_2():
    print("== 2. Soll-Gewichte ==")
    tw = {"pass_pct": .30, "note_resid": .40, "rating": .30}
    iv = {"pass_pct": .20, "prog_p90": .20, "header_pct": .15, "int_p90": .10,
          "duels_p90": .15, "rec_p90": .05, "loss_rate": .15}
    av = {"xa_p90": .20, "keyp_p90": .15, "dribbles_p90": .15, "prog_p90": .15,
          "rec_p90": .10, "duels_p90": .15, "loss_rate": .10}
    dm = {"rec_p90": .20, "pass_pct": .15, "loss_rate": .20, "prog_p90": .10,
          "int_p90": .10, "press_p90": .10, "duels_p90": .15}
    st = {"xg_p90": .45, "goals_p90": .25, "xa_p90": .10, "press_p90": .10,
          "duels_p90": .10}
    for key, soll in [("tw", tw), ("ivl", iv), ("ivr", iv), ("lv", av), ("rv", av),
                      ("dml", dm), ("dmr", dm), ("st", st)]:
        pruefe(f"Slot {key}", gleich(SLOTS[key]["gewichte"], soll), True)

    pruefe("Profil tw", gleich_profil(profil("tw"), {
        "rating": (.45, False), "note_resid": (.40, False), "conceded_adj": (.15, True)}), True)
    pruefe("Profil iv", gleich_profil(profil("iv"), {
        "duelwon_adj": (.25, False), "headwon_adj": (.10, False), "header_pct": (.10, False),
        "intercept_adj": (.10, False), "clear_adj": (.10, False), "rec_adj": (.05, False),
        "prog_adj": (.05, False), "rating": (.20, False), "loss_p90": (.05, True)}), True)
    pruefe("Profil st", gleich_profil(profil("st"), {
        "xg_adj": (.35, False), "goals_adj": (.25, False), "shot_acc": (.10, False),
        "xa_adj": (.10, False), "rating": (.10, False), "duelwon_adj": (.05, False),
        "long_share": (.05, True)}), True)


def fall_3():
    print("== 3. Entfernte Kennzahlen ==")
    weg_slot = {"duel_pct", "finishing", "shot_acc", "gp_shot", "save_pct"}
    for key, s in SLOTS.items():
        pruefe(f"Slot {key} ohne entfernte Kennzahlen", sorted(weg_slot & set(s["gewichte"])), [])
    weg_profil = {"fin_adj", "duel_pct", "gp_shot", "save_pct"}
    for name in moneyball.PROFILES:
        pruefe(f"Profil {name} ohne entfernte Kennzahlen",
               sorted(weg_profil & set(profil(name))), [])
    pruefe("Profil tw ohne err_adj", "err_adj" in profil("tw"), False)
    pruefe("Profil iv ohne err_adj", "err_adj" in profil("iv"), False)
    print(f"   (Info) Profil av: err_adj {profil('av').get('err_adj')}")
    pruefe("moneyball.K_SOT gibt es nicht mehr", hasattr(moneyball, "K_SOT"), False)
    pruefe("moneyball.FIN_SHRINK_MIN gibt es nicht mehr", hasattr(moneyball, "FIN_SHRINK_MIN"), False)
    for key, archetypen in tactics.ARCHETYPEN.items():
        fehlt = sorted({k for stats in archetypen.values() for k in stats}
                       - set(SLOTS[key]["gewichte"]))
        pruefe(f"Archetypen von {key} stehen in den Gewichten", fehlt, [])


def torwart(**extra):
    p = {"id": 1, "name": "Testkeeper", "is_gk": True, "stat_quelle": "export",
         "minutes": 1700, "rating": 7.0, "league": None, "conceded": 20,
         "saves_tipped": 20, "saves_parried": 30, "saves_held": 10, "pos_mask": 1}
    p.update(extra)
    return p


def fall_4():
    print("== 4. Torwart-NüE ueber moneyball.enrich ==")
    m = 1700
    p = moneyball.enrich([torwart()])[0]
    gt90 = 20 * 90 / m
    sot90 = 80 * 90 / m
    # BEWUSST gerundet: die NüE rechnet mit denselben auf zwei Stellen gerundeten
    # Werten, die angezeigt werden (conceded_p90 und rating_adj, moneyball._p90).
    # So rechnet auch die Abnahme des Datenanalysten – Rang und Punkt stimmen.
    # Mit ungerundetem GT/90 kaemen bis ~0,003 Abweichung, und Raenge an
    # Rundungsgrenzen koennten um einen Punkt kippen. Deshalb: dieselbe Rechnung
    # mit dem Wert aus enrich auf den Tausendstel, die exakte Gerade nur auf 0,005.
    pruefe("Gegentore/90 ist auf zwei Stellen gerundet", p["conceded_p90"], round(gt90, 2))
    pruefe("Schuesse aufs Tor gegen ihn (Paraden 60 + Gegentore 20)", p["sot_faced"], 80)
    erw = moneyball.GK_NOTE_A - moneyball.GK_NOTE_B * gt90 + moneyball.GK_NOTE_C * sot90
    soll = round(7.0 - erw, 3)
    erw_code = (moneyball.GK_NOTE_A - moneyball.GK_NOTE_B * p["conceded_p90"]
                + moneyball.GK_NOTE_C * sot90)
    pruefe("note_resid mit dem gerundeten GT/90 aus enrich",
           p["note_resid"], round(7.0 - erw_code, 3), abs_tol=0.0005)
    pruefe("note_resid gegen die exakte Gerade (Toleranz 0,005)",
           p["note_resid"], soll, abs_tol=0.005)
    schr = round(p["note_resid"] * m / (m + moneyball.NUE_SHRINK_MIN), 3)
    pruefe("note_resid_s = note_resid · m/(m + NUE_SHRINK_MIN)", p["note_resid_s"], schr, abs_tol=0.001)
    pruefe("bei m = NUE_SHRINK_MIN genau die Haelfte",
           p["note_resid_s"], round(p["note_resid"] / 2, 3), abs_tol=0.001)
    pruefe("Profil rechnet mit dem geschrumpften Wert (nicht doppelt)",
           moneyball._score_metrics(p, 1.0)["note_resid"], p["note_resid_s"], abs_tol=0.001)
    pruefe("Slot rechnet mit dem geschrumpften Wert (tactics._wert)",
           tactics._wert(p, "note_resid"), p["note_resid_s"], abs_tol=0.001)

    print("-- mehr Minuten, weniger Schrumpfung")
    p2 = moneyball.enrich([torwart(minutes=3400, conceded=40, saves_tipped=40,
                                   saves_parried=60, saves_held=20)])[0]
    pruefe("bei 3400 Minuten zwei Drittel (3400/5100)",
           p2["note_resid_s"], round(p2["note_resid"] * 3400 / 5100, 3), abs_tol=0.001)

    print("-- keine Aussage")
    feld = moneyball.enrich([{"id": 2, "name": "Feldspieler", "is_gk": False,
                              "stat_quelle": "export", "minutes": 1700, "rating": 7.0,
                              "league": None, "conceded": 20, "pos_mask": 1 << 8,
                              "goals": 0, "xg": 0.0}])[0]
    pruefe("Feldspieler: note_resid None", feld["note_resid"], None)
    pruefe("Feldspieler: note_resid_s None", feld["note_resid_s"], None)
    ram = moneyball.enrich([{"id": 3, "name": "RAM-Keeper", "is_gk": True,
                             "stat_quelle": "ram", "minutes": 1700, "rating": 7.0,
                             "league": None, "conceded": 20, "pos_mask": 1}])[0]
    pruefe("Torwart ohne Export-Herkunft: note_resid None", ram["note_resid"], None)
    pruefe("Torwart ohne Export-Herkunft: note_resid_s None", ram["note_resid_s"], None)
    kurz = moneyball.enrich([torwart(minutes=60)])[0]
    pruefe("unter 90 Minuten: note_resid None", kurz["note_resid"], None)
    ohne_gt = moneyball.enrich([torwart(conceded=0)])[0]
    pruefe("0 Gegentore: note_resid None (Bereichsgrenze 0 < GT/90)", ohne_gt["note_resid"], None)


def main():
    fall_1()
    fall_2()
    fall_3()
    fall_4()
    print()
    print(f"{_bestanden} von {_gesamt} Prüfungen bestanden")
    if _bestanden != _gesamt:
        sys.exit(1)


if __name__ == "__main__":
    main()
