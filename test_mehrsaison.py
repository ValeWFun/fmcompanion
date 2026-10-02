"""Regressionsskript fuer die Mehrsaison-Wertung (D11b) – ohne FM24, ohne echte DB.

Geprueft wird auf einer Wegwerf-DB mit einer kleinen Import-Historie
(Winter 2026, Sommer 2026, Saisonstart 2027/28, Winter 2027, aktueller Stand
Sommer 2027):
- Gewichte d je Stand und Art, U23, M_eff, Mischung
- Saisonwahl ueber das Saison-Feld: die Saisonstart-Datei (171 Minuten) zaehlt
  NICHT als Vorsaison (Fall Mainoo), Staende mit 0 Minuten zaehlen nicht,
  duenne Saisons zaehlen nicht, je Verein der Stand mit den meisten Minuten
- Wechsel in der laufenden Saison: Stationen ueber saisons.aus_teilen
- Brett (Form) und Tabelle (Transfer): gemischte Werte gegen eine
  unabhaengige Nachrechnung, M_eff und Band
- Hinweise Sprung/Absturz (nur ganze Saisons, je mindestens 900 Minuten) und
  Kalenderjahr-Liga (keine Mischung)
- Invarianz: ein Spieler OHNE Vorsaison rechnet bitgleich zum Stand vor D11b
- Horizont je Ansicht; Planspiel-Ist = Brett-Rechnung im Horizont Transfer

Ausfuehren wie die anderen Skripte, kein pytest:
    .venv\\Scripts\\python.exe test_mehrsaison.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from fmcompanion import db, moneyball, saisons, tactics
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


rng = np.random.default_rng(11)
POSITIONEN = ["TW", "V (Z)", "V (L)", "V (R)", "V (RZ)", "DM, M (Z)", "M (Z)", "M (L)",
              "M (R)", "OM (L)", "OM (R)", "OM (Z)", "OM (RL), ST (Z)", "M/OM (L)", "ST (Z)",
              "DM, M (Z), OM (RZ)"]
LIGEN = ["Premier League", "Bundesliga", "Serie A", "Spaniens First Division",
         "Ligue 1 Uber Eats", "Eredivisie"]
BRA = "Brasileirão Betano Série A"
# Spieldaten (Jahr, Tag im Jahr) der Importe
S25, W26, S26, START, W27, S27 = (2026, 135), (2026, 355), (2027, 135), (2027, 224), (2027, 355), (2028, 135)


def alter(geb, datum):
    (gj, gt), (j, t) = geb, datum
    return j - gj - (1 if t < gt else 0)


def zeile(eid, pos, club, liga, minuten, geb, datum, stark=1.0):
    """Export-Zeile mit Zaehlwerten; stark skaliert die Leistungszahlen."""
    tw = pos == "TW"
    r = lambda a, b: float(rng.uniform(a, b))
    m = minuten
    f = m / 2500.0 * stark
    dt, pt, ht, prt, st = int(r(100, 300) * f), int(r(800, 2000) * m / 2500), int(r(40, 150) * f), \
        int(r(80, 250) * f), int(r(10, 60) * f)
    conc = int(r(15, 50) * m / 2500) if tw else 0
    return {"eid": eid, "name": f"Spieler {eid}", "position": pos, "age": alter(geb, datum),
            "club": club, "league": liga, "value": float(int(r(5, 80)) * 1e6),
            "minutes": m, "rating": round(min(8.5, r(6.6, 7.2) + 0.3 * (stark - 1)), 2),
            "apps": m // 85,
            "goals": 0 if tw else int(r(1, 12) * f), "assists": 0 if tw else int(r(1, 8) * f),
            "xg": 0.0 if tw else r(1, 10) * f, "xa": 0.0 if tw else r(1, 7) * f,
            "duels": int(dt * r(0.4, 0.7)), "duels_total": dt,
            "shots_total": st, "shots_on": int(st * r(0.3, 0.6)),
            "pass_try": pt, "pass_ok": int(pt * r(0.75, 0.9)),
            "dribbles": int(r(5, 60) * f), "prog_passes": int(r(20, 150) * f),
            "press_win": int(prt * r(0.2, 0.5)), "press_try": prt,
            "interceptions": int(r(10, 60) * f), "key_passes": int(r(5, 50) * f),
            "clearances": int(r(5, 90) * f), "headers_won": int(ht * r(0.3, 0.7)),
            "headers_total": ht, "losses": int(r(100, 300) * m / 2500),
            "recoveries": int(r(80, 250) * f),
            "pen_goals": 0, "conceded": conc, "xga": (conc + r(-3, 3)) if tw else None,
            "chances": int(r(0, 30) * f), "long_goals": 0, "blocks": int(r(0, 25) * f),
            "errors": 0, "crosses_ok": int(r(5, 30) * f), "crosses_try": int(r(30, 90) * f),
            "sprints": int(r(100, 300) * f), "saves_tipped": 20 if tw else None,
            "saves_parried": 20 if tw else None, "saves_held": 20 if tw else None,
            "personality": None, "foot": "Rechts", "height": 183}


def felder(zeilen):
    return sorted({k for z in zeilen for k in z} & set(db.EXPORT_COLS))


# --------------------------------------------------- A: reine Funktionen
print("== A Gewichte, M_eff, Stationen, Saisonen ==")
pruefe("d je Stand und Art (Datenanalyst)",
       saisons.gewicht_d(("sommer", "form")) == 1.0 and saisons.gewicht_d(("sommer", "transfer")) == 1.0
       and saisons.gewicht_d(("winter", "form")) == 0.375
       and saisons.gewicht_d(("winter", "transfer")) == 1.0)
pruefe("bis 23 im Transfer-Horizont die Form-Gewichte",
       saisons.gewicht_d(("winter", "transfer"), 23) == 0.375
       and saisons.gewicht_d(("winter", "transfer"), 24) == 1.0)
pruefe("M_eff: eine Saison = ihre Minuten; d = 1 = Summe",
       saisons.m_eff([(2400, 0)], 0.375) == 2400 and saisons.m_eff([(3000, 0), (1500, 1)], 1.0) == 4500)
pruefe("M_eff mit d = 0,375: (3000+1125)^2 / (3000+421,875)",
       abs(saisons.m_eff([(3000, 0), (3000, 1)], 0.375) - 4125 ** 2 / 3421.875) < 1e-9)
pruefe("gemischt: gewichtetes Mittel, None zaehlt nicht",
       saisons.gemischt([(80, 3000), (60, 1000), (None, 500)]) == 75.0
       and saisons.gemischt([(None, 1)]) is None)
st = saisons.stationen([
    {"club": "A", "minutes": 2900, "imported_at": "2026-02-10"},
    {"club": "A", "minutes": 1700, "imported_at": "2026-02-11"},     # spaeter eingelesener Winterstand
    {"club": "B", "minutes": 400, "imported_at": "2026-02-01"}])
pruefe("Stationen: je Verein die meisten Minuten (Re-Import aelterer Datei)",
       [(s["club"], s["minutes"]) for s in st] == [("B", 400), ("A", 2900)], str(st))
pruefe("Saison aus dem Spieldatum; Saisonende = Sommer",
       saisons.saison_aus(*S26) == 2026 and saisons.saison_aus(*START) == 2027
       and saisons.saison_aus(*W26) == 2026 and saisons.saisonende(S26[1])
       and not saisonende_start if (saisonende_start := saisons.saisonende(START[1])) is not None else False)
pruefe("Horizont: Stand aus dem Bezugsdatum",
       saisons.horizont_von({"ref_year": 2028, "ref_day": 135}, "form") == ("sommer", "form")
       and saisons.horizont_von({"ref_year": 2027, "ref_day": 355}, "transfer") == ("winter", "transfer"))
alt_min = saisons.VORSAISON_MIN_SPIELER
saisons.VORSAISON_MIN_SPIELER = 3
duenn = {1: [{"saison": 2024}], 2: [{"saison": 2025}], 3: [{"saison": 2025}], 4: [{"saison": 2025}],
         5: [{"saison": 2026}]}
pruefe("duenne Vorsaison zaehlt nicht (VORSAISON_MIN_SPIELER)",
       saisons.vorsaisons(duenn, 2026) == {2025})
pruefe("Band ohne Drift (BAND_MIT_DRIFT False): mit Horizont = ohne",
       not moneyball.BAND_MIT_DRIFT
       and tactics.leistung_band("st", 2500, ("sommer", "transfer")) == tactics.leistung_band("st", 2500)
       and moneyball.score_band("st", 2500, ("sommer", "transfer")) == moneyball.score_band("st", 2500))
ohne = tactics.vorsprung("st", 70, 2500, 60, 2500)
mit = tactics.vorsprung("st", 70, 2500, 60, 2500, ("sommer", "transfer"))
pruefe("Vorsprung: Drift und laengerer Horizont machen unsicherer",
       mit["vorsprung_p"] < ohne["vorsprung_p"], f"{ohne['vorsprung_p']} / {mit['vorsprung_p']}")
pruefe("ST/TW mit hoeherer Drift als Feldspieler",
       moneyball.horizont_zukunft(("sommer", "form"), "st")[1] == 144.0
       and moneyball.horizont_zukunft(("sommer", "form"), "iv")[1] == 64.0
       and moneyball.horizont_zukunft(("winter", "form"), "iv")[1] == 4.0)

print("== A2 Erwartete Staerke und Auswahl der Auto-Elf ==")
pruefe("erwartete Staerke: viele Minuten ~ Leistung, wenige Minuten zur Mitte (50) gezogen",
       abs(tactics.erwartete_staerke("lv", 85, 20000) - 85) < 1.0
       and 50 < tactics.erwartete_staerke("lv", 85, 400) < tactics.erwartete_staerke("lv", 85, 3000) < 85)
post = tactics.erwartete_staerke("lv", 80, 1500)
pruefe("erwartete Staerke mit Charakter: post^0,7 x Charakter^0,3",
       abs(tactics.erwartete_staerke("lv", 80, 1500, 90) - post ** 0.7 * 90 ** 0.3) < 0.01)
pruefe("ohne Minuten: ungeschrumpft; ohne Leistung: None",
       tactics.erwartete_staerke("lv", 80, None) == 80.0
       and tactics.erwartete_staerke("lv", None, 1500) is None)
brett_syn = [{"key": "lv", "kandidaten": [{"id": 1, "score": 85, "erwartet": 70.0},
                                          {"id": 2, "score": 80, "erwartet": 75.0}]}]
pruefe("Auto-Elf waehlt nach der erwarteten Staerke, ohne sie nach der Gesamtzahl",
       tactics.startelf(brett_syn)["lv"]["id"] == 2
       and tactics.startelf([{"key": "lv", "kandidaten": [{"id": 1, "score": 85},
                                                          {"id": 2, "score": 80}]}])["lv"]["id"] == 1)
tiefe_syn = tactics.kadertiefe([{"key": "lv", "startelf_id": 9, "kandidaten": [
    {"id": 9, "fit": 90, "mb": 90},
    {"id": 1, "fit": 85, "mb": 85, "erwartet_leistung": 60.0},
    {"id": 2, "fit": 80, "mb": 80, "erwartet_leistung": 70.0}]}])
pruefe("Backup nach der erwarteten Leistung, angezeigt die rohe",
       tiefe_syn["slots"]["lv"]["backup"][0]["id"] == 2 and tiefe_syn["slots"]["lv"]["backup"][1] == 80)

# --------------------------------------------------- Wegwerf-DB mit Historie
ordner = tempfile.mkdtemp(prefix="test_mehrsaison_")
saisons.VORSAISON_MIN_SPIELER = 30
try:
    conn = db.connect(os.path.join(ordner, "test.db"))
    geb = {}
    allg = []
    for i in range(96):
        eid = 5000 + i
        geb[eid] = (int(rng.integers(1995, 2006)), int(rng.integers(1, 366)))
        club = "Test FC" if i < 22 else f"Verein {i % 12}"
        allg.append((eid, POSITIONEN[i % len(POSITIONEN)], club, LIGEN[i % len(LIGEN)]))
    for j in range(8):                   # genug Torhueter fuer die TW-Verteilungen (>= 8 Werte)
        eid = 5900 + j
        geb[eid] = (int(rng.integers(1995, 2006)), int(rng.integers(1, 366)))
        allg.append((eid, "TW", f"Verein {j}", LIGEN[j % len(LIGEN)]))
    (SPRUNG, MAINOO, KALENDER, OHNE, WENIG, WINTER, NULL, WECHSEL, LEER, REIMPORT,
     TW_WECHSEL, TW_BLEIBER) = range(9001, 9013)
    spezial = {SPRUNG: "ST (Z)", MAINOO: "DM, M (Z)", KALENDER: "OM (RL), ST (Z)", OHNE: "M (Z)",
               WENIG: "ST (Z)", WINTER: "V (Z)", NULL: "V (L)", WECHSEL: "OM (Z)", LEER: "M (Z)",
               REIMPORT: "M (Z)", TW_WECHSEL: "TW", TW_BLEIBER: "TW"}
    for eid in spezial:
        geb[eid] = (1999, 200)
    PL = LIGEN[0]
    # Historie (nur export_importe/export_stand) – aelteste zuerst
    s25 = [zeile(e, p, c, lg, int(rng.integers(1200, 3200)), geb[e], S25) for e, p, c, lg in allg[:40]]
    s25 += [zeile(TW_BLEIBER, "TW", "Test FC", PL, 3000, geb[TW_BLEIBER], S25)]
    db.historie_nachtragen(conn, s25, felder(s25), "alt/s25.html", "2026-07-01T10:00:00")
    w26 = [zeile(e, p, c, lg, int(rng.integers(900, 1500)), geb[e], W26) for e, p, c, lg in allg[:50]]
    w26 += [zeile(WINTER, spezial[WINTER], "Test FC", PL, 1400, geb[WINTER], W26)]
    db.historie_nachtragen(conn, w26, felder(w26), "alt/w26.html", "2026-07-10T10:00:00")
    s26 = [zeile(e, p, c, lg, int(rng.integers(1200, 3200)), geb[e], S26) for e, p, c, lg in allg]
    s26 += [zeile(SPRUNG, "ST (Z)", "Verein 3", LIGEN[3], 2500, geb[SPRUNG], S26, stark=0.15),
            zeile(MAINOO, "DM, M (Z)", "Verein 5", LIGEN[5], 2800, geb[MAINOO], S26),
            zeile(KALENDER, "OM (RL), ST (Z)", "Flamengo", BRA, 2500, geb[KALENDER], S26),
            zeile(WENIG, "ST (Z)", "Verein 4", LIGEN[4], 600, geb[WENIG], S26, stark=0.15),
            dict(zeile(NULL, "V (L)", "Verein 6", PL, 0, geb[NULL], S26), minutes=0),
            zeile(REIMPORT, "M (Z)", "Verein 9", PL, 2800, geb[REIMPORT], S26),
            zeile(TW_WECHSEL, "TW", "Verein 4", LIGEN[4], 3000, geb[TW_WECHSEL], S26),
            zeile(TW_BLEIBER, "TW", "Test FC", PL, 2900, geb[TW_BLEIBER], S26)]
    db.historie_nachtragen(conn, s26, felder(s26), "alt/s26.html", "2026-08-01T10:00:00")
    # eine aeltere Winterdatei, NACH dem Sommer-Export noch einmal eingelesen:
    # gleiche Saison, gleicher Verein, weniger Minuten
    nach = [zeile(e, p, c, lg, int(rng.integers(300, 800)), geb[e], W26) for e, p, c, lg in allg[:40]]
    nach += [zeile(REIMPORT, "M (Z)", "Verein 9", PL, 1500, geb[REIMPORT], W26)]
    db.historie_nachtragen(conn, nach, felder(nach), "alt/w26_nochmal.html", "2026-08-05T10:00:00")
    start = [zeile(e, p, c, lg, int(rng.integers(90, 400)), geb[e], START) for e, p, c, lg in allg[:40]]
    start += [zeile(MAINOO, "DM, M (Z)", "Test FC", PL, 171, geb[MAINOO], START)]
    db.historie_nachtragen(conn, start, felder(start), "alt/start.html", "2026-08-15T10:00:00")
    w27 = [zeile(e, p, c, lg, int(rng.integers(900, 1500)), geb[e], W27) for e, p, c, lg in allg[:40]]
    w27 += [zeile(WECHSEL, "OM (Z)", "Verein 7", LIGEN[2], 1300, geb[WECHSEL], W27)]
    db.historie_nachtragen(conn, w27, felder(w27), "alt/w27.html", "2026-09-01T10:00:00")
    # aktueller Stand (Sommer 2027) ueber den normalen Import
    s27 = [zeile(e, p, c, lg, int(rng.integers(1200, 3200)), geb[e], S27) for e, p, c, lg in allg]
    s27 += [zeile(SPRUNG, "ST (Z)", "Test FC", PL, 2600, geb[SPRUNG], S27, stark=3.0),
            zeile(MAINOO, "DM, M (Z)", "Test FC", PL, 2000, geb[MAINOO], S27),
            zeile(KALENDER, "OM (RL), ST (Z)", "Flamengo", BRA, 2700, geb[KALENDER], S27),
            zeile(OHNE, "M (Z)", "Test FC", PL, 2400, geb[OHNE], S27),
            zeile(WENIG, "ST (Z)", "Test FC", PL, 2500, geb[WENIG], S27, stark=3.0),
            zeile(WINTER, "V (Z)", "Test FC", PL, 2600, geb[WINTER], S27),
            zeile(NULL, "V (L)", "Test FC", PL, 2500, geb[NULL], S27),
            zeile(WECHSEL, "OM (Z)", "Test FC", PL, 900, geb[WECHSEL], S27),
            dict(zeile(LEER, "M (Z)", "Verein 8", PL, 0, geb[LEER], S27), minutes=0),
            zeile(REIMPORT, "M (Z)", "Verein 2", PL, 2400, geb[REIMPORT], S27),
            zeile(TW_WECHSEL, "TW", "Test FC", PL, 1800, geb[TW_WECHSEL], S27),
            zeile(TW_BLEIBER, "TW", "Test FC", PL, 2700, geb[TW_BLEIBER], S27)]
    db.save_export(conn, s27, None, datei="neu.html", ts="2026-09-20T10:00:00")
    # Geburtsdaten aus dem "RAM" (Snapshot) – daraus bestimmt saisons.staende die Spieldaten
    db.save_snapshot(conn, [{"id": 100000 + i, "name": f"RAM {e}", "eid": e, "birth_year": gj,
                             "birth_day": gt, "minutes": 0}
                            for i, (e, (gj, gt)) in enumerate(geb.items())])
    kader = [e for e, _, c, _ in allg if c == "Test FC"] + [SPRUNG, MAINOO, OHNE, WENIG, WINTER,
                                                           NULL, WECHSEL, TW_WECHSEL, TW_BLEIBER]
    db.set_setting(conn, "squad_eids", json.dumps(kader))
    db.set_setting(conn, "own_club", "Test FC")
    db.set_setting(conn, "squad_imported_at", "2026-09-20T11:00:00")
    db.set_setting(conn, "ref_year_cached", 2028)
    db.set_setting(conn, "ref_day_cached", 135)
    db.set_setting(conn, "bezug_version", app.Api.BEZUG_VERSION)
    api = app.Api()
    api._local.conn = conn
    basis = api._basis(conn)
    ms = api._mehrsaison(conn, basis)
    roh = {int(e["eid"]): e for e in basis["export"]}

    # --------------------------------------------------- B: Saisonwahl
    print("== B Saisonwahl ueber das Saison-Feld ==")
    stm = saisons.staende(conn)
    pruefe("Mainoo: Sommer 2026 = 2026 (ganz), Saisonstart = 2027 (nicht ganz), aktuell = 2027",
           [(z["saison"], z["ende"], z["minutes"]) for z in stm[MAINOO]]
           == [(2026, True, 2800), (2027, False, 171), (2027, True, 2000)],
           str([(z["saison"], z["ende"], z["minutes"]) for z in stm[MAINOO]]))
    ein = ms.eintraege(dict(basis["export_rows"][[int(r["eid"]) for r in basis["export_rows"]].index(MAINOO)]))
    pruefe("Mainoo: Vorsaison ist 2026 bei Verein 5 (2.800), NICHT die Saisonstart-Datei (171)",
           [(e["saison"], e["minuten"], [t["club"] for t in e["teile"]]) for e in ein]
           == [(2027, 2000, ["Test FC"]), (2026, 2800, ["Verein 5"])],
           str([(e["saison"], e["minuten"]) for e in ein]))
    zeile_von = {int(r["eid"]): r for r in basis["export_rows"]}
    pruefe("Stand mit 0 Minuten zaehlt nicht",
           [e["saison"] for e in ms.eintraege(zeile_von[NULL])] == [2027])
    ew = ms.eintraege(zeile_von[WECHSEL])
    pruefe("Wechsel in der Saison: aktuelle Saison ueber beide Stationen",
           len(ew) == 1 and ew[0]["minuten"] == 2200 and not ew[0]["einfach"]
           and [t.get("club") for t in ew[0]["teile"]] == ["Verein 7", "Test FC"], str(ew[:1]))
    pruefe("Kalenderjahr-Liga: nur die aktuelle Saison",
           [e["saison"] for e in ms.eintraege(zeile_von[KALENDER])] == [2027])
    pruefe("Winterstand 2026 zaehlt als Saison 2026, aber nicht als ganze",
           [(e["saison"], e["ende"]) for e in ms.eintraege(zeile_von[WINTER])]
           == [(2027, True), (2026, False)])
    er_ = ms.eintraege(zeile_von[REIMPORT])
    pruefe("spaeter eingelesene aeltere Datei: die Saison zaehlt mit dem Stand der meisten Minuten",
           [(e["saison"], e["minuten"], e["ende"]) for e in er_] == [(2027, 2400, True), (2026, 2800, True)]
           and [z["minutes"] for z in stm[REIMPORT]] == [2800, 1500, 2400],
           str([(e["saison"], e["minuten"], e["ende"]) for e in er_]))
    pruefe("ohne Vorsaison: nur die aktuelle Saison, einfach",
           [(e["saison"], e["einfach"]) for e in ms.eintraege(zeile_von[OHNE])] == [(2027, True)])

    # --------------------------------------------------- C: Brett (Form)
    print("== C Brett im Horizont Form ==")
    tb = api.tactic_board()
    pruefe("Brett rechnet und nennt den Horizont",
           tb.get("ok") and tb["horizont"] == {"key": "form", "stand": "sommer",
                                               "text": saisons.HORIZONT_TEXT["form"]},
           str(tb.get("horizont")))
    h_form = ("sommer", "form")

    def kand(slot_key, eid):
        sl = next(s for s in tb["slots"] if s["key"] == slot_key)
        return next((k for k in sl["kandidaten"] if int(k["id"]) == eid), None)

    def nachrechnen(stand, slot):
        """Fit und Score einer Vorsaison-Station, unabhaengig von saisons.Mehrsaison."""
        r = moneyball.enrich([moneyball.export_als_spieler(stand)], **basis["bz"])[0]
        moneyball.add_scores([r], basis["referenz"], {int(r["eid"]): r.get("league")},
                             ref_stats=api._ref_scores(basis),
                             zusatz_profile=moneyball.PROFIL_REIHENFOLGE)
        dists, _ = tactics.slot_dists(slot, basis["referenz"], cache=basis["verteilungen"])
        b = tactics.score_slot(r, slot, dists, basis["teams"])
        return b["score"], moneyball.profil_score(r, tactics.slot_profil(slot))

    slot_dm = next(s for s in tactics.FORMATION if s["key"] == "dml")
    k = kand("dml", MAINOO)
    f26, m26 = nachrechnen(stm[MAINOO][0], slot_dm)
    d = saisons.gewicht_d(h_form)
    soll_fit = round((k["fit_aktuell"] * 2000 + f26 * 2800 * d) / (2000 + 2800 * d))
    soll_mb = round((k["mb_aktuell"] * 2000 + m26 * 2800 * d) / (2000 + 2800 * d))
    pruefe("Mainoo im Slot: Fit und Score gemischt wie nachgerechnet",
           k["fit"] == soll_fit and k["mb"] == soll_mb,
           f"fit {k['fit']}/{soll_fit} mb {k['mb']}/{soll_mb}")
    pruefe("Mainoo: Leistung = sqrt(Fit x Score), M_eff und Band dazu",
           k["leistung"] == tactics.gesamt(k["fit"], k["mb"])
           and k["m_eff"] == round(saisons.m_eff([(2000, 0), (2800, 1)], d))
           and k["leistung_band"] == tactics.leistung_band("dml", k["m_eff"], h_form))
    pruefe("Mainoo: Saisons mit Minuten, Gewicht und Vereinen",
           [(s["saison"], s["minuten"], s["gewicht"], s["vereine"]) for s in k["saisons"]]
           == [(2027, 2000, 2000.0, ["Test FC"]), (2026, 2800, round(2800 * d, 1), ["Verein 5"])],
           str(k["saisons"]))
    # Invarianz: ohne Vorsaison bitgleich zur Rechnung ohne Mischung
    kader_rows = api._kader_rows(conn, basis["bz"], kader, basis["export"])
    moneyball.add_scores(kader_rows, basis["referenz"], basis["ligen"], ref_stats=api._ref_scores(basis))
    alt = tactics.build_board(kader_rows, basis["referenz"], teams=basis["teams"],
                              cache=basis["verteilungen"])
    alt_k = {(sl["key"], int(x["id"])): x for sl in alt for x in sl["kandidaten"]}
    neu_k = {(sl["key"], int(x["id"])): x for sl in tb["slots"] for x in sl["kandidaten"]}
    ohne_vor = [(s, e) for (s, e) in neu_k if e in (OHNE, NULL)]
    pruefe("Invarianz: ohne Vorsaison Fit und Score bitgleich zum Stand vor D11b",
           ohne_vor and all(neu_k[x]["fit"] == alt_k[x]["score"] and neu_k[x]["mb"] == alt_k[x]["mb"]
                            and neu_k[x]["m_eff"] == neu_k[x]["minutes"] for x in ohne_vor),
           str([(x, neu_k[x]["fit"], alt_k[x]["score"]) for x in ohne_vor][:3]))
    pruefe("Invarianz: ohne Vorsaison das Band wie vor D11b (ohne Drift)",
           all(neu_k[x]["leistung_band"] == tactics.leistung_band(x[0], neu_k[x]["minutes"])
               for x in ohne_vor))
    ks = kand("st", SPRUNG)
    hin = {h["art"]: h for h in ks["hinweise"]}
    pruefe("Sprung: zwei ganze Saisons mit je >= 900 Min und |dL| >= 10",
           "sprung" in hin and hin["sprung"]["saisons"] == [2026, 2027]
           and hin["sprung"]["delta"] >= saisons.SPRUNG_PUNKTE
           and hin["sprung"]["text"] == saisons.SPRUNG_TEXT, str(ks["hinweise"]))
    kw = kand("st", WENIG)
    pruefe("kein Sprung unter 900 Minuten in einer der Saisons",
           kw is not None and not any(h["art"] in ("sprung", "absturz") for h in kw["hinweise"]),
           str(kw and kw["hinweise"]))
    kwi = next((x for (s, e), x in neu_k.items() if e == WINTER), None)
    pruefe("kein Sprung, wenn die Vorsaison nur ein Winterstand ist",
           kwi is not None and not any(h["art"] in ("sprung", "absturz") for h in kwi["hinweise"]))
    pruefe("Brett: jeder Kandidat mit erwarteter Staerke (Leistung, M_eff, Charakter)",
           all(x["erwartet"] == tactics.erwartete_staerke(s, x["leistung"], x["m_eff"], x["charakter"])
               and x["erwartet_leistung"] == tactics.erwartete_staerke(s, x["leistung"], x["m_eff"])
               for (s, e), x in neu_k.items()))
    elf_ids = {sl["key"]: sl["startelf_id"] for sl in tb["slots"]}
    pruefe("Auto-Elf: bitgleich zu tactics.startelf ueber die erwartete Staerke",
           elf_ids == {k: v["id"] for k, v in tactics.startelf(tb["slots"], api._pins(conn)).items()}
           | {k: None for k in elf_ids if k not in tactics.startelf(tb["slots"], api._pins(conn))})
    kwe, kbl = neu_k.get(("tw", TW_WECHSEL)), neu_k.get(("tw", TW_BLEIBER))
    pruefe("Torwart mit Vereinswechsel: nur die aktuelle Station zaehlt, Hinweis",
           kwe is not None and [s["saison"] for s in kwe["saisons"]] == [2027]
           and kwe["fit"] == kwe["fit_aktuell"] and kwe["mb"] == kwe["mb_aktuell"]
           and any(h["art"] == "torwart" and h["text"] == saisons.TORWART_TEXT["verein"]
                   for h in kwe["hinweise"]),
           str(kwe and (kwe["saisons"], kwe["hinweise"])))
    pruefe("Torwart, drei Saisons beim selben Verein: alle drei gemischt, kein Hinweis",
           kbl is not None and [s["saison"] for s in kbl["saisons"]] == [2027, 2026, 2025]
           and all(s["zaehlt"] for s in kbl["saisons"]) and kbl["m_eff"] == 2700 + 2900 + 3000
           and not any(h["art"] == "torwart" for h in kbl["hinweise"]),
           str(kbl and (kbl["saisons"], kbl["m_eff"], kbl["hinweise"])))
    kx = next((x for (s, e), x in neu_k.items() if e == WECHSEL), None)
    pruefe("Wechsel in der Saison: M_eff = Minuten beider Stationen",
           kx is not None and kx["m_eff"] == 2200 and kx["saisons"][0]["vereine"] == ["Verein 7", "Test FC"],
           str(kx and (kx["m_eff"], kx["saisons"])))

    # --------------------------------------------------- D: Tabelle (Transfer)
    print("== D Tabelle im Horizont Transfer ==")
    ls = api.load_saved()
    zl = {}
    for p in ls["players"]:
        if p.get("eid") and p.get("aktuell"):
            zl[int(p["eid"])] = p
    h_tr = ("sommer", "transfer")
    pm = zl[MAINOO]
    pruefe("Tabelle: Saisons, M_eff und Hinweise je Zeile",
           [s["saison"] for s in pm["saisons"]] == [2027, 2026]
           and pm["m_eff"] == round(saisons.m_eff([(2000, 0), (2800, 1)], saisons.gewicht_d(h_tr, pm.get("age"))))
           and isinstance(pm["hinweise"], list), str((pm.get("saisons"), pm.get("m_eff"))))
    pr = pm["profil"]
    r26 = moneyball.enrich([moneyball.export_als_spieler(stm[MAINOO][0])], **basis["bz"])[0]
    moneyball.add_scores([r26], basis["referenz"], {MAINOO: r26.get("league")},
                         ref_stats=api._ref_scores(basis), zusatz_profile=moneyball.PROFIL_REIHENFOLGE)
    akt = dict(roh[MAINOO])
    akt_p = moneyball.enrich([moneyball.export_als_spieler(akt)], **basis["bz"])[0]
    moneyball.add_scores([akt_p], basis["referenz"], basis["ligen"], ref_stats=api._ref_scores(basis))
    s27, s26_ = akt_p["score_je_profil"][pr]["score"], r26["score_je_profil"][pr]["score"]
    dt = saisons.gewicht_d(h_tr, pm.get("age"))
    pruefe("Tabelle: Listen-Score gemischt wie nachgerechnet",
           pm["score"] == round((s27 * 2000 + s26_ * 2800 * dt) / (2000 + 2800 * dt)),
           f"{pm['score']} / {s27} {s26_}")
    po = zl[OHNE]
    oz = moneyball.enrich([moneyball.export_als_spieler(dict(roh[OHNE]))], **basis["bz"])[0]
    moneyball.add_scores([oz], basis["referenz"], basis["ligen"], ref_stats=api._ref_scores(basis))
    pruefe("Invarianz Tabelle: ohne Vorsaison Score, Profil, Talent und score_kal wie vor D11b",
           po["score"] == oz["score"] and po["profil"] == oz["profil"] and po["talent"] == oz["talent"]
           and po["score_kal"] == oz["score_kal"] and po["m_eff"] == po["minutes"],
           f"{po['score']}/{oz['score']} {po['score_kal']}/{oz['score_kal']}")
    pl_ = zl.get(LEER)
    pruefe("aktuelle Zeile ohne Minuten und ohne Vorsaison: keine Mischung, kein Absturz",
           pl_ is not None and pl_["m_eff"] == 0 and saisons.gemischt([(70, 0.0)]) is None,
           str(pl_ and (pl_.get("m_eff"), pl_.get("score"))))
    pwe, pbl = zl.get(TW_WECHSEL), zl.get(TW_BLEIBER)
    pruefe("Tabelle: Torwart-Wechsler nur aktuelle Station mit Hinweis, Bleiber mit drei Saisons",
           pwe is not None and len(pwe["saisons"]) == 1
           and any(h["art"] == "torwart" for h in pwe["hinweise"])
           and pbl is not None and [s["saison"] for s in pbl["saisons"]] == [2027, 2026, 2025]
           and not any(h["art"] == "torwart" for h in pbl["hinweise"]))
    pk = zl[KALENDER]
    pruefe("Kalenderjahr-Liga: Hinweis, keine Mischung",
           any(h["art"] == "kalenderjahr" and h["text"] == saisons.KALENDER_TEXT for h in pk["hinweise"])
           and [s["saison"] for s in pk["saisons"]] == [2027])

    # --------------------------------------------------- E: Horizonte der Ansichten
    print("== E Horizont je Ansicht, Planspiel = Brett mit Transfer ==")
    pv = api.planer_vergleich("Ist", {"name": "T", "zugaenge": [], "abgaenge": []})
    pruefe("Planspiel: Horizont Transfer", pv.get("ok") and pv["horizont"]["key"] == "transfer")
    bt = api._brett(conn, kader, api._pins(conn), basis, art="transfer")[0]
    elf_t = {sl["key"]: next(k for k in sl["kandidaten"] if k["id"] == sl["startelf_id"])
             for sl in bt["slots"] if sl["startelf_id"] is not None}
    links = {sl["key"]: sl["stamm"] for sl in pv["links"]["brett"]["slots"]}
    pruefe("Planspiel-Ist = Brett-Rechnung im Horizont Transfer (gleiche Elf, gleiche Leistung)",
           all(links[k] and links[k]["id"] == v["id"] and links[k]["leistung"] == v["leistung"]
               for k, v in elf_t.items()),
           str([(k, links[k] and links[k]["leistung"], v["leistung"]) for k, v in elf_t.items()][:3]))
    lc = api.league_comparison()
    pruefe("Ligavergleich: Horizont Form", lc.get("ok") and lc["horizont"]["key"] == "form",
           str(lc.get("error")))
    st_id = next(sl["startelf_id"] for sl in tb["slots"] if sl["key"] == "st")
    er = api.tactic_replacements("st", st_id, "aehnlich", alle=True)
    pruefe("Ersatzsuche: Horizont Transfer, Treffer mit Saisons und M_eff",
           er.get("ok") and er["horizont"]["key"] == "transfer"
           and all("saisons" in t and "m_eff" in t for t in er["treffer"]), str(er.get("error")))
    sc = api.slot_compare("st", [SPRUNG])
    pruefe("Slot-Vergleich: Horizont Transfer, jede Zeile mit M_eff",
           sc.get("ok") and sc["horizont"]["key"] == "transfer"
           and all("m_eff" in z for z in sc["spieler"] if "leistung" in z))
    kv = api.vergleich_konstanten()
    pruefe("vergleich_konstanten: d, M_f und Drift je Art im aktuellen Stand",
           kv["horizonte"]["transfer"]["m_f"] == 3000 and kv["horizonte"]["form"]["stand"] == "sommer")
    # Winter: Form mischt mit d = 0,375, Transfer mit d = 1 (Datenanalyst)
    db.set_setting(conn, "ref_year_cached", 2027)
    db.set_setting(conn, "ref_day_cached", 355)
    tw_ = api.tactic_board()
    bw = api._basis(conn)
    kw_ = next(x for sl in tw_["slots"] if sl["key"] == "dml" for x in sl["kandidaten"]
               if int(x["id"]) == MAINOO)
    bt_ = api._brett(conn, kader, api._pins(conn), bw, art="transfer")[0]
    kt_ = next(x for sl in bt_["slots"] if sl["key"] == "dml" for x in sl["kandidaten"]
               if int(x["id"]) == MAINOO)
    gew = {(s["saison"]): s["gewicht"] for s in kw_["saisons"]}
    pruefe("Winter: Brett (Form) mit d = 0,375, Planspiel (Transfer) mit d = 1",
           tw_["horizont"]["stand"] == "winter" and gew == {2027: 2000.0, 2026: round(2800 * 0.375, 1)}
           and {s["saison"]: s["gewicht"] for s in kt_["saisons"]} == {2027: 2000.0, 2026: 2800.0},
           f"{gew} / {[(s['saison'], s['gewicht']) for s in kt_['saisons']]}")
    pruefe("Winter: aktuelle Saison nicht ganz – kein Sprung aus nur einer ganzen Saison",
           not any(h["art"] in ("sprung", "absturz")
                   for sl in tw_["slots"] for x in sl["kandidaten"] if int(x["id"]) == SPRUNG
                   for h in x["hinweise"]))
    db.set_setting(conn, "ref_year_cached", 2028)
    db.set_setting(conn, "ref_day_cached", 135)
    try:
        json.dumps([tb, ls, pv, er, sc], allow_nan=False)
        pruefe("alle Antworten als reines JSON serialisierbar", True)
    except (TypeError, ValueError) as e:
        pruefe("alle Antworten als reines JSON serialisierbar", False, str(e))
    conn.close()
finally:
    saisons.VORSAISON_MIN_SPIELER = alt_min
    shutil.rmtree(ordner, ignore_errors=True)

print(f"\n{_bestanden} von {_gesamt} Prüfungen bestanden")
sys.exit(0 if _bestanden == _gesamt else 1)
