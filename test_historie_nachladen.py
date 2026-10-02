"""Regressionsskript fuer das Nachladen alter Exporte in die Historie (D21).

Ohne FM24, ohne echte DB: Mini-Exporte im FM-Format in Wegwerf-DBs.
Geprueft wird, dass db.historie_nachtragen NUR die Import-Historie ergaenzt:
- export_players (aktueller Stand), export_history und settings bleiben
  bitgleich,
- die alte Datei steht mit ihrem Zeitpunkt VOR dem neueren Import,
- ein zweiter Lauf legt nichts doppelt an,
- Spieler, die nur in alten Dateien stehen, tauchen im aktuellen Stand nicht auf,
- eine noch leere Historie uebernimmt vorher den Altbestand.

Ausfuehren wie die anderen Skripte, kein pytest:
    .venv\\Scripts\\python.exe test_historie_nachladen.py
"""
import hashlib
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fmcompanion import db, importer

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
    rumpf = "".join("<tr>" + "".join(f"<td>{z}</td>" for z in zeile) + "</tr>" for zeile in zeilen)
    return f"<table>{kopf}{rumpf}</table>"


def schreibe(ordner, name, inhalt):
    pfad = os.path.join(ordner, name)
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(inhalt)
    return importer.parse_export_felder(pfad)


def pruefsumme(conn, tabelle):
    h = hashlib.sha256()
    for r in conn.execute(f"SELECT * FROM {tabelle} ORDER BY 1, 2"):
        h.update(repr(tuple(r)).encode())
    return h.hexdigest()


SPALTEN = ["EID", "Name", "Position", "Alter", "Verein", "Transferwert", "Min.", "Tore", "Ø Note",
           "Persönlichkeit"]
NEU = [[101, "Anton", "M (Z)", 27, "Testverein", "€17.5Mio", "2,198", 7, "6.54", "Perfektionist"],
       [102, "Bruno", "V (R)", 23, "Testverein", "€850Tsd", "1,040", 1, "6.81", "Konsequent"]]
ALT = [[101, "Anton", "M (Z)", 26, "Altverein", "€9Mio", "3,010", 11, "6.90", "Perfektionist"],
       [103, "Carlo", "ST (Z)", 30, "Altverein", "€4Mio", "1,500", 9, "6.70", "Ausgeglichen"]]

ordner = tempfile.mkdtemp(prefix="test_historie_nachladen_")
try:
    print("== Nachtragen neben einem aktuellen Stand ==")
    conn = db.connect(os.path.join(ordner, "a.db"))
    sp, fe = schreibe(ordner, "neu.html", html(SPALTEN, NEU))
    db.save_export(conn, sp, fe, datei="neu/liste.html", ts="2026-09-20T10:00:00")
    db.set_setting(conn, "squad_eids", "[101, 102]")
    vorher = {t: pruefsumme(conn, t) for t in ("export_players", "export_history", "settings")}
    n_players = conn.execute("SELECT COUNT(*) FROM export_players").fetchone()[0]

    sp_alt, fe_alt = schreibe(ordner, "alt.html", html(SPALTEN, ALT))
    iid = db.historie_nachtragen(conn, sp_alt, fe_alt, "alt/liste.html", "2026-08-20T10:00:00")
    pruefe("neuer Historien-Eintrag angelegt", iid is not None, True)
    for t in vorher:
        pruefe(f"{t} bitgleich", pruefsumme(conn, t), vorher[t])
    pruefe("aktueller Stand: keine neuen Spieler", conn.execute(
        "SELECT COUNT(*) FROM export_players").fetchone()[0], n_players)
    pruefe("Spieler nur in alter Datei (103) nicht im aktuellen Stand",
           [e["eid"] for e in db.load_export(conn) if e["eid"] == 103], [])
    imp = db.export_importe(conn)
    pruefe("Reihenfolge: alte Datei vor dem neueren Import",
           [(i["datei"], i["imported_at"]) for i in imp],
           [("alt/liste.html", "2026-08-20T10:00:00"), ("neu/liste.html", "2026-09-20T10:00:00")])
    pruefe("Verlauf Anton: erst alt (Altverein, 3.010 Min), dann neu",
           [(v["club"], v["minutes"]) for v in db.export_verlauf(conn, 101)],
           [("Altverein", 3010.0), ("Testverein", 2198.0)])
    alt_stand = {int(e["eid"]): e for e in db.export_stand_bis(conn, "2026-09-01")}
    pruefe("export_stand_bis vor dem neuen Import: alter Stand samt Carlo",
           (alt_stand[101]["club"], alt_stand[101]["minutes"], 103 in alt_stand),
           ("Altverein", 3010.0, True))
    jetzt = {int(e["eid"]): e for e in db.export_stand_bis(conn, "2026-12-31")}
    aktuell = {int(e["eid"]): e for e in db.load_export(conn)}
    pruefe("export_stand_bis heute = aktueller Stand (fuer alle aktuellen Spieler)",
           all(jetzt[e][c] == aktuell[e][c] for e in aktuell for c in fe), True)

    print("== Idempotent ==")
    zahl = conn.execute("SELECT COUNT(*) FROM export_stand").fetchone()[0]
    pruefe("zweiter Lauf legt nichts an",
           db.historie_nachtragen(conn, sp_alt, fe_alt, "alt/liste.html", "2026-08-20T10:00:00"), None)
    pruefe("export_stand unveraendert", conn.execute("SELECT COUNT(*) FROM export_stand").fetchone()[0], zahl)
    pruefe("gleicher Dateiname in anderem Ordner = eigener Eintrag",
           db.historie_nachtragen(conn, sp_alt, fe_alt, "alt2/liste.html", "2026-08-28T10:00:00") is not None,
           True)
    for t in vorher:
        pruefe(f"{t} nach allen Laeufen bitgleich", pruefsumme(conn, t), vorher[t])
    conn.close()

    print("== Leere Historie: erst der Altbestand ==")
    conn = db.connect(os.path.join(ordner, "b.db"))
    db._init_export(conn)
    cols = ["eid", "imported_at"] + db.EXPORT_COLS
    conn.execute(f"INSERT INTO export_players ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [201, "2026-09-10T10:00:00"] + [None] * len(db.EXPORT_COLS))
    conn.execute("UPDATE export_players SET name = 'Dora', minutes = 900 WHERE eid = 201")
    conn.commit()
    summe = pruefsumme(conn, "export_players")
    db.historie_nachtragen(conn, sp_alt, fe_alt, "alt/liste.html", "2026-08-20T10:00:00")
    pruefe("Altbestand zuerst uebernommen",
           [i["datei"] for i in db.export_importe(conn)], ["alt/liste.html", db.BESTAND_DATEI])
    pruefe("export_players bitgleich (bis auf nichts)", pruefsumme(conn, "export_players"), summe)
    conn.close()
finally:
    shutil.rmtree(ordner, ignore_errors=True)

print(f"\n{_bestanden} von {_gesamt} Prüfungen bestanden")
sys.exit(0 if _bestanden == _gesamt else 1)
