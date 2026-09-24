"""Scoutkit – die App-Logik der Kaderplanung, rein lesend nutzbar.

Baut aus einer READ-ONLY-Verbindung zur Datenbank dieselbe Sicht wie das
Taktikbrett: angereicherte Export-Spieler, Perzentil-Referenz (Export + Kohorte),
Positionsverteilungen, eigenes Niveau je Position. Darauf setzen Aufrufer-
Skripte (Carries, Kaderlücken, Kandidatenlisten) auf.

Regeln:
- Es wird nie in die DB geschrieben. Die Verbindung wird mit mode=ro geöffnet;
  jeder versteckte Schreibpfad in db.py scheitert laut mit "attempt to write a
  readonly database", statt still zu schreiben. (CREATE ... IF NOT EXISTS auf
  bestehendem Schema läuft bei mode=ro als No-op durch.)
- Das Kit migriert nie. Hat die DB ein älteres Schema als der Code erwartet,
  bricht oeffnen() mit einer verständlichen Meldung ab: FM Companion einmal
  starten und schließen (das migriert), dann das Kit neu öffnen.
- Das Kit schreibt auch keine Dateien und kennt keine festen Savegame-Pfade.
  Es gibt Daten zurück; der Aufrufer entscheidet, was er speichert.
- Die Rechenlogik wird nicht dupliziert: Kader, Pool-Stand, Bezugsjahr und
  Ligavergleich kommen aus app.Api, gebunden an die ro-Verbindung.
"""
import bisect
import sqlite3
import sys
from pathlib import Path

from fmcompanion import db, moneyball, tactics, valuemodel

# Ligavergleich (Api.league_comparison) kennt nur eine Zeile je Positionsgruppe:
# beide Innenverteidiger und beide Sechser teilen sich Niveau. Stimmt nur, solange
# die Slot-Keys in tactics.FORMATION so heißen.
LIGA_KEY = {"ivl": "iv", "ivr": "iv", "dml": "dm", "dmr": "dm"}

# Wirkungs-Kennzahlen je Kategorie: Name -> (Kennzahl, Textformat). Der Text
# zeigt den Wert so, wie er auf der Kandidatenkarte stehen soll.
DIMS = {
    "Torjäger": ("goals_p90", lambda p: f"{int(p.get('goals') or 0)} Tore ({p['goals_p90']:.2f}/90, xG {p.get('xg') or 0:.1f})"),
    "Chancen": ("xg_p90", lambda p: f"xG {p['xg_p90']:.2f}/90"),
    "Vorbereiter": ("assists_p90", lambda p: f"{int(p.get('assists') or 0)} Vorlagen ({p['assists_p90']:.2f}/90)"),
    "xA": ("xa_p90", lambda p: f"xA {p['xa_p90']:.2f}/90"),
    "Schlüsselpässe": ("keyp_p90", lambda p: f"{p['keyp_p90']:.2f} Schlüsselpässe/90"),
    "Abfangen": ("int_p90", lambda p: f"{p['int_p90']:.2f} abgefangen/90 ({int(p.get('interceptions') or 0)} gesamt)"),
    "Ballgewinne": ("rec_p90", lambda p: f"{p['rec_p90']:.2f} Ballgewinne/90"),
    "Dribbler": ("dribbles_p90", lambda p: f"{p['dribbles_p90']:.2f} Dribblings/90"),
    # Zweikampf als gewonnene Duelle/90 – die Zweikampfquote ist zwischen zwei
    # Saisonhälften Rauschen (Split-Half r 0,01–0,10) und keine Stärke.
    "Zweikampf": ("duels_p90", lambda p: f"{p['duels_p90']:.1f} gewonnene Zweikämpfe/90"),
    "Kopfball": ("header_pct", lambda p: f"{p['header_pct']}% Kopfbälle"),
    "Note": ("rating_adj", lambda p: f"Ø {p['rating']:.2f}"),
}
# Torhüter haben eigene Kennzahlen (siehe moneyball.PROFILES["tw"]). Kein
# Shot-Stopping und keine Paradenquote: beides wiederholt sich zwischen zwei
# Saisonhälften nicht und wäre als „Stärke“ nur Rauschen. Die NüE zählt im
# Perzentil geschrumpft (note_resid_s), angezeigt wird der rohe Wert.
DIMS_TW = {
    "Note über Erwartung": ("note_resid_s", lambda p: f"NüE {p['note_resid']:+.2f}"),
    "Note": ("rating_adj", lambda p: f"Ø {p['rating']:.2f}"),
}
# Quoten zählen nur bei genug Volumen: 100 % Kopfbälle aus zwei Duellen sind
# keine Stärke. Kennzahl -> Volumenfeld; verlangt wird mindestens der Median der
# Vergleichsspieler dieser Position.
VOLUMEN = {"header_pct": "headers_total"}

PEER_MIN_MINUTEN = 900      # Vergleichsspieler für Stärken-Perzentile
LIGA_MIN_MINUTEN = 450      # Mindestminuten des Ligavergleichs (wie carries.py)
# Ab diesem Tag im Jahr gehoeren die Zahlen eines Exports zur Saison, die im
# selben Jahr begonnen hat (ab Juli); davor zur Saison des Vorjahres. Sommer-
# Exporte liegen bei Tag ~134 (beendete Saison), Winter-Exporte um den
# Jahreswechsel (laufende Saison). Nicht zu verwechseln mit
# tactics.SAISON_AB_TAG (Meldeliste: ab Mai wird fuer die NAECHSTE gemeldet).
STAT_SAISON_AB_TAG = 182


def _pz(werte, v):
    """Perzentil (0-100) von v in der sortierten Liste werte, sonst None."""
    if not werte or v is None:
        return None
    return round(100 * bisect.bisect_left(werte, v) / len(werte))


def oeffnen(pfad=None):
    """Kit auf einer read-only-Verbindung öffnen. pfad=None -> db.DEFAULT_PATH."""
    pfad = Path(pfad or db.DEFAULT_PATH)
    conn = sqlite3.connect(f"file:{pfad.as_posix()}?mode=ro", uri=True,
                           check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        return Kit(conn)
    except sqlite3.OperationalError as e:
        conn.close()
        if "readonly" in str(e):
            # Fehlt eine Spalte, will db._init_export sie per ALTER TABLE anlegen –
            # auf der ro-Verbindung scheitert das mit einer kryptischen Meldung.
            raise RuntimeError(
                "Die Datenbank hat noch das alte Schema, der Code erwartet ein "
                "neueres. FM Companion einmal starten und wieder schließen, dann "
                "das Kit neu öffnen. Das Kit selbst migriert nie.") from e
        raise


class Kit:
    """Alles, was beim Öffnen einmal berechnet wird, als Attribute."""

    def __init__(self, conn):
        # app.py liegt im Projektwurzelordner, nicht im Paket. Erst hier
        # importiert: es bringt pywebview mit, öffnet aber kein Fenster.
        wurzel = str(Path(__file__).resolve().parent.parent)
        if wurzel not in sys.path:
            sys.path.insert(0, wurzel)
        from app import Api

        self.conn = conn
        # Api._db() liefert die gebundene ro-Verbindung; db.connect() (WAL,
        # _init) wird nie gerufen.
        self.api = api = Api()
        api._local.conn = conn

        self.kader_eids = api._squad_eids(conn)
        self.stand = api._pool_stand(conn)
        # Bezugsdatum nur LESEN: _bezug kalibriert sonst nach, und das waere
        # ein Schreibzugriff (siehe app._kalibrieren)
        self.bezug = api._bezug(conn, kalibrieren=False)
        self.ref_year = self.bezug["ref_year"]
        self.export = db.load_export(conn)

        # Reihenfolge wie im Taktikbrett: anreichern, Referenz bilden, dann
        # Scores gegen die Referenz (add_scores ergänzt die Zeilen in place).
        self.rows = moneyball.enrich(
            [api._export_row_to_player(e) for e in self.export if e.get("eid")],
            **self.bezug)
        self.kohorte = moneyball.enrich(db.cohort_load(conn), **self.bezug)
        self.referenz = self.rows + self.kohorte
        self.ligen = {int(e["eid"]): e["league"] for e in self.export
                      if e.get("eid") and e.get("league")}
        moneyball.add_scores(self.rows, self.referenz, self.ligen)

        # aktuell = seit dem letzten Kader-Import eingelesen (Vorsaison-Zeilen
        # tragen alte Vereine und Zahlen)
        grenze = self.stand or ""
        self.aktuell = [p for p in self.rows if (p.get("imported_at") or "") >= grenze]
        self.pool = [p for p in self.aktuell if int(p["eid"]) not in self.kader_eids]
        self.kader = [p for p in self.aktuell if int(p["eid"]) in self.kader_eids]

        self.slots = {s["key"]: s for s in tactics.FORMATION}
        self.teams = tactics.team_strength(self.export)
        self.dists = {k: tactics.slot_dists(s, self.referenz)[0]
                      for k, s in self.slots.items()}
        # Eigenes Niveau je Position, mit Pins wie in der App.
        vergleich = api.league_comparison(LIGA_MIN_MINUTEN)
        self.niveau = {z["key"]: z for z in vergleich.get("positionen", [])}
        self._peers = {}            # je Slot einmal berechnet (siehe _peers_von)
        self._fair = None           # valuemodel.fair_values, erst bei Bedarf
        self.fair_modell = None
        self._staende_cache = None  # Import-Historie je Spieler (zwei_saisons)

    # ------------------------------------------------------------ Leistung
    def slot_leistung(self, p, slot_key):
        """Leistung des Spielers auf der Position, oder None.

        None, wenn er dort nicht spielen kann, die Datenbasis für den
        Positionsscore fehlt oder er keinen Moneyball-Score hat.
        leistung = sqrt(Fit x Score) ohne Charakter; gesamt bezieht den
        Charakter mit ein (tactics.gesamt).
        """
        slot = self.slots[slot_key]
        if not tactics.eligible(p, slot)[0]:
            return None
        b = tactics.score_slot(p, slot, self.dists[slot_key], self.teams)
        if not b or p.get("score") is None:
            return None
        return {"fit": b["score"], "score": p["score"],
                "leistung": tactics.gesamt(b["score"], p["score"], None),
                "gesamt": tactics.gesamt(b["score"], p["score"], p.get("pers_score")),
                "carry": b.get("carry")}

    def niveau_von(self, slot_key):
        """Ligavergleich-Zeile (wert, top4, median, …) zur Position."""
        return self.niveau[LIGA_KEY.get(slot_key, slot_key)]

    def kandidaten(self, slot_key, min_minuten=0, max_wert=None, menge="pool",
                   filter=None):
        """Spieler der Menge, die die Position spielen können, mit Leistung.

        menge: "pool" (aktuell ohne eigenen Kader), "aktuell" oder "kader".
        max_wert: Marktwert-Obergrenze in Euro; wer keinen Marktwert hat, fällt
        dann raus (unbekannt ist nicht billig).
        filter: optional callable(p, l) -> bool.
        Rückgabe: Liste (p, leistungs_dict), absteigend nach leistung.
        """
        quelle = {"pool": self.pool, "aktuell": self.aktuell, "kader": self.kader}
        if menge not in quelle:
            raise ValueError(f"Unbekannte Menge: {menge!r}")
        out = []
        for p in quelle[menge]:
            if (p.get("minutes") or 0) < min_minuten:
                continue
            if max_wert is not None:
                v = p.get("value")
                if not v or v > max_wert:
                    continue
            l = self.slot_leistung(p, slot_key)
            if l is None:
                continue
            if filter is not None and not filter(p, l):
                continue
            out.append((p, l))
        out.sort(key=lambda t: -t[1]["leistung"])
        return out

    # ------------------------------------------------------------- Stärken
    def _peers_von(self, slot_key):
        """(Verteilungen, Median-Volumen) der Vergleichsspieler dieser Position.

        Vergleichsspieler: mindestens PEER_MIN_MINUTEN, Position spielbar.
        Je Slot einmal berechnet und gemerkt.
        """
        if slot_key in self._peers:
            return self._peers[slot_key]
        slot = self.slots[slot_key]
        peers = [r for r in self.referenz
                 if (r.get("minutes") or 0) >= PEER_MIN_MINUTEN
                 and tactics.eligible(r, slot)[0]]
        dims = DIMS_TW if slot_key == "tw" else DIMS
        verteil = {mk: sorted(v for v in (r.get(mk) for r in peers) if v is not None)
                   for _, (mk, _) in dims.items()}
        leist = []
        for r in peers:
            if r.get("score") is None:
                continue
            b = tactics.score_slot(r, slot, self.dists[slot_key], self.teams)
            if b:
                leist.append(tactics.gesamt(b["score"], r["score"], None))
        verteil["_leistung"] = sorted(leist)
        med_vol = {mk: (sorted(r.get(vk) or 0 for r in peers)[len(peers) // 2]
                        if peers else 0)
                   for mk, vk in VOLUMEN.items()}
        self._peers[slot_key] = (verteil, med_vol)
        return self._peers[slot_key]

    def staerken(self, p, slot_key, min_pz=95):
        """Wirkungs-Kennzahlen, in denen der Spieler ab min_pz Perzentil liegt.

        Rückgabe: [(pz, name, text)], stärkste zuerst. Quoten zählen nur bei
        mindestens dem Median-Volumen der Vergleichsspieler.
        """
        verteil, med_vol = self._peers_von(slot_key)
        dims = DIMS_TW if slot_key == "tw" else DIMS
        out = []
        for name, (mk, text) in dims.items():
            v = p.get(mk)
            if v is None:
                continue
            if mk in VOLUMEN and (p.get(VOLUMEN[mk]) or 0) < med_vol[mk]:
                continue
            pz = _pz(verteil[mk], v)
            if pz is not None and pz >= min_pz:
                out.append((pz, name, text(p)))
        out.sort(key=lambda t: -t[0])
        return out

    def leistung_pz(self, slot_key, leistung):
        """Perzentil einer Leistungszahl unter den Vergleichsspielern der Position."""
        return _pz(self._peers_von(slot_key)[0]["_leistung"], leistung)

    # ------------------------------------------------------------ Fair Value
    def fair(self, eid):
        """Marktwert gegen Fair Value als fertiges Urteil, oder None.

        {"markt_mio", "fair_mio", "abweichung_pct", "urteil", "text",
        "verlaesslich"} aus valuemodel.fair_values – nur umbenannt, nichts wird
        selbst gerechnet. Torhüter und Spieler ohne Minuten/Note bekommen None.
        Nie eine nackte Prozentzahl ausgeben: positiv heißt unterbewertet.
        """
        if self._fair is None:
            self._fair, self.fair_modell = valuemodel.fair_values(self.export)
        fv = self._fair.get(int(eid))
        if not fv:
            return None
        markt = next((e.get("value") for e in self.export
                      if e.get("eid") and int(e["eid"]) == int(eid)), None)
        return {"markt_mio": round(markt / 1e6, 1) if markt else None,
                "fair_mio": fv["fair_value_m"], "abweichung_pct": fv["value_delta_pct"],
                "urteil": fv["fair_urteil"], "text": fv["fair_text"],
                "verlaesslich": fv["value_reliable"]}

    # ------------------------------------------------- Zwei Saisons (D11 a)
    # Zaehlwerte, die sich ueber Teile addieren lassen. Nicht dabei: Zustaende
    # (Alter, Marktwert, Gehalt, Note, Groesse, Ablöseforderung) – die kommen
    # aus dem aktuellen Stand, die Note minutengewichtet.
    NICHT_SUMMIERBAR = {"age", "value", "wage", "rating", "height", "transfer_fee"}
    # Zaehlwerte, deren /90-Rate die Score-Engine (moneyball._score_metrics,
    # adj) bzw. der Positions-Fit (tactics.SKALIERBAR) mit dem Liga-
    # Koeffizienten multipliziert. Tore/xG/xGA werden gesondert behandelt.
    MB_SKALIERT = {"assists", "press_win", "interceptions", "prog_passes",
                   "clearances", "xa", "dribbles", "key_passes", "chances",
                   "duels", "recoveries", "blocks", "headers_won", "crosses_ok",
                   "sprints"}
    TAKTIK_SKALIERT = {"xa", "key_passes", "prog_passes", "dribbles",
                       "recoveries", "interceptions", "press_win", "duels"}

    def _staende(self):
        """{eid: [Stand, …]} aller Statistik-Staende der Import-Historie,
        chronologisch, je Stand mit 'saison' = Startjahr der Saison, zu der
        die Zahlen gehoeren.

        Die Saison kommt aus dem Spieldatum des Imports (moneyball.bezugsdatum
        ueber RAM-Geburtsjahr + Export-Alter): Sommer-Exporte (Tag ~134) tragen
        die gerade beendete Saison, Winter-Exporte die laufende. So bleibt ein
        alter Stand, dessen Spieler seither nicht mehr exportiert wurde, in
        SEINER Saison und wird nie als Vorsaison missverstanden.
        """
        if self._staende_cache is not None:
            return self._staende_cache
        self._staende_cache = {}
        if not db._hat_tabelle(self.conn, "export_importe"):
            return self._staende_cache
        geburt = db.geburtsdaten(self.conn)
        importe = {i["id"]: i for i in db.export_importe(self.conn)
                   if "minutes" in i["felder"]}
        je_import = {}
        for r in self.conn.execute("SELECT * FROM export_stand").fetchall():
            if r["import_id"] in importe and r["minutes"] is not None:
                je_import.setdefault(r["import_id"], []).append(dict(r))
        saison = {}
        for iid, zeilen in je_import.items():
            paare = [(*geburt[int(z["eid"])], z["age"]) for z in zeilen
                     if int(z["eid"]) in geburt and z.get("age")]
            jahr, tag = moneyball.bezugsdatum(paare)
            saison[iid] = (None if not jahr else
                           jahr if (tag is None or tag >= STAT_SAISON_AB_TAG) else jahr - 1)
        for iid in sorted(je_import, key=lambda i: (importe[i]["imported_at"], i)):
            for z in je_import[iid]:
                z["imported_at"], z["saison"] = importe[iid]["imported_at"], saison[iid]
                liste = self._staende_cache.setdefault(int(z["eid"]), [])
                # mehrere Dateien eines Laufs (Sechser- UND Achterliste): ein Stand
                if liste and liste[-1]["imported_at"] == z["imported_at"]:
                    liste[-1] = z
                else:
                    liste.append(z)
        return self._staende_cache

    @staticmethod
    def _vereinslaeufe(staende):
        """Staende EINER Saison -> je Vereinsstation der letzte Stand.

        Der Export zaehlt nach einem Wechsel nur die Zeit beim aktuellen
        Verein (Transfer wie Leihe; an Winter3 gegen Summer3 gemessen: Shpendi
        Forest 1678 -> United 722 Minuten), innerhalb eines Vereins addiert er
        auf. Saisonsumme = Summe der letzten Staende je Station.
        """
        laeufe = []
        for s in staende:
            if laeufe and laeufe[-1]["club"] == s["club"]:
                laeufe[-1] = s
            else:
                laeufe.append(s)
        return laeufe

    def _aus_teilen(self, basis, teile):
        """Kombinierte Zeile aus mehreren Teilen -> (zeile_score, zeile_fit).

        Summiert werden die Zaehlwerte; Raten ergeben sich daraus minuten-
        gewichtet, Quoten aus den Summen. Liga-Koeffizient und Noten-Offset
        gelten JE TEIL: die Engine rechnet mit dem Koeffizienten der aktuellen
        Liga, deshalb wird jeder Teil vorher auf ihn umgerechnet
        (Zaehlwert x Koeff_Teil / Koeff_aktuell, Note - Offset_Teil +
        Offset_aktuell). Weil Score-Engine und Positions-Fit nicht dieselben
        Kennzahlen skalieren, entstehen zwei Zeilen: eine fuer den Score, eine
        fuer den Fit. Tore und xG skaliert die Engine ohne Elfmeter, xGA nur
        als Differenz zu den Gegentoren. Naeherung bleibt nur bei Wechslern
        zwischen Ligen fuer "Eiskaelte" (Tore ueber xG) und den Fernschuss-
        anteil – beide rechnen mit den umgerechneten Toren.
        """
        liga = basis.get("league")
        c_jetzt = moneyball.league_coeff(liga)
        off_jetzt = moneyball.note_offset(liga)
        summe = [c for c in db.EXPORT_COLS
                 if c not in db.EXPORT_TEXT and c not in self.NICHT_SUMMIERBAR]
        mb, fit = dict(basis), dict(basis)
        for c in summe:
            werte = [t.get(c) for t in teile]
            if any(v is None for v in werte):
                mb[c] = fit[c] = None
                continue
            mb[c] = fit[c] = 0.0
            for t, v in zip(teile, werte):
                f = moneyball.league_coeff(t.get("league")) / c_jetzt
                mb[c] += v * f if c in self.MB_SKALIERT else v
                fit[c] += v * f if c in self.TAKTIK_SKALIERT else v
        # Sonderfaelle: Tore/xG (Engine ohne Elfmeter, Fit mit), xGA (Differenz)
        for c, zeile in (("goals", mb), ("xg", mb), ("goals", fit), ("xg", fit), ("xga", mb), ("xga", fit)):
            if zeile.get(c) is None:
                continue
            zeile[c] = 0.0
            for t in teile:
                f = moneyball.league_coeff(t.get("league")) / c_jetzt
                v, pen, conc = t.get(c) or 0, t.get("pen_goals") or 0, t.get("conceded") or 0
                if c == "xga":
                    zeile[c] += conc + (v - conc) * f
                elif zeile is mb:
                    fest = pen if c == "goals" else 0.76 * pen
                    zeile[c] += fest + (v - fest) * f
                else:
                    zeile[c] += v * f
        m = sum(t.get("minutes") or 0 for t in teile)
        noten = [(t.get("minutes") or 0, t.get("rating"), t.get("league")) for t in teile]
        if m and all(r for _, r, _ in noten):
            note = sum(mi * (r - moneyball.note_offset(lg) + off_jetzt)
                       for mi, r, lg in noten) / m
            mb["rating"] = fit["rating"] = note
        return mb, fit

    # ------------------------------------------------ Kaderplaner (D14)
    def szenario(self, zugaenge=(), abgaenge=(), pins=None, gegen="Ist"):
        """Transferpaket durchrechnen – dieselbe Rechnung wie der Kaderplaner
        der App (Api.planer_vergleich), rein lesend.

        zugaenge: Export-EIDs, abgaenge: EIDs aus dem Kader, pins: optionale
        Szenario-Pins {slot: eid}. gegen: "Ist" oder der Name eines in der App
        gespeicherten Szenarios. -> {ok, links, rechts, delta}; rechts ist das
        Paket, links der Vergleich. Brett, Meldeliste, Liga-/CL-Niveau,
        Kadertiefe, Gehalt und Transferbilanz stehen je Seite drin.
        """
        return self.api.planer_vergleich(
            gegen, {"zugaenge": list(zugaenge), "abgaenge": list(abgaenge),
                    "pins": dict(pins or {})})

    def zwei_saisons(self):
        """Vorsaison und laufende Saison gemeinsam bewertet – nur Analyse (D11 a).

        Je Spieler aus Pool und Kader:
          saison_jetzt / saison_vor : Startjahr der Saisons (siehe _staende)
          teile_jetzt / teile_vor   : Vereinsstationen je Saison
                                      [{imported_at, club, league, minutes}]
          minuten_jetzt / minuten_vor
          wechsel_in_saison : mehr als eine Station in der laufenden Saison
          luecke            : Spiele zwischen letztem Export beim alten Verein
                              und dem Wechsel fehlen (bei jedem Wechsel in
                              einer Saison) – die Summe ist eine Untergrenze
          ligawechsel       : Teile aus anderen Ligen; Koeffizient und Noten-
                              Offset sind je Teil umgerechnet (exakt bis auf
                              Eiskaelte/Fernschussanteil, siehe _aus_teilen)
          jetzt   : Zeile des Kits (nur der letzte Stand – nach einem Wechsel
                    nur die Zeit beim neuen Verein!)
          saison  : laufende Saison ueber alle Stationen, bewertet (oder None)
          kombi   : laufende + Vorsaison, bewertet (oder None)
        saison/kombi sind Zeilen fuer den Fit mit dem Score der Score-Zeile –
        kit.slot_leistung(eintrag["kombi"], slot_key) funktioniert damit direkt.
        Die bestehende Bewertung aendert sich nicht.
        """
        staende = self._staende()
        roh = {int(e["eid"]): e for e in self.export if e.get("eid")}
        aus, zu_scoren = [], []

        def teil_info(t):
            return {k: t.get(k) for k in ("imported_at", "club", "league", "minutes")}

        for p in self.pool + self.kader:
            e = int(p["eid"])
            st = staende.get(e) or []
            s_jetzt = st[-1]["saison"] if st else None
            jetzt = self._vereinslaeufe([s for s in st if s["saison"] == s_jetzt]) if st else []
            vor = (self._vereinslaeufe([s for s in st if s["saison"] == s_jetzt - 1])
                   if s_jetzt is not None else [])
            teile = vor + jetzt
            liga = p.get("league")
            eintrag = {"eid": e, "name": p.get("name"),
                       "saison_jetzt": s_jetzt, "saison_vor": s_jetzt - 1 if vor else None,
                       "teile_jetzt": [teil_info(t) for t in jetzt],
                       "teile_vor": [teil_info(t) for t in vor],
                       "minuten_jetzt": sum(t.get("minutes") or 0 for t in jetzt) if jetzt else None,
                       "minuten_vor": sum(t.get("minutes") or 0 for t in vor) if vor else None,
                       "wechsel_in_saison": len(jetzt) > 1,
                       "luecke": len(jetzt) > 1 or len(vor) > 1,
                       "ligawechsel": any(t.get("league") != liga for t in teile),
                       "jetzt": p, "saison": None, "kombi": None}
            aus.append(eintrag)
            if e not in roh:
                continue
            if len(jetzt) > 1:
                zu_scoren.append((eintrag, "saison", self._aus_teilen(roh[e], jetzt)))
            if vor and jetzt:
                zu_scoren.append((eintrag, "kombi", self._aus_teilen(roh[e], teile)))
        if zu_scoren:
            als_spieler = self.api._export_row_to_player
            mb = moneyball.enrich([als_spieler(z[2][0]) for z in zu_scoren], **self.bezug)
            fit = moneyball.enrich([als_spieler(z[2][1]) for z in zu_scoren], **self.bezug)
            moneyball.add_scores(mb, self.referenz, self.ligen)
            for (eintrag, feld, _), z_mb, z_fit in zip(zu_scoren, mb, fit):
                for k in ("score", "score_parts", "profile_label", "talent", "prospect"):
                    z_fit[k] = z_mb.get(k)
                eintrag[feld] = z_fit
        return aus
