"""SQLite-Speicherung: jeder Scan ist ein Snapshot; Spielerwerte werden je
Snapshot abgelegt, sodass sich Entwicklung ueber die Zeit verfolgen laesst."""
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path

_init_lock = threading.Lock()
_initialized = set()          # bereits migrierte DB-Pfade (nur einmal _init)

# Als .exe (PyInstaller) liegt die DB neben der .exe (persistent), sonst im Projekt.
if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).resolve().parent
else:
    _BASE = Path(__file__).resolve().parent.parent
DEFAULT_PATH = _BASE / "fmcompanion.db"

STAT_COLS = ["minutes", "goals", "assists", "rating", "xg", "xa",
             "duels", "duels_total", "dribbles", "shots_total", "shots_on",
             "pass_try", "pass_ok", "headers_total", "headers_won",
             "clearances", "prog_passes", "recoveries", "losses",
             "press_try", "press_win",
             "interceptions", "key_passes", "fouls", "fouls_against",
             "yellow", "conceded", "apps", "xga", "is_gk", "pos_mask", "eid",
             # Geburtsdatum aus dem Personen-Record (Tag im Jahr + Jahr);
             # age wird daraus gegen ein Bezugsjahr gerechnet, siehe app._ref_year
             "birth_day", "birth_year", "age"]


def connect(path=None):
    """Neue Connection. WICHTIG: JEDER Thread braucht seine EIGENE Connection –
    dieselbe sqlite3.Connection gleichzeitig aus Auto-Scan-Thread (Write) und
    UI-Thread (Read) haengt PyWebView komplett auf. WAL erlaubt paralleles
    Lesen/Schreiben, busy_timeout laesst kurze Schreibsperren warten statt
    fehlschlagen. _init laeuft nur einmal pro DB (Migration nicht mehrfach).
    path=None loest DEFAULT_PATH ERST HIER auf – als Default-Argument waere der
    Pfad beim Import festgenagelt und ein Umbiegen (Tests!) wirkungslos."""
    path = path or DEFAULT_PATH
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    with _init_lock:
        if str(path) not in _initialized:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
            _init(conn)
            _initialized.add(str(path))
    return conn


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        taken_at TEXT NOT NULL,
        note TEXT)""")
    cols = ", ".join(f"{c} REAL" for c in STAT_COLS)
    conn.execute(f"""CREATE TABLE IF NOT EXISTS player_stats (
        snapshot_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        {cols},
        PRIMARY KEY (snapshot_id, player_id),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(id))""")
    # ALLE Records je Spieler (auch Altsaison/andere Wettbewerbe): Grundlage fuer
    # Saison-Historie/Entwicklung und spaetere Wachstums-Erkennung (eingefrorene
    # Records = abgeschlossene Saison; wachsende = laufende).
    conn.execute(f"""CREATE TABLE IF NOT EXISTS season_stats (
        snapshot_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        rec_no INTEGER NOT NULL,
        is_current INTEGER DEFAULT 0,
        {cols},
        PRIMARY KEY (snapshot_id, player_id, rec_no))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS watchlist (
        player_id INTEGER PRIMARY KEY,
        status TEXT NOT NULL,
        note TEXT DEFAULT '',
        updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER,
        name TEXT NOT NULL,
        config_json TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    # Namensgedaechtnis: FM haelt Namens-Records NUR fuer gerade geladene
    # Spieler vor, die Statistik-Records bleiben dagegen resident. Wer einmal
    # gesehen wurde, bleibt deshalb dauerhaft scanbar, sobald wir seinen Namen
    # nicht mehr aus dem RAM, sondern von hier holen.
    conn.execute("""CREATE TABLE IF NOT EXISTS known_players (
        player_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        eid INTEGER,
        first_seen TEXT,
        last_seen TEXT)""")
    # bestehende DBs um neu hinzugekommene Spalten erweitern
    for table in ("player_stats", "season_stats"):
        have = {r["name"] for r in
                conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for c in STAT_COLS:
            if c not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} REAL")
    conn.commit()


def _init_cohort(conn):
    cols = ", ".join(f"{c} REAL" for c in STAT_COLS)
    conn.execute(f"""CREATE TABLE IF NOT EXISTS cohort_stats (
        player_id INTEGER PRIMARY KEY, updated_at TEXT, {cols})""")
    # bestehende Tabellen um neu hinzugekommene Spalten erweitern (wie bei
    # player_stats/season_stats) – sonst bricht cohort_save nach jeder
    # Erweiterung von STAT_COLS
    have = {r["name"] for r in
            conn.execute("PRAGMA table_info(cohort_stats)").fetchall()}
    for c in STAT_COLS:
        if c not in have:
            conn.execute(f"ALTER TABLE cohort_stats ADD COLUMN {c} REAL")
    conn.commit()


def cohort_save(conn, rows):
    """Vergleichskohorte ersetzen (KEINE Historie – das waeren je Scan
    zehntausende Zeilen). Sie dient nur als Perzentil-Grundlage."""
    _init_cohort(conn)
    if not rows:
        return 0
    ts = datetime.now().isoformat(timespec="seconds")
    cols = ["player_id", "updated_at"] + STAT_COLS
    ph = ", ".join("?" * len(cols))
    conn.executemany(
        f"INSERT OR REPLACE INTO cohort_stats ({', '.join(cols)}) VALUES ({ph})",
        [[r["id"], ts] + [r.get(c) for c in STAT_COLS] for r in rows])
    conn.commit()
    return len(rows)


def cohort_load(conn):
    _init_cohort(conn)
    rows = conn.execute("SELECT * FROM cohort_stats").fetchall()
    return [dict(r) for r in rows]


def cohort_count(conn):
    _init_cohort(conn)
    return conn.execute("SELECT COUNT(*) AS n FROM cohort_stats").fetchone()["n"]


def names_all(conn):
    """{pid: {"name":…, "eid":…}} aller je gesehenen Spieler."""
    rows = conn.execute("SELECT player_id, name, eid FROM known_players").fetchall()
    return {r["player_id"]: {"name": r["name"], "eid": r["eid"]} for r in rows}


def names_learn(conn, names, eids=None):
    """Frisch aus dem RAM gelesene Namen dauerhaft merken. Gibt zurueck, wie
    viele davon neu waren."""
    if not names:
        return 0
    ts = datetime.now().isoformat(timespec="seconds")
    eids = eids or {}
    before = conn.execute("SELECT COUNT(*) AS n FROM known_players").fetchone()["n"]
    conn.executemany(
        "INSERT INTO known_players (player_id, name, eid, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(player_id) DO UPDATE SET name = excluded.name, "
        "eid = COALESCE(excluded.eid, known_players.eid), "
        "last_seen = excluded.last_seen",
        [(int(pid), nm, eids.get(pid), ts, ts) for pid, nm in names.items()])
    conn.commit()
    after = conn.execute("SELECT COUNT(*) AS n FROM known_players").fetchone()["n"]
    return after - before


def names_count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM known_players").fetchone()["n"]


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (key, str(value)))
    conn.commit()


EXPORT_COLS = ["name", "position", "age", "club", "league", "nation",
               "value", "wage", "goals", "assists", "xg", "xa",
               "minutes", "rating", "apps",
               "duels", "duels_total", "shots_total", "shots_on", "pass_try",
               "pass_ok", "dribbles", "prog_passes", "press_win", "press_try",
               "interceptions", "key_passes", "clearances", "headers_won",
               "headers_total", "losses", "recoveries",
               "pen_goals", "conceded", "xga", "chances", "long_goals", "blocks",
               "errors", "crosses_ok", "crosses_try", "sprints",
               "pen_saved", "pen_faced",
               # Paraden (dreigeteilt) und Zu-Null-Spiele der Torhueter
               "saves_tipped", "saves_parried", "saves_held", "clean_sheets",
               # sichtbare Stammdaten: Persoenlichkeit, Medienumgang, starker
               # Fuss, Statuskuerzel, Groesse (cm)
               "personality", "media", "foot", "info", "height",
               # Ablöseforderung (Euro) und Eigengewaechs-Status (Text)
               "transfer_fee", "homegrown"]
# Spalten, die Text tragen – alles andere ist REAL
EXPORT_TEXT = {"name", "position", "club", "league", "nation",
               "personality", "media", "foot", "info", "homegrown"}
# Nur in export_players: WANN ein Feld zuletzt aus einem Export kam.
# pers_stand – die Persoenlichkeit bleibt bei "Scouting erforderlich" stehen
# und aendert sich bei jungen Spielern; homegrown_stand – der Eigengewaechs-
# Status bezieht sich auf den Verein des Nutzers ZUM EXPORTZEITPUNKT, ein
# Wert aus der Benfica-Zeit sagt ueber United nichts (siehe app, own_club_seit).
STAND_COLS = ["pers_stand", "homegrown_stand"]
# Felder, die ein leerer Wert NICHT ueberschreibt. "Scouting erforderlich"
# heisst nicht, dass die Persoenlichkeit weg ist, sondern dass der EIGENE
# Verein sie gerade nicht kennt – das Scouting-Wissen haengt am Verein. Nach
# dem Wechsel von Benfica zu Manchester United haette sonst der erste Import
# jede bei Benfica gesehene Beschreibung geloescht.
BEHALTEN_WENN_LEER = {"personality", "media"}
# Name der Uebernahme des Altbestands in export_importe
BESTAND_DATEI = "Bestand vor Import-Historie"


def _init_export(conn):
    cols = ", ".join(
        f"{c} {'TEXT' if c in EXPORT_TEXT else 'REAL'}" for c in EXPORT_COLS)
    conn.execute(f"""CREATE TABLE IF NOT EXISTS export_players (
        eid INTEGER PRIMARY KEY, imported_at TEXT,
        {', '.join(f'{c} TEXT' for c in STAND_COLS)}, {cols})""")
    # Wertverlauf: je Import eine Zeile (nicht ueberschreiben)
    conn.execute("""CREATE TABLE IF NOT EXISTS export_history (
        eid INTEGER NOT NULL, imported_at TEXT NOT NULL,
        value REAL, wage REAL, age REAL, rating REAL,
        PRIMARY KEY (eid, imported_at))""")
    have = {r["name"] for r in
            conn.execute("PRAGMA table_info(export_players)").fetchall()}
    # bestehende DBs nachziehen (nur ADD COLUMN, nie umbauen)
    for c, typ in ([(c, "TEXT") for c in STAND_COLS]
                   + [(c, "TEXT" if c in EXPORT_TEXT else "REAL") for c in EXPORT_COLS]):
        if c not in have:
            conn.execute(f"ALTER TABLE export_players ADD COLUMN {c} {typ}")
    conn.commit()


def _init_historie(conn):
    """Import-Historie: jede Datei vollstaendig und einzeln.

    export_players haelt nur den zusammengefuehrten AKTUELLEN Stand, und der
    Winter-Import ersetzt darin die vollen Vorsaisonzahlen durch die der
    Halbserie. export_history hebt nur Wert, Gehalt, Alter und Note auf. Wer
    Halbserie gegen Vorsaison pruefen will (Split-Half, Torwartprofil), braucht
    aber jede Kennzahl beider Staende – deshalb hier je Datei eine Zeile in
    export_importe (mit den Feldern, die sie enthielt) und je Spieler eine in
    export_stand. Nicht Teil von _init_export: die App legt die Tabellen beim
    Start NICHT an, erst ein Import oder bestand_sichern().
    """
    cols = ", ".join(
        f"{c} {'TEXT' if c in EXPORT_TEXT else 'REAL'}" for c in EXPORT_COLS)
    conn.execute("""CREATE TABLE IF NOT EXISTS export_importe (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        imported_at TEXT NOT NULL,
        datei TEXT,
        felder TEXT NOT NULL,
        anzahl INTEGER)""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS export_stand (
        import_id INTEGER NOT NULL, eid INTEGER NOT NULL, {cols},
        PRIMARY KEY (import_id, eid))""")
    have = {r["name"] for r in
            conn.execute("PRAGMA table_info(export_stand)").fetchall()}
    for c in EXPORT_COLS:
        if c not in have:
            typ = "TEXT" if c in EXPORT_TEXT else "REAL"
            conn.execute(f"ALTER TABLE export_stand ADD COLUMN {c} {typ}")
    conn.commit()


def _hat_tabelle(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (name,)).fetchone() is not None


def _zusammenfuehren(alt, neu, felder, ts):
    """Eine Export-Zeile in den bisherigen Stand eines Spielers einarbeiten.

    Die EINZIGE Stelle mit den Import-Regeln – save_export schreibt damit,
    export_stand_bis rechnet damit zurueck. Regeln:
    - Nur Felder, die die Datei enthielt, aendern sich (auch auf leer).
    - Ausnahme BEHALTEN_WENN_LEER: eine bekannte Persoenlichkeit bleibt.
    - imported_at ist der Stand der STATISTIK (Pool "aktuell", Ersatzsuche)
      und rueckt nur vor, wenn die Datei Minuten enthaelt. Sonst machte eine
      Shortlist mit blossen Marktwerten Vorsaisonzahlen zu aktuellen.
    - STAND_COLS merken, aus welchem Export Persoenlichkeit und Eigengewaechs-
      Status stammen. Der Status gilt auch als "-" – der neueste Stand zaehlt.
    """
    zeile = (dict(alt) if alt else
             dict({c: None for c in EXPORT_COLS + STAND_COLS},
                  eid=int(neu["eid"]), imported_at=None))
    for f in felder:
        v = neu.get(f)
        if v is None and f in BEHALTEN_WENN_LEER:
            continue
        zeile[f] = v
    if "personality" in felder and neu.get("personality") is not None:
        zeile["pers_stand"] = ts
    if "homegrown" in felder:
        zeile["homegrown_stand"] = ts
    if "minutes" in felder or not zeile.get("imported_at"):
        zeile["imported_at"] = ts
    return zeile


def _jetzt():
    return datetime.now().isoformat(timespec="seconds")


def bestand_sichern(conn):
    """Einmalig: den heutigen export_players-Stand als Anfang der Historie.

    Ohne diesen Schritt kennt die Historie nur Importe ab ihrer Einfuehrung –
    die vollen Summer3-Saisonzahlen waeren mit dem ersten Winter-Import weg.
    Je bisherigem Import-Zeitpunkt entsteht ein Eintrag in export_importe mit
    Datei BESTAND_DATEI. Schreibt nur in die neuen Tabellen und in die neue
    Spalte pers_stand; die Werte in export_players bleiben unberuehrt.

    -> Zahl der uebernommenen Spieler; 0, wenn die Historie schon Eintraege hat.
    """
    _init_export(conn)
    _init_historie(conn)
    if conn.execute("SELECT 1 FROM export_importe LIMIT 1").fetchone():
        return 0
    cols = ", ".join(EXPORT_COLS)
    zaehler = ", ".join(f"COUNT({c})" for c in EXPORT_COLS)
    n = 0
    zeiten = [r["imported_at"] for r in conn.execute(
        "SELECT DISTINCT imported_at FROM export_players "
        "WHERE imported_at IS NOT NULL ORDER BY imported_at").fetchall()]
    for ts in zeiten:
        anzahl = conn.execute("SELECT COUNT(*) AS n FROM export_players "
                              "WHERE imported_at = ?", (ts,)).fetchone()["n"]
        # Welche Spalten die Datei damals hatte, weiss niemand mehr. Ein Feld,
        # das bei KEINEM Spieler dieses Zeitpunkts gefuellt ist, gilt als
        # nicht enthalten – sonst behauptete die Historie etwa, der nie
        # importierte Eigengewaechs-Status sei damals "-" gewesen.
        gezaehlt = conn.execute(f"SELECT {zaehler} FROM export_players "
                                f"WHERE imported_at = ?", (ts,)).fetchone()
        felder = ",".join(c for c, k in zip(EXPORT_COLS, gezaehlt) if k)
        iid = conn.execute(
            "INSERT INTO export_importe (imported_at, datei, felder, anzahl) "
            "VALUES (?, ?, ?, ?)", (ts, BESTAND_DATEI, felder, anzahl)).lastrowid
        conn.execute(f"INSERT INTO export_stand (import_id, eid, {cols}) "
                     f"SELECT ?, eid, {cols} FROM export_players WHERE imported_at = ?",
                     (iid, ts))
        n += anzahl
    # Bisher ueberschrieb jeder Import die Persoenlichkeit – was heute da
    # steht, war also beim letzten Import des Spielers sichtbar.
    conn.execute("UPDATE export_players SET pers_stand = imported_at "
                 "WHERE personality IS NOT NULL AND pers_stand IS NULL")
    conn.commit()
    return n


def save_export(conn, players, felder=None, datei=None, ts=None):
    """Export-Spieler nach EID zusammenfuehren und die Datei festhalten.

    felder: die Felder, die die Datei wirklich enthaelt
    (importer.parse_export_felder). Nur sie aendern sich, alles andere bleibt
    stehen – frueher setzte INSERT OR REPLACE bei einer schmalen Shortlist
    jede fehlende Kennzahl lautlos auf NULL. None heisst "alle Felder".
    ts: Zeitpunkt des Import-Laufs; mehrere Dateien eines Laufs teilen ihn.

    Die Datei landet zusaetzlich vollstaendig in export_stand (siehe
    _init_historie). Ist die Historie noch leer, wird VOR dem ersten Schreiben
    der Altbestand uebernommen (bestand_sichern) – sonst waere genau der
    Stand verloren, den dieser Import ueberschreibt.
    """
    _init_export(conn)
    _init_historie(conn)
    if not conn.execute("SELECT 1 FROM export_importe LIMIT 1").fetchone():
        bestand_sichern(conn)
    ts = ts or _jetzt()
    felder = [c for c in EXPORT_COLS if felder is None or c in felder]
    players = [p for p in players if p.get("eid") is not None]
    eids = [int(p["eid"]) for p in players]

    alt = {}
    for i in range(0, len(eids), 500):
        teil = eids[i:i + 500]
        for r in conn.execute(
                f"SELECT * FROM export_players WHERE eid IN ({','.join('?' * len(teil))})",
                teil).fetchall():
            alt[r["eid"]] = dict(r)
    zeilen = [_zusammenfuehren(alt.get(int(p["eid"])), p, felder, ts) for p in players]

    # UPSERT statt REPLACE: Spalten, die dieser Code nicht kennt, bleiben stehen
    cols = ["eid", "imported_at"] + STAND_COLS + EXPORT_COLS
    setzen = ", ".join(f"{c} = excluded.{c}" for c in cols[1:])
    conn.executemany(
        f"INSERT INTO export_players ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT(eid) DO UPDATE SET {setzen}",
        [[z.get(c) for c in cols] for z in zeilen])
    # Wertverlauf wie bisher, aber mit dem zusammengefuehrten Stand
    conn.executemany(
        "INSERT OR REPLACE INTO export_history (eid, imported_at, value, wage, age, rating) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [[z["eid"], ts, z.get("value"), z.get("wage"), z.get("age"), z.get("rating")]
         for z in zeilen])
    # Historie: die Datei so, wie sie war – fehlende Felder bleiben NULL
    iid = conn.execute(
        "INSERT INTO export_importe (imported_at, datei, felder, anzahl) "
        "VALUES (?, ?, ?, ?)", (ts, datei, ",".join(felder), len(players))).lastrowid
    conn.executemany(
        f"INSERT OR REPLACE INTO export_stand (import_id, eid, {', '.join(EXPORT_COLS)}) "
        f"VALUES ({', '.join('?' * (len(EXPORT_COLS) + 2))})",
        [[iid, int(p["eid"])] + [p.get(c) if c in felder else None for c in EXPORT_COLS]
         for p in players])
    conn.commit()
    return len(players)


def export_importe(conn):
    """Alle Import-Eintraege, aelteste zuerst: id, imported_at, datei, felder, anzahl."""
    if not _hat_tabelle(conn, "export_importe"):
        return []
    rows = conn.execute("SELECT * FROM export_importe ORDER BY imported_at, id").fetchall()
    return [dict(r, felder=r["felder"].split(",")) for r in rows]


def export_verlauf(conn, eid):
    """Alle Staende eines Spielers, aeltester zuerst – je Datei eine Zeile mit
    imported_at, datei und felder. Nicht enthaltene Felder sind None."""
    if not _hat_tabelle(conn, "export_stand"):
        return []
    rows = conn.execute(
        "SELECT i.imported_at, i.datei, i.felder, s.* FROM export_stand s "
        "JOIN export_importe i ON i.id = s.import_id WHERE s.eid = ? "
        "ORDER BY i.imported_at, i.id", (int(eid),)).fetchall()
    return [dict(r, felder=r["felder"].split(",")) for r in rows]


def import_vereine(conn, seit=""):
    """{import_id: {verein: anzahl}} fuer alle Importe ab `seit`, ohne den
    uebernommenen Altbestand (dessen "Dateien" sind nur Zeitpunkte). Daraus
    laesst sich ablesen, welche Datei ein Kader-Export war."""
    if not _hat_tabelle(conn, "export_importe"):
        return {}
    out = {}
    for r in conn.execute(
            "SELECT i.id, s.club, COUNT(*) AS n FROM export_stand s "
            "JOIN export_importe i ON i.id = s.import_id "
            "WHERE i.imported_at >= ? AND (i.datei IS NULL OR i.datei != ?) "
            "GROUP BY i.id, s.club", (seit or "", BESTAND_DATEI)).fetchall():
        if r["club"]:
            out.setdefault(r["id"], {})[r["club"]] = r["n"]
    return out


def export_stand_bis(conn, bis):
    """Der zusammengefuehrte Export-Stand, wie er zum Zeitpunkt `bis` war.

    Dieselbe Form wie load_export() – die Zeilen lassen sich genauso anreichern
    und bewerten. `bis` ist ein ISO-Zeitpunkt; ein reines Datum
    ("2026-09-23") meint dessen Tagesbeginn, Importe dieses Tages zaehlen also
    nicht mehr mit. Beispiel Winter: export_stand_bis(conn, <Tag des
    Winter-Imports>) ist die Vorsaison, load_export(conn) die Halbserie.
    """
    if not _hat_tabelle(conn, "export_importe"):
        return []
    stand = {}
    for imp in conn.execute(
            "SELECT id, imported_at, felder FROM export_importe "
            "WHERE imported_at <= ? ORDER BY imported_at, id", (bis,)).fetchall():
        felder = imp["felder"].split(",")
        for r in conn.execute("SELECT * FROM export_stand WHERE import_id = ?",
                              (imp["id"],)).fetchall():
            e = int(r["eid"])
            stand[e] = _zusammenfuehren(stand.get(e), dict(r), felder,
                                        imp["imported_at"])
    for z in stand.values():
        z.pop("import_id", None)
    return list(stand.values())


def export_value_history(conn, eid):
    _init_export(conn)
    rows = conn.execute(
        "SELECT imported_at, value, rating FROM export_history WHERE eid = ? "
        "ORDER BY imported_at", (int(eid),)).fetchall()
    return [dict(r) for r in rows]


def load_export(conn):
    _init_export(conn)
    rows = conn.execute("SELECT * FROM export_players").fetchall()
    return [dict(r) for r in rows]


def rechenstand(conn):
    """Fingerabdruck der Tabellen, aus denen Brett, Liga-/CL-Vergleich und
    Kaderplaner rechnen (app.Api._rechenstand): Export, Import-Historie,
    Vergleichskohorte, Snapshots. Aendert sich mit jedem Import und jedem
    Scan – auch wenn ein anderer Prozess schreibt oder jemand die Tabelle
    per SQL aendert. Rein lesend, legt keine Tabelle an.

    Der Export (einige tausend Zeilen) geht mit seinem vollen Inhalt ein,
    ~20 ms. Die Kohorte hat ~50.000 Zeilen: sie zu holen kostete ~250 ms je
    Aufruf, deshalb dort eine Pruefsumme in SQL (Summe je Spalte, dazu
    gewichtet mit der player_id, damit auch ein Tausch zweier Zeilen
    auffaellt) – ~120 ms.
    """
    def eins(tabelle, spalten):
        if not _hat_tabelle(conn, tabelle):
            return None
        return tuple(conn.execute(f"SELECT {spalten} FROM {tabelle}").fetchone())

    export = None
    if _hat_tabelle(conn, "export_players"):
        cur = conn.cursor()
        cur.row_factory = None              # Tupel, keine Row-Objekte
        export = hash(tuple(cur.execute("SELECT * FROM export_players ORDER BY eid")))
    kohorte = None
    if _hat_tabelle(conn, "cohort_stats"):
        cols = [r[1] for r in conn.execute("PRAGMA table_info(cohort_stats)")]
        teile = (["COUNT(*)", "MAX(updated_at)"]
                 + [f"TOTAL({c})" for c in cols if c != "updated_at"]
                 + [f"TOTAL({c} * (player_id % 1009))" for c in cols
                    if c not in ("updated_at", "player_id")])
        kohorte = eins("cohort_stats", ", ".join(teile))
    return (export, kohorte,
            # jede Datei eines Imports ist hier eine Zeile
            eins("export_importe", "COUNT(*), MAX(id)"),
            eins("snapshots", "COUNT(*), MAX(id)"))


def export_count(conn):
    _init_export(conn)
    return conn.execute("SELECT COUNT(*) AS n FROM export_players").fetchone()["n"]


def save_snapshot(conn, players, note=""):
    cur = conn.execute("INSERT INTO snapshots (taken_at, note) VALUES (?, ?)",
                       (datetime.now().isoformat(timespec="seconds"), note))
    sid = cur.lastrowid
    cols = ["snapshot_id", "player_id", "name"] + STAT_COLS
    ph = ", ".join("?" * len(cols))
    rows = [[sid, p["id"], p["name"]] + [p.get(c) for c in STAT_COLS] for p in players]
    conn.executemany(f"INSERT INTO player_stats ({', '.join(cols)}) VALUES ({ph})", rows)
    # alle Einzel-Records (auch Altsaison) fuer die Saison-Historie ablegen
    scols = ["snapshot_id", "player_id", "rec_no", "is_current"] + STAT_COLS
    sph = ", ".join("?" * len(scols))
    srows = []
    for p in players:
        for i, r in enumerate(p.get("all_recs") or []):
            srows.append([sid, p["id"], i, 1 if r.get("is_current") else 0]
                         + [r.get(c) for c in STAT_COLS])
    if srows:
        conn.executemany(
            f"INSERT INTO season_stats ({', '.join(scols)}) VALUES ({sph})", srows)
    conn.commit()
    return sid


def latest_snapshot_id(conn):
    row = conn.execute("SELECT id FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def geburtsdaten(conn):
    """{eid: (birth_year, birth_day)} aus dem neuesten Snapshot.

    Die Geburtsdaten stammen aus dem RAM und aendern sich nie – der letzte
    Scan reicht, um das Bezugsjahr auch beim Import zu kalibrieren, ohne
    dass FM24 laufen muss (app._kalibrieren)."""
    sid = latest_snapshot_id(conn)
    if sid is None:
        return {}
    rows = conn.execute(
        "SELECT eid, birth_year, birth_day FROM player_stats "
        "WHERE snapshot_id = ? AND eid IS NOT NULL AND birth_year IS NOT NULL",
        (sid,)).fetchall()
    return {int(r["eid"]): (int(r["birth_year"]),
                            int(r["birth_day"]) if r["birth_day"] else None)
            for r in rows if r["birth_year"]}


def latest_players(conn):
    sid = latest_snapshot_id(conn)
    if sid is None:
        return []
    rows = conn.execute("SELECT * FROM player_stats WHERE snapshot_id = ?", (sid,)).fetchall()
    return [dict(r) for r in rows]


def pool_players(conn, pids=None):
    """Je Spieler der Datensatz aus seinem NEUESTEN Snapshot: der Pool waechst
    mit jedem Scan (Scouting-Listen ansehen -> mehr Spieler) und behaelt
    Spieler, die gerade nicht mehr im RAM geladen sind. 'stale' + taken_at
    kennzeichnen aeltere Staende.

    pids: nur diese Spieler laden (z.B. der eigene Kader). Spart bei 1,1 Mio.
    Zeilen den Grossteil der Arbeit.

    Die frühere Fassung nutzte eine KORRELIERTE Unterabfrage
    (WHERE snapshot_id = (SELECT MAX(...) WHERE player_id = p.player_id)).
    Die lief je Ergebniszeile erneut und ohne passenden Index: gemessen
    35 Zeilen in 30 Sekunden. Jetzt eine einzige Gruppierung plus Join.
    """
    latest = latest_snapshot_id(conn)
    if latest is None:
        return []
    _ensure_indexes(conn)
    wo, args = "", []
    if pids is not None:
        ids = [int(p) for p in pids]
        if not ids:
            return []
        wo = f" WHERE player_id IN ({','.join('?' * len(ids))})"
        args = ids
    rows = conn.execute(f"""
        SELECT p.*, s.taken_at FROM player_stats p
        JOIN (SELECT player_id, MAX(snapshot_id) AS sid
              FROM player_stats{wo} GROUP BY player_id) m
          ON m.player_id = p.player_id AND m.sid = p.snapshot_id
        JOIN snapshots s ON s.id = p.snapshot_id""", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["stale"] = d["snapshot_id"] != latest
        out.append(d)
    return out


_indexed = set()


def squad_pids_expand(conn, pids):
    """Gemerkte Kader-IDs ueber die EID auf alle Datensaetze desselben Spielers
    ausweiten.

    Der eigene Kader wird als RAM-player_id gespeichert, und die ist NICHT
    stabil: FM fuehrt je Wettbewerb einen eigenen Stat-Record mit eigener id,
    und nach dem Neuladen eines Spielstands verschieben sich die ids ohnehin.
    Dadurch zeigte der gemerkte Kader auf Pavlidis' 14-Minuten-Pokaleintrag
    statt auf seine 256 Saisonminuten – das Taktikbrett bewertete ihn mit 21
    statt 83, und zwar auf der Startseite.

    Aufgeloest wird ueber den NEUESTEN Snapshot. Dessen Zeilen liegen dank des
    Primaerschluessels (snapshot_id, player_id) beieinander, ein eigener Index
    auf eid ist dafuer nicht noetig. Zurueck kommen die urspruenglichen ids
    PLUS alle weiteren desselben Spielers; welcher davon gewinnt, entscheidet
    danach die Entdopplung nach Minuten.
    """
    pids = {int(p) for p in (pids or ())}
    latest = latest_snapshot_id(conn)
    if latest is None or not pids:
        return pids
    rows = conn.execute(
        "SELECT player_id, eid FROM player_stats WHERE snapshot_id = ?",
        (latest,)).fetchall()
    eid_von = {r["player_id"]: r["eid"] for r in rows if r["eid"]}
    gesuchte_eids = {eid_von[p] for p in pids if p in eid_von}
    if not gesuchte_eids:
        return pids
    return pids | {r["player_id"] for r in rows if r["eid"] in gesuchte_eids}


def _ensure_indexes(conn):
    """Indizes, ohne die die Historie quadratisch bremst. Werden erst angelegt,
    wenn sie gebraucht werden – bei 1,1 Mio. Zeilen dauert das ein paar
    Sekunden, aber nur einmal."""
    key = id(conn)
    if key in _indexed:
        return
    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_pstats_player "
        "ON player_stats(player_id, snapshot_id)",
        "CREATE INDEX IF NOT EXISTS ix_sstats_player "
        "ON season_stats(player_id, snapshot_id)",
        # form_trend() sucht ueber die EID, nicht ueber die player_id (die ist
        # je Wettbewerb verschieden). Ohne diesen Index scannt jede Formkurve
        # die ganze Tabelle – gemessen 1,0-1,4 s je Taktikbrett.
        "CREATE INDEX IF NOT EXISTS ix_pstats_eid "
        "ON player_stats(eid, snapshot_id)",
    ):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    _indexed.add(key)


def watchlist_set(conn, player_id, status, note=""):
    """status: beobachten|kaufen|verkaufen; leer = von der Liste nehmen."""
    if not status:
        conn.execute("DELETE FROM watchlist WHERE player_id = ?", (player_id,))
    else:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist (player_id, status, note, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (player_id, status, note or "",
             datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def watchlist_all(conn):
    rows = conn.execute("SELECT * FROM watchlist").fetchall()
    return {r["player_id"]: {"status": r["status"], "note": r["note"] or ""}
            for r in rows}


def card_save(conn, player_id, name, config_json, card_id=None):
    ts = datetime.now().isoformat(timespec="seconds")
    if card_id:                              # bestehende Karte aktualisieren
        conn.execute("UPDATE cards SET name=?, config_json=? WHERE id=?",
                     (name, config_json, card_id))
    else:
        cur = conn.execute(
            "INSERT INTO cards (player_id, name, config_json, created_at) "
            "VALUES (?, ?, ?, ?)", (player_id, name, config_json, ts))
        card_id = cur.lastrowid
    conn.commit()
    return card_id


def card_all(conn):
    rows = conn.execute(
        "SELECT id, player_id, name, config_json, created_at "
        "FROM cards ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def card_rename(conn, card_id, name):
    conn.execute("UPDATE cards SET name=? WHERE id=?", (name, card_id))
    conn.commit()


def card_delete(conn, card_id):
    conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
    conn.commit()


def player_seasons(conn, player_id):
    """Alle Einzel-Records eines Spielers aus seinem neuesten Snapshot
    (aktuelle Saison + Altsaison/Wettbewerbe), fuer Entwicklungs-Ansichten."""
    row = conn.execute("SELECT MAX(snapshot_id) AS sid FROM season_stats "
                       "WHERE player_id = ?", (player_id,)).fetchone()
    if not row or row["sid"] is None:
        return []
    rows = conn.execute("SELECT * FROM season_stats WHERE snapshot_id = ? AND "
                        "player_id = ? ORDER BY rec_no", (row["sid"], player_id)).fetchall()
    return [dict(r) for r in rows]


def player_history(conn, player_id):
    rows = conn.execute("""SELECT s.taken_at, p.* FROM player_stats p
        JOIN snapshots s ON s.id = p.snapshot_id
        WHERE p.player_id = ? ORDER BY s.id""", (player_id,)).fetchall()
    return [dict(r) for r in rows]


FORM_MIN_DELTA = 180       # so viele neue Minuten braucht ein Formurteil
FORM_SCHWELLE = 0.15       # ab dieser Notendifferenz zeigt der Pfeil


def form_trend(conn, eids, max_snapshots=400):
    """Formkurve je Spieler: Note der zuletzt gespielten Minuten gegen den
    Saisonschnitt. Rueckgabe {eid: {...}}, Spieler ohne Urteil fehlen.

    Drei Eigenheiten der Snapshot-Daten machen das noetig:

    1. Je Spieler steht pro Snapshot EINE ZEILE JE WETTBEWERB (Liga, Pokal,
       Champions League). Erst die Summe ueber alle ergibt die Saison, deshalb
       wird je Snapshot aggregiert und die Note ueber die Minuten gewichtet.
    2. `name` ist als Schluessel unbrauchbar - mehrere Spieler teilen sich
       Namen, und die Wettbewerbszeilen sehen gleich aus. Gejoint wird ueber
       `eid`, die zu 98,7 % gefuellt und stabil ist.
    3. Die Reihe ist NICHT monoton: wird der Spielstand neu geladen oder eine
       Saison beendet, fallen die Minuten zurueck. Ausgewertet wird deshalb nur
       die LETZTE durchgehend steigende Strecke.

    Innerhalb dieser Strecke laesst sich die Note der neuen Minuten exakt
    herausrechnen: Saisonnote und Minuten sind an beiden Enden bekannt, also
    ist (Note_neu * Min_neu - Note_alt * Min_alt) / (Min_neu - Min_alt) der
    Schnitt genau der Spiele dazwischen. Das ist echte Form, nicht der traege
    Saisonschnitt.
    """
    eids = [int(e) for e in eids if e]
    if not eids:
        return {}
    _ensure_indexes(conn)
    ph = ",".join("?" * len(eids))
    rows = conn.execute(f"""
        SELECT ps.eid AS eid, ps.snapshot_id AS sid, s.taken_at AS taken_at,
               SUM(ps.minutes) AS minuten, SUM(ps.goals) AS tore,
               SUM(ps.rating * ps.minutes) AS notensumme
        FROM player_stats ps JOIN snapshots s ON s.id = ps.snapshot_id
        WHERE ps.eid IN ({ph}) AND ps.minutes > 0
        GROUP BY ps.eid, ps.snapshot_id
        HAVING SUM(ps.minutes) > 0
        ORDER BY ps.eid, s.taken_at""", eids).fetchall()

    je_spieler = {}
    for r in rows:
        je_spieler.setdefault(r["eid"], []).append(
            (r["taken_at"], float(r["minuten"]),
             float(r["notensumme"]) / float(r["minuten"]), float(r["tore"] or 0)))

    raus = {}
    for eid, reihe in je_spieler.items():
        reihe = reihe[-max_snapshots:]
        # letzte durchgehend steigende Strecke ruecklaufend suchen
        ende = len(reihe) - 1
        start = ende
        while start > 0 and reihe[start - 1][1] <= reihe[start][1]:
            start -= 1
        min_a, note_a = reihe[start][1], reihe[start][2]
        min_b, note_b, tore_b = reihe[ende][1], reihe[ende][2], reihe[ende][3]
        delta_min = min_b - min_a
        if delta_min < FORM_MIN_DELTA:
            continue                      # zu wenig neue Spielzeit fuer ein Urteil
        note_neu = (note_b * min_b - note_a * min_a) / delta_min
        diff = note_neu - note_b
        raus[eid] = {
            "form_note": round(note_neu, 2), "saison_note": round(note_b, 2),
            "diff": round(diff, 2),
            "richtung": 1 if diff > FORM_SCHWELLE else (-1 if diff < -FORM_SCHWELLE else 0),
            "minuten": int(delta_min), "tore": int(tore_b - reihe[start][3]),
            "seit": reihe[start][0][:16], "punkte": ende - start + 1,
        }
    return raus


def snapshot_count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"]
