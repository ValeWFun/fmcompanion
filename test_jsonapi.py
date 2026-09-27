"""Regressionsskript: jede lesende js_api-Antwort muss sich serialisieren lassen –
ohne FM24, ohne echte DB, auf einer Wegwerf-DB.

pywebview gibt Rueckgabewerte von js_api-Aufrufen VOLLSTAENDIG als JSON an die
Oberflaeche. Ein set, ein Objekt oder ein NaN irgendwo in einer Zeile laesst den
ganzen Aufruf scheitern – im September 2026 lud die App dadurch keinen
einzigen Spieler (ein set als `_gruppen` an jeder Tabellenzeile; der
Unterstrich schuetzt nur Attribute des js_api-Objekts, keine Dict-Schluessel).
Die Vorschau des Designers serialisiert mit default=str und sah davon nichts.

Geprueft wird deshalb mit json.dumps OHNE default= und mit allow_nan=False,
dazu fachliche Kleinigkeiten, die nur in echten Zeilen auffallen:
- keine Tabellenzeile mit Export-Position hat POS "?" (RAM-Maske 0)

Die Wegwerf-DB hat Export-Zeilen (aktuell und aelter), einen RAM-Snapshot mit
Export-Spielern (Export gewinnt / RAM gewinnt, Maske 0), reine RAM-Zeilen und
eine Kohorte – so laufen alle Zweige von load_saved.

Vor JEDEM Merge nach main laufen lassen: main ist die App des Nutzers.
    .venv\\Scripts\\python.exe test_jsonapi.py
"""
import json
import math
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from fmcompanion import db, moneyball
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


rng = np.random.default_rng(22)
POSITIONEN = ["TW", "V (Z)", "V (L)", "V (R)", "V (RZ)", "DM, M (Z)", "M (Z)", "M (L)",
              "M (R)", "OM (L)", "OM (R)", "OM (Z)", "OM (RL), ST (Z)", "M/OM (L)", "ST (Z)",
              "DM, M (Z), OM (RZ)"]
LIGEN = ["Premier League", "Bundesliga", "Serie A", "Spaniens First Division",
         "Ligue 1 Uber Eats", "Eredivisie", "Portugals Premier League"]


def statistik(tw, m):
    r = lambda a, b: float(rng.uniform(a, b))
    dt, pt, ht, prt, st = int(r(40, 400)), int(r(300, 2500)), int(r(20, 250)), int(r(30, 300)), int(r(0, 80))
    conc = int(r(15, 60)) if tw else 0
    return {"minutes": m, "goals": 0 if tw else int(r(0, 20)), "assists": 0 if tw else int(r(0, 12)),
            "xg": 0.0 if tw else r(0, 15), "xa": 0.0 if tw else r(0, 10),
            "rating": round(r(6.5, 7.4), 2), "apps": m // 85,
            "duels": int(dt * r(0.4, 0.7)), "duels_total": dt,
            "shots_total": st, "shots_on": int(st * r(0.2, 0.6)),
            "pass_try": pt, "pass_ok": int(pt * r(0.7, 0.92)),
            "dribbles": int(r(0, 80)), "prog_passes": int(r(10, 200)),
            "press_win": int(prt * r(0.2, 0.5)), "press_try": prt,
            "interceptions": int(r(5, 80)), "key_passes": int(r(0, 60)),
            "clearances": int(r(0, 120)), "headers_won": int(ht * r(0.3, 0.7)),
            "headers_total": ht, "losses": int(r(50, 400)), "recoveries": int(r(50, 300)),
            "conceded": conc, "xga": (conc + r(-5, 5)) if tw else None}


def zeile(eid, pos, club, liga):
    m = int(rng.integers(900, 3400))
    tw = pos == "TW"
    z = {"eid": eid, "name": f"Spieler {eid}", "position": pos, "age": int(rng.integers(18, 33)),
         "club": club, "league": liga, "value": float(int(rng.integers(5, 80)) * 1e6),
         "pen_goals": 0, "chances": int(rng.integers(0, 40)), "long_goals": 0,
         "blocks": int(rng.integers(0, 30)), "errors": int(rng.integers(0, 3)),
         "crosses_ok": 10, "crosses_try": 40, "sprints": 200,
         "saves_tipped": 20 if tw else None, "saves_parried": 20 if tw else None,
         "saves_held": 20 if tw else None, "personality": "Perfektionist" if eid % 3 else None,
         "foot": "Rechts", "height": 183}
    z.update(statistik(tw, m))
    return z


def ram(pid, eid, minuten, maske, tw=False):
    r = {"id": pid, "name": f"RAM {pid}", "eid": eid, "pos_mask": maske, "is_gk": 1.0 if tw else 0.0,
         "birth_year": 2000.0, "birth_day": 100.0, "fouls": 10, "fouls_against": 12, "yellow": 2}
    r.update(statistik(tw, minuten))
    return r


def js(x):
    """Wie pywebview: ohne default=; NaN/Infinity waeren im Browser kein JSON."""
    return json.dumps(x, allow_nan=False)


def fragezeichen(zeilen):
    return [(p.get("name"), p.get("position"), p.get("source")) for p in zeilen
            if (p.get("position") or "").strip() and p.get("pos_label") == "?"]


ordner = tempfile.mkdtemp(prefix="test_jsonapi_")
try:
    conn = db.connect(os.path.join(ordner, "test.db"))
    db._init_cohort(conn)
    aktuell = [zeile(5000 + i, pos, "Test FC" if i < 26 else f"Verein {i % 12}", LIGEN[i % len(LIGEN)])
               for i, pos in enumerate(POSITIONEN * 10)]
    alt = [zeile(6000 + i, pos, "Alter FC", "Eredivisie") for i, pos in enumerate(POSITIONEN)]
    db.save_export(conn, alt, None, datei="alt.html", ts="2026-09-01T10:00:00")
    db.save_export(conn, aktuell, None, datei="syn.html", ts="2026-09-26T10:00:00")
    # RAM-Snapshot: Export-Spieler mit Maske 0 (Export gewinnt / RAM gewinnt),
    # reine RAM-Zeilen mit und ohne Maske
    snap = []
    for i, e in enumerate(aktuell[:40]):
        tw = e["position"] == "TW"
        snap.append(ram(100000 + i, e["eid"], e["minutes"] + (500 if i % 2 else -100), 0, tw))
    for i in range(30):
        snap.append(ram(200000 + i, None, 1500, 1 << 14 if i % 2 else 1 << 8))
    snap.append(ram(299999, None, 1200, 0))
    db.save_snapshot(conn, snap)
    koh = [dict(ram(900000 + i, None, 1800, 1 << 14 if i % 3 else 1 << 8)) for i in range(200)]
    db.cohort_save(conn, koh)
    kader = [e["eid"] for e in aktuell if e["club"] == "Test FC"]
    db.set_setting(conn, "squad_eids", json.dumps(kader))
    db.set_setting(conn, "own_club", "Test FC")
    db.set_setting(conn, "squad_imported_at", "2026-09-26T11:00:00")
    api = app.Api()
    api._local.conn = conn
    api.set_cl_clubs([f"Verein {j}" for j in range(2, 10)])

    tb = api.tactic_board()
    pruefe("Brett rechnet", tb.get("ok") is True, str(tb.get("error")))
    st = next(s for s in tb["slots"] if s["key"] == "st")
    fremd = next(e["eid"] for e in aktuell if e["club"] != "Test FC" and e["position"] == "ST (Z)")
    szenario = {"name": "T", "zugaenge": [fremd], "abgaenge": [kader[1]]}
    aufrufe = {
        "get_settings": lambda: api.get_settings(),
        "coverage": lambda: api.coverage(),
        "auto_status": lambda: api.auto_status(),
        "load_saved last": lambda: api.load_saved("last"),
        "load_saved pool": lambda: api.load_saved("pool"),
        "value_history": lambda: api.value_history(fremd),
        "seasons": lambda: api.seasons(100001),
        "history": lambda: api.history(100001),
        "signals": lambda: api.signals(),
        "export_clubs": lambda: api.export_clubs(),
        "tactic_board": lambda: api.tactic_board(),
        "registration": lambda: api.registration(),
        "league_comparison": lambda: api.league_comparison(),
        "cl_clubs": lambda: api.cl_clubs(),
        "cl_comparison": lambda: api.cl_comparison(),
        "planer_liste": lambda: api.planer_liste(),
        "planer_vergleich": lambda: api.planer_vergleich("Ist", szenario),
        "archetypes": lambda: api.archetypes("st"),
        "tactic_replacements besser": lambda: api.tactic_replacements("st", st["startelf_id"], "besser"),
        "tactic_replacements alle": lambda: api.tactic_replacements("st", st["startelf_id"], "aehnlich",
                                                                    alle=True),
        "slot_compare": lambda: api.slot_compare("st", [fremd, 200001]),
        "watchlist": lambda: api.watchlist(),
        "listen_vergleich": lambda: api.listen_vergleich(
            [{"id": e["eid"], "score": 60 + i, "minutes": e["minutes"], "profil": "st"}
             for i, e in enumerate(aktuell[:5])]),
        "vergleich_konstanten": lambda: api.vergleich_konstanten(),
        "rankings": lambda: api.rankings(),
        "load_export": lambda: api.load_export(),
        "export_rankings": lambda: api.export_rankings(),
        "card_all": lambda: api.card_all(),
    }
    if hasattr(api, "referenz_status"):
        aufrufe["referenz_status"] = lambda: api.referenz_status()

    print(f"== {len(aufrufe)} lesende js_api-Aufrufe, json.dumps ohne default ==")
    antworten = {}
    for name, f in aufrufe.items():
        try:
            r = f()
        except Exception as e:           # noqa: BLE001 – jeder Fehler zaehlt
            pruefe(f"{name}: laeuft", False, f"{type(e).__name__}: {e}")
            continue
        try:
            js(r)
            pruefe(f"{name}: serialisierbar", True)
        except (TypeError, ValueError) as e:
            pruefe(f"{name}: serialisierbar", False, f"{type(e).__name__}: {e}")
        antworten[name] = r

    print("== fachliche Pruefungen an den Zeilen ==")
    for modus in ("last", "pool"):
        ls = antworten.get(f"load_saved {modus}") or {}
        zeilen = ls.get("players") or []
        pruefe(f"load_saved {modus}: alle Zweige vorhanden (ram, ram+export, export)",
               {"ram", "ram+export", "export"} <= {p.get("source") for p in zeilen},
               str({p.get("source") for p in zeilen}))
        fz = fragezeichen(zeilen)
        pruefe(f"load_saved {modus}: keine Zeile mit Export-Position hat POS '?'", not fz, str(fz[:3]))
    zeilen = (antworten.get("load_saved last") or {}).get("players") or []
    ohne = [p for p in zeilen if p.get("id") == 299999]
    pruefe("reine RAM-Zeile ohne Maske und ohne Position bleibt '?'",
           ohne and ohne[0].get("pos_label") == "?")
    pruefe("keine Zeile traegt interne Merk-Schluessel (_gruppen)",
           not any("_gruppen" in p for p in zeilen))
    nan = [(p.get("name"), k) for p in zeilen for k, v in p.items()
           if isinstance(v, float) and not math.isfinite(v)]
    pruefe("keine NaN/Infinity in Tabellenzeilen", not nan, str(nan[:3]))

    print("== Feldliste der Tabelle (D23) ==")
    felder = set(app.Api.TABELLEN_FELDER)
    rang = ({k for k, _ in moneyball.RANKINGS.values()}
            | {k for k, _ in moneyball.EXPORT_RANKINGS.values()})
    pruefe("TABELLEN_FELDER ohne Doppelte", len(felder) == len(app.Api.TABELLEN_FELDER))
    pruefe("jede Rangliste sortiert nach einem gelieferten Feld",
           rang <= app.Api._tabellen_schluessel())
    for modus in ("last", "pool"):
        zeilen = (antworten.get(f"load_saved {modus}") or {}).get("players") or []
        fremd_felder = {k for p in zeilen for k in p} - app.Api._tabellen_schluessel()
        pruefe(f"load_saved {modus}: nur Felder aus der Feldliste", zeilen and not fremd_felder,
               str(sorted(fremd_felder)[:8]))
    ls = antworten.get("load_saved last") or {}
    pruefe("Feldliste laesst nichts weg, was die Zeile hat und die Tabelle liest",
           all(k in p for p in ls.get("players", []) for k in ("score", "aktuell", "profile_erlaubt",
                                                                  "pos_label", "score_je_profil")))
    pruefe("load_saved: referenz mit Grenze des aktuellen Stands, snapshot_stand gesetzt",
           ls.get("referenz", {}).get("stand") == api._pool_stand(conn)
           and ls.get("snapshot_stand") == db.latest_snapshot_at(conn) is not None
           and (antworten.get("tactic_board") or {}).get("referenz", {}).get("stand")
           == api._pool_stand(conn))
    zwei = api.load_saved("last")
    pruefe("die Feldliste arbeitet auf Kopien: zweiter Aufruf liefert dasselbe",
           js(zwei["players"]) == js(ls["players"]))
    conn.close()
finally:
    shutil.rmtree(ordner, ignore_errors=True)

print(f"\n{_bestanden} von {_gesamt} Prüfungen bestanden")
sys.exit(0 if _bestanden == _gesamt else 1)
