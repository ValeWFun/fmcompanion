"""Regressionsskript fuer Bezugsdatum (D6) und Meldelisten (D4) – reine Funktionen,
ohne FM24, ohne DB und ohne Savegame-Dateien.

Sichert ab:
1. moneyball.bezugsdatum: Spieljahr und Exporttag aus RAM-Geburtsdaten und
   Export-Altern, so dass compute_age das Export-Alter reproduziert.
2. tactics.saisonstart: Startjahr der Saison, fuer die gemeldet wird.
3. tactics.pl_status: PL-Meldestatus (U21 / heimisch / braucht Platz / unbekannt),
   auch aus dem Export-Alter mit Grenzfall.
4. tactics.meldeliste: PL-Zaehler, Kadergroesse, CL-Richtwert.

Ausfuehren wie test_importer.py, kein pytest (Arbeitsordner = Projektordner):
    .venv\\Scripts\\python.exe test_meldeliste.py

Importiert nur moneyball und tactics – kein db, kein app.py, kein Datei-I/O.
"""
import math
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from fmcompanion import moneyball, tactics

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


def paare_fuer(rng, jahr, tag, n=200):
    """n Spieler mit Geburtsjahr/-tag und dem Export-Alter zum Exporttag.

    Alter = Spieljahr - Geburtsjahr, minus 1, wenn der Geburtstag (Tag im Jahr)
    nach dem Exporttag liegt.
    """
    paare = []
    for _ in range(n):
        by = int(rng.integers(1990, 2011))
        bd = int(rng.integers(1, 366))
        paare.append((by, bd, jahr - by - (1 if bd > tag else 0)))
    return paare


def falsche_alter(paare, jahr, tag):
    """Spieler, deren compute_age nicht dem Export-Alter entspricht."""
    return [(by, bd, a) for by, bd, a in paare
            if moneyball.compute_age(by, bd, jahr, tag) != a]


def fall_1():
    print("== 1. moneyball.bezugsdatum ==")
    rng = np.random.default_rng(11)

    print("-- Sommerfall: Spieljahr 2028, Exporttag 134")
    paare = paare_fuer(rng, 2028, 134)
    jahr, tag = moneyball.bezugsdatum(paare)
    pruefe("Spieljahr", jahr, 2028)
    pruefe("Tag gefunden", tag is not None, True)
    pruefe("compute_age == Export-Alter fuer ALLE 200 Spieler",
           len(falsche_alter(paare, jahr, tag)), 0)

    print("-- Randfaelle")
    pruefe("weniger als 5 Paare", moneyball.bezugsdatum(paare[:4]), (None, None))
    pruefe("leere Liste", moneyball.bezugsdatum([]), (None, None))
    ohne_tag = [(by, None, 2028 - by) for by, _, _ in paare]
    pruefe("Paare ohne birth_day", moneyball.bezugsdatum(ohne_tag), (2028, None))

    print("-- Winterfall: Spieljahr 2029, Exporttag 20")
    winter = paare_fuer(rng, 2029, 20)
    hatten = sum(1 for _, bd, _ in winter if bd <= 20)
    print(f"   (Info) {hatten} von 200 Spielern hatten am Exporttag schon Geburtstag")
    jahr, tag = moneyball.bezugsdatum(winter)
    pruefe("Spieljahr = Jahr des Exports (2029)", jahr, 2029)
    # Der Tag muss beide Gruppen trennen: mindestens der Exporttag und unter dem
    # fruehesten Geburtstag der Spieler, die noch keinen hatten.
    kleinster_ohne = min(bd for _, bd, _ in winter if bd > 20)
    pruefe("Tag trennt (20 <= Tag < kleinster Geburtstag ohne Geburtstag)",
           tag is not None and 20 <= tag < kleinster_ohne, True)
    pruefe("compute_age == Export-Alter fuer ALLE 200 Spieler",
           len(falsche_alter(winter, jahr, tag)), 0)

    print("-- Randfall: Export am 1. Januar (Spieljahr 2029, Tag 1), niemand hatte Geburtstag")
    neujahr = [(by, bd, 2029 - by - 1) for by, bd, _ in paare_fuer(rng, 2029, 1)
               if bd > 1]
    jahr, tag = moneyball.bezugsdatum(neujahr)
    pruefe("Spieljahr = Vorjahr (2028)", jahr, 2028)
    pruefe("compute_age == Export-Alter fuer ALLE Spieler",
           len(falsche_alter(neujahr, jahr, tag)), 0)


def fall_2():
    print("== 2. tactics.saisonstart ==")
    for args, soll in [((2028, 134), 2028), ((2029, 20), 2028), ((2028, 364), 2028),
                       ((2030, None), 2030), ((None, 5), None)]:
        pruefe(f"saisonstart{args}", tactics.saisonstart(*args), soll)


HG_LAND = "Ausgebildet im Land (15–21)"
HG_VEREIN = "Ausgebildet im Verein (0–21)"


def fall_3():
    print("== 3. tactics.pl_status (Saisonstart 2028: U21 ab Jahrgang 2007) ==")
    start = 2028
    pruefe("Jahrgang 2007 -> u21",
           tactics.pl_status({"birth_year": 2007}, start), ("u21", False))
    pruefe("Jahrgang 2006, homegrown, Stand gesetzt -> heimisch",
           tactics.pl_status({"birth_year": 2006, "homegrown": HG_LAND,
                              "homegrown_stand": "2026-09-23"}, start), ("heimisch", False))
    pruefe("Jahrgang 2006, homegrown None -> braucht_platz",
           tactics.pl_status({"birth_year": 2006, "homegrown": None,
                              "homegrown_stand": "2026-09-23"}, start), ("braucht_platz", False))
    pruefe("Stand 2026-09-01 vor seit 2026-09-15 -> unbekannt",
           tactics.pl_status({"birth_year": 2006, "homegrown": HG_LAND,
                              "homegrown_stand": "2026-09-01"}, start, "2026-09-15"),
           ("unbekannt", False))
    pruefe("Stand am seit-Tag zaehlt noch",
           tactics.pl_status({"birth_year": 2006, "homegrown": HG_LAND,
                              "homegrown_stand": "2026-09-15"}, start, "2026-09-15"),
           ("heimisch", False))
    pruefe("ohne homegrown_stand -> unbekannt",
           tactics.pl_status({"birth_year": 2006, "homegrown": HG_LAND}, start),
           ("unbekannt", False))

    print("-- Export-Alter statt Geburtsjahr (ref_year 2028)")
    stand = "2026-09-23"
    pruefe("Alter 21, homegrown None -> braucht_platz, Grenzfall",
           tactics.pl_status({"age": 21, "homegrown": None, "homegrown_stand": stand},
                             start, None, 2028), ("braucht_platz", True))
    pruefe("Alter 19 -> u21",
           tactics.pl_status({"age": 19}, start, None, 2028), ("u21", False))
    pruefe("Alter 25 -> braucht_platz, kein Grenzfall",
           tactics.pl_status({"age": 25, "homegrown": None, "homegrown_stand": stand},
                             start, None, 2028), ("braucht_platz", False))
    pruefe("Alter 25 ohne Stand -> unbekannt, kein Grenzfall",
           tactics.pl_status({"age": 25}, start, None, 2028), ("unbekannt", False))
    print("-- ohne Saisonstart oder ohne Geburtsdaten")
    pruefe("kein Geburtsjahr, kein Alter -> Grenzfall",
           tactics.pl_status({"homegrown": None, "homegrown_stand": stand}, start, None, 2028),
           ("braucht_platz", True))


def kader(nicht=16, heim_land=2, heim_verein=3, u21=2):
    """Synthetischer Kader: Jahrgang 2000 = ueber 21, 2008 = U21 (Saisonstart 2028)."""
    spieler = []
    stand = "2026-09-23"

    def neu(name, by, hg):
        spieler.append({"id": len(spieler) + 1, "eid": len(spieler) + 1, "name": name,
                        "birth_year": by, "homegrown": hg, "homegrown_stand": stand})
    for i in range(nicht):
        neu(f"Nicht {i}", 2000, None)
    for i in range(heim_land):
        neu(f"Land {i}", 2000, HG_LAND)
    for i in range(heim_verein):
        neu(f"Verein {i}", 2000, HG_VEREIN)
    for i in range(u21):
        neu(f"Jung {i}", 2008, None)
    return spieler


def fall_4():
    print("== 4. tactics.meldeliste (23 Spieler: 16 braucht_platz, 5 heimisch, 2 U21) ==")
    m = tactics.meldeliste(kader(), 2028, None, 2028)
    pl, cl = m["pl"], m["cl"]
    pruefe("Kader hat 23 Spieler", len(m["spieler"]), 23)
    pruefe("pl.nicht_heimisch", pl["nicht_heimisch"], 16)
    pruefe("pl.heimisch", pl["heimisch"], 5)
    pruefe("pl.u21", pl["u21"], 2)
    pruefe("pl.unbekannt", pl["unbekannt"], 0)
    pruefe("pl.ueber_21", pl["ueber_21"], 21)
    pruefe("pl.kader_max (25 - (8 - 5))", pl["kader_max"], 22)
    pruefe("pl.ok", pl["ok"], True)
    pruefe("pl.text", pl["text"], "PL: 16/17 Nicht-Heimische über 21, 5 Heimische")
    pruefe("cl.land_15", cl["land_15"], 2)
    pruefe("cl.verein_15", cl["verein_15"], 0)
    pruefe("cl.pruefen_0_21", cl["pruefen_0_21"], 3)
    pruefe("cl.liste_a_max (17 + 2)", cl["liste_a_max"], 19)
    pruefe("saisonstart und u21_ab_jahrgang",
           (m["saisonstart"], m["u21_ab_jahrgang"]), (2028, 2007))
    pruefe("keine Grenzfaelle bei Geburtsjahren", m["grenzfaelle"], [])

    print("-- Grenze der Nicht-Heimischen")
    m17 = tactics.meldeliste(kader(nicht=17), 2028, None, 2028)["pl"]
    pruefe("17 Nicht-Heimische, 22 ueber 21: ok (Grenze erreicht, nicht ueberschritten)",
           (m17["nicht_heimisch"], m17["ueber_21"], m17["ok"]), (17, 22, True))
    m18 = tactics.meldeliste(kader(nicht=18), 2028, None, 2028)["pl"]
    pruefe("18 Nicht-Heimische: ok = False", (m18["nicht_heimisch"], m18["ok"]), (18, False))

    print("-- Kadergroesse waechst mit den Heimischen")
    m9 = tactics.meldeliste(kader(nicht=16, heim_land=4, heim_verein=5), 2028, None, 2028)["pl"]
    pruefe("9 Heimische: heimisch = 9, kader_max = 25",
           (m9["heimisch"], m9["kader_max"]), (9, 25))
    m8 = tactics.meldeliste(kader(nicht=16, heim_land=4, heim_verein=4), 2028, None, 2028)["pl"]
    pruefe("8 Heimische: kader_max = 25 (Mindestzahl erreicht)", m8["kader_max"], 25)
    m0 = tactics.meldeliste(kader(nicht=10, heim_land=0, heim_verein=0), 2028, None, 2028)["pl"]
    pruefe("0 Heimische: kader_max = 17", m0["kader_max"], 17)

    print("-- Unbekannter Status zaehlt als ueber 21")
    k = kader()
    k[0]["homegrown_stand"] = None
    mu = tactics.meldeliste(k, 2028, None, 2028)["pl"]
    pruefe("ein Spieler ohne Stand: unbekannt = 1, nicht_heimisch = 15",
           (mu["unbekannt"], mu["nicht_heimisch"], mu["ueber_21"]), (1, 15, 21))


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
