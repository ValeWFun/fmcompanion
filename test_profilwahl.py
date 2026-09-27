"""Regressionsskript fuer die Profilwahl (D19) – ohne FM24, ohne echte DB.

Bis D19 bekam jeder Spieler EIN Score-Profil, das seiner defensivsten
Positionsgruppe; ein 'OM (RL), ST (Z)' wurde auch im Sturm als Fluegel
bewertet. Jetzt gibt es einen Score je erlaubtem Profil, und jeder Slot zaehlt
den Score SEINES Profils. Geprueft wird:
- erlaubte Profile und Slot-Profile ('OM (RL), ST (Z)' im ST-Slot st, in AML
  off; 'M (L), OM (RLZ), ST (Z)' in AML off)
- Invariante: Spieler, deren Positionen nur das Hauptprofil erlauben, bleiben
  BITGLEICH zum Algorithmus vor D19 (hier als Referenz nachgebaut)
- Brett, Auto-Elf und Ligavergleich rechnen mit dem Slot-Score
- die Schalter REFERENZ_WAHL und LISTEN_WAHL, das Zusatzprofil fuer
  positionsfremde Kandidaten, der Rechen-Cache-Fingerabdruck, die js_api-Felder

Ausfuehren wie die anderen Skripte, kein pytest:
    .venv\\Scripts\\python.exe test_profilwahl.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from fmcompanion import db, moneyball, positionen, tactics
import app

_gesamt = 0
_bestanden = 0


def pruefe(titel, ok, info=""):
    global _gesamt, _bestanden
    _gesamt += 1
    if ok:
        _bestanden += 1
        print(f"OK      {titel}")
    else:
        print(f"FEHLER: {titel}" + (f"  [{info}]" if info else ""))


rng = np.random.default_rng(19)
POSITIONEN = ["TW", "V (Z)", "V (L)", "V (R)", "V (RZ)", "V/FV (L)", "DM, M (Z)", "M (Z)",
              "M (L)", "M (R)", "OM (L)", "OM (R)", "OM (Z)", "OM (RL), ST (Z)",
              "M (L), OM (RLZ), ST (Z)", "M/OM (L)", "ST (Z)", "DM, M (Z), OM (RZ)"]


def zeile(eid, pos, club="Verein A", liga="Premier League"):
    m = int(rng.integers(900, 3400))
    tw = pos == "TW"
    r = lambda a, b: float(rng.uniform(a, b))
    dt, pt, ht, prt, st, ct = (int(r(40, 400)), int(r(300, 2500)), int(r(20, 250)),
                               int(r(30, 300)), int(r(0, 80)), int(r(0, 120)))
    conc = int(r(15, 60)) if tw else 0
    return {"eid": eid, "name": f"Spieler {eid}", "position": pos, "age": int(rng.integers(18, 33)),
            "club": club, "league": liga, "value": float(int(r(5, 80)) * 1e6),
            "goals": 0 if tw else int(r(0, 20)), "assists": 0 if tw else int(r(0, 12)),
            "xg": 0.0 if tw else r(0, 15), "xa": 0.0 if tw else r(0, 10),
            "minutes": m, "rating": round(r(6.5, 7.4), 2), "apps": m // 85,
            "duels": int(dt * r(0.4, 0.7)), "duels_total": dt,
            "shots_total": st, "shots_on": int(st * r(0.2, 0.6)),
            "pass_try": pt, "pass_ok": int(pt * r(0.7, 0.92)),
            "dribbles": int(r(0, 80)), "prog_passes": int(r(10, 200)),
            "press_win": int(prt * r(0.2, 0.5)), "press_try": prt,
            "interceptions": int(r(5, 80)), "key_passes": int(r(0, 60)),
            "clearances": int(r(0, 120)), "headers_won": int(ht * r(0.3, 0.7)),
            "headers_total": ht, "losses": int(r(50, 400)), "recoveries": int(r(50, 300)),
            "pen_goals": 0, "conceded": conc, "xga": (conc + r(-5, 5)) if tw else None,
            "chances": int(r(0, 40)), "long_goals": 0, "blocks": int(r(0, 30)),
            "errors": int(r(0, 3)), "crosses_ok": int(ct * r(0.2, 0.4)), "crosses_try": ct,
            "sprints": int(r(50, 400)), "saves_tipped": 20 if tw else None,
            "saves_parried": 20 if tw else None, "saves_held": 20 if tw else None,
            "personality": None, "foot": "Rechts", "height": 183}


def als_spieler(rows):
    return moneyball.enrich([app.Api._export_row_to_player(e) for e in rows])


# ------------------------------------------------ Referenz: Algorithmus vor D19
def alt_add_scores(players, reference, leagues):
    """add_scores, wie es bis D19 rechnete: ein Profil je Spieler (_profile)."""
    def lg(p):
        eid = p.get("eid")
        return leagues.get(int(eid)) if eid else None
    summe = {}
    for r in reference:
        prof = moneyball._profile(r)
        if not prof:
            continue
        for key, (w, t) in moneyball.QUOTEN.items():
            tot = r.get(t) or 0
            if not tot or r.get(w) is None:
                continue
            s = summe.setdefault((prof, key), [0.0, 0.0])
            s[0] += float(r.get(w) or 0)
            s[1] += float(tot)
    priors = {k: w / t for k, (w, t) in summe.items() if t}
    dists, ref_mets = {}, []
    for r in reference:
        prof = moneyball._profile(r)
        if not prof:
            continue
        mets = moneyball._score_metrics(r, moneyball.league_coeff(lg(r)), priors)
        ref_mets.append((r, prof, mets))
        for key, _, _, _ in moneyball.PROFILES[prof]:
            if mets[key] is not None:
                dists.setdefault((prof, key), []).append(mets[key])
    for v in dists.values():
        v.sort()
    age_dists = {}
    for r, prof, mets in ref_mets:
        band = moneyball.age_band(r.get("age"))
        if not band:
            continue
        s, ws = 0.0, 0.0
        for key, _l, w, inv in moneyball.PROFILES[prof]:
            if mets[key] is None:
                continue
            pc = moneyball._pct(dists.get((prof, key), []), mets[key])
            s += w * (100.0 - pc if inv else pc)
            ws += w
        if ws >= 0.5:
            age_dists.setdefault((prof, band), []).append(s / ws)
    for v in age_dists.values():
        v.sort()
    for p in players:
        prof = moneyball._profile(p)
        coeff = moneyball.league_coeff(lg(p))
        if not prof:
            p["score"], p["score_parts"], p["profile_label"] = None, [], "unbekannt"
            continue
        if prof == "tw" and p.get("xga") is None and p.get("sot_faced") is None:
            p["score"], p["score_parts"] = None, []
            p["profile_label"] = "Torhüter – keine Detailstatistik"
            continue
        mets = moneyball._score_metrics(p, coeff, priors)
        total, wsum, parts = 0.0, 0.0, []
        for key, label, w, inv in moneyball.PROFILES[prof]:
            if mets[key] is None:
                parts.append({"label": label, "pct": None, "w": w})
                continue
            pct = moneyball._pct(dists.get((prof, key), []), mets[key])
            if inv:
                pct = 100.0 - pct
            total += w * pct
            wsum += w
            parts.append({"label": label, "pct": round(pct), "w": w})
        p["score_parts"], p["profile_label"] = parts, moneyball.PROFILE_LABEL[prof]
        if wsum < 0.5:
            p["score"] = None
            continue
        total /= wsum
        p["score"] = round(total)
        band = moneyball.age_band(p.get("age"))
        pool = age_dists.get((prof, band), []) if band else []
        p["talent"] = round(moneyball._pct(pool, total)) if len(pool) >= 8 else None
        a = p.get("age")
        p["prospect"] = p["talent"] if (a is not None and a <= moneyball.PROSPECT_AGE) else None


# ------------------------------------------------ A: erlaubte Profile, Slots
print("== A Erlaubte Profile und Slot-Profile ==")
beispiel = {pos: als_spieler([zeile(i, pos)])[0] for i, pos in enumerate(POSITIONEN, 1)}
ep = {pos: moneyball.erlaubte_profile(p) for pos, p in beispiel.items()}
pruefe("'OM (RL), ST (Z)' erlaubt off und st", ep["OM (RL), ST (Z)"] == {"off", "st"}, str(ep["OM (RL), ST (Z)"]))
pruefe("'M (L), OM (RLZ), ST (Z)' erlaubt off und st (M (L) ist Fluegel)",
       ep["M (L), OM (RLZ), ST (Z)"] == {"off", "st"}, str(ep["M (L), OM (RLZ), ST (Z)"]))
pruefe("... Hauptprofil bleibt mid (defensivste Gruppe)",
       moneyball._profile(beispiel["M (L), OM (RLZ), ST (Z)"]) == "mid")
pruefe("'M (L)' allein ist Fluegel (off), nicht zentrales Mittelfeld", ep["M (L)"] == {"off"})
pruefe("'V (RZ)' erlaubt iv und av", ep["V (RZ)"] == {"iv", "av"})
pruefe("'DM, M (Z), OM (RZ)' erlaubt mid und off", ep["DM, M (Z), OM (RZ)"] == {"mid", "off"})
pruefe("Torwart nur tw", ep["TW"] == {"tw"})
slot = {s["key"]: s for s in tactics.FORMATION}
pruefe("Slot-Profile", {k: tactics.slot_profil(s) for k, s in slot.items()} ==
       {"tw": "tw", "lv": "av", "ivl": "iv", "ivr": "iv", "rv": "av", "dml": "mid",
        "dmr": "mid", "aml": "off", "amc": "off", "amr": "off", "st": "st"})
pruefe("tactics re-exportiert die Positionsgruppen",
       tactics.player_groups is positionen.player_groups and tactics.GROUPS is positionen.GROUPS)

# ------------------------------------------------ B: Invariante bitgleich
print("== B Schalterstellung laut Datenanalyst (D19) ==")
pruefe("REFERENZ_WAHL steht auf 'gruppe' (alle, deren Positionen das Profil erlauben)",
       moneyball.REFERENZ_WAHL == "gruppe")
pruefe("LISTEN_WAHL steht auf 'bester' (Maximum der erlaubten Profile)",
       moneyball.LISTEN_WAHL == "bester")
REF_STANDARD = moneyball.REFERENZ_WAHL
om_st = beispiel["OM (RL), ST (Z)"]
pruefe("'gruppe': 'OM (RL), ST (Z)' speist die Verteilungen off UND st",
       moneyball.referenz_profile(om_st) == ("off", "st"))
pruefe("'gruppe': 'M (L)' speist off, nicht mid", moneyball.referenz_profile(beispiel["M (L)"]) == ("off",))
moneyball.REFERENZ_WAHL = "maske"
pruefe("'maske': 'M (L)' speist mid (Masken-Sicht)", moneyball.referenz_profile(beispiel["M (L)"]) == ("mid",))
moneyball.REFERENZ_WAHL = "haupt"
pruefe("'haupt': nur die erste Gruppe", moneyball.referenz_profile(om_st) == ("off",))

print("== B Nur Hauptprofil erlaubt: bitgleich zum Stand vor D19 (Referenz 'haupt') ==")
# Der Algorithmus selbst muss mit der alten Referenz exakt das Alte liefern;
# mit 'gruppe' aendern sich die Verteilungen und damit jeder Score – gewollt.
# Die Invariante gilt NUR, wenn das einzige erlaubte Profil das Hauptprofil
# ist. Fluegel vom Typ 'M (L), OM (RL)' (Hauptprofil mid, erlaubt nur off,
# in Summer3 96 von 502 Ein-Profil-Spielern) wechseln im Listen-Score GEWOLLT
# auf off – das ist keine Regression (Datenanalyst, D19-Abnahme).
rows = [zeile(1000 + i, POSITIONEN[i % len(POSITIONEN)]) for i in range(420)]
ligen = {r["eid"]: r["league"] for r in rows}
basis = als_spieler(rows)
neu, alt = copy.deepcopy(basis), copy.deepcopy(basis)
moneyball.add_scores(neu, neu, ligen)
alt_add_scores(alt, alt, ligen)
einzeln = [i for i, p in enumerate(neu)
           if moneyball.erlaubte_profile(p) == ({moneyball._profile(p)} - {None})]
felder = ("score", "score_parts", "profile_label", "talent", "prospect")
abw = [(neu[i]["position"], f, alt[i].get(f), neu[i].get(f))
       for i in einzeln for f in felder if alt[i].get(f) != neu[i].get(f)]
pruefe(f"{len(einzeln)} Einzelprofil-Spieler bitgleich (score, parts, label, talent, prospect)",
       len(einzeln) > 100 and not abw, str(abw[:3]))
mehr = [i for i in range(len(neu)) if i not in set(einzeln)]
pruefe("Mehrprofil-Spieler haben je erlaubtem Profil einen Score",
       all(set(moneyball.erlaubte_profile(neu[i])) <= set(neu[i]["score_je_profil"]) for i in mehr))
pruefe("Hauptprofil-Score unveraendert auch bei Mehrprofil-Spielern",
       all(neu[i]["score_je_profil"][moneyball._profile(neu[i])]["score"] == alt[i]["score"]
           for i in mehr if moneyball._profile(neu[i])))
pruefe("Listen-Score 'bester' = Maximum der erlaubten Profile",
       all(neu[i]["score"] == max((w["score"] for pr, w in neu[i]["score_je_profil"].items()
                                   if pr in moneyball.erlaubte_profile(neu[i]) and w["score"] is not None),
                                  default=None) for i in range(len(neu))))
pruefe("'profil' und profile_label passen zusammen",
       all(neu[i]["profile_label"] == neu[i]["score_je_profil"][neu[i]["profil"]]["label"]
           for i in range(len(neu)) if neu[i]["profil"]))

print("== B Schalter ==")
alt_listen = moneyball.LISTEN_WAHL
moneyball.LISTEN_WAHL = "haupt"
haupt = copy.deepcopy(basis)
moneyball.add_scores(haupt, haupt, ligen)
pruefe("LISTEN_WAHL 'haupt': Listen-Score ueberall wie vor D19",
       all(haupt[i].get(f) == alt[i].get(f) for i in range(len(haupt)) for f in felder))
moneyball.LISTEN_WAHL = alt_listen
for wahl in ("gruppe", "maske"):
    moneyball.REFERENZ_WAHL = wahl
    stats = moneyball.score_referenz(basis, ligen)
    n_neu = sum(len(v) for v in stats[1].values())
    moneyball.REFERENZ_WAHL = "haupt"
    n_alt = sum(len(v) for v in moneyball.score_referenz(basis, ligen)[1].values())
    pruefe(f"REFERENZ_WAHL '{wahl}': mehr Zeilen in den Verteilungen ({n_alt} -> {n_neu})", n_neu > n_alt)
moneyball.REFERENZ_WAHL = "haupt"
try:
    moneyball.REFERENZ_WAHL = "falsch"
    moneyball.score_referenz(basis[:5], ligen)
    pruefe("unbekannte REFERENZ_WAHL faellt auf", False)
except ValueError:
    pruefe("unbekannte REFERENZ_WAHL faellt auf", True)
finally:
    moneyball.REFERENZ_WAHL = "haupt"
v_z = copy.deepcopy([p for p in basis if p["position"] == "V (Z)"][:1])
moneyball.add_scores(v_z, basis, ligen, zusatz_profile=("st",))
pruefe("Zusatzprofil (Umschulung): Score als Stuermer, Listen-Score bleibt IV",
       "st" in v_z[0]["score_je_profil"] and v_z[0]["profil"] == "iv")
moneyball.REFERENZ_WAHL = REF_STANDARD           # ab hier die Stellung des Analysten

# ------------------------------------------------ C: Slot-Score auf dem Brett
print("== C Brett und Auto-Elf rechnen mit dem Slot-Score ==")
ref = copy.deepcopy(basis)
moneyball.add_scores(ref, ref, ligen)
gleich = zeile(9001, "OM (RL), ST (Z)")
x = als_spieler([gleich])[0]
y = als_spieler([dict(gleich, eid=9002, name="Reiner Stuermer", position="ST (Z)")])[0]
# Profilwertungen gesetzt statt gerechnet: X ist als Stuermer stark, als
# Fluegel schwach; Y ist reiner Stuermer mit mittlerem Score. Gleiche
# Statistik -> gleicher Fit, den Ausschlag gibt nur der Slot-Score.
x["score_je_profil"] = {"off": {"score": 40}, "st": {"score": 95}}
y["score_je_profil"] = {"st": {"score": 60}}
tafel = tactics.build_board([x, y], ref)
s = {sl["key"]: sl for sl in tafel}
k_st = {k["id"]: k for k in s["st"]["kandidaten"]}
k_aml = {k["id"]: k for k in s["aml"]["kandidaten"]}
pruefe("ST-Slot bewertet X als Stuermer (mb 95)", k_st[9001]["mb"] == 95)
pruefe("AML-Slot bewertet X als Fluegel (mb 40)", k_aml[9001]["mb"] == 40)
pruefe("Slot traegt sein Profil fuer die Anzeige",
       (s["st"]["profil"], s["st"]["profil_label"], s["aml"]["profil"]) == ("st", "Stürmer", "off"))
for sl in tafel:                       # wie app._brett: Gesamtzahl aus Fit und Slot-Score
    for k in sl["kandidaten"]:
        k["fit"] = k["score"]
        k["score"] = tactics.gesamt(k["fit"], k.get("mb"), k.get("pers_score"))
elf = tactics.startelf(tafel)
pruefe("Auto-Elf stellt X in den Sturm (Slot-Score 95 schlaegt 60)",
       elf.get("st", {}).get("id") == 9001, str({k: v["id"] for k, v in elf.items()}))
ohne = dict(x, score_je_profil=None, mb=55)
pruefe("ohne Profilwertung gilt wie frueher das gesetzte mb",
       {k["id"]: k["mb"] for k in tactics.build_board([ohne], ref)[-1]["kandidaten"]} == {9001: 55})

# ------------------------------------------------ D: App auf Wegwerf-DB
print("== D App: Brett, Ligavergleich, Slot-Vergleich, Rechen-Cache ==")
ordner = tempfile.mkdtemp(prefix="test_profilwahl_")
try:
    conn = db.connect(os.path.join(ordner, "test.db"))
    db._init_cohort(conn)
    ex = [zeile(5000 + i, pos, club=("Test FC" if i < 30 else f"Verein {i % 12}"))
          for i, pos in enumerate(POSITIONEN * 12)]
    db.save_export(conn, ex, None, datei="syn", ts="2026-09-26T10:00:00")
    kader = [e["eid"] for e in ex if e["club"] == "Test FC"]
    db.set_setting(conn, "squad_eids", json.dumps(kader))
    db.set_setting(conn, "own_club", "Test FC")
    db.set_setting(conn, "squad_imported_at", "2026-09-26T11:00:00")
    api = app.Api()
    api._local.conn = conn
    tb = api.tactic_board()
    pruefe("Brett rechnet", tb.get("ok") is True)
    pruefe("jeder Slot nennt sein Profil",
           all(sl["profil"] == tactics.slot_profil(slot[sl["key"]]) and sl["profil_label"]
               for sl in tb["slots"]))
    basis_app = api._basis(conn)
    von = {int(p["eid"]): p for p in api._kader_rows(conn, basis_app["bz"], kader, basis_app["export"])}
    moneyball.add_scores(list(von.values()), basis_app["referenz"], basis_app["ligen"])
    pruefe("Brett-mb = Score im Slot-Profil (alle Slots, alle Kandidaten)",
           all(k["mb"] == moneyball.profil_score(von[k["id"]], sl["profil"])
               for sl in tb["slots"] for k in sl["kandidaten"]))
    lv = api.league_comparison()
    pruefe("Ligavergleich nennt je Position das Profil",
           lv.get("ok") and all(z.get("profil") for z in lv["positionen"]), str(lv.get("error")))
    fremd = next(e["eid"] for e in ex if e["position"] == "V (Z)" and e["club"] != "Test FC")
    sc = api.slot_compare("st", [fremd])
    kand = next((z for z in sc["spieler"] if z.get("rolle") == "kandidat"), {})
    pruefe("Slot-Vergleich: Slot-Profil st, Innenverteidiger als Stuermer bewertet",
           sc["slot"]["profil"] == "st" and kand.get("umschulung") and kand.get("mb") is not None,
           str({k: kand.get(k) for k in ("name", "mb", "umschulung", "fehlt")}))
    vorher = api._konstanten()
    moneyball.LISTEN_WAHL = "haupt"
    pruefe("Rechen-Cache: LISTEN_WAHL steckt im Fingerabdruck", api._konstanten() != vorher)
    moneyball.LISTEN_WAHL = alt_listen
    moneyball.REFERENZ_WAHL = "maske"
    pruefe("Rechen-Cache: REFERENZ_WAHL steckt im Fingerabdruck", api._konstanten() != vorher)
    moneyball.REFERENZ_WAHL = REF_STANDARD
    pruefe("Rechen-Cache: zurueckgesetzt = alter Fingerabdruck", api._konstanten() == vorher)
    alt_sem = tactics.LEISTUNG_SEM["st"]
    tactics.LEISTUNG_SEM["st"] = (9.9, 1378)
    pruefe("Rechen-Cache: die Band-Konstanten stecken im Fingerabdruck", api._konstanten() != vorher)
    tactics.LEISTUNG_SEM["st"] = alt_sem

    print("== D Baender und 'gleichauf' in Brett, Planer, Slot-Vergleich, Ersatzsuche ==")
    tb = api.tactic_board()
    kand = [k for sl in tb["slots"] for k in sl["kandidaten"]]
    pruefe("Brett: jede Bewertung hat Leistung und Band",
           all({"leistung", "leistung_band", "leistung_von", "leistung_bis"} <= set(k) for k in kand))
    pruefe("Brett: Leistung = sqrt(Fit x Slot-Score)",
           all(k["leistung"] == tactics.gesamt(k["fit"], k["mb"]) for k in kand))
    pruefe("Brett: Band nach der Formel des Analysten",
           all(k["leistung_band"] == tactics.leistung_band(sl["key"], k["minutes"])
               for sl in tb["slots"] for k in sl["kandidaten"]))
    stamm_ok = all(next(k for k in sl["kandidaten"] if k["id"] == sl["startelf_id"])["gleichauf_stamm"] is None
                   for sl in tb["slots"] if sl["startelf_id"] is not None)
    pruefe("Brett: der Stammspieler selbst hat kein 'gleichauf'", stamm_ok)
    pruefe("Brett: Herausforderer tragen gleichauf_stamm (True/False/None)",
           all(k["gleichauf_stamm"] in (True, False, None) for k in kand)
           and any(k["gleichauf_stamm"] is not None for k in kand))
    pv = api.planer_vergleich("Ist", {"name": "T", "zugaenge": [fremd]})
    ps = pv["rechts"]["brett"]["slots"]
    pruefe("Planer: Stamm/Backup mit Band, Slot mit 'gleichauf' und Profil",
           all(("leistung_band" in (sl["stamm"] or {"leistung_band": 0})) and "gleichauf" in sl
               and sl.get("profil") for sl in ps))
    sc = api.slot_compare("st", [fremd])
    zeilen = [z for z in sc["spieler"] if "leistung" in z]
    pruefe("Slot-Vergleich: Band und gleichauf_stamm je Zeile",
           zeilen and all("leistung_band" in z and "gleichauf_stamm" in z for z in zeilen))
    st_id = next(sl["startelf_id"] for sl in tb["slots"] if sl["key"] == "st")
    er = api.tactic_replacements("st", st_id, "aehnlich", alle=True)
    pruefe("Ersatzsuche: Original und Treffer mit Band, Treffer mit 'gleichauf'",
           er.get("ok") and "leistung_band" in er["original"]
           and all("leistung_band" in t and "gleichauf" in t for t in er["treffer"]),
           str(er.get("error")))
    conn.close()
finally:
    shutil.rmtree(ordner, ignore_errors=True)

# ------------------------------------------------ E: Baender, reine Formeln
print("== E Unsicherheitsbaender (Datenanalyst, Auftrag 5, Nachtrag C2) ==")
pruefe("ST-Band bei 900 / 1.800 / 3.000 Min etwa +-24 / +-17 / +-13",
       [round(tactics.leistung_band("st", m)) for m in (900, 1800, 3000)] == [24, 17, 13],
       str([tactics.leistung_band("st", m) for m in (900, 1800, 3000)]))
pruefe("ST-Band bei 180 Min etwa +-53", round(tactics.leistung_band("st", 180)) == 53)
baender = [tactics.leistung_band("st", m) for m in (200, 500, 1000, 2000, 4000)]
pruefe("Band schrumpft mit den Minuten", baender == sorted(baender, reverse=True))
pruefe("0 Minuten / ohne Minuten -> None",
       tactics.leistung_band("st", 0) is None and tactics.leistung_band("st", None) is None)
pruefe("Band gedeckelt bei 100 (1 Minute)", tactics.leistung_band("tw", 1) == 100.0)
f = tactics.band_felder("st", 95, 900)
pruefe("Intervall auf 0-100 begrenzt", (f["leistung_von"], f["leistung_bis"]) == (71, 100)
       and tactics.band_felder("st", 5, 900)["leistung_von"] == 0)
pruefe("rechte Slots uebernehmen die Werte der linken",
       tactics.LEISTUNG_SEM["rv"] == tactics.LEISTUNG_SEM["lv"]
       and tactics.LEISTUNG_SEM["ivr"] == tactics.LEISTUNG_SEM["ivl"]
       and tactics.LEISTUNG_SEM["dmr"] == tactics.LEISTUNG_SEM["dml"])
pruefe("Tabelle des Analysten", tactics.LEISTUNG_SEM["st"] == (9.8, 1378)
       and tactics.LEISTUNG_SEM["tw"] == (11.1, 1739) and tactics.LEISTUNG_SEM["amc"] == (7.3, 1389))
# Schwelle zweier Spieler mit je M Minuten: 1,96 x sqrt(2) x SEM_h x sqrt(M_h/M)
m = 1378 * (1.96 * 2 ** 0.5 * 9.8 / 21.7) ** 2          # Minuten fuer die Schwelle 21,7
pruefe("gleichauf knapp unter der Schwelle (21,5 bei Schwelle 21,7)",
       tactics.gleichauf("st", 80, m, 58.5, m) is True)
pruefe("nicht gleichauf knapp ueber der Schwelle (21,9)", tactics.gleichauf("st", 80, m, 58.1, m) is False)
pruefe("unter MIN_MINUTES kein 'gleichauf' (None)",
       tactics.gleichauf("st", 80, 170, 79, 2000) is None and tactics.gleichauf("st", 80, 2000, None, 2000) is None)

print(f"\n{_bestanden} von {_gesamt} Prüfungen bestanden")
sys.exit(0 if _bestanden == _gesamt else 1)
