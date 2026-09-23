"""Regressionsskript fuer Import-Historie, Fair Value und Abloese/Eigengewaechs
(D0-D2). Laeuft ohne FM24, ohne echte DB und ohne Savegame-Dateien.

Alle Eingaben sind synthetisch: kleine HTML-Exporte als Strings, geschrieben in
ein Wegwerf-Verzeichnis, dazu eine DB darin (db.connect(<tempdir>/...)). Die
echte fmcompanion.db wird nicht angefasst, auch nicht lesend. app.py wird nicht
importiert (zieht pywebview), nur db, importer und valuemodel.

Prueffaelle:
1. Voller Import gleich altem Verfahren (parse_export -> load_export)
2. Schmale Datei aendert nur ihre Felder, bekannte Persoenlichkeit bleibt
3. Zwei Staende derselben EID: Verlauf, export_stand_bis, load_export
4. bestand_sichern (Altbestand ohne Historie) und der Fallback in save_export
5. Fair Value: Urteilszonen, Modell auf synthetischer Trainingsmenge
6. D2-Parsing: Abloeseforderung und Eigengewaechs-Status

Ausfuehren wie test_importer.py, kein pytest (Arbeitsordner = Projektordner):
    .venv\\Scripts\\python.exe test_import_historie.py
"""
import math
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from fmcompanion import db, importer, valuemodel

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


def html(spalten, zeilen):
    """Mini-Export im FM-Format: <table><tr><th>..</th></tr><tr><td>..</td></tr></table>."""
    kopf = "<tr>" + "".join(f"<th>{s}</th>" for s in spalten) + "</tr>"
    rumpf = "".join("<tr>" + "".join(f"<td>{z}</td>" for z in zeile) + "</tr>"
                    for zeile in zeilen)
    return f"<table>{kopf}{rumpf}</table>"


def schreibe(ordner, name, inhalt):
    pfad = os.path.join(ordner, name)
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(inhalt)
    return pfad


def frische_db(ordner, name):
    return db.connect(os.path.join(ordner, name))


def zeilen(conn):
    return {r["eid"]: r for r in db.load_export(conn)}


def unterschiede(a, b, spalten):
    """Spalten, in denen sich zwei Zeilen unterscheiden (Floats per isclose)."""
    out = []
    for c in spalten:
        x, y = a.get(c), b.get(c)
        if isinstance(x, float) and isinstance(y, float):
            if not math.isclose(x, y):
                out.append(c)
        elif x != y:
            out.append(c)
    return out


def einfuegen_alt(conn, spieler, ts):
    """Zeilen DIREKT in export_players schreiben, wie eine DB aus der Zeit vor der Historie."""
    cols = ["eid", "imported_at"] + db.EXPORT_COLS
    conn.executemany(
        f"INSERT INTO export_players ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [[p["eid"], ts] + [p.get(c) for c in db.EXPORT_COLS] for p in spieler])
    conn.commit()


SPALTEN_VOLL = ["EID", "Name", "Position", "Alter", "Verein", "Transferwert",
                "Min.", "Tore", "Ø Note", "Persönlichkeit"]
ZEILEN_VOLL = [
    [101, "Anton Alt", "M (Z)", 27, "Testverein", "€17.5Mio", "2,198", 7, "6.54", "Perfektionist"],
    [102, "Bruno Beta", "V (R)", 23, "Testverein", "€850Tsd", "1,040", 1, "6.81", "Konsequent"],
    [103, "Carlo Camp", "ST (Z)", 31, "Anderer FC", "€157Mio - €189Mio", "2,900", 21, "7.42", "Scouting erforderlich"],
    [104, "Dario Dorn", "TW", 29, "Anderer FC", "€3,5Mio", "3,060", 0, "6.90", "Ausgeglichen"],
    [105, "Egon Eck", "OM (RLZ)", 19, "Testverein", "-", "310", 2, "6.20", "Ehrgeizig"],
]


def fall_1(ordner):
    print("== 1. Voller Import gleich altem Verfahren ==")
    conn = frische_db(ordner, "voll.db")
    pfad = schreibe(ordner, "voll.html", html(SPALTEN_VOLL, ZEILEN_VOLL))
    spieler, felder = importer.parse_export_felder(pfad)
    alt = {p["eid"]: p for p in importer.parse_export(pfad)}
    pruefe("Anzahl geparster Spieler", len(spieler), 5)
    db.save_export(conn, spieler, felder, datei="voll.html", ts="2026-01-01T10:00:00")
    neu = zeilen(conn)
    pruefe("Anzahl gespeicherter Spieler", len(neu), 5)
    # Nur Felder, die die Datei enthielt, werden gespeichert. Der Parser fuellt
    # Ersatzwerte fuer fehlende Spalten (pen_goals 0, losses/recoveries 0), die
    # das alte INSERT OR REPLACE mitschrieb, save_export aber bewusst nicht.
    abw_datei = [(e, c) for e, p in alt.items()
                 for c in unterschiede(neu[e], p, [f for f in db.EXPORT_COLS if f in felder])]
    pruefe("alle Felder der Datei identisch mit parse_export", abw_datei, [])
    abw_fremd = [(e, c) for e in alt for c in db.EXPORT_COLS
                 if c not in felder and neu[e].get(c) is not None]
    pruefe("Felder ausserhalb der Datei bleiben leer (NULL)", abw_fremd, [])
    abw_alle = sorted({c for e, p in alt.items()
                       for c in unterschiede(neu[e], p, db.EXPORT_COLS)})
    print(f"   (Info) Felder, in denen load_export von parse_export abweicht: {abw_alle}")
    pruefe("Persönlichkeit 'Scouting erforderlich' wird None", neu[103]["personality"], None)
    pruefe("Marktwert mit Nachkomma (€17.5Mio)", neu[101]["value"], 17500000)
    pruefe("Minuten mit Tausendertrenner (2,198)", neu[101]["minutes"], 2198)
    pruefe("imported_at = ts (Datei hat Minuten)", neu[101]["imported_at"], "2026-01-01T10:00:00")
    conn.close()


def fall_2(ordner):
    print("== 2. Schmale Datei aendert nur ihre Felder ==")
    conn = frische_db(ordner, "schmal.db")
    pfad = schreibe(ordner, "voll2.html", html(SPALTEN_VOLL, ZEILEN_VOLL))
    spieler, felder = importer.parse_export_felder(pfad)
    db.save_export(conn, spieler, felder, datei="voll2.html", ts="2026-01-01T10:00:00")
    vorher = zeilen(conn)

    schmal = [
        [101, "Anton Alt-Neu", 9, "Verantwortungsvoll"],   # Name, Tore, neue Persönlichkeit
        [102, "Bruno Beta", 2, "Konsequent"],
        [103, "Carlo Camp", 25, "Scouting erforderlich"],  # bekannter Wert fehlt hier: keine
        [105, "Egon Eck", 5, "Scouting erforderlich"],     # Persönlichkeit vorher "Ehrgeizig"
    ]
    pfad2 = schreibe(ordner, "schmal.html", html(["EID", "Name", "Tore", "Persönlichkeit"], schmal))
    sp2, fe2 = importer.parse_export_felder(pfad2)
    pruefe("erkannte Felder der schmalen Datei", sorted(fe2), ["goals", "name", "personality"])
    ts2 = "2026-02-01T09:00:00"
    db.save_export(conn, sp2, fe2, datei="schmal.html", ts=ts2)
    nachher = zeilen(conn)

    geaendert = set()
    for e in vorher:
        geaendert |= set(unterschiede(vorher[e], nachher[e],
                                      db.EXPORT_COLS + ["imported_at"] + db.STAND_COLS))
    pruefe("nur name/goals/personality/pers_stand geaendert",
           sorted(geaendert), ["goals", "name", "pers_stand", "personality"])
    pruefe("Tore uebernommen", [nachher[e]["goals"] for e in (101, 102, 103, 105)], [9, 2, 25, 5])
    pruefe("Name uebernommen", nachher[101]["name"], "Anton Alt-Neu")
    pruefe("neue Persönlichkeit uebernommen", nachher[101]["personality"], "Verantwortungsvoll")
    pruefe("imported_at bleibt (keine Minuten in der Datei)",
           [nachher[e]["imported_at"] for e in vorher], [vorher[e]["imported_at"] for e in vorher])
    pruefe("bekannte Persönlichkeit bleibt bei 'Scouting erforderlich'",
           nachher[105]["personality"], "Ehrgeizig")
    pruefe("Spieler ohne Zeile in der schmalen Datei unveraendert",
           unterschiede(vorher[104], nachher[104], db.EXPORT_COLS + ["imported_at"] + db.STAND_COLS), [])
    pruefe("pers_stand gesetzt bei neuer Persönlichkeit", nachher[101]["pers_stand"], ts2)
    pruefe("pers_stand bleibt bei 'Scouting erforderlich'",
           nachher[105]["pers_stand"], vorher[105]["pers_stand"])
    v = db.export_verlauf(conn, 101)
    pruefe("Verlauf: zwei Staende", [x["datei"] for x in v], ["voll2.html", "schmal.html"])
    pruefe("schmaler Stand speichert fehlende Felder als NULL",
           (v[-1]["minutes"], v[-1]["goals"]), (None, 9))
    conn.close()


def fall_3(ordner):
    print("== 3. Zwei Staende derselben EID ==")
    conn = frische_db(ordner, "zwei.db")
    p_alt = schreibe(ordner, "sommer.html", html(SPALTEN_VOLL, ZEILEN_VOLL))
    neue_zeilen = [list(z) for z in ZEILEN_VOLL]
    for z in neue_zeilen:
        z[6] = "1,111"                     # andere Minuten
        z[7] = 3                           # andere Tore
    p_neu = schreibe(ordner, "winter.html", html(SPALTEN_VOLL, neue_zeilen))
    sp_alt, fe_alt = importer.parse_export_felder(p_alt)
    sp_neu, fe_neu = importer.parse_export_felder(p_neu)
    db.save_export(conn, sp_alt, fe_alt, datei="sommer.html", ts="2026-01-01T10:00:00")
    db.save_export(conn, sp_neu, fe_neu, datei="winter.html", ts="2026-06-01T10:00:00")
    verlauf = db.export_verlauf(conn, 101)
    pruefe("export_verlauf: 2 Eintraege", len(verlauf), 2)
    pruefe("export_verlauf: richtige Minuten", [v["minutes"] for v in verlauf], [2198, 1111])
    bis = {r["eid"]: r for r in db.export_stand_bis(conn, "2026-03-01")}
    pruefe("export_stand_bis(2026-03-01): alte Minuten", bis[101]["minutes"], 2198)
    pruefe("export_stand_bis(2026-03-01): alle Spieler alt",
           sorted((e, r["minutes"]) for e, r in bis.items()),
           sorted((p["eid"], p["minutes"]) for p in sp_alt))
    pruefe("load_export: neue Minuten", zeilen(conn)[101]["minutes"], 1111)
    pruefe("export_stand_bis hat die Form von load_export",
           sorted(bis[101]), sorted(zeilen(conn)[101]))
    conn.close()


def fall_4(ordner):
    print("== 4. bestand_sichern (Altbestand ohne Historie) ==")
    pfad = schreibe(ordner, "alt.html", html(SPALTEN_VOLL, ZEILEN_VOLL))
    spieler = importer.parse_export(pfad)

    conn = frische_db(ordner, "alt.db")
    db._init_export(conn)
    einfuegen_alt(conn, spieler, "2026-01-01T10:00:00")
    vorher = zeilen(conn)
    pruefe("App-Start legt keine Historie an", db._hat_tabelle(conn, "export_importe"), False)
    n = db.bestand_sichern(conn)
    pruefe("bestand_sichern liefert die Zeilenzahl", n, 5)
    pruefe("zweiter Aufruf liefert 0", db.bestand_sichern(conn), 0)
    nachher = zeilen(conn)
    ohne_stand = [c for c in db.EXPORT_COLS + ["imported_at"]]
    pruefe("export_players bis auf pers_stand unveraendert",
           [(e, c) for e in vorher for c in unterschiede(vorher[e], nachher[e], ohne_stand)], [])
    pruefe("Historie hat einen Eintrag mit BESTAND_DATEI",
           [i["datei"] for i in db.export_importe(conn)], [db.BESTAND_DATEI])
    bis = {r["eid"]: r for r in db.export_stand_bis(conn, "2026-02-01")}
    pruefe("export_stand_bis liefert den Altbestand",
           [(e, c) for e in vorher for c in unterschiede(bis[e], vorher[e], db.EXPORT_COLS)], [])
    conn.close()

    print("== 4b. Fallback: erster Import ohne vorheriges bestand_sichern ==")
    conn = frische_db(ordner, "fallback.db")
    db._init_export(conn)
    einfuegen_alt(conn, spieler, "2026-01-01T10:00:00")
    neue = [list(z) for z in ZEILEN_VOLL]
    for z in neue:
        z[6] = "500"
    sp2, fe2 = importer.parse_export_felder(schreibe(ordner, "neu.html", html(SPALTEN_VOLL, neue)))
    db.save_export(conn, sp2, fe2, datei="neu.html", ts="2026-06-01T10:00:00")
    pruefe("Altbestand vor dem ersten Import gesichert",
           [i["datei"] for i in db.export_importe(conn)], [db.BESTAND_DATEI, "neu.html"])
    alt = {r["eid"]: r for r in db.export_stand_bis(conn, "2026-03-01")}
    pruefe("alte Minuten nach dem Import noch abrufbar",
           sorted((e, r["minutes"]) for e, r in alt.items()),
           sorted((p["eid"], p["minutes"]) for p in spieler))
    conn.close()


def fall_5():
    print("== 5a. valuemodel.urteil: je Zone ein Beispiel (sigma = 1.0) ==")
    for ln, soll in [(0.2, "marktgerecht"), (0.7, "leicht unterbewertet"),
                     (-0.7, "leicht überbewertet"), (1.5, "deutlich unterbewertet"),
                     (-1.5, "deutlich überbewertet")]:
        pruefe(f"urteil({ln}, 1.0)", valuemodel.urteil(ln, 1.0), soll)

    print("== 5b. Fair Value auf synthetischer Trainingsmenge ==")
    rng = np.random.default_rng(7)
    rows = []
    for i in range(300):
        rating = float(rng.uniform(6.3, 7.6))
        goals = int(rng.integers(0, 15))
        assists = int(rng.integers(0, 12))
        pass_try = int(rng.integers(400, 1800))
        duels_total = int(rng.integers(100, 500))
        ln_wert = 15.0 + 1.8 * (rating - 6.95) + 0.06 * goals + float(rng.normal(0, 0.35))
        rows.append({
            "eid": 1000 + i, "name": f"Spieler {i}", "position": "M (Z)", "league": None,
            "age": int(rng.integers(18, 34)), "minutes": int(rng.integers(900, 3001)),
            "rating": rating, "goals": goals, "assists": assists,
            "xg": goals * float(rng.uniform(0.7, 1.3)), "xa": assists * float(rng.uniform(0.7, 1.3)),
            "prog_passes": int(rng.integers(20, 300)), "press_win": int(rng.integers(10, 200)),
            "interceptions": int(rng.integers(5, 120)), "key_passes": int(rng.integers(2, 80)),
            "clearances": int(rng.integers(0, 150)),
            "pass_try": pass_try, "pass_ok": int(pass_try * float(rng.uniform(0.7, 0.92))),
            "duels_total": duels_total, "duels": int(duels_total * float(rng.uniform(0.4, 0.65))),
            "value": math.exp(ln_wert),
        })
    basis = dict(rows[0], value=5_000_000.0)
    torwart = dict(basis, eid=2001, name="Torwart", position="TW")
    ohne_min = dict(basis, eid=2002, name="Ohne Minuten", minutes=0)
    ohne_note = dict(basis, eid=2003, name="Ohne Note", rating=None)
    vals, model = valuemodel.fair_values(rows + [torwart, ohne_min, ohne_note])
    pruefe("Modell vorhanden", model is not None, True)
    if model is None:
        return
    pruefe("model.usable", model.usable, True)
    pruefe("Torwart fehlt in vals", 2001 in vals, False)
    pruefe("Spieler ohne Minuten fehlt in vals", 2002 in vals, False)
    pruefe("Spieler ohne Note fehlt in vals", 2003 in vals, False)
    pruefe("alle 300 Feldspieler bewertet", len(vals), 300)
    pruefe("jeder Eintrag hat fair_urteil und fair_text",
           [e for e, v in vals.items() if not v.get("fair_urteil") or not v.get("fair_text")], [])
    falsch_unter = [e for e, v in vals.items()
                    if "unterbewertet" in v["fair_urteil"] and not v["value_delta_pct"] > 0]
    falsch_ueber = [e for e, v in vals.items()
                    if "überbewertet" in v["fair_urteil"] and not v["value_delta_pct"] < 0]
    pruefe("Vorzeichen: 'unterbewertet' hat value_delta_pct > 0", falsch_unter, [])
    pruefe("Vorzeichen: 'überbewertet' hat value_delta_pct < 0", falsch_ueber, [])
    pruefe("Urteile kommen in allen drei Richtungen vor",
           sorted({("unter" if "unterbewertet" in v["fair_urteil"]
                    else "ueber" if "überbewertet" in v["fair_urteil"] else "gerecht")
                   for v in vals.values()}), ["gerecht", "ueber", "unter"])
    status = model.status()
    pruefe("status() hat sigma, streuung_faktor und text",
           [k for k in ("sigma", "streuung_faktor", "text") if status.get(k) is None], [])


def fall_6(ordner):
    print("== 6. D2-Parsing: Abloeseforderung und Eigengewaechs ==")
    spalten = ["EID", "Name", "Ablöseforderung", "Status Eigengewächs"]
    zeilen_d2 = [
        [201, "Fritz Fee", "€115Mio", "Ausgebildet im Verein (0–21)"],
        [202, "Gustav Gar", "-", "-"],
        [203, "Hans Heim", "€90Mio", "Ausgebildet im Land (15–21)"],
    ]
    pfad = schreibe(ordner, "d2.html", html(spalten, zeilen_d2))
    spieler, felder = importer.parse_export_felder(pfad)
    nach = {p["eid"]: p for p in spieler}
    pruefe("Felder erkannt", {"transfer_fee", "homegrown"} <= felder, True)
    pruefe("transfer_fee", [nach[e]["transfer_fee"] for e in (201, 202, 203)],
           [115_000_000, None, 90_000_000])
    pruefe("homegrown", [nach[e]["homegrown"] for e in (201, 202, 203)],
           ["Ausgebildet im Verein (0–21)", None, "Ausgebildet im Land (15–21)"])
    conn = frische_db(ordner, "d2.db")
    ts = "2026-09-22T13:00:00"
    db.save_export(conn, spieler, felder, datei="d2.html", ts=ts)
    z = zeilen(conn)
    pruefe("DB: transfer_fee gespeichert", [z[e]["transfer_fee"] for e in (201, 202, 203)],
           [115_000_000.0, None, 90_000_000.0])
    pruefe("DB: homegrown_stand gesetzt, auch bei '-'",
           [z[e]["homegrown_stand"] for e in (201, 202, 203)], [ts, ts, ts])
    conn.close()


def main():
    ordner = tempfile.mkdtemp(prefix="test_import_historie_")
    try:
        fall_1(ordner)
        fall_2(ordner)
        fall_3(ordner)
        fall_4(ordner)
        fall_5()
        fall_6(ordner)
    finally:
        shutil.rmtree(ordner, ignore_errors=True)
    print()
    print(f"{_bestanden} von {_gesamt} Prüfungen bestanden")
    if _bestanden != _gesamt:
        sys.exit(1)


if __name__ == "__main__":
    main()
