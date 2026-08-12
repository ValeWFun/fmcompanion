"""FM Companion – Desktop-App (PyWebView).
Natives Fenster mit HTML-Dashboard; Python-Backend liest den RAM, speichert
Snapshots und liefert Moneyball-Kennzahlen an das Frontend.
"""
import os
import sys
import threading
import time
import webview

from fmcompanion import scanner, moneyball, db, importer, tactics

# Pfad zu den UI-Dateien (funktioniert auch im PyInstaller-Bundle)
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
UI_INDEX = os.path.join(BASE, "ui", "index.html")


class Api:
    def __init__(self):
        self._local = threading.local()         # DB-Connection PRO Thread
        # WICHTIG der fuehrende Unterstrich: pywebview spiegelt das js_api-Objekt
        # nach JavaScript und laeuft dafuer in generate_js_object() REKURSIV durch
        # alle oeffentlichen Attribute (webview/util.py get_functions, Zeile 190ff;
        # Namen mit _ werden uebersprungen). Ein oeffentliches "window" fuehrt in
        # das PyWebView-Window, von dort in window.native und damit in den
        # gesamten WinForms-Objektbaum - das laeuft minutenlang gegen die
        # Rekursionsgrenze und blockiert den Programmstart.
        self._window = None
        self._scan_lock = threading.Lock()      # nie zwei Scans gleichzeitig
        self._auto_thread = None
        self._auto_stop = threading.Event()
        self._auto_idle = False                 # FM nicht erreichbar
        self._scanning = False                  # gerade ein Auto-Scan aktiv
        self._last_meta = {}                    # Kennzahlen des letzten Scans
        self._hot_regions = None                # Bloecke mit Namens-Records
        self._scan_no = 0                       # jeder N-te Lauf sucht voll
        self._last_snap_ts = 0.0                # Drosselung der Snapshots
        self._last_pids = set()
        self._collect_until = 0.0               # Scouting-Modus laeuft bis …
        self._names_start = None                # Namensstand bei dessen Beginn
        # Suchmenge der Ersatzsuche: Pool + Vergleichsmenge, aufgehoben, damit
        # das Umschalten zwischen den Suchmodi nicht jedes Mal 30.000 Zeilen
        # neu anreichert. Unterstrich, sonst spiegelt pywebview das nach JS.
        self._repl_cache = None

    def _db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = db.connect()
            self._local.conn = conn
        return conn

    # cur_max_min gilt JE WETTBEWERBS-RECORD, nicht je Saison: die Saison eines
    # Spielers ist die Summe seiner Wettbewerbe (siehe scanner._aggregate).
    SETTING_DEFAULTS = {"cur_max_min": scanner.CUR_MAX_MIN, "min_minutes": 45,
                        "sig_min_minutes": 900, "auto_scan": 1, "auto_pause": 180,
                        # Vergleichskohorte: namenlose RAM-Records als Perzentil-
                        # Basis (0 = aus). Mindestminuten, damit Kurzeinsaetze die
                        # /90-Verteilungen nicht verzerren.
                        "cohort_on": 1, "cohort_min_minutes": 180}

    def get_settings(self):
        conn = self._db()
        return {k: int(db.get_setting(conn, k, d))
                for k, d in self.SETTING_DEFAULTS.items()}

    def set_setting(self, key, value):
        if key not in self.SETTING_DEFAULTS:
            return {"ok": False, "error": f"Unbekannte Einstellung: {key}"}
        try:
            v = max(0, min(6000, int(value)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Ungültiger Wert"}
        db.set_setting(self._db(), key, v)
        return {"ok": True, "value": v}

    # Rohzaehler, die der HTML-Export mitbringt. Abgeleitetes (/90-Raten,
    # Quoten) rechnet moneyball.enrich daraus neu – deshalb wird VOR dem
    # enrich zusammengefuehrt.
    EXPORT_STATS = ("minutes", "apps", "goals", "assists", "xg", "xa", "rating",
                    "duels", "duels_total", "shots_total", "shots_on",
                    "pass_try", "pass_ok", "dribbles", "prog_passes",
                    "press_win", "press_try", "interceptions", "key_passes",
                    "clearances", "headers_won", "headers_total", "losses")
    # Zaehler, die es NUR im RAM gibt. Gewinnt der Export, gehoeren sie nicht
    # mehr zu seinen Minuten – eine Ballgewinn-Rate aus RAM-Zaehlern und
    # Export-Minuten waere schlicht falsch. Dann lieber leer lassen: die
    # Score-Engine ueberspringt fehlende Kennzahlen und gewichtet neu.
    RAM_ONLY_STATS = ("recoveries", "conceded", "xga", "fouls",
                      "fouls_against", "yellow")

    def _merge_export_stats(self, rows, exp_by_eid):
        """Export-Zahlen in die RAM-Zeilen ziehen. VOR moneyball.enrich rufen.

        Regel: der Export gewinnt, ausser der RAM hat mehr Minuten.

        Der Export ist FMs eigene Zahl und damit exakt – aber nur so frisch wie
        der letzte Import. Der RAM ist live, sieht jedoch nur, was FM gerade
        geladen hat: an Buendia gemessen 10 Minuten im RAM gegen 337 im Export,
        ueber 300 Spieler stimmten beide Quellen nur in 5 % der Faelle ueberein.
        Wer mehr Minuten hat, hat den vollstaendigeren Stand.

        Stammdaten (Position, Verein, Liga, Marktwert) kommen IMMER aus dem
        Export – die kennt der RAM-Scan gar nicht.
        """
        n = 0
        for p in rows:
            e = exp_by_eid.get(int(p["eid"])) if p.get("eid") else None
            if not e:
                continue
            for k in ("position", "club", "league", "value", "wage"):
                if e.get(k) not in (None, ""):
                    p[k] = e[k]
            if (e.get("minutes") or 0) <= (p.get("minutes") or 0):
                continue                      # RAM ist vollstaendiger
            for k in self.EXPORT_STATS:
                p[k] = e.get(k)
            for k in self.RAM_ONLY_STATS:
                p[k] = None
            p["stat_quelle"] = "export"
            p["stat_stand"] = e.get("imported_at")
            n += 1
        return n

    def _ref_year(self, conn, players=None):
        """Bezugsjahr fuers Alter. Der RAM liefert das GEBURTSJAHR, nicht das
        Alter – ohne aktuelles Spieljahr ist es wertlos. Das Spieldatum selbst
        liess sich im Speicher nicht isolieren (die haeufigsten Datumsangaben
        sind Vertragsenden), deshalb kalibriert es sich an den Export-Altern:
        Geburtsjahr + Alter = Jahr des Exports. Manuell ueberschreibbar ueber
        die Einstellung season_year (0 = automatisch)."""
        manual = int(db.get_setting(conn, "season_year", 0) or 0)
        if manual:
            return manual
        cand = []
        if players:
            exp = {int(e["eid"]): e.get("age") for e in db.load_export(conn)
                   if e.get("eid") and e.get("age")}
            for p in players:
                a = exp.get(int(p["eid"])) if p.get("eid") else None
                if a and p.get("birth_year"):
                    cand.append(int(p["birth_year"]) + int(a))
        if len(cand) >= 5:
            cand.sort()
            return cand[len(cand) // 2]
        return int(db.get_setting(conn, "ref_year_cached", 0) or 0) or None

    def _add_scores(self, players, reference=None):
        """Moneyball-Scores: Perzentil-Basis ist der Pool PLUS die namenlose
        Vergleichskohorte aus dem RAM. Ohne sie rechnen die Perzentile gegen
        gut hundert Spieler, mit ihr gegen Zehntausende – erst dann heisst
        'im 90. Perzentil' auch etwas. Liga-Koeffizienten kommen aus dem
        Export (EID-Join); Kohorten-Spieler haben keine EID -> Faktor 1.0."""
        conn = self._db()
        ry = self._ref_year(conn)
        if reference is None:
            reference = moneyball.enrich(db.pool_players(conn), ref_year=ry)
        if int(db.get_setting(conn, "cohort_on",
                              self.SETTING_DEFAULTS["cohort_on"])):
            cohort = db.cohort_load(conn)
            if cohort:
                reference = list(reference) + moneyball.enrich(cohort, ref_year=ry)
        leagues = {int(r["eid"]): r["league"]
                   for r in db.load_export(conn)
                   if r.get("eid") and r.get("league")}
        moneyball.add_scores(players, reference, leagues)
        return players

    def _scan_and_save(self):
        """Ein RAM-Scan + Snapshot, serialisiert (Auto- und Klick-Scan teilen
        sich den Lock, damit sie sich nicht in die Quere kommen).

        Namen werden dauerhaft gemerkt und beim naechsten Scan wieder
        mitgegeben: FM raeumt Namens-Records geladener Spieler wieder weg,
        die Statistik-Records bleiben aber liegen. So bleibt ein einmal
        gesehener Scouting-Spieler dauerhaft mit AKTUELLEN Zahlen im Pool,
        statt nur als alter Snapshot zu verstauben."""
        conn = self._db()
        cur = int(db.get_setting(conn, "cur_max_min",
                                 self.SETTING_DEFAULTS["cur_max_min"]))
        cmin = (int(db.get_setting(conn, "cohort_min_minutes",
                                   self.SETTING_DEFAULTS["cohort_min_minutes"]))
                if int(db.get_setting(conn, "cohort_on",
                                      self.SETTING_DEFAULTS["cohort_on"])) else None)
        meta = {}
        with self._scan_lock:
            # Namen liegen nur in ~6 % des Speichers. Die bekannten Bloecke
            # reichen fuer den Alltag; jeder 5. Lauf sucht voll, damit neu
            # belegte Bereiche dazukommen.
            self._scan_no += 1
            full = self._hot_regions is None or self._scan_no % 5 == 1
            raw = scanner.scan_players(
                cur_max_min=cur, known=db.names_all(conn), meta=meta,
                cohort_min_minutes=cmin,
                hot_regions=self._hot_regions, full_sweep=full)
            ref = self._ref_year(conn, raw)
            if ref:
                db.set_setting(conn, "ref_year_cached", ref)
            meta["ref_year"] = ref
            players = moneyball.enrich(raw, ref_year=ref)
            if meta.get("cohort"):          # Kohorte braucht dasselbe Bezugsjahr
                meta["cohort"] = moneyball.enrich(meta["cohort"], ref_year=ref)
            self._hot_regions = meta.pop("hot_regions", None) or self._hot_regions
            meta["names_new"] = db.names_learn(conn, meta.get("names_ram") or {},
                                               meta.get("eids_ram"))
            if meta.get("cohort"):
                meta["cohort_saved"] = db.cohort_save(conn, meta["cohort"])
            if players and self._should_snapshot(conn, players):
                db.save_snapshot(conn, players)
                self._last_snap_ts = time.time()
                self._last_pids = {p["id"] for p in players}
        for k in ("names_ram", "eids_ram", "cohort"):
            meta.pop(k, None)               # Rohdaten nicht ins Frontend schleppen
        meta["names_known"] = db.names_count(conn)
        self._last_meta = meta
        return players

    def _should_snapshot(self, conn, players):
        """Im Scouting-Modus wird im 20-Sekunden-Takt gescannt – daraus jedes
        Mal einen Snapshot zu schreiben wuerde die Historie mit dutzenden
        identischen Punkten zumuellen (Sparklines, Signal-Bestaetigung). Also
        nur schreiben, wenn neue Spieler dazugekommen sind oder die normale
        Pause ohnehin verstrichen ist."""
        if not self._last_snap_ts:
            return True
        if {p["id"] for p in players} - self._last_pids:
            return True
        normal = int(db.get_setting(conn, "auto_pause",
                                    self.SETTING_DEFAULTS["auto_pause"]))
        return time.time() - self._last_snap_ts >= normal

    COLLECT_PAUSE = 20          # Sekunden zwischen Scans im Scouting-Modus
    COLLECT_MINUTES = 10        # danach faellt er von selbst zurueck

    def set_collect(self, on):
        """Scouting-Modus: derselbe Scan, nur enger getaktet. Gedacht zum
        Mitlaufen, waehrend in FM Listen durchgeblaettert werden – jeder dabei
        geladene Name wird dauerhaft gemerkt und macht die (ohnehin residenten)
        Statistiken des Spielers ab da nutzbar."""
        if on:
            self._collect_until = time.time() + 60 * self.COLLECT_MINUTES
            self._names_start = db.names_count(self._db())
            self._start_auto()
        else:
            self._collect_until = 0.0
            self._names_start = None
        return {"ok": True, "on": bool(on)}

    def _collecting(self):
        return time.time() < self._collect_until

    def coverage(self):
        """Wie viele Spieler kennen wir namentlich, wie viele Datensaetze
        liegen insgesamt im RAM? Macht die Luecke sichtbar, die der
        Sammelmodus schliesst."""
        conn = self._db()
        m = self._last_meta or {}
        known = db.names_count(conn)
        total = m.get("pids_total") or 0
        return {"known": known, "ram_ids": total, "cohort": db.cohort_count(conn),
                "pct": round(100 * known / total, 1) if total else None,
                "records": m.get("records_total") or 0}

    def scan(self):
        """Liest das laufende Spiel, speichert einen Snapshot, gibt Spieler zurueck."""
        try:
            players = self._scan_and_save()
        except Exception as e:
            name = type(e).__name__
            if "ProcessNotFound" in name:
                return {"ok": False, "error": "Football Manager 2024 läuft nicht. "
                        "Bitte das Spiel starten und ein Savegame laden."}
            return {"ok": False, "error": f"{name}: {e}"}
        if not players:
            return {"ok": False, "error": "Keine Spielerdaten gefunden. "
                    "Ist ein Savegame geladen?"}
        self._add_scores(players)      # Referenz = Pool inkl. frischem Snapshot
        return {"ok": True, "players": players, "count": len(players),
                "meta": self._last_meta}

    # -------------------------------------------------- Auto-Scan (Hintergrund)
    def auto_status(self):
        on = int(db.get_setting(self._db(), "auto_scan",
                                self.SETTING_DEFAULTS["auto_scan"]))
        pause = int(db.get_setting(self._db(), "auto_pause",
                                   self.SETTING_DEFAULTS["auto_pause"]))
        return {"on": bool(on), "pause": pause,
                "running": bool(self._auto_thread and self._auto_thread.is_alive())}

    def auto_poll(self):
        """Von der UI periodisch abgefragt (kein Thread->GUI-Push, das friert
        PyWebView ein). Liefert die aktuelle Snapshot-ID; aendert sie sich, holt
        die UI die neuen Daten selbst per load_saved()."""
        conn = self._db()
        return {"on": bool(int(db.get_setting(conn, "auto_scan",
                                              self.SETTING_DEFAULTS["auto_scan"]))),
                "running": bool(self._auto_thread and self._auto_thread.is_alive()),
                "scanning": self._scanning, "idle": self._auto_idle,
                "last_id": db.latest_snapshot_id(conn) or 0,
                "collect": self._collecting(),
                "collect_left": max(0, int(self._collect_until - time.time())),
                "names_known": db.names_count(conn),
                "names_gained": (db.names_count(conn) - self._names_start
                                 if self._names_start is not None else 0)}

    def set_auto_scan(self, on):
        on = bool(on)
        db.set_setting(self._db(), "auto_scan", 1 if on else 0)
        if on:
            self._start_auto()
        else:
            self._auto_stop.set()
        return {"ok": True, "on": on}

    def set_auto_pause(self, seconds):
        try:
            v = max(3, min(600, int(seconds)))
        except (TypeError, ValueError):
            return {"ok": False}
        db.set_setting(self._db(), "auto_pause", v)
        return {"ok": True, "pause": v}

    def _start_auto(self):
        if self._auto_thread and self._auto_thread.is_alive():
            return
        self._auto_stop.clear()
        self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
        self._auto_thread.start()

    def _auto_loop(self):
        """Scannt selbststaendig, solange FM laeuft, und speichert Snapshots.
        KEIN window.evaluate_js aus diesem Thread (deadlockt PyWebView) – die UI
        pollt auto_poll() und holt neue Daten selbst. Pause zwischen Laeufen,
        damit das Dauer-Lesen FM nicht ausbremst."""
        self._auto_stop.wait(3)            # UI zuerst laden lassen
        while not self._auto_stop.is_set():
            try:
                self._scanning = True
                players = self._scan_and_save()
                self._auto_idle = not bool(players)
            except Exception as e:
                self._auto_idle = "ProcessNotFound" in type(e).__name__
            finally:
                self._scanning = False
            if self._collecting():          # Scouting-Modus: enger Takt
                pause = self.COLLECT_PAUSE
            else:
                pause = int(db.get_setting(self._db(), "auto_pause",
                                           self.SETTING_DEFAULTS["auto_pause"]))
            self._auto_stop.wait(max(3, pause))

    def load_saved(self, mode="last"):
        """Gespeicherte Daten + Export-Merge: RAM-Spieler werden mit Marktwert/
        Alter aus dem Export angereichert, reine Export-Spieler (z.B. Scouting-
        Ziele, die FM nicht in den RAM laedt) kommen als vollwertige Eintraege
        mit Score dazu. mode='last'=letzter Snapshot, 'pool'=akkumuliert."""
        conn = self._db()
        ry = self._ref_year(conn)
        raw = db.pool_players(conn) if mode == "pool" else db.latest_players(conn)
        exp = db.load_export(conn)
        exp_by_eid = {int(e["eid"]): e for e in exp if e.get("eid")}
        aus_export = self._merge_export_stats(raw, exp_by_eid)
        players = moneyball.enrich(raw, ref_year=ry)
        for p in players:
            p.setdefault("id", p.get("player_id"))
            p["source"] = "ram"
        ram_eids = {int(p["eid"]) for p in players if p.get("eid")}
        for p in players:                       # RAM mit Wert/Alter anreichern
            e = exp_by_eid.get(int(p["eid"])) if p.get("eid") else None
            if e:
                p.update(value=e.get("value"), wage=e.get("wage"),
                         exp_at=e.get("imported_at"), source="ram+export")
                # Alter aus dem RAM hat Vorrang: es ist immer aktuell, das
                # Export-Alter ist der Stand des letzten Imports.
                if p.get("age") is None and e.get("age"):
                    p["age"] = e["age"]
                    p["age_band"] = moneyball.age_band(p["age"])
        extra = []                              # reine Export-Spieler ergaenzen
        for e in exp:
            if not e.get("eid") or int(e["eid"]) in ram_eids:
                continue
            pos = e.get("position") or ""
            extra.append(dict(e, id=int(e["eid"]),
                              is_gk=pos.strip().startswith("TW"),
                              pos_mask=moneyball.pos_mask_from_string(pos),
                              source="export", exp_at=e.get("imported_at")))
        players += moneyball.enrich(extra, ref_year=ry)
        self._add_scores(players, reference=players)
        for p in players:                       # Wert-Effizienz (Score je Mio)
            v = p.get("value")
            p["value_m"] = round(v / 1e6, 1) if v else None
            p["value_score"] = (round(p["score"] / (v / 1e6), 1)
                                if v and v > 0 and p.get("score") is not None else None)
        return {"ok": True, "players": players, "count": len(players),
                "snapshots": db.snapshot_count(conn), "exports": len(exp),
                "aus_export": aus_export}

    def value_history(self, eid):
        """Marktwert-Verlauf eines Spielers ueber die Export-Importe."""
        return db.export_value_history(self._db(), int(eid))

    def seasons(self, player_id):
        """Alle Einzel-Records (Saison-Historie) aus dem neuesten Snapshot."""
        return db.player_seasons(self._db(), int(player_id))

    def _overperf_snaps(self, conn, pid, thresh=1.0):
        """Anzahl Snapshots, in denen der Spieler ueber xG lag (Stabilitaet)."""
        hist = db.player_history(conn, pid)
        return sum(1 for r in hist
                   if (r.get("goals") or 0) - (r.get("xg") or 0) > thresh)

    def signals(self, min_minutes=None):
        """Buy-Low / Sell-High-Kandidaten. Erst ab einer Mindest-Stichprobe
        (Default 900 Min ~ 10 Spiele), damit Glueck/Pech nach 2-3 Spielen
        KEIN Signal ausloest. Basis = Pool (neuester Stand je Spieler)."""
        conn = self._db()
        if min_minutes is None:
            min_minutes = int(db.get_setting(conn, "sig_min_minutes",
                                             self.SETTING_DEFAULTS["sig_min_minutes"]))
        min_minutes = max(180, int(min_minutes))
        players = moneyball.enrich(db.pool_players(conn),
                                   ref_year=self._ref_year(conn))
        for p in players:
            p.setdefault("id", p.get("player_id"))
        self._add_scores(players, reference=players)
        field = [p for p in players
                 if not p.get("is_gk") and (p.get("minutes") or 0) > 0]
        max_min = max((int(p.get("minutes") or 0) for p in field), default=0)
        eligible = [p for p in field if (p.get("minutes") or 0) >= min_minutes]

        xgs = sorted((p.get("xg_p90") or 0) for p in eligible)

        def pctl(v):
            n = len(xgs)
            if n < 4:
                return 50
            import bisect
            lo = bisect.bisect_left(xgs, v)
            hi = bisect.bisect_right(xgs, v)
            return round(100 * (lo + 0.5 * (hi - lo)) / n)

        buy, sell = [], []
        for p in eligible:
            fin = round(p.get("finishing") or 0, 2)
            if fin < -0.8:
                xgp = pctl(p.get("xg_p90") or 0)
                if xgp >= 60:
                    buy.append(dict(p, signal_kind="buy", signal_pctl=xgp,
                        reason=(f"Schafft klare Chancen (xG/90 im P{xgp} seiner "
                                f"Kohorte), trifft aber {fin:+.2f} unter Erwartung "
                                f"– Pechvogel, Marktwert meist gedrückt.")))
            elif fin > 1.5:
                snaps = self._overperf_snaps(conn, p["id"])
                sell.append(dict(p, signal_kind="sell", signal_snaps=snaps,
                    reason=(f"Trifft {fin:+.2f} ÜBER xG (in {snaps} Scans bestätigt) "
                            f"– Überperformance ist selten dauerhaft, Marktwert "
                            f"vermutlich auf dem Höhepunkt.")))
        buy.sort(key=lambda p: p.get("finishing") or 0)
        sell.sort(key=lambda p: -(p.get("finishing") or 0))
        return {"ok": True, "buy": buy, "sell": sell,
                "min_minutes": min_minutes, "max_minutes": max_min,
                "eligible": len(eligible), "total": len(field)}

    # -------------------------------------------------- Taktik-Analyse
    def detect_squads(self):
        """Sucht Kaderlisten im Speicher. FM haelt mehrere Vereine gleichzeitig
        vor – welcher der eigene ist, geht aus den Daten NICHT hervor, das muss
        der Nutzer einmal auswaehlen."""
        try:
            with self._scan_lock:
                pm, _ = scanner.memlib.attach()
                regions = list(scanner.memlib.read_regions(pm))
                names, _e = scanner._build_name_index(regions, with_eid=True)
                # Dauerhaft gemerkte Namen ergaenzen: direkt nach dem Laden des
                # Savegames stehen oft nur ein paar Dutzend Namen im RAM, dann
                # findet die Kadersuche fast nichts.
                for pid, info in db.names_all(self._db()).items():
                    if info.get("name"):
                        names.setdefault(int(pid), info["name"])
                stats = scanner._find_records(regions, set(names))
                gefunden = tactics.find_squads(regions, names, set(stats))
        except Exception as e:
            if "ProcessNotFound" in type(e).__name__:
                return {"ok": False, "error": "Football Manager läuft nicht."}
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        conn = self._db()
        aktuell = set(self._squad_pids(conn))
        for s in gefunden[:12]:
            s["gewaehlt"] = bool(aktuell and set(s["pids"]) & aktuell)
        return {"ok": True, "kader": gefunden[:12]}

    def _squad_pids(self, conn):
        import json
        try:
            return json.loads(db.get_setting(conn, "squad_pids", "[]") or "[]")
        except Exception:
            return []

    def set_squad(self, pids):
        import json
        conn = self._db()
        liste = sorted({int(p) for p in (pids or [])})
        db.set_setting(conn, "squad_pids", json.dumps(liste))
        return {"ok": True, "anzahl": len(liste)}

    def tactic_board(self):
        """Je Position der Formation die passenden Kaderspieler mit Score und
        Aufschluesselung. Perzentile gegen alle Spieler, die dort spielen
        koennen (Pool + namenlose Vergleichskohorte)."""
        conn = self._db()
        pids = set(self._squad_pids(conn))
        if not pids:
            return {"ok": False, "kein_kader": True}
        ry = self._ref_year(conn)
        # NUR den Kader laden, nicht den ganzen Pool: fuer 25 Spieler die
        # Historie von tausenden durchzugehen war die eigentliche Bremse.
        # Export vor dem enrich einmischen: er liefert die Position (kennt auch
        # die Seite) und gewinnt bei den Zahlen, solange er mehr Minuten hat.
        exp = {int(e["eid"]): e for e in db.load_export(conn) if e.get("eid")}
        roh = db.pool_players(conn, pids)
        aus_export = self._merge_export_stats(roh, exp)
        kader = moneyball.enrich(roh, ref_year=ry)
        for p in kader:
            p.setdefault("id", p.get("player_id"))
        # Vergleichsmenge fuer die Perzentile: die Kohorte reicht: sie enthaelt
        # zehntausende Spieler mit Position und Statistik.
        referenz = kader + moneyball.enrich(db.cohort_load(conn), ref_year=ry)
        fehlt = len(pids) - len(kader)
        return {"ok": True, "slots": tactics.build_board(kader, referenz),
                "kader": len(kader), "ohne_daten": fehlt, "aus_export": aus_export,
                "export_positionen": sum(1 for p in kader if p.get("position"))}

    def _repl_pool(self, conn):
        """Suchmenge der Ersatzsuche: der GANZE Pool (rund 7000 Spieler, in
        0,3 s geladen) plus dieselbe Vergleichsmenge wie im Taktikbrett.

        Dieselbe Referenz ist keine Kosmetik: nur so liegen die Scores der
        Ersatzkandidaten auf derselben Skala wie die Zahl auf dem Spielfeld –
        sonst hiesse "besser als 71" nichts.

        Gepuffert bis zum naechsten Snapshot oder Kaderwechsel.
        """
        kader_ids = frozenset(self._squad_pids(conn))
        key = (db.latest_snapshot_id(conn), kader_ids)
        if self._repl_cache and self._repl_cache[0] == key:
            return self._repl_cache[1:]
        ry = self._ref_year(conn)
        exp = {int(e["eid"]): e for e in db.load_export(conn) if e.get("eid")}
        roh = db.pool_players(conn)
        # Dieselbe Zusammenfuehrung wie im Taktikbrett – sonst laegen die
        # Scores der Kandidaten auf einer anderen Skala als die auf dem Feld.
        self._merge_export_stats(roh, exp)
        pool = moneyball.enrich(roh, ref_year=ry)
        for p in pool:
            p.setdefault("id", p.get("player_id"))
        referenz = ([p for p in pool if p["id"] in kader_ids]
                    + moneyball.enrich(db.cohort_load(conn), ref_year=ry))
        self._repl_cache = (key, pool, referenz, kader_ids)
        return pool, referenz, kader_ids

    def tactic_replacements(self, slot_key, player_id, modus="aehnlich",
                            min_minutes=None):
        """Ersatz fuer einen Spieler auf einer Position suchen.

        modus: 'aehnlich' (gleiches Niveau, gleiches Profil), 'besser' oder
        'juenger'. Positionsfremde Kandidaten sind zugelassen, bekommen aber
        den Umschulungsaufwand abgezogen – siehe tactics.UMSCHULUNG_KANTEN.
        """
        slot = next((s for s in tactics.FORMATION if s["key"] == slot_key), None)
        if slot is None:
            return {"ok": False, "error": "Unbekannte Position."}
        conn = self._db()
        if not self._squad_pids(conn):
            return {"ok": False, "kein_kader": True}
        pool, referenz, kader_ids = self._repl_pool(conn)
        original = next((p for p in pool if p["id"] == int(player_id)), None)
        if original is None:
            return {"ok": False, "error": "Spieler nicht im Datenbestand."}
        mm = (tactics.MIN_MINUTES if min_minutes in (None, "")
              else max(90, int(min_minutes)))
        return tactics.find_replacements(
            slot, original, pool, referenz, modus=modus,
            min_minutes=mm, kader_ids=kader_ids)

    def watchlist(self):
        return db.watchlist_all(self._db())

    def watchlist_set(self, player_id, status, note=""):
        db.watchlist_set(self._db(), int(player_id), status or "", note or "")
        return {"ok": True}

    def rankings(self):
        return [{"title": t, "key": k, "desc": d}
                for t, (k, d) in moneyball.RANKINGS.items()]

    def import_export(self):
        """Oeffnet einen Datei-Dialog, importiert einen FM-HTML-Export."""
        try:
            # KEIN Bindestrich in der Beschreibung: pywebview prueft den Filter
            # gegen ^([\w ]+)\(…\) – "HTML-Export" faellt durch und der Dialog
            # geht gar nicht erst auf (webview/util.py parse_file_type).
            paths = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("HTML Export (*.html;*.htm)", "Alle Dateien (*.*)"))
        except Exception as e:
            return {"ok": False, "error": f"Dialog-Fehler: {e}"}
        if not paths:
            return {"ok": False, "error": "Keine Datei gewählt."}
        try:
            players = importer.parse_export(paths[0])
        except Exception as e:
            return {"ok": False, "error": f"Import fehlgeschlagen: {e}"}
        if not players:
            return {"ok": False, "error": "Keine Spieler in der Datei gefunden."}
        n = db.save_export(self._db(), players)
        return {"ok": True, "count": n}

    def load_export(self):
        players = moneyball.enrich_export(db.load_export(self._db()))
        return {"ok": True, "players": players, "count": len(players)}

    def export_rankings(self):
        return [{"title": t, "key": k, "desc": d}
                for t, (k, d) in moneyball.EXPORT_RANKINGS.items()]

    def history(self, player_id):
        return db.player_history(self._db(), int(player_id))

    # -------------------------------------------------- Hall of Fame (Karten)
    def card_save(self, config_json, card_id=None):
        import json
        try:
            cfg = json.loads(config_json)
        except Exception:
            return {"ok": False, "error": "Ungültige Karte"}
        cid = db.card_save(self._db(), cfg.get("pid"), cfg.get("name", "Karte"),
                           config_json, card_id or None)
        return {"ok": True, "id": cid}

    def card_all(self):
        return db.card_all(self._db())

    def card_rename(self, card_id, name):
        db.card_rename(self._db(), int(card_id), name or "Karte")
        return {"ok": True}

    def card_delete(self, card_id):
        db.card_delete(self._db(), int(card_id))
        return {"ok": True}

    def save_png(self, data_url, suggested="karte.png"):
        """PNG (data-URL vom Canvas) via nativen Speicherdialog ablegen."""
        import base64
        try:
            path = self._window.create_file_dialog(
                webview.SAVE_DIALOG, save_filename=suggested,
                file_types=("PNG Bild (*.png)",))     # Bindestrich: s.o.
        except Exception as e:
            return {"ok": False, "error": f"Dialog-Fehler: {e}"}
        if not path:
            return {"ok": False}
        p = path if isinstance(path, str) else path[0]
        if not p.lower().endswith(".png"):
            p += ".png"
        try:
            raw = base64.b64decode(data_url.split(",", 1)[1])
            with open(p, "wb") as f:
                f.write(raw)
            return {"ok": True, "path": p}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pick_image(self):
        """Bild fuer die Spielerkarte waehlen -> als data-URI zurueck (bleibt
        im Karten-Config, keine Datei-Referenz)."""
        try:
            paths = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("Bilder (*.png;*.jpg;*.jpeg;*.webp)", "Alle Dateien (*.*)"))
        except Exception as e:
            return {"ok": False, "error": f"Dialog-Fehler: {e}"}
        if not paths:
            return {"ok": False}
        import base64
        import mimetypes
        try:
            data = open(paths[0], "rb").read()
            if len(data) > 4_000_000:
                return {"ok": False, "error": "Bild zu groß (max. 4 MB)."}
            mime = mimetypes.guess_type(paths[0])[0] or "image/png"
            return {"ok": True, "data": f"data:{mime};base64,"
                    + base64.b64encode(data).decode()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _own_hwnd(self):
        """Top-Level-Fenster DES EIGENEN PROZESSES finden (robuster als Titel-
        Suche – der HTML <title> kann den Fenstertitel veraendern)."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            wpid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if (wpid.value == pid and user32.IsWindowVisible(hwnd)
                    and user32.GetWindowTextLengthW(hwnd) > 0):
                found.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        return found[0] if found else None

    def set_titlebar(self, dark=True):
        """Faerbt die native Windows-Titelleiste passend zum Theme (DWM). Kein
        rahmenloses Fenster -> Groessenziehen/Snap/Buttons bleiben nativ, nur die
        Leiste wird dunkel und passt zum App-Header. Win11 22000+ (User hat 26200)."""
        try:
            import ctypes
            from ctypes import wintypes
            dwm = ctypes.windll.dwmapi
            dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                  ctypes.c_void_p, wintypes.DWORD]
            hwnd = self._own_hwnd()
            if not hwnd:
                return {"ok": False, "error": "kein Fenster"}

            def setattr_(attr, value):
                v = ctypes.c_uint(value)
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

            for immersive in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE
                setattr_(immersive, 1 if dark else 0)
            # Muss den --surface/--text-Werten aus ui/index.html folgen, sonst
            # sitzt eine warme Leiste auf einer kuehlen Oberflaeche.
            if dark:                            # COLORREF = 0x00BBGGRR
                setattr_(35, 0x00201A16)        # CAPTION_COLOR = surface #161a20
                setattr_(36, 0x00EFEAE7)        # TEXT_COLOR    = #e7eaef
            else:
                setattr_(35, 0x00FFFFFF)        # #ffffff
                setattr_(36, 0x001C1714)        # #14171c
            return {"ok": True, "hwnd": int(hwnd)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _boot(self):
        """Laeuft nach dem Fenster-Start: Titelleiste einfaerben + Auto-Scan."""
        import time
        for _ in range(20):         # bis das native Fenster wirklich existiert
            if self.set_titlebar(True).get("ok"):
                break
            time.sleep(0.2)
        try:
            if int(db.get_setting(self._db(), "auto_scan",
                                  self.SETTING_DEFAULTS["auto_scan"])):
                self._start_auto()
        except Exception:
            pass


def main():
    api = Api()
    api._window = webview.create_window(
        "FM Companion – Moneyball", UI_INDEX, js_api=api,
        width=1280, height=820, min_size=(960, 600),
        background_color="#0b0e12",   # --bg (dunkel): kein heller Blitz beim Start
    )
    webview.start(api._boot)


if __name__ == "__main__":
    main()
