"""Regressionsskript fuer den Rechen-Cache (D15) – ohne FM24, ohne echte DB.

Zwei Api-Instanzen auf derselben Wegwerf-DB: eine mit Cache, eine, die jedes
Mal neu rechnet (_rechen_cache_aus). Nach jeder Aenderung – Import in der App,
Import und SQL-Aenderung von aussen, Kohorte, Pins, Kader, Bezugsjahr,
Gewichte, Liga-Offset – muessen beide Seiten BITGLEICH sein, und die Aenderung
muss im Ergebnis ankommen (sonst beweist die Gleichheit nichts). Dazu:
der Cache wird wirklich benutzt, er schreibt nichts in die DB, und parallele
Aufrufe aus mehreren Threads liefern dieselben Zahlen.

Ausfuehren wie die anderen Skripte, kein pytest:
    .venv\\Scripts\\python.exe test_rechencache.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading

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


POS = ["TW", "V (Z)", "V (L)", "V (R)", "DM, M (Z)", "OM (L)", "OM (R)", "OM (Z)", "ST (Z)"]
rng = np.random.default_rng(7)


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


def kohorte(n, faktor=1.0):
    """Namenlose RAM-Zeilen fuer die Perzentile (wie cohort_save sie bekommt)."""
    out = []
    for i in range(n):
        m = float(rng.integers(600, 3200))
        st = i % 4 == 0
        out.append({"id": 900000 + i, "minutes": m, "goals": float(rng.integers(0, 15)),
                    "assists": float(rng.integers(0, 10)), "rating": float(rng.uniform(6.4, 7.3)),
                    "xg": float(rng.uniform(0, 12)) * (faktor if st else 1.0),
                    "xa": float(rng.uniform(0, 8)),
                    "duels": float(rng.integers(40, 250)), "duels_total": float(rng.integers(250, 450)),
                    "dribbles": float(rng.integers(0, 60)), "shots_total": float(rng.integers(0, 70)),
                    "shots_on": float(rng.integers(0, 30)), "pass_try": float(rng.integers(300, 2000)),
                    "pass_ok": float(rng.integers(200, 300)) * 5, "headers_total": 80.0,
                    "headers_won": float(rng.integers(10, 60)), "clearances": float(rng.integers(0, 90)),
                    "prog_passes": float(rng.integers(5, 150)), "recoveries": float(rng.integers(40, 250)),
                    "losses": float(rng.integers(50, 300)), "press_try": 200.0,
                    "press_win": float(rng.integers(20, 90)), "interceptions": float(rng.integers(5, 60)),
                    "key_passes": float(rng.integers(0, 50)), "apps": m // 85, "is_gk": 0.0,
                    "pos_mask": float(1 << 14) if st else float(1 << 8),
                    "birth_year": 2000.0, "birth_day": 100.0})
    return out


ordner = tempfile.mkdtemp(prefix="test_rechencache_")
try:
    pfad = os.path.join(ordner, "test.db")
    db.DEFAULT_PATH = pfad                   # Threads oeffnen ihre eigene Connection hierher
    conn = db.connect(pfad)
    db._init_cohort(conn)
    alle, kader = [], []
    eid = 1000
    for i, pos in enumerate(POS):
        for j in range(12):
            eid += 1
            if j < (3 if pos in ("V (Z)", "OM (L)", "OM (R)", "OM (Z)", "ST (Z)") else 2):
                club, hg = "Test FC", ("Ausgebildet im Land (15–21)" if j == 0 and i % 2 else None)
                kader.append(eid)
            else:
                club, hg = f"Verein {j}", ("Ausgebildet im Land (15–21)" if j == 5 else None)
            alle.append(spieler(eid, pos, club, hg, 30e6 if j == 7 else None))
    db.save_export(conn, alle, None, datei="syn", ts="2026-09-22T10:00:00")
    db.cohort_save(conn, kohorte(400))
    von = {p["eid"]: p for p in alle}
    st_kader = [e for e in kader if von[e]["position"] == "ST (Z)"]
    db.set_setting(conn, "squad_eids", json.dumps(sorted(kader)))
    db.set_setting(conn, "own_club", "Test FC")
    db.set_setting(conn, "squad_imported_at", "2026-09-22T11:00:00")
    db.set_setting(conn, "startelf_pins", json.dumps({"st": st_kader[0]}))

    mit = app.Api()
    mit._local.conn = conn
    ohne = app.Api()
    ohne._local.conn = db.connect(pfad)
    ohne._rechen_cache_aus = True
    fremd = db.connect(pfad)                 # "anderer Prozess": Import per Skript, SQL
    mit.set_cl_clubs([f"Verein {j}" for j in range(2, 10)])

    zugang = next(p["eid"] for p in alle if p["club"] == "Verein 4" and p["position"] == "ST (Z)")
    SZ = {"name": "Test", "zugaenge": [zugang], "abgaenge": [kader[1]]}
    st_id = st_kader[0]
    AUFRUFE = {
        "Brett": lambda a: a.tactic_board(),
        "Liga 450": lambda a: a.league_comparison(),
        "Liga 2000": lambda a: a.league_comparison(2000),
        "CL": lambda a: a.cl_comparison(),
        "Planer": lambda a: a.planer_vergleich("Ist", SZ),
        "Planer 900": lambda a: a.planer_vergleich("Ist", SZ, min_minutes=900),
        "Meldeliste": lambda a: a.registration(),
        # Ersatzsuche und Slot-Vergleich nur mit der aktuellen Suchmenge: die
        # Ersatzsuche merkt sich EINE Suchmenge, ein Wechsel auf alle=True
        # baut sie neu – dann bewiese die Runde nichts ueber den Cache.
        "Ersatz": lambda a: a.tactic_replacements("st", st_id, "besser"),
        "Ersatz juenger": lambda a: a.tactic_replacements("st", st_id, "juenger"),
        "Slot-Vergleich": lambda a: a.slot_compare("st", [zugang]),
    }

    def js(x):
        return json.dumps(x, sort_keys=True, default=str)

    def vergleich(titel):
        """Mit Cache (zweimal) gegen ohne Cache; gibt den Stand ohne Cache zurueck."""
        stand, abweichend = {}, []
        for name, f in AUFRUFE.items():
            m1, m2, o = js(f(mit)), js(f(mit)), js(f(ohne))
            if not (m1 == o and m2 == o):
                abweichend.append(name)
            stand[name] = o
        pruefe(f"{titel}: mit und ohne Cache bitgleich", not abweichend, ", ".join(abweichend))
        return stand

    def geaendert(titel, vorher, nachher, namen):
        anders = [n for n in namen if vorher[n] != nachher[n]]
        pruefe(f"{titel}: Aenderung kommt an ({', '.join(namen)})", anders == list(namen),
               f"unveraendert: {sorted(set(namen) - set(anders))}")

    # ------------------------------------------------ Grundzustand
    print(f"== Grundzustand: Export {len(alle)}, Kader {len(kader)}, Kohorte 400 ==")
    pruefe("Brett rechnet", mit.tactic_board().get("ok") is True)
    pruefe("CL-Vergleich rechnet", mit.cl_comparison().get("ok") is True)
    tabellen_vorher = [r[0] for r in fremd.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    settings_vorher = {r["key"]: r["value"] for r in fremd.execute("SELECT key, value FROM settings")}
    s0 = vergleich("Grundzustand")
    b1 = mit._basis(mit._db())
    mit.planer_vergleich("Ist", SZ)
    pruefe("Cache wird benutzt (dieselbe Basis beim naechsten Aufruf)", mit._basis(mit._db()) is b1)
    pruefe("Verteilungen einmal je Basis gerechnet",
           {"scores", "dna"} <= set(b1["verteilungen"]) and
           sum(1 for k in b1["verteilungen"] if isinstance(k, tuple)) >= 11,
           str(list(b1["verteilungen"])[:5]))
    pruefe("Ersatzsuche ueber alle Importe bitgleich",
           js(mit.tactic_replacements("st", st_id, "aehnlich", alle=True))
           == js(ohne.tactic_replacements("st", st_id, "aehnlich", alle=True)))
    mit.tactic_replacements("st", st_id, "besser")     # wieder die aktuelle Suchmenge
    pruefe("ohne Cache wird nichts aufgehoben", ohne._rechen_cache is None)
    pruefe("Cache schreibt keine Tabelle in die DB", tabellen_vorher == [r[0] for r in fremd.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")])
    pruefe("Cache schreibt keine Einstellung",
           settings_vorher == {r["key"]: r["value"] for r in fremd.execute("SELECT key, value FROM settings")})

    # ------------------------------------------------ Import von aussen (Skript)
    neu = [dict(von[st_id], goals=38, xg=33.0, minutes=3300)]
    db.save_export(fremd, neu, None, datei="skript.html", ts="2026-09-23T09:00:00")
    s1 = vergleich("Import von aussen (anderer Prozess)")
    geaendert("Import von aussen", s0, s1, ["Brett", "Planer", "Ersatz"])
    pruefe("Import von aussen: Basis neu aufgebaut", mit._basis(mit._db()) is not b1)

    # ------------------------------------------------ Import in der App (schmale Shortlist)
    # Nur Persoenlichkeit, keine Minuten: imported_at rueckt nicht vor, die
    # Zeilenzahl bleibt – genau der Fall, den der alte Schluessel der
    # Ersatzsuche (Zeilenzahl + neuester Import) nicht bemerkte.
    html = ("<table><tr><th>EID</th><th>Name</th><th>Persönlichkeit</th></tr>"
            + "".join(f"<tr><td>{e}</td><td>Spieler {e}</td><td>Perfektionist</td></tr>"
                      for e in st_kader) + "</table>")
    datei = os.path.join(ordner, "shortlist.html")
    with open(datei, "w", encoding="utf-8") as f:
        f.write(html)
    r = mit._import_files([datei])
    pruefe("Shortlist eingelesen", r.get("ok") is True, str(r.get("error")))
    s2 = vergleich("Import in der App (nur Persoenlichkeit)")
    geaendert("Import in der App", s1, s2, ["Brett", "Planer", "Ersatz"])

    # ------------------------------------------------ SQL-Aenderung von aussen
    fremd.execute("UPDATE export_players SET transfer_fee = 55000000, rating = 7.9 WHERE eid = ?",
                  (kader[1],))
    fremd.commit()
    s3 = vergleich("SQL-Aenderung von aussen")
    geaendert("SQL-Aenderung", s2, s3, ["Planer"])

    # ------------------------------------------------ Kohorte (Scan)
    db.cohort_save(fremd, kohorte(400, faktor=3.0))
    s4 = vergleich("Neue Kohorte")
    geaendert("Neue Kohorte", s3, s4, ["Brett", "Liga 450"])

    # ------------------------------------------------ Pins, Kader, min_minutes
    b_vor = mit._basis(mit._db())
    mit.set_pin("st", st_kader[1])
    s5 = vergleich("Pin gesetzt")
    geaendert("Pin", s4, s5, ["Brett", "Planer"])
    pruefe("Pin: Basis bleibt (Pins stecken nicht im Cache)", mit._basis(mit._db()) is b_vor)
    db.set_setting(fremd, "squad_eids", json.dumps(sorted(set(kader) - {kader[-1]})))
    s6 = vergleich("Kader von aussen geaendert")
    geaendert("Kader", s5, s6, ["Brett", "Meldeliste"])
    pruefe("min_minutes wirkt (Liga 450 gegen 2000)", s6["Liga 450"] != s6["Liga 2000"])

    # ------------------------------------------------ Bezugsjahr
    db.set_setting(fremd, "season_year", 2031)
    s7 = vergleich("Bezugsjahr geaendert")
    geaendert("Bezugsjahr", s6, s7, ["Meldeliste"])
    db.set_setting(fremd, "season_year", 0)
    s7 = vergleich("Bezugsjahr zurueck")
    pruefe("Bezugsjahr zurueck: wieder die Zahlen von vorher", s7 == s6,
           str([n for n in s7 if s7[n] != s6[n]]))

    # ------------------------------------------------ Konstanten zur Laufzeit
    slot = next(s for s in tactics.FORMATION if s["key"] == "st")
    alt_gew = dict(slot["gewichte"])
    k = sorted(alt_gew)
    slot["gewichte"] = {**alt_gew, k[0]: alt_gew[k[1]], k[1]: alt_gew[k[0]]}
    s8 = vergleich("Slot-Gewichte getauscht")
    geaendert("Slot-Gewichte", s7, s8, ["Brett"])
    slot["gewichte"] = alt_gew
    alt_off = dict(moneyball.LEAGUE_NOTE_OFFSET)
    moneyball.LEAGUE_NOTE_OFFSET["Premier League"] = 0.6
    s9 = vergleich("Liga-Offset geaendert")
    geaendert("Liga-Offset", s7, s9, ["Brett"])
    moneyball.LEAGUE_NOTE_OFFSET.clear()
    moneyball.LEAGUE_NOTE_OFFSET.update(alt_off)
    s10 = vergleich("Konstanten zurueckgesetzt")
    pruefe("Konstanten zurueck: wieder die Zahlen von vorher", s10 == s7,
           str([n for n in s10 if s10[n] != s7[n]]))

    # ------------------------------------------------ Threads
    mit._rechen_neu()                         # kalt starten, alle bauen gleichzeitig
    soll = {"Brett": s10["Brett"], "Planer": s10["Planer"], "Liga 450": s10["Liga 450"]}
    ergebnisse, fehler = [], []

    def lauf(name):
        try:
            ergebnisse.append((name, js(AUFRUFE[name](mit))))
        except Exception as e:           # noqa: BLE001 – jeder Fehler zaehlt
            fehler.append(f"{name}: {e!r}")

    threads = [threading.Thread(target=lauf, args=(n,)) for n in list(soll) * 3]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    pruefe("Threads: keine Ausnahme", not fehler, "; ".join(fehler))
    pruefe("Threads: alle Ergebnisse gleich ohne Cache",
           len(ergebnisse) == 9 and all(soll[n] == x for n, x in ergebnisse),
           str([n for n, x in ergebnisse if soll[n] != x]))

    for c in (conn, ohne._local.conn, fremd):
        c.close()
finally:
    shutil.rmtree(ordner, ignore_errors=True)

print(f"\n{_bestanden} von {_gesamt} Prüfungen bestanden")
sys.exit(0 if _bestanden == _gesamt else 1)
