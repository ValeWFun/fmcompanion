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

from fmcompanion import db, moneyball, tactics

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
    "Zweikampf": ("duel_pct", lambda p: f"{p['duel_pct']}% Zweikämpfe ({p['duels_p90']:.1f}/90)"),
    "Kopfball": ("header_pct", lambda p: f"{p['header_pct']}% Kopfbälle"),
    "Note": ("rating_adj", lambda p: f"Ø {p['rating']:.2f}"),
}
# Torhüter haben eigene Kennzahlen (siehe moneyball.PROFILES["tw"]).
DIMS_TW = {
    "Note über Erwartung": ("note_resid", lambda p: f"NüE {p['note_resid']:+.2f}"),
    "Shot-Stopping": ("gp_shot", lambda p: f"{p['gp_shot']:.1f} verhindert je 100 Schüsse"),
    "Paradenquote": ("save_pct", lambda p: f"{p['save_pct']:.0f}% Paraden"),
    "Note": ("rating_adj", lambda p: f"Ø {p['rating']:.2f}"),
}
# Quoten zählen nur bei genug Volumen: 100 % Kopfbälle aus zwei Duellen sind
# keine Stärke. Kennzahl -> Volumenfeld; verlangt wird mindestens der Median der
# Vergleichsspieler dieser Position.
VOLUMEN = {"duel_pct": "duels_p90", "header_pct": "headers_total"}

PEER_MIN_MINUTEN = 900      # Vergleichsspieler für Stärken-Perzentile
LIGA_MIN_MINUTEN = 450      # Mindestminuten des Ligavergleichs (wie carries.py)


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
        self.ref_year = ry = api._ref_year(conn)
        self.export = db.load_export(conn)

        # Reihenfolge wie im Taktikbrett: anreichern, Referenz bilden, dann
        # Scores gegen die Referenz (add_scores ergänzt die Zeilen in place).
        self.rows = moneyball.enrich(
            [api._export_row_to_player(e) for e in self.export if e.get("eid")],
            ref_year=ry)
        self.kohorte = moneyball.enrich(db.cohort_load(conn), ref_year=ry)
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
        self._fair = None           # kommt mit D1 (valuemodel.fair_values)

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
        """Marktwert gegen Fair Value als fertiges Urteil – NOCH NICHT VERFÜGBAR.

        Platzhalter: liefert immer None, bis valuemodel.fair_values (D1) die
        fertigen Felder liefert. Später gibt die Methode
        {"markt_mio", "fair_mio", "abweichung_pct", "urteil", "text",
        "verlaesslich"} zurück, aus fair_value_m, value_delta_pct, fair_urteil,
        fair_text und value_reliable – nur umbenannt, nichts wird selbst
        gerechnet. Torhüter und Spieler ohne Minuten/Note bekommen None.
        Nie eine nackte Prozentzahl ausgeben: positiv heißt unterbewertet.
        """
        return None
