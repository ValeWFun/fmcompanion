"""Mehrere Saisons je Spieler (D11b): Saisonzuordnung, Stationen, Mischung.

Jede Saison wird einzeln bewertet und danach nach Minuten x d^k gemittelt
(Datenanalyst: "Leistungen mitteln, nicht Zaehlwerte summieren" – gepoolt
0,79 gegen 0,77, IV +0,06). Bewertet wird jede Saison gegen die AKTUELLE
Referenz: gegen ihre eigene lagen dieselben Vorsaison-Zeilen 3–7 Punkte
tiefer, jeder Spieler mit Vorsaison waere abgewertet worden und "Sprung"
ein Skalenartefakt. Zwei Horizonte (Art):
  form     : Brett, Auto-Elf, Kadertiefe, Liga-/CL-Vergleich – naechste Spiele
  transfer : Ersatzsuche, Kandidaten, Planspiel, Tabelle, Talent-Board –
             naechste Saison; Spieler bis U23_ALTER rechnen auch hier mit den
             Form-Gewichten
Gewicht d und Drift haengen dazu am Stand (Sommer/Winter, siehe
moneyball.HORIZONT_PARAMETER). k = Saisons zurueck. Die aktuelle Saison ist
der aktuelle Export-Stand, die Vorsaisons kommen aus der Import-Historie
(export_stand), zugeordnet ueber das Saison-Feld, NIE ueber das Datum: die
Datei "Saisonstart 2027/28" mit 171 Minuten gehoert nicht als Vorsaison zu
2026/27.

Rein lesend. Von app._basis und scoutkit gemeinsam benutzt; die App importiert
weiter kein Scoutkit.
"""
from . import db, moneyball, tactics

# Ab diesem Tag im Jahr gehoeren die Zahlen eines Exports zur Saison, die im
# selben Jahr begonnen hat (ab Juli); davor zur Saison des Vorjahres. Sommer-
# Exporte liegen bei Tag ~134 (beendete Saison), Winter-Exporte um den
# Jahreswechsel (laufende Saison). Nicht zu verwechseln mit
# tactics.SAISON_AB_TAG (Meldeliste: ab Mai wird fuer die NAECHSTE gemeldet).
STAT_SAISON_AB_TAG = 182

HORIZONTE = ("form", "transfer")
HORIZONT_TEXT = {"form": "Form: nächste Spiele", "transfer": "Transfer: nächste Saison"}
U23_ALTER = 23             # bis zu diesem Alter (aktueller Stand) Form-Gewichte
assert {(st, a) for st in ("sommer", "winter") for a in HORIZONTE} == set(moneyball.HORIZONT_PARAMETER)

# Hinweis "Sprung"/"Absturz": Leistung der letzten zwei GANZEN Saisons
# unterscheidet sich um mindestens SPRUNG_PUNKTE, in beiden mindestens
# SPRUNG_MIN_MINUTEN. Im Mittel haelt etwa die Haelfte davon (Datenanalyst).
SPRUNG_PUNKTE = 10
SPRUNG_MIN_MINUTEN = 900
SPRUNG_TEXT = "Im Mittel hält etwa die Hälfte eines Sprungs oder Absturzes."
KALENDER_TEXT = "Kalenderjahr-Liga: nur aktuelle Saison"
# Torhueter mischen nur Saisons beim AKTUELLEN Verein (Head Scout nach der
# Messung des Datenanalysten, D11b): Note, Note ueber Erwartung und Gegentore
# tragen die Abwehr mit. Jahr gegen Jahr bleiben Keeper im selben Verein
# stabil (r 0,71), nach einem Wechsel kaum (0,39, die NueE gar nicht: -0,11);
# ueber drei Saisons im selben Verein hilft das Mischen (0,30 -> 0,51). Mit
# allen Vereinen zogen Vorsaisons bei schwaecheren Vereinen Keeper um 16–21
# Punkte herunter. Eine Vereinsbereinigung ueber xG gegen nimmt echtes Signal
# heraus und bleibt draussen. Stationen bei anderen Vereinen zaehlen nicht,
# auch nicht in der laufenden Saison.
#   "aus"    : nur die aktuelle Station (Stand vor D11b)
#   "verein" : nur Saisons und Stationen beim aktuellen Verein
#   "alle"   : wie Feldspieler
TORWART_MISCHEN = "verein"
TORWART_TEXT = {
    "aus": "Torwart: nur aktuelle Saison (Werte hängen stark an der Abwehr)",
    "verein": "Torwart: nur Saisons beim aktuellen Verein (Werte hängen stark an der Abwehr)",
}
assert TORWART_MISCHEN in ("aus", "verein", "alle")
# Eine Vorsaison zaehlt erst ab so vielen Spielern in der Historie – wie die
# Untergrenze der Referenz (moneyball.REFERENZ_MIN_ZEILEN).
VORSAISON_MIN_SPIELER = moneyball.REFERENZ_MIN_ZEILEN

# Zaehlwerte, die sich ueber Stationen einer Saison addieren lassen. Nicht
# dabei: Zustaende (Alter, Marktwert, Gehalt, Note, Groesse, Ablöseforderung,
# Wert-Spanne) – die kommen aus der letzten Station, die Note minutengewichtet.
NICHT_SUMMIERBAR = {"age", "value", "wage", "rating", "height", "transfer_fee",
                    "value_min", "value_max"}
# Zaehlwerte, deren /90-Rate die Score-Engine (moneyball._score_metrics, adj)
# bzw. der Positions-Fit (tactics.SKALIERBAR) mit dem Liga-Koeffizienten
# multipliziert. Tore/xG/xGA werden gesondert behandelt.
MB_SKALIERT = {"assists", "press_win", "interceptions", "prog_passes",
               "clearances", "xa", "dribbles", "key_passes", "chances",
               "duels", "recoveries", "blocks", "headers_won", "crosses_ok",
               "sprints"}
TAKTIK_SKALIERT = {"xa", "key_passes", "prog_passes", "dribbles",
                   "recoveries", "interceptions", "press_win", "duels"}


# ------------------------------------------------------------ Saisonen
def saison_aus(jahr, tag):
    """Startjahr der Saison, zu der die Zahlen eines Exports vom Spieltag
    (jahr, tag) gehoeren; None ohne Jahr."""
    if not jahr:
        return None
    return jahr if (tag is None or tag >= STAT_SAISON_AB_TAG) else jahr - 1


def saisonende(tag):
    """Stammt ein Export mit diesem Tag im Jahr vom Saisonende (Sommer)?"""
    return tag is not None and tag < STAT_SAISON_AB_TAG


def saison_aktuell(bz):
    """(Saison, beendet) des aktuellen Stands aus dem Bezugsdatum der App."""
    jahr, tag = bz.get("ref_year"), bz.get("ref_day")
    return saison_aus(jahr, tag), saisonende(tag)


def horizont_von(bz, art):
    """Horizont (Stand, Art) fuer moneyball.HORIZONT_PARAMETER: Stand
    "sommer", wenn der aktuelle Stand vom Saisonende stammt, sonst "winter"."""
    return ("sommer" if saison_aktuell(bz)[1] else "winter", art)


def staende(conn):
    """{eid: [Stand, …]} aller Statistik-Staende der Import-Historie,
    chronologisch nach imported_at, je Stand mit 'saison' (Startjahr) und
    'ende' (Saisonende-Export).

    Die Saison kommt aus dem Spieldatum des Imports (moneyball.bezugsdatum
    ueber RAM-Geburtsjahr + Export-Alter). So bleibt ein alter Stand, dessen
    Spieler seither nicht mehr exportiert wurde, in SEINER Saison. Staende
    ohne Minuten fehlen (sie tragen keine Leistung).
    """
    aus = {}
    if not db._hat_tabelle(conn, "export_importe"):
        return aus
    geburt = db.geburtsdaten(conn)
    importe = {i["id"]: i for i in db.export_importe(conn) if "minutes" in i["felder"]}
    je_import = {}
    for r in conn.execute("SELECT * FROM export_stand").fetchall():
        if r["import_id"] in importe and (r["minutes"] or 0) > 0:
            je_import.setdefault(r["import_id"], []).append(dict(r))
    datum = {}
    for iid, zeilen in je_import.items():
        paare = [(*geburt[int(z["eid"])], z["age"]) for z in zeilen
                 if int(z["eid"]) in geburt and z.get("age")]
        datum[iid] = moneyball.bezugsdatum(paare)
    for iid in sorted(je_import, key=lambda i: (importe[i]["imported_at"], i)):
        jahr, tag = datum[iid]
        for z in je_import[iid]:
            z["imported_at"] = importe[iid]["imported_at"]
            z["saison"], z["ende"] = saison_aus(jahr, tag), saisonende(tag)
            liste = aus.setdefault(int(z["eid"]), [])
            # mehrere Dateien eines Laufs (Sechser- UND Achterliste): ein Stand
            if liste and liste[-1]["imported_at"] == z["imported_at"]:
                liste[-1] = z
            else:
                liste.append(z)
    return aus


def stationen(staende_einer_saison):
    """Staende EINER Saison -> je Verein der Stand mit den meisten Minuten,
    geordnet nach seinem Importzeitpunkt (die letzte Station zuletzt).

    Der Export zaehlt nach einem Wechsel nur die Zeit beim aktuellen Verein,
    innerhalb eines Vereins addiert er auf: Saisonsumme = Summe der Stationen.
    Die MEISTEN Minuten statt des letzten Imports: eine spaeter erneut
    eingelesene aeltere Datei (ein Winterkader nach dem Sommer-Export) traegt
    sonst den aelteren, kleineren Stand als "letzten" ein.
    """
    beste = {}
    for s in staende_einer_saison:
        c = s.get("club")
        if c not in beste or (s.get("minutes") or 0) >= (beste[c].get("minutes") or 0):
            beste[c] = s
    return sorted(beste.values(), key=lambda s: s.get("imported_at") or "")


def aus_teilen(basis, teile):
    """Kombinierte Zeile aus mehreren Stationen -> (zeile_score, zeile_fit).

    Summiert werden die Zaehlwerte; Raten ergeben sich daraus minuten-
    gewichtet, Quoten aus den Summen. Liga-Koeffizient und Noten-Offset
    gelten JE TEIL: die Engine rechnet mit dem Koeffizienten der Liga von
    `basis`, deshalb wird jeder Teil vorher auf ihn umgerechnet
    (Zaehlwert x Koeff_Teil / Koeff_Basis, Note - Offset_Teil + Offset_Basis).
    Weil Score-Engine und Positions-Fit nicht dieselben Kennzahlen skalieren,
    entstehen zwei Zeilen: eine fuer den Score, eine fuer den Fit. Tore und xG
    skaliert die Engine ohne Elfmeter, xGA nur als Differenz zu den
    Gegentoren. Naeherung bleibt nur bei Wechslern zwischen Ligen fuer
    "Eiskaelte" (Tore ueber xG) und den Fernschussanteil – beide rechnen mit
    den umgerechneten Toren.
    """
    liga = basis.get("league")
    c_basis = moneyball.league_coeff(liga)
    off_basis = moneyball.note_offset(liga)
    summe = [c for c in db.EXPORT_COLS
             if c not in db.EXPORT_TEXT and c not in NICHT_SUMMIERBAR]
    mb, fit = dict(basis), dict(basis)
    for c in summe:
        werte = [t.get(c) for t in teile]
        if any(v is None for v in werte):
            mb[c] = fit[c] = None
            continue
        mb[c] = fit[c] = 0.0
        for t, v in zip(teile, werte):
            f = moneyball.league_coeff(t.get("league")) / c_basis
            mb[c] += v * f if c in MB_SKALIERT else v
            fit[c] += v * f if c in TAKTIK_SKALIERT else v
    # Sonderfaelle: Tore/xG (Engine ohne Elfmeter, Fit mit), xGA (Differenz)
    for c, zeile in (("goals", mb), ("xg", mb), ("goals", fit), ("xg", fit),
                     ("xga", mb), ("xga", fit)):
        if zeile.get(c) is None:
            continue
        zeile[c] = 0.0
        for t in teile:
            f = moneyball.league_coeff(t.get("league")) / c_basis
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
        note = sum(mi * (r - moneyball.note_offset(lg) + off_basis)
                   for mi, r, lg in noten) / m
        mb["rating"] = fit["rating"] = note
    return mb, fit


# ------------------------------------------------------------- Gewichte
def gewicht_d(horizont, alter=None):
    """Abschlag d je Saison zurueck im Horizont (Stand, Art). Bis U23_ALTER
    gilt auch im Transfer-Horizont der Form-Abschlag des Stands."""
    stand, art = horizont
    if art == "transfer" and alter is not None and alter <= U23_ALTER:
        return moneyball.HORIZONT_PARAMETER[(stand, "form")]["d"]
    return moneyball.HORIZONT_PARAMETER[horizont]["d"]


def m_eff(minuten_k, d):
    """Effektive Minuten einer gewichteten Mischung:
    (Σ M_i·d^k)² / Σ M_i·d^(2k). minuten_k: [(Minuten, k)]. Eine einzige
    Saison (k = 0) ergibt genau ihre Minuten."""
    zaehler = sum(m * d ** k for m, k in minuten_k)
    nenner = sum(m * d ** (2 * k) for m, k in minuten_k)
    return zaehler * zaehler / nenner if nenner > 0 else 0.0


def gemischt(werte):
    """Gewichtetes Mittel aus [(wert, gewicht)]; Werte None zaehlen nicht.
    None, wenn nichts uebrig bleibt."""
    paare = [(w, g) for w, g in werte if w is not None and g > 0]
    gs = sum(g for _, g in paare)
    return sum(w * g for w, g in paare) / gs if gs > 0 else None


# ------------------------------------------------------- Saisonbewertung
class Saisonwerte:
    """Bewertung EINER Saison gegen ihre eigene Referenz.

    referenz: angereicherte Zeilen dieser Saison (fuer die aktuelle Saison die
    Referenz der App, fuer Vorsaisons die letzte Station aller Spieler dieser
    Saison), ligen: {eid: Liga} dazu. ref_stats, teams und verteilungen werden
    gebildet, wenn der Aufrufer sie nicht mitgibt (die App gibt fuer die
    aktuelle Saison ihre gecachten mit). Bewertete Spielerzeilen werden je
    Schluessel gemerkt; alles danach ist nur noch zu lesen.
    """

    def __init__(self, saison, referenz, ligen, ref_stats=None, teams=None, verteilungen=None):
        self.saison = saison
        self.referenz = referenz
        self.ligen = ligen
        self.ref_stats = (ref_stats if ref_stats is not None
                          else moneyball.score_referenz(referenz, ligen))
        self.teams = teams if teams is not None else tactics.team_strength(referenz)
        self.verteilungen = verteilungen if verteilungen is not None else {}
        self._zeilen = {}
        self._fit = {}

    def zeilen(self, schluessel, zeile_mb, zeile_fit):
        """Angereicherte Score- und Fit-Zeile eines Spielers in dieser Saison,
        die Score-Zeile mit Wertung in ALLEN Profilen (ein positionsfremder
        Slot fragt das Profil des Slots) -> (mb, fit), gemerkt."""
        if schluessel not in self._zeilen:
            mb, fit = dict(zeile_mb), dict(zeile_fit)
            eid = int(mb["eid"])
            moneyball.add_scores([mb], self.referenz, {eid: mb.get("league")},
                                 ref_stats=self.ref_stats,
                                 zusatz_profile=moneyball.PROFIL_REIHENFOLGE)
            self._zeilen[schluessel] = (mb, fit)
        return self._zeilen[schluessel]

    def fit(self, schluessel, zeile_fit, slot):
        """Positions-Fit (tactics.score_slot) in dieser Saison oder None."""
        k = (schluessel, slot["key"])
        if k not in self._fit:
            dists, _ = tactics.slot_dists(slot, self.referenz, cache=self.verteilungen)
            b = tactics.score_slot(zeile_fit, slot, dists, self.teams)
            self._fit[k] = None if b is None else b["score"]
        return self._fit[k]


def vorsaisons(staende_alle, s_akt):
    """Saisons vor s_akt, die als Vorsaison zaehlen: mit mindestens
    VORSAISON_MIN_SPIELER Spielern in der Historie. Duennere fehlen, etwa
    Teilstaende aus den ersten Wochen eines Spielstands ohne einen Spieler
    ab 900 Minuten."""
    je = {}
    for liste in staende_alle.values():
        for s in {z["saison"] for z in liste}:
            if s is not None and s_akt is not None and s < s_akt:
                je[s] = je.get(s, 0) + 1
    return {s for s, n in je.items() if n >= VORSAISON_MIN_SPIELER}


def _torwart(zeile):
    """Torhueter nach der Export-Position (wie moneyball.export_als_spieler)."""
    return (zeile.get("position") or "").strip().upper().startswith("TW")


def saison_label(saison):
    """2026 -> "2026/27"."""
    return None if saison is None else f"{saison}/{(saison + 1) % 100:02d}"


class Mehrsaison:
    """Aktuelle Saison und Vorsaisons der Spieler eines Stands, gemischt je
    Horizont.

    staende_alle: staende(conn); aktuell: Saisonwerte der aktuellen Referenz
    (der App) – gegen sie wird JEDE Saison bewertet; vorsaisons: Saisons, die
    als Vorsaison zaehlen (vorsaisons()); s_akt/akt_ende: saison_aktuell(bz);
    stand: Grenze des aktuellen Stands (app._pool_stand); roh: {eid:
    export_players-Zeile}; bz: Bezugsdatum fuers Anreichern.

    Gemischt wird nur fuer Zeilen des aktuellen Stands (Export, imported_at ab
    `stand`). Ohne weitere Saison und ohne Wechsel in der laufenden Saison
    bleiben alle Werte unveraendert – bitgleich zum Stand vor D11b.
    """

    def __init__(self, staende_alle, aktuell, vorsaisons, s_akt, akt_ende, stand, roh, bz):
        self.staende = staende_alle
        self.aktuell = aktuell
        self.vorsaisons = vorsaisons
        self.s_akt, self.akt_ende = s_akt, akt_ende
        self.stand = stand or ""
        self.roh = roh
        self.bz = bz
        self._eintraege = {}

    # ---------------------------------------------------------- Saisonliste
    def eintraege(self, p):
        """Saisons eines Spielers -> Liste (neueste zuerst) oder None, wenn
        nicht gemischt wird (keine Zeile des aktuellen Stands).

        Je Eintrag: saison, k, teile (Stationen), minuten, ende (ganze
        Saison), einfach (nur die aktuelle Zeile, Werte vom Aufrufer)."""
        eid = p.get("eid")
        if eid is None or self.s_akt is None:
            return None
        eid = int(eid)
        # Statistik aus einem Export des aktuellen Stands – dieselbe Regel wie
        # das Feld "aktuell" der Tabelle (app._listen_felder)
        if p.get("source") == "export":
            datum = p.get("imported_at")
        elif p.get("stat_quelle") == "export":
            datum = p.get("stat_stand")
        else:
            return None
        if (datum or "") < self.stand:
            return None
        if eid in self._eintraege:
            return self._eintraege[eid]
        st = self.staende.get(eid) or []
        roh = self.roh.get(eid) or p
        tw = _torwart(roh) and TORWART_MISCHEN != "alle"
        if tw and TORWART_MISCHEN == "aus":
            aus = [{"saison": self.s_akt, "k": 0, "teile": [roh],
                    "minuten": roh.get("minutes") or 0, "ende": self.akt_ende, "einfach": True}]
            self._eintraege[eid] = aus
            return aus
        # Torhueter ("verein"): andere Vereine zaehlen nicht, auch nicht in
        # der laufenden Saison
        frueher = ([] if tw else
                   [z for z in stationen([z for z in st if z["saison"] == self.s_akt])
                    if z.get("club") != roh.get("club")])
        akt = {"saison": self.s_akt, "k": 0, "teile": frueher + [roh],
               "minuten": sum(z.get("minutes") or 0 for z in frueher) + (roh.get("minutes") or 0),
               "ende": self.akt_ende, "einfach": not frueher}
        aus = [akt]
        if not moneyball.ist_kalenderjahr_liga(roh.get("league")):
            for s in sorted({z["saison"] for z in st if z["saison"] is not None
                             and z["saison"] < self.s_akt}, reverse=True):
                teile = stationen([z for z in st if z["saison"] == s])
                if tw:
                    teile = [t for t in teile if t.get("club") == roh.get("club")]
                if (s not in self.vorsaisons or not teile
                        or any(moneyball.ist_kalenderjahr_liga(t.get("league")) for t in teile)):
                    continue
                aus.append({"saison": s, "k": self.s_akt - s, "teile": teile,
                            "minuten": sum(t.get("minutes") or 0 for t in teile),
                            "ende": bool(teile[-1].get("ende")), "einfach": False})
        self._eintraege[eid] = aus
        return aus

    def _anderswo(self, p, roh):
        """Hat der Spieler Staende bei einem anderen Verein in einer Saison,
        die zaehlen koennte (aktuelle oder Vorsaison)? Bei "aus" zaehlt jede
        weitere Saison mit."""
        saisons_ = self.vorsaisons | {self.s_akt}
        for z in self.staende.get(int(p["eid"])) or []:
            if z["saison"] not in saisons_:
                continue
            if TORWART_MISCHEN == "aus":
                if z["saison"] != self.s_akt or z.get("club") != roh.get("club"):
                    return True
            elif z.get("club") != roh.get("club"):
                return True
        return False

    def _zeilen(self, eid, e):
        """Bewertete (mb, fit)-Zeilen eines Saison-Eintrags, gegen die
        aktuelle Referenz -> (Saisonwerte, (mb, fit))."""
        sw = self.aktuell
        teile = e["teile"]
        if len(teile) == 1:
            mb_roh = fit_roh = teile[0]
        else:
            mb_roh, fit_roh = aus_teilen(teile[-1], teile)
        mb, fit = moneyball.enrich([moneyball.export_als_spieler(mb_roh),
                                    moneyball.export_als_spieler(fit_roh)], **self.bz)
        return sw, sw.zeilen((e["saison"], eid), mb, fit)

    def _info(self, p, eintraege, werte, horizont):
        """Gemeinsame Felder: saisons (mit Minuten und Gewicht), m_eff,
        hinweise. werte: {Saison: (Wert, Gewicht)} der Saisons, die zaehlen."""
        d = gewicht_d(horizont, p.get("age"))
        saisons = []
        for e in eintraege:
            wert, gew = werte.get(e["saison"], (None, 0.0))
            saisons.append({"saison": e["saison"], "label": saison_label(e["saison"]),
                            "k": e["k"], "minuten": e["minuten"],
                            "gewicht": round(gew, 1), "zaehlt": e["saison"] in werte,
                            "ganz": e["ende"],
                            "vereine": [t.get("club") for t in e["teile"]],
                            "wert": None if wert is None else round(wert, 1)})
        me = m_eff([(e["minuten"], e["k"]) for e in eintraege if e["saison"] in werte], d)
        hinweise = []
        roh = self.roh.get(int(p["eid"])) or p
        if _torwart(roh) and TORWART_MISCHEN != "alle" and self._anderswo(p, roh):
            hinweise.append({"art": "torwart", "text": TORWART_TEXT[TORWART_MISCHEN]})
        if moneyball.ist_kalenderjahr_liga(roh.get("league")):
            hinweise.append({"art": "kalenderjahr", "text": KALENDER_TEXT})
        ganze = [e for e in eintraege if e["ende"] and e["saison"] in werte][:2]
        if len(ganze) == 2 and all(e["minuten"] >= SPRUNG_MIN_MINUTEN for e in ganze):
            delta = werte[ganze[0]["saison"]][0] - werte[ganze[1]["saison"]][0]
            if abs(delta) >= SPRUNG_PUNKTE:
                hinweise.append({"art": "sprung" if delta > 0 else "absturz",
                                 "delta": round(delta, 1),
                                 "saisons": [ganze[1]["saison"], ganze[0]["saison"]],
                                 "text": SPRUNG_TEXT})
        return {"saisons": saisons, "m_eff": round(me), "horizont": horizont[1],
                "hinweise": hinweise}

    # --------------------------------------------------------------- Slot
    def slot(self, p, slot, fit_akt, mb_akt, horizont):
        """Fit und Score im Slot ueber alle Saisons -> dict oder None (nicht
        gemischt). fit_akt/mb_akt: die Werte der aktuellen Zeile, wie das
        Brett sie heute rechnet.

        -> {"fit", "mb" (gemischt), "saisons", "m_eff", "horizont",
        "hinweise"}. Eine Saison zaehlt nur mit Fit UND Score."""
        eintraege = self.eintraege(p)
        if eintraege is None or fit_akt is None or mb_akt is None:
            return None
        eid = int(p["eid"])
        d = gewicht_d(horizont, p.get("age"))
        profil = tactics.slot_profil(slot)
        fits, mbs, werte = [], [], {}
        for e in eintraege:
            if e["einfach"]:
                f, m = fit_akt, mb_akt
            else:
                sw, (mb, fit) = self._zeilen(eid, e)
                f = sw.fit((e["saison"], eid), fit, slot)
                m = moneyball.profil_score(mb, profil)
            if f is None or m is None:
                continue
            g = e["minuten"] * d ** e["k"]
            fits.append((f, g))
            mbs.append((m, g))
            werte[e["saison"]] = (tactics.gesamt(f, m), g)
        fit, mb = gemischt(fits), gemischt(mbs)
        if eintraege[0]["saison"] not in werte or fit is None:
            return None                          # ohne aktuelle Saison oder ohne Minuten
        aus = self._info(p, eintraege, werte, horizont)
        aus["fit"] = round(fit)
        aus["mb"] = round(mb)
        return aus

    # -------------------------------------------------------------- Liste
    def liste(self, p, je, horizont):
        """Listen-Scores je Profil ueber alle Saisons -> dict oder None.

        je: score_je_profil der aktuellen Zeile, wie add_scores ihn rechnet
        ({profil: {score, parts, label, talent, band}}). -> {"je": gemischt
        (score und talent ueber die Saisons, parts und Altersband der
        aktuellen Saison), "info": {profil: {saisons, m_eff, horizont,
        hinweise}}} – add_scores nimmt die Info des Listen-Profils."""
        eintraege = self.eintraege(p)
        if eintraege is None:
            return None
        eid = int(p["eid"])
        d = gewicht_d(horizont, p.get("age"))
        je_saison = []
        for e in eintraege:
            if e["einfach"]:
                je_saison.append((e, je))
            else:
                _sw, (mb, _fit) = self._zeilen(eid, e)
                je_saison.append((e, mb.get("score_je_profil") or {}))
        gemischt_je, info = {}, {}
        for pr, w in je.items():
            sc, ta, werte = [], [], {}
            for e, js in je_saison:
                x = js.get(pr) or {}
                s = x.get("score")
                if s is None:
                    continue
                g = e["minuten"] * d ** e["k"]
                sc.append((s, g))
                ta.append((x.get("talent"), g))
                werte[e["saison"]] = (s, g)
            info[pr] = self._info(p, eintraege, werte, horizont)
            if eintraege[0]["saison"] not in werte:
                gemischt_je[pr] = dict(w, score=None)      # ohne aktuelle Saison kein Score
                continue
            s_mix, t_mix = gemischt(sc), gemischt(ta)
            if s_mix is None:                   # alle Gewichte 0 (keine Minuten)
                gemischt_je[pr] = dict(w)
                continue
            gemischt_je[pr] = dict(w, score=round(s_mix),
                                   talent=None if t_mix is None else round(t_mix))
        return {"je": gemischt_je, "info": info}
