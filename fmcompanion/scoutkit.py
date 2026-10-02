"""Scoutkit – die App-Logik der Kaderplanung, rein lesend nutzbar.

Baut aus einer READ-ONLY-Verbindung zur Datenbank dieselbe Sicht wie das
Taktikbrett: angereicherte Export-Spieler, Perzentil-Referenz (REFERENZ_MENGE),
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

from fmcompanion import db, moneyball, saisons, tactics, valuemodel

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
STAT_SAISON_AB_TAG = saisons.STAT_SAISON_AB_TAG    # siehe saisons


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
        # Referenz wie in der App (moneyball.REFERENZ_MENGE, seit D22 der
        # aktuelle Export-Stand ohne RAM-Kohorte). Die Kohorte wird nur
        # geladen, wenn die Einstellung sie verlangt.
        self.kohorte = (moneyball.enrich(db.cohort_load(conn), **self.bezug)
                        if moneyball.referenz_braucht_kohorte() else [])
        self.referenz = moneyball.referenz_menge(self.rows, self.kohorte, self.stand)
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
        # Moneyball-Score im Profil des Slots (D19), wie auf dem Brett
        mb = moneyball.profil_score(p, tactics.slot_profil(slot))
        if not b or mb is None:
            return None
        return {"fit": b["score"], "score": mb,
                "leistung": tactics.gesamt(b["score"], mb, None),
                "gesamt": tactics.gesamt(b["score"], mb, p.get("pers_score")),
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
        profil = tactics.slot_profil(slot)       # D19: Score im Slot-Profil
        for r in peers:
            mb = moneyball.profil_score(r, profil)
            if mb is None:
                continue
            b = tactics.score_slot(r, slot, self.dists[slot_key], self.teams)
            if b:
                leist.append(tactics.gesamt(b["score"], mb, None))
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
    # Saisonzuordnung, Stationen und das Zusammensetzen mehrerer Stationen
    # stehen seit D11b in fmcompanion/saisons.py – dieselbe Stelle fuer App
    # und Kit.
    def _staende(self):
        """{eid: [Stand, …]} der Import-Historie mit 'saison' und 'ende'
        (saisons.staende), gemerkt."""
        if self._staende_cache is None:
            self._staende_cache = saisons.staende(self.conn)
        return self._staende_cache

    @staticmethod
    def _vereinslaeufe(staende):
        """Staende EINER Saison -> je Verein der Stand mit den meisten Minuten
        (saisons.stationen)."""
        return saisons.stationen(staende)

    @staticmethod
    def _aus_teilen(basis, teile):
        """Kombinierte Zeile aus mehreren Stationen (saisons.aus_teilen)."""
        return saisons.aus_teilen(basis, teile)

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
                for k in ("score", "score_parts", "profile_label", "talent", "prospect",
                          "score_je_profil", "profil"):
                    z_fit[k] = z_mb.get(k)
                eintrag[feld] = z_fit
        return aus
