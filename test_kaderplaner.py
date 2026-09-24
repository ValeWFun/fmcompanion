"""Regressionsskript fuer den Kaderplaner (D14) – ohne FM24, ohne echte DB.

Rein synthetisch: ein erfundener Export (numpy-Saat) in einer Wegwerf-DB im
Temp-Ordner. Geprueft wird vor allem, dass ein Szenario NIE den echten Kader,
den eigenen Verein, den Vereinswechsel-Stichtag oder die echten Pins
veraendert. Dazu Kadertiefe, Meldeliste, Gehalt, Transferbilanz, Pins,
Warnungen und das Speichern der Szenarien.

Ausfuehren wie die anderen Skripte, kein pytest:
    .venv\\Scripts\\python.exe test_kaderplaner.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from fmcompanion import db, moneyball, tactics
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


# ------------------------------------------------ Teil A: reine Funktionen
print("== A1 kadertiefe ==")
slots = [
    {"key": "a", "startelf_id": 1, "kandidaten": [
        {"id": 1, "name": "S1", "fit": 90, "mb": 90},
        {"id": 3, "name": "X", "fit": 81, "mb": 81},      # 81 hier
        {"id": 4, "name": "Y", "fit": 70, "mb": 70}]},
    {"key": "b", "startelf_id": 2, "kandidaten": [
        {"id": 2, "name": "S2", "fit": 85, "mb": 85},
        {"id": 3, "name": "X", "fit": 75, "mb": 75},      # 75 hier -> bekommt a
        {"id": 5, "name": "Z", "fit": 73, "mb": 73},
        {"id": 6, "name": "W", "fit": 40, "mb": 40}]},
]
t = tactics.kadertiefe(slots)
pruefe("Stamm a = 1", t["slots"]["a"]["stamm"][0]["id"] == 1)
pruefe("X wird Backup auf a (hoechste Leistung)", t["slots"]["a"]["backup"][0]["id"] == 3)
pruefe("b bekommt den naechsten (Z)", t["slots"]["b"]["backup"][0]["id"] == 5)
pruefe("ueberzaehlig: Y und W, nach Leistung",
       [u["id"] for u in t["ueberzaehlig"]] == [4, 6], str(t["ueberzaehlig"]))
pruefe("ab_schwelle a = 2 (90, 81)", t["slots"]["a"]["ab_schwelle"] == 2)
pruefe("ab_schwelle b = 3 (85, 75, 73)", t["slots"]["b"]["ab_schwelle"] == 3)
pruefe("a nicht duenn", t["slots"]["a"]["duenn"] is False)
t2 = tactics.kadertiefe(slots, schwelle=86)
pruefe("mit Schwelle 86 ist b duenn", t2["slots"]["b"]["duenn"] is True)

print("== A2 Kalenderjahr-Ligen ==")
for liga in ("Brasileirão Betano Série A", "Argentiniens Premier Division",
             "Major League Soccer", "Norwegens Premier Division"):
    pruefe(f"{liga}: Kalenderjahr", moneyball.ist_kalenderjahr_liga(liga) is True)
for liga in ("Premier League", "Bundesliga", None, ""):
    pruefe(f"{liga!r}: kein Kalenderjahr", moneyball.ist_kalenderjahr_liga(liga) is False)

# ------------------------------------------------ Teil B: synthetische DB
POS = ["TW", "V (Z)", "V (L)", "V (R)", "DM, M (Z)", "OM (L)", "OM (R)", "OM (Z)", "ST (Z)"]
rng = np.random.default_rng(3)


def spieler(eid, pos, club, homegrown=None, fee=None):
    m = int(rng.integers(900, 3400))
    tw = pos == "TW"
    r = lambda a, b: float(rng.uniform(a, b))
    duels_t = int(r(40, 400)); pass_t = int(r(300, 2500))
    head_t = int(r(20, 250)); press_t = int(r(30, 300)); shots_t = int(r(0, 80))
    cross_t = int(r(0, 120)); conc = int(r(15, 60)) if tw else 0
    saves = int(r(40, 120)) if tw else 0
    return {
        "eid": eid, "name": f"Spieler {eid}", "position": pos, "age": int(rng.integers(22, 32)),
        "club": club, "league": "Premier League", "nation": "Testland",
        "value": float(int(r(5, 80)) * 1e6), "wage": float(int(r(20, 200)) * 1000),
        "goals": 0 if tw else int(r(0, 20)), "assists": 0 if tw else int(r(0, 12)),
        "xg": 0.0 if tw else r(0, 15), "xa": 0.0 if tw else r(0, 10),
        "minutes": m, "rating": round(r(6.5, 7.4), 2), "apps": m // 85,
        "duels": int(duels_t * r(0.4, 0.7)), "duels_total": duels_t,
        "shots_total": shots_t, "shots_on": int(shots_t * r(0.2, 0.6)),
        "pass_try": pass_t, "pass_ok": int(pass_t * r(0.7, 0.92)),
        "dribbles": int(r(0, 80)), "prog_passes": int(r(10, 200)),
        "press_win": int(press_t * r(0.2, 0.5)), "press_try": press_t,
        "interceptions": int(r(5, 80)), "key_passes": int(r(0, 60)),
        "clearances": int(r(0, 120)), "headers_won": int(head_t * r(0.3, 0.7)),
        "headers_total": head_t, "losses": int(r(50, 400)), "recoveries": int(r(50, 300)),
        "pen_goals": 0, "conceded": conc,
        "xga": (conc + r(-5, 5)) if tw else None, "chances": int(r(0, 40)),
        "long_goals": 0, "blocks": int(r(0, 30)), "errors": int(r(0, 3)),
        "crosses_ok": int(cross_t * r(0.2, 0.4)), "crosses_try": cross_t,
        "sprints": int(r(50, 400)), "pen_saved": 0, "pen_faced": 0,
        "saves_tipped": saves // 3 if tw else None, "saves_parried": saves // 3 if tw else None,
        "saves_held": saves - 2 * (saves // 3) if tw else None, "clean_sheets": 0,
        "personality": None, "media": None, "foot": "Rechts", "info": None, "height": 183,
        "transfer_fee": fee, "homegrown": homegrown}


ordner = tempfile.mkdtemp(prefix="test_kaderplaner_")
try:
    conn = db.connect(os.path.join(ordner, "test.db"))
    db._init_cohort(conn)
    alle, kader = [], []
    eid = 1000
    for i, pos in enumerate(POS):
        for j in range(12):                     # 12 je Position: Verteilungen >= 8
            eid += 1
            if j < (3 if pos in ("V (Z)", "OM (L)", "OM (R)", "OM (Z)", "ST (Z)") else 2):
                club, hg = "Test FC", ("Ausgebildet im Land (15–21)" if j == 0 and i % 2 else None)
                kader.append(eid)
            else:
                club, hg = f"Verein {j}", ("Ausgebildet im Land (15–21)" if j == 5 else None)
            fee = 30e6 if j == 7 else None
            alle.append(spieler(eid, pos, club, hg, fee))
    db.save_export(conn, alle, None, datei="syn", ts="2026-09-22T10:00:00")
    st_kader = [e for e in kader if next(p for p in alle if p["eid"] == e)["position"] == "ST (Z)"]
    db.set_setting(conn, "squad_eids", json.dumps(sorted(kader)))
    db.set_setting(conn, "own_club", "Test FC")
    db.set_setting(conn, "squad_imported_at", "2026-09-22T11:00:00")
    db.set_setting(conn, "startelf_pins", json.dumps({"st": st_kader[0]}))
    api = app.Api()
    api._local.conn = conn
    von = {p["eid"]: p for p in alle}
    KEYS = ("squad_eids", "own_club", "own_club_seit", "startelf_pins", "squad_imported_at")

    def zustand():
        return {k: db.get_setting(conn, k) for k in KEYS}

    ref = zustand()

    def unveraendert(nach):
        pruefe(f"Invarianten unveraendert nach {nach}", zustand() == ref,
               str({k: (ref[k], zustand()[k]) for k in KEYS if ref[k] != zustand()[k]}))

    print(f"== B Kader {len(kader)} Spieler, Export {len(alle)} ==")
    # Zugaenge: z1 fremd ohne homegrown (mit Fee), z2 fremd ohne alles Besondere
    fremd = [p for p in alle if p["club"] != "Test FC"]
    z1 = next(p["eid"] for p in fremd if p["transfer_fee"] and not p["homegrown"]
              and p["position"] == "V (Z)")
    z2 = next(p["eid"] for p in fremd if not p["transfer_fee"] and not p["homegrown"]
              and p["position"] == "ST (Z)")
    a1 = next(e for e in kader if not von[e]["homegrown"] and von[e]["position"] == "DM, M (Z)")

    r = api.planer_vergleich("Ist", {"zugaenge": [z1, z2], "abgaenge": [a1]})
    unveraendert("planer_vergleich")
    pruefe("ok", r.get("ok"), str(r.get("error")))
    li, re_, d = r["links"], r["rechts"], r["delta"]
    pruefe("kader_anzahl 21 + 2 - 1", re_["kader_anzahl"] == len(kader) + 1, str(re_["kader_anzahl"]))
    # Meldeliste: z1, z2 ueber 21 und nicht heimisch, a1 nicht heimisch -> +1
    pruefe("Meldeliste: nicht_heimisch +1",
           re_["meldeliste"]["pl"]["nicht_heimisch"] == li["meldeliste"]["pl"]["nicht_heimisch"] + 1,
           f"{li['meldeliste']['pl']} / {re_['meldeliste']['pl']}")
    pruefe("Delta Meldeliste = rechts - links",
           d["meldeliste"]["nicht_heimisch"] == re_["meldeliste"]["pl"]["nicht_heimisch"]
           - li["meldeliste"]["pl"]["nicht_heimisch"])
    erw = von[z1]["wage"] + von[z2]["wage"] - von[a1]["wage"]
    pruefe("Gehalt/Woche: + Zugaenge - Abgang", re_["gehalt"]["woche"] - li["gehalt"]["woche"] == erw)
    pruefe("Gehalt/Jahr = Woche x 52", re_["gehalt"]["jahr"] == re_["gehalt"]["woche"] * 52)
    pruefe("heutiges_gehalt wahr", re_["gehalt"]["heutiges_gehalt"] is True)
    pruefe("Delta Gehalt", d["gehalt"]["woche"] == erw)
    zeilen = {z["eid"]: z for z in re_["transfer"]["zeilen"]}
    pruefe("Zugang mit Fee: Ablöseforderung",
           zeilen[z1]["quelle"] == "Ablöseforderung" and zeilen[z1]["betrag"] == von[z1]["transfer_fee"])
    pruefe("Zugang ohne Fee: Marktwert (Näherung)",
           zeilen[z2]["quelle"] == "Marktwert (Näherung)" and zeilen[z2]["betrag"] == von[z2]["value"])
    pruefe("Abgang ohne Fee: Marktwert (Näherung)", zeilen[a1]["quelle"] == "Marktwert (Näherung)")
    tr = re_["transfer"]
    pruefe("Saldo = Einnahmen - Ausgaben", tr["saldo"] == tr["einnahmen"] - tr["ausgaben"])
    z2p = next(p for p in re_["zugaenge"] if p["eid"] == z2)
    z1p = next(p for p in re_["zugaenge"] if p["eid"] == z1)
    pruefe("Stuermer-Zugang: Hinweis", z2p["stuermer"] and z2p["hinweis"]["art"] == "stuermer_wechsel")
    pruefe("IV-Zugang: kein Hinweis", not z1p["stuermer"] and z1p["hinweis"] is None)
    pruefe("Person: kalenderjahr falsch, stand gesetzt",
           z1p["kalenderjahr"] is False and z1p["stand"] == "2026-09-22T10:00:00")
    tb = api.tactic_board()
    unveraendert("tactic_board")
    pruefe("Ist = Taktikbrett (Elf)",
           {s["key"]: (s["stamm"] or {}).get("id") for s in li["brett"]["slots"]}
           == {s["key"]: s["startelf_id"] for s in tb["slots"]})
    pruefe("Ist = Taktikbrett (Meldeliste)", li["meldeliste"]["pl"] == tb["meldeliste"]["pl"])
    pruefe("Delta Elf markiert Aenderungen",
           all(e["geaendert"] == ((e["links"] or {}).get("id") != (e["rechts"] or {}).get("id"))
               for e in d["elf"]))

    # Abgang mit unserer Forderung
    a2 = next(e for e in kader if von[e]["position"] == "V (Z)")
    conn.execute("UPDATE export_players SET transfer_fee = 12000000 WHERE eid = ?", (a2,))
    conn.commit()
    r_f = api.planer_vergleich("Ist", {"abgaenge": [a2]})
    zf = next(z for z in r_f["rechts"]["transfer"]["zeilen"] if z["eid"] == a2)
    pruefe("Abgang mit Fee: unsere Forderung", zf["quelle"] == "unsere Forderung" and zf["betrag"] == 12e6)
    conn.execute("UPDATE export_players SET transfer_fee = NULL, value = NULL WHERE eid = ?", (a2,))
    conn.commit()
    r_u = api.planer_vergleich("Ist", {"abgaenge": [a2]})
    zu = next(z for z in r_u["rechts"]["transfer"]["zeilen"] if z["eid"] == a2)
    pruefe("ohne Fee und Marktwert: unbekannt", zu["quelle"] == "unbekannt" and zu["betrag"] is None)
    unveraendert("Abgangs-Rechnungen")

    print("== B Pins ==")
    r3 = api.planer_vergleich("Ist", {"abgaenge": [st_kader[0]]})
    pruefe("Pin auf Abgang: Warnung mit Slot-Label", any(w.startswith("Pin auf Sturmspitze") for w in r3["rechts"]["warnungen"]), str(r3["rechts"]["warnungen"]))
    pruefe("Szenario ohne st-Pin", "st" not in r3["rechts"]["brett"]["pins"])
    pruefe("Ist behaelt st-Pin", r3["links"]["brett"]["pins"].get("st") == st_kader[0])
    dm_fremd = next(p["eid"] for p in fremd if p["position"] == "DM, M (Z)")
    r4 = api.planer_vergleich("Ist", {"zugaenge": [dm_fremd], "pins": {"dmr": dm_fremd}})
    dmr = next(s for s in r4["rechts"]["brett"]["slots"] if s["key"] == "dmr")
    pruefe("Zugang pinnbar (neu, gepinnt)",
           dmr["stamm"]["id"] == dm_fremd and dmr["stamm"]["neu"] and dmr["gepinnt"], str(dmr["stamm"]))
    unveraendert("Pins")

    print("== B Warnungen ==")
    r5 = api.planer_vergleich("Ist", {"zugaenge": [999999, kader[0]], "abgaenge": [dm_fremd]})
    w = r5["rechts"]["warnungen"]
    pruefe("drei Warnungen", len(w) == 3, str(w))
    pruefe("kader_anzahl unveraendert", r5["rechts"]["kader_anzahl"] == len(kader))

    print("== B Speichern ==")
    pruefe("speichern", api.planer_speichern("Paket A", [z1, z2], [a1])["ok"])
    pruefe("„Ist“ abgelehnt", not api.planer_speichern("Ist", [], [])["ok"])
    pruefe("leerer Name abgelehnt", not api.planer_speichern("  ", [], [])["ok"])
    unveraendert("planer_speichern")
    l = api.planer_liste()
    pruefe("Liste zeigt Paket A", [s["name"] for s in l["szenarien"]] == ["Paket A"]
           and l["szenarien"][0]["veraltet"] is False)
    db.set_setting(conn, "squad_imported_at", "2026-12-31T10:00:00")
    pruefe("nach neuem Kader-Import veraltet", api.planer_liste()["szenarien"][0]["veraltet"] is True)
    db.set_setting(conn, "squad_imported_at", ref["squad_imported_at"])
    unveraendert("planer_liste")
    r6 = api.planer_vergleich("Ist", "Paket A")
    pruefe("Vergleich per Name = per Dict", r6["rechts"]["meldeliste"] == re_["meldeliste"]
           and r6["rechts"]["name"] == "Paket A")
    pruefe("A gegen B", api.planer_vergleich("Paket A", {"zugaenge": [z1]})["ok"])
    pruefe("unbekannter Name", "error" in api.planer_vergleich("Ist", "gibtsnicht"))
    pruefe("loeschen", api.planer_loeschen("Paket A")["ok"] and not api.planer_liste()["szenarien"])
    pruefe("loeschen unbekannt", not api.planer_loeschen("Paket A")["ok"])
    unveraendert("planer_loeschen")
    conn.close()
finally:
    shutil.rmtree(ordner, ignore_errors=True)

print()
print(f"{_bestanden} von {_gesamt} Prüfungen bestanden")
if _bestanden != _gesamt:
    sys.exit(1)
