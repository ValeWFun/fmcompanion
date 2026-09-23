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
               "personality", "media", "foot", "info", "height"]
# Spalten, die Text tragen – alles andere ist REAL
EXPORT_TEXT = {"name", "position", "club", "league", "nation",
               "personality", "media", "foot", "info"}


def _init_export(conn):
    cols = ", ".join(
        f"{c} {'TEXT' if c in EXPORT_TEXT else 'REAL'}" for c in EXPORT_COLS)
    conn.execute(f"""CREATE TABLE IF NOT EXISTS export_players (
        eid INTEGER PRIMARY KEY, imported_at TEXT, {cols})""")
    # Wertverlauf: je Import eine Zeile (nicht ueberschreiben)
    conn.execute("""CREATE TABLE IF NOT EXISTS export_history (
        eid INTEGER NOT NULL, imported_at TEXT NOT NULL,
        value REAL, wage REAL, age REAL, rating REAL,
        PRIMARY KEY (eid, imported_at))""")
    have = {r["name"] for r in
            conn.execute("PRAGMA table_info(export_players)").fetchall()}
    for c in EXPORT_COLS:                     # bestehende DBs nachziehen
        if c not in have:
            typ = "TEXT" if c in EXPORT_TEXT else "REAL"
            conn.execute(f"ALTER TABLE export_players ADD COLUMN {c} {typ}")
    conn.commit()


def save_export(conn, players):
    """Upsert der Export-Spieler nach EID + eine Verlaufszeile je Import."""
    _init_export(conn)
    from datetime import datetime as _dt
    ts = _dt.now().isoformat(timespec="seconds")
    cols = ["eid", "imported_at"] + EXPORT_COLS
    ph = ", ".join("?" * len(cols))
    rows = [[p["eid"], ts] + [p.get(c) for c in EXPORT_COLS] for p in players]
    conn.executemany(
        f"INSERT OR REPLACE INTO export_players ({', '.join(cols)}) VALUES ({ph})", rows)
    conn.executemany(
        "INSERT OR REPLACE INTO export_history (eid, imported_at, value, wage, age, rating) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [[p["eid"], ts, p.get("value"), p.get("wage"), p.get("age"), p.get("rating")]
         for p in players])
    conn.commit()
    return len(rows)


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
