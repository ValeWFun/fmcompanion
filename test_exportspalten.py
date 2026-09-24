"""Regressionsskript fuer die neuen Export-Spalten (D17) – ohne FM24, ohne echte DB.

Alle Eingaben sind synthetische Mini-Exporte im FM-Format (Wegwerf-Ordner):
- Parser: Spielbilanz (S/U/Niederlage), Einsaetze getrennt, Vertragsende und
  Geburtsdatum als ISO-Datum (Tag/Monat und Monat/Tag), Fuss je Seite samt
  Rangfolge, Transferstatus, Transferwert-Spanne, Team-Tore/90.
- Nie gelesen: 'Eignung' (Regel 2), 'SiegQ' (kaputt) – weder als Feld noch
  als Text in irgendeinem Wert.
- Zusammenfuehren (D0): ein alter Export ohne die neuen Spalten setzt sie
  nicht auf NULL; eine alte DB bekommt die Spalten ohne Datenverlust.
- Meldeliste: mit Geburtsdatum ist der U21-Stichtag exakt, kein Grenzfall.

Ausfuehren wie die anderen Skripte, kein pytest:
    .venv\\Scripts\\python.exe test_exportspalten.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fmcompanion import db, importer, tactics
from fmcompanion.scoutkit import Kit
import app

_gesamt = 0
_bestanden = 0


def pruefe(titel, ist, soll):
    global _gesamt, _bestanden
    _gesamt += 1
    if ist == soll:
        _bestanden += 1
        print(f"OK      {titel}")
    else:
        print(f"FEHLER: {titel}: ist {ist!r} soll {soll!r}")


def html(spalten, zeilen):
    kopf = "<tr>" + "".join(f"<th>{s}</th>" for s in spalten) + "</tr>"
    rumpf = "".join("<tr>" + "".join(f"<td>{z}</td>" for z in zeile) + "</tr>"
                    for zeile in zeilen)
    return f"<table>{kopf}{rumpf}</table>"


def schreibe(ordner, name, inhalt):
    pfad = os.path.join(ordner, name)
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(inhalt)
    return pfad


NEU = ["EID", "Name", "Position", "Alter", "Verein", "Min.", "Eins", "S", "U", "Niederlage",
       "SiegQ", "Endet", "Geb.", "Rechter Fuß", "Linker Fuß", "Transferstatus",
       "Transferwert", "Eignung", "TGgt/90", "Ttor/90"]
ZEILEN_NEU = [
    [101, "Anton", "M (Z)", 26, "Testverein", "810", "9", 3, 5, 1, "0%", "30/6/2031",
     "15/5/2002 (26 Jahre alt)", "Schwach", "Sehr stark", "Nicht zugewiesen",
     "€120K - €1.2Mio", "Gut - Gut", "2.04", "1.74"],
    [102, "Bruno", "ST (Z)", 20, "Testverein", "12", "0 (1)", 0, 1, 0, "0%", "31/12/2029",
     "14/10/2007 (20 Jahre alt)", "Sehr stark", "Passabel", "Transferliste",
     "€191Mio - €208Mio", "Hervorragend - Hervorragend", "-", "-"],
    [103, "Carlo", "TW", 17, "Testverein", "-", "-", "-", "-", "-", "-", "-",
     "1/3/2011 (17 Jahre alt)", "-", "-", "-", "Unbekannt", "Gut - Gut", "-", "-"],
    [104, "Dario", "V (Z)", 30, "Anderer FC", "3,690", "41 (4)", 20, 15, 10, "0%",
     "30/6/2028", "2/1/1998 (30 Jahre alt)", "Stark", "Ziemlich stark", "Nicht zugewiesen",
     "€5Mio", "Gut - Gut", "1.10", "1.90"],
]

ordner = tempfile.mkdtemp(prefix="test_exportspalten_")
try:
    # ------------------------------------------------ Parser
    print("== Parser: neue Spalten ==")
    sp, felder = importer.parse_export_felder(schreibe(ordner, "neu.html", html(NEU, ZEILEN_NEU)))
    p = {x["eid"]: x for x in sp}
    pruefe("vier Spieler", len(sp), 4)
    pruefe("Siege/Remis/Niederlagen", (p[101]["wins"], p[101]["draws"], p[101]["defeats"]), (3, 5, 1))
    pruefe("S + U + N = Eins bei allen mit Einsatz",
           all(x["wins"] + x["draws"] + x["defeats"] == x["apps"] for x in sp if x["apps"]), True)
    pruefe("ohne Einsatz: Bilanz None", (p[103]["wins"], p[103]["apps"]), (None, None))
    pruefe("Eins '0 (1)': 0 Start, 1 Einwechslung, apps 1",
           (p[102]["apps_start"], p[102]["apps_sub"], p[102]["apps"]), (0, 1, 1))
    pruefe("Eins '41 (4)': 41 + 4 = 45", (p[104]["apps_start"], p[104]["apps_sub"], p[104]["apps"]),
           (41, 4, 45))
    pruefe("Eins '9': 9 Starts, 0 Einwechslungen", (p[101]["apps_start"], p[101]["apps_sub"]), (9, 0))
    pruefe("Vertragsende ISO", (p[101]["contract_end"], p[102]["contract_end"]),
           ("2031-06-30", "2029-12-31"))
    pruefe("Vertragsende '-' -> None", p[103]["contract_end"], None)
    pruefe("Geburtsdatum ISO (mit 'Jahre alt')", (p[101]["birth_date"], p[103]["birth_date"]),
           ("2002-05-15", "2011-03-01"))
    pruefe("Fuss als Wort", (p[101]["foot_right"], p[101]["foot_left"]), ("Schwach", "Sehr stark"))
    pruefe("Fuss '-' -> None", p[103]["foot_right"], None)
    pruefe("Transferstatus", (p[101]["transfer_status"], p[102]["transfer_status"],
                              p[103]["transfer_status"]),
           ("Nicht zugewiesen", "Transferliste", None))
    pruefe("Spanne '€120K - €1.2Mio'", (p[101]["value_min"], p[101]["value_max"]), (120000, 1200000))
    pruefe("value bleibt der Mittelwert", p[101]["value"], 660000)
    pruefe("Einzelwert '€5Mio': Unter = Obergrenze", (p[104]["value_min"], p[104]["value_max"]),
           (5000000, 5000000))
    pruefe("'Unbekannt' -> None", (p[103]["value_min"], p[103]["value_max"], p[103]["value"]),
           (None, None, None))
    pruefe("Team-Tore/90 gelesen (nur gespeichert)", (p[101]["team_gt_p90"], p[101]["team_tore_p90"]),
           (2.04, 1.74))
    pruefe("Felder der Datei enthalten die neuen",
           {"wins", "draws", "defeats", "apps_start", "apps_sub", "contract_end", "birth_date",
            "foot_right", "foot_left", "transfer_status", "value_min", "value_max",
            "team_gt_p90", "team_tore_p90"} <= felder, True)

    print("== Nie gelesen: Eignung (Regel 2), SiegQ (kaputt) ==")
    pruefe("Eignung und SiegQ stehen in NIE_LESEN",
           {"Eignung", "SiegQ", "Fähigkeit", "Potenzial"} <= set(importer.NIE_LESEN), True)
    pruefe("kein COLUMNS-Alias steht in NIE_LESEN",
           {n for ns in importer.COLUMNS.values() for n in ns} & set(importer.NIE_LESEN), set())
    pruefe("kein Wert enthaelt die Eignung",
           [k for x in sp for k, v in x.items()
            if isinstance(v, str) and ("Gut - Gut" in v or "Hervorragend" in v)], [])
    pruefe("keine Siegquote aus dem Export", any("sieg" in k.lower() for x in sp for k in x), False)

    print("== Datumsformat folgt der Datei ==")
    mdy, _ = importer.parse_export_felder(schreibe(ordner, "mdy.html", html(
        ["EID", "Name", "Endet", "Geb."],
        [[1, "X", "6/30/2031", "5/15/2002 (26 years old)"], [2, "Y", "1/3/2030", "3/1/2011"]])))
    pruefe("Monat/Tag erkannt (30 steht in der Mitte)",
           (mdy[0]["contract_end"], mdy[0]["birth_date"], mdy[1]["contract_end"]),
           ("2031-06-30", "2002-05-15", "2030-01-03"))
    pruefe("unmoegliches Datum -> None", importer._datum("31/2/2030"), None)

    print("== Fuss-Rangfolge ==")
    pruefe("Stufen -> Rang", [tactics.fuss_rang(s) for s in
                              ("Sehr schwach", "Schwach", "Passabel", "Stark", "Sehr stark")],
           [1, 2, 3, 5, 6])
    pruefe("englische Stufen", [tactics.fuss_rang(s) for s in ("Very Weak", "Fairly Strong",
                                                                "Very Strong")], [1, 4, 6])
    pruefe("Unbekanntes -> None (Stufe 4 deutsch noch offen)",
           [tactics.fuss_rang(s) for s in (None, "", "-", "Irgendwie", "Ziemlich stark")],
           [None, None, None, None, None])
    pruefe("unbekannte Stufe wird gemeldet, nicht verschluckt",
           tactics.unbekannte_fussstufen(sp), {"Ziemlich stark": 1})
    pruefe("Import-Hinweis nennt das Wort einmal",
           [h for h in app.Api._import_hinweise(sp) if "Ziemlich stark" in h] != [], True)

    # ------------------------------------------------ Zusammenfuehren
    print("== Zusammenfuehren: alter Export setzt nichts auf NULL ==")
    conn = db.connect(os.path.join(ordner, "merge.db"))
    db.save_export(conn, sp, felder, datei="neu.html", ts="2026-09-24T10:00:00")
    ALT = ["EID", "Name", "Position", "Alter", "Verein", "Min.", "Tore", "Ø Note"]
    alt, fe_alt = importer.parse_export_felder(schreibe(ordner, "alt.html", html(ALT, [
        [101, "Anton", "M (Z)", 26, "Testverein", "900", 2, "6.80"],
        [104, "Dario", "V (Z)", 30, "Anderer FC", "3,780", 1, "7.01"]])))
    db.save_export(conn, alt, fe_alt, datei="alt.html", ts="2026-09-25T10:00:00")
    z = {r["eid"]: dict(r) for r in conn.execute("SELECT * FROM export_players")}
    neue = ["wins", "draws", "defeats", "apps_start", "apps_sub", "contract_end", "birth_date",
            "foot_right", "foot_left", "transfer_status", "value_min", "value_max",
            "team_gt_p90", "team_tore_p90"]
    pruefe("alter Export: neue Felder bleiben stehen",
           [(e, f) for e in (101, 104) for f in neue if z[e][f] != p[e][f]], [])
    pruefe("alter Export: seine eigenen Felder aendern sich", (z[101]["minutes"], z[104]["minutes"]),
           (900.0, 3780.0))
    pruefe("Textspalten bleiben Text", (z[101]["birth_date"], z[101]["foot_left"]),
           ("2002-05-15", "Sehr stark"))
    # Ein Export MIT Transferwert, aber ohne die anderen neuen Spalten: die
    # Spanne kommt aus derselben Zelle wie 'value' und aendert sich mit.
    wert, fe_wert = importer.parse_export_felder(schreibe(ordner, "wert.html", html(
        ["EID", "Name", "Transferwert"], [[101, "Anton", "€200K - €2Mio"]])))
    db.save_export(conn, wert, fe_wert, datei="wert.html", ts="2026-09-26T10:00:00")
    z = {r["eid"]: dict(r) for r in conn.execute("SELECT * FROM export_players")}
    pruefe("Transferwert-Liste: Spanne zieht mit dem Wert mit",
           (z[101]["value"], z[101]["value_min"], z[101]["value_max"]),
           (1100000.0, 200000.0, 2000000.0))
    pruefe("Transferwert-Liste: Bilanz und Geburtsdatum bleiben", (z[101]["wins"], z[101]["birth_date"]),
           (3.0, "2002-05-15"))
    st = conn.execute("SELECT wins, birth_date, foot_left FROM export_stand s JOIN export_importe i "
                      "ON i.id = s.import_id WHERE i.datei = 'neu.html' AND s.eid = 101").fetchone()
    pruefe("Import-Historie haelt die neuen Felder der Datei", tuple(st), (3.0, "2002-05-15", "Sehr stark"))
    conn.close()

    print("== Alte DB: Spalten kommen dazu, nichts geht verloren ==")
    alt_db = os.path.join(ordner, "alt.db")
    roh = sqlite3.connect(alt_db)
    alte_cols = [c for c in db.EXPORT_COLS if c not in neue]
    roh.execute(f"CREATE TABLE export_players (eid INTEGER PRIMARY KEY, imported_at TEXT, "
                f"pers_stand TEXT, homegrown_stand TEXT, "
                f"{', '.join(c + ' ' + ('TEXT' if c in db.EXPORT_TEXT else 'REAL') for c in alte_cols)})")
    roh.execute("INSERT INTO export_players (eid, imported_at, name, minutes, personality) "
                "VALUES (7, '2026-09-01T00:00:00', 'Alt', 1234, 'Perfektionist')")
    roh.commit()
    roh.close()
    conn = db.connect(alt_db)
    db.load_export(conn)                  # wie die App: _init_export beim ersten Lesen
    spalten = {r["name"] for r in conn.execute("PRAGMA table_info(export_players)")}
    pruefe("neue Spalten angelegt", set(neue) <= spalten, True)
    r = dict(conn.execute("SELECT * FROM export_players WHERE eid = 7").fetchone())
    pruefe("alte Zeile unversehrt", (r["name"], r["minutes"], r["personality"], r["wins"]),
           ("Alt", 1234.0, "Perfektionist", None))
    conn.close()

    # ------------------------------------------------ Meldeliste
    print("== Meldeliste: Geburtsdatum macht den U21-Stichtag exakt ==")
    # Saisonstart 2028 -> U21 heisst Jahrgang 2007 und juenger
    pruefe("21 Jahre, geboren Okt. 2007: sicher U21",
           tactics.pl_status({"age": 21, "birth_date": "2007-10-14"}, 2028, ref_year=2028),
           ("u21", False))
    pruefe("ohne Geburtsdatum dieselben 21 Jahre: Grenzfall",
           tactics.pl_status({"age": 21, "homegrown_stand": "x"}, 2028, ref_year=2028)[1], True)
    pruefe("21 Jahre, geboren Dez. 2006: sicher ueber 21, kein Grenzfall",
           tactics.pl_status({"age": 21, "birth_date": "2006-12-31", "homegrown": None,
                              "homegrown_stand": "2026-09-24"}, 2028, ref_year=2028),
           ("braucht_platz", False))
    pruefe("RAM-Geburtsjahr gilt weiter, wenn kein Datum da ist",
           tactics.pl_status({"age": 22, "birth_year": 2007}, 2028, ref_year=2028), ("u21", False))

    print("== Scoutkit: Spanne und Raten werden nicht summiert ==")
    pruefe("NICHT_SUMMIERBAR", {"value_min", "value_max", "team_gt_p90", "team_tore_p90"}
           <= Kit.NICHT_SUMMIERBAR, True)
    pruefe("Siege und Einsaetze sind summierbar",
           {"wins", "draws", "defeats", "apps_start", "apps_sub"} & Kit.NICHT_SUMMIERBAR, set())
finally:
    shutil.rmtree(ordner, ignore_errors=True)

print(f"\n{_bestanden} von {_gesamt} Prüfungen bestanden")
sys.exit(0 if _bestanden == _gesamt else 1)
