"""Moneyball-Bewertung: leitet aus den Rohstatistiken transparente Kennzahlen
ab – Effizienz statt Ruf. Kern ist die Leistung pro 90 Minuten und die
Ueber-/Unterperformance gegenueber den Expected-Werten (xG/xA)."""
import bisect
import re

from . import charakter


def _p90(value, minutes):
    return round((value or 0) * 90 / minutes, 2) if minutes else 0.0


# Positions-Bitmaske (Stat-Record M-8), per RE dekodiert an bekannten Spielern
# (Trubin TW, Sylla IV, Florentino DM, Guendouzi ZM, Isaksen Flügel, Estêvão OM,
# Pavlidis ST u.v.m.). Kurzlabel je Bit + Zuordnung zu Dashboard-Gruppen.
POS_LABEL = {0: "TW", 1: "AV", 2: "RV", 3: "LV", 4: "IV", 5: "AV", 6: "DM",
             7: "DM", 8: "ZM", 9: "ZM", 10: "ZM", 11: "FL", 12: "OM", 13: "OM",
             14: "ST", 15: "ST"}
POS_GROUP = {0: "tw", 1: "def", 2: "def", 3: "def", 4: "def", 5: "def",
             6: "def", 7: "mid", 8: "mid", 9: "mid", 10: "mid", 11: "off",
             12: "off", 13: "off", 14: "st", 15: "st"}
# Anzeigereihenfolge/Titel der Positions-Dashboards
POS_TABS = [("tw", "Torhüter"), ("def", "Abwehr"), ("mid", "Mittelfeld"),
            ("off", "Offensiv"), ("st", "Sturm"), ("unk", "Unbekannt")]


def pos_mask_from_string(s):
    """FM-Positions-String (z.B. 'M (Z), OM (RLZ), ST (Z)') -> Positions-Bitmaske
    (gleiche Bits wie der RAM), damit Export-Spieler dasselbe Score-Profil
    bekommen. Niedrigstes Bit = Hauptgruppe.

    FM fasst mehrere Positionen mit SCHRAEGSTRICH zusammen, wenn sie sich
    dieselbe Seitenangabe teilen: 'M/OM (R)', 'V/FV (R)', 'V/FV/M/OM (R)'.
    Frueher passte bei solchen Koepfen kein einziger Zweig, die Maske blieb 0
    und der Spieler bekam GAR KEIN Score-Profil - er fiel lautlos aus jeder
    Auswertung. Live aufgefallen an Francisco Conceicao ('M/OM (R)'), Rafel
    Obrador ('V/FV/M (L)') und Joao Veloso ('M/OM (Z)').
    """
    mask = 0
    for tok in (s or "").split(","):
        t = tok.strip()
        if not t:
            continue
        koepfe = re.split(r"[ (]", t)[0]
        m = re.search(r"\(([^)]*)\)", t)
        sd = m.group(1) if m else ""
        # Die Klammer gilt fuer ALLE Koepfe des Tokens: 'V/FV (R)' heisst
        # rechter Verteidiger UND rechter Fluegelverteidiger.
        for head in koepfe.split("/"):
            if head == "TW":
                mask |= 1 << 0
            elif head in ("V", "FV"):
                if "Z" in sd or (head == "V" and not sd):
                    mask |= 1 << 4             # Innenverteidiger
                if "R" in sd or "L" in sd or head == "FV":
                    mask |= 1 << 2             # Aussenverteidiger
            elif head == "DM":
                mask |= 1 << 7
            elif head == "M":
                mask |= (1 << 8) if ("R" in sd or "L" in sd) else (1 << 10)
            elif head == "OM":
                mask |= 1 << 12
            elif head == "ST":
                mask |= 1 << 14
    return mask


def pos_info(mask, is_gk=False):
    """(Gruppen-Codes, Kurzlabel) aus der Positionsmaske; TW ueber is_gk sicher."""
    bits = [b for b in range(16) if mask & (1 << b)]
    groups, labs = [], []
    for b in bits:
        g, l = POS_GROUP[b], POS_LABEL[b]
        if g not in groups:
            groups.append(g)
        if l not in labs:
            labs.append(l)
    if is_gk and "tw" not in groups:
        groups.insert(0, "tw")
        labs.insert(0, "TW")
    if not groups:
        return ["unk"], "?"
    return groups, ", ".join(labs)


# Altersbaender fuer altersbereinigte Vergleiche. Ein 19-Jaehriger im 90.
# Perzentil SEINES Jahrgangs ist ein Talent, ein 28-Jaehriger dort ist es nicht.
AGE_BANDS = [(0, 18, "U18"), (19, 20, "19-20"), (21, 22, "21-22"),
             (23, 25, "23-25"), (26, 29, "26-29"), (30, 99, "30+")]
PROSPECT_AGE = 21          # bis hier zaehlt jemand fuers Talent-Board


def age_band(age):
    if age is None:
        return None
    for lo, hi, lab in AGE_BANDS:
        if lo <= age <= hi:
            return lab
    return None


def compute_age(birth_year, birth_day, ref_year, ref_day=None):
    """Alter aus dem RAM-Geburtsdatum. ref_day (Tag im Jahr) beruecksichtigt,
    ob der Geburtstag im Bezugsjahr schon war – ohne ihn ist das Ergebnis um
    bis zu ein Jahr zu hoch."""
    if not birth_year or not ref_year:
        return None
    a = int(ref_year) - int(birth_year)
    if ref_day and birth_day and int(birth_day) > int(ref_day):
        a -= 1
    return a if 10 <= a <= 60 else None


def _trenntag(tage):
    """Tag im Jahr, der "Geburtstag schon gewesen" am besten trennt.
    tage: [(birth_day, gehabt)] -> (tag, Zahl der Widersprueche)."""
    gehabt, offen = [0] * 367, [0] * 367
    for bd, g in tage:
        (gehabt if g else offen)[max(1, min(366, int(bd)))] += 1
    rest, bis_offen = sum(gehabt), 0
    bester = (1, None)
    for t in range(1, 367):
        rest -= gehabt[t]              # schon gewesen, aber bd > t: Widerspruch
        bis_offen += offen[t]          # noch nicht, aber bd <= t: Widerspruch
        fehler = rest + bis_offen
        if bester[1] is None or fehler < bester[1]:
            bester = (t, fehler)
    return bester


def bezugsdatum(paare):
    """(Spieljahr, Tag im Jahr) eines Exports aus RAM-Geburtsdaten und Export-Altern.

    paare: [(birth_year, birth_day, export_alter)]. Fuer jeden Spieler ist
    Geburtsjahr + Alter entweder das Spieljahr Y (Geburtstag schon gewesen)
    oder Y - 1. Gewaehlt wird das Y, das die meisten Spieler WIDERSPRUCHSFREI
    erklaert: Spieler mit Y oder Y - 1, abzueglich derer, die kein Trenntag
    richtig einordnet. Der frueher benutzte Median lag bei Summer3 (2027: 700
    Spieler, 2028: 502) ein Jahr zu tief. Auch "der hoechste Wert mit 10 %
    Anteil" reicht nicht: bei einem Export Anfang Januar hatten erst ~5 %
    Geburtstag, das Vorjahr gewann, und genau diese Spieler kamen ein Jahr zu
    jung heraus (vom Junior-Dev im Test gefunden). Einzelne falsche EID-Joins
    erklaeren nur sich selbst und gewinnen nie.
    Der Tag trennt beide Gruppen (birth_day <= Tag: schon Geburtstag) – damit
    stimmt compute_age auch vor dem Geburtstag.
    -> (jahr, tag); (None, None) bei weniger als 5 Paaren, tag None ohne
    Geburtstage.
    """
    from collections import Counter
    gueltig = [(int(by), bd, int(a)) for by, bd, a in paare if by and a]
    if len(gueltig) < 5:
        return None, None
    zaehl = Counter(by + a for by, _, a in gueltig)
    bestes = None
    for jahr in zaehl:
        tage = [(bd, by + a == jahr) for by, bd, a in gueltig
                if bd and by + a in (jahr, jahr - 1)]
        tag, fehler = _trenntag(tage) if tage else (None, 0)
        erklaert = zaehl[jahr] + zaehl.get(jahr - 1, 0) - (fehler or 0)
        if bestes is None or (erklaert, jahr) > bestes[0]:
            bestes = ((erklaert, jahr), jahr, tag)
    return bestes[1], bestes[2]


def enrich(players, ref_year=None, ref_day=None):
    """Ergaenzt jeden Spieler um abgeleitete Moneyball-Kennzahlen.

    ref_year: Bezugsjahr fuers Alter (aktuelles Spieljahr). Fehlt es, bleibt
    ein bereits vorhandenes Alter (z.B. aus dem Export) unangetastet."""
    out = []
    for p in players:
        p = dict(p)
        by = p.get("birth_year")
        if by:
            a = compute_age(by, p.get("birth_day"), ref_year, ref_day)
            if a is not None:
                p["age"] = a            # RAM schlaegt Export (immer aktuell)
        p["age_band"] = age_band(p.get("age"))
        m = p.get("minutes") or 0
        g, xg, xa = p.get("goals") or 0, p.get("xg") or 0.0, p.get("xa") or 0.0
        pt, po = p.get("pass_try") or 0, p.get("pass_ok") or 0
        p["goals_p90"] = _p90(g, m)
        p["xg_p90"] = _p90(xg, m)
        p["xa_p90"] = _p90(xa, m)
        p["shots_p90"] = _p90(p.get("shots_on", 0), m)
        p["duels_p90"] = _p90(p.get("duels", 0), m)
        p["dribbles_p90"] = _p90(p.get("dribbles", 0), m)
        # Abschluss-Effizienz: Tore ueber/unter Erwartung
        p["finishing"] = round(g - xg, 2)
        # offensive Gesamtbeteiligung pro 90 (Tore + erwartete Vorlagen)
        p["attack_p90"] = round(p["goals_p90"] + p["xa_p90"], 2)
        p["pass_pct"] = round(100 * po / pt) if pt else 0
        # Ø-Note (aus RAM verifiziert) + Vorlagen + Quoten der neuen Rohfelder
        p["rating"] = round(float(p.get("rating") or 0), 2)
        p["assists"] = int(p.get("assists") or 0)
        p["assists_p90"] = _p90(p["assists"], m)
        dt = int(p.get("duels_total") or 0)
        p["duel_pct"] = round(100 * (p.get("duels") or 0) / dt) if dt else 0
        ht = int(p.get("headers_total") or 0)
        p["header_pct"] = round(100 * (p.get("headers_won") or 0) / ht) if ht else 0
        st = int(p.get("shots_total") or 0)
        p["shot_acc"] = round(100 * (p.get("shots_on") or 0) / st) if st else 0
        # echtes Pressing + progressive Paesse (RAM-verifiziert via Kader-Export)
        pt2 = int(p.get("press_try") or 0)
        p["press_pct"] = round(100 * (p.get("press_win") or 0) / pt2) if pt2 else 0
        p["press_p90"] = _p90(p.get("press_win") or 0, m)
        p["prog_p90"] = _p90(p.get("prog_passes") or 0, m)
        p["int_p90"] = _p90(p.get("interceptions") or 0, m)
        p["keyp_p90"] = _p90(p.get("key_passes") or 0, m)
        p["loss_p90"] = _p90(p.get("losses") or 0, m)
        p["rec_p90"] = _p90(p.get("recoveries") or 0, m)
        # Ballverluste je 100 Ballaktionen statt je 90 Minuten. Die /90-Variante
        # bestraft ballaktive Spieler: wer den Ball fordert, verliert ihn
        # zwangslaeufig oefter als jemand, der ihn sofort abgibt.
        akt = (p.get("pass_try") or 0) + (p.get("dribbles") or 0)
        p["loss_rate"] = round(100 * (p.get("losses") or 0) / akt, 1) if akt else None
        # Non-Penalty-Tore: Elfer-Offset noch unverifiziert (kein Elfer im Save)
        # -> pen_goals ist 0, bis der Matcher ihn nach den ersten Elfern pinnt.
        p["np_goals"] = int(p.get("goals") or 0) - int(p.get("pen_goals") or 0)
        # Verlaesslichkeit der Stichprobe (Minuten); < 1 Spiel = wackelig
        p["reliability"] = min(1.0, round(m / 270, 2))  # ~3 Spiele = voll
        # Torhueter: Goals Prevented = erwartete minus tatsaechliche Gegentore
        # (>0 = haelt besser als der Schnitt). Rein aus dem RAM verifiziert.
        # is_gk setzt nur der RAM-Scanner. Export-Spieler kamen ohne, und weil
        # _profile() daran haengt, hatte KEIN gescouteter Torwart je einen
        # Score – auch nicht nach dem Import von Gegentoren und xGA. Die
        # Positionszeichenkette ist eindeutig ('TW'), also reicht sie.
        # Steht eine Export-Position da, entscheidet SIE: das RAM-Flag ist
        # unzuverlaessig – im letzten Snapshot trugen es 976 Spieler ohne
        # TW-Position, darunter Bellingham und zwei Trainer. Mit dem alten
        # Torwart-Profil fiel das kaum auf (verhinderte Tore 0 -> Mittelmass),
        # mit 30 % Ø-Note stand Bellingham als Torwart mit 81 in der Tabelle.
        pos_txt = str(p.get("position") or "").strip().upper()
        p["is_gk"] = pos_txt.startswith("TW") if pos_txt else bool(p.get("is_gk"))
        # Ø-Note ligabereinigt: FM vergibt die Note eigentlich relativ zum
        # Spielniveau, nur nicht ueberall – siehe LEAGUE_NOTE_OFFSET.
        p["rating_adj"] = round(p["rating"] - note_offset(p.get("league")), 2) if p["rating"] else 0.0
        conc = int(p.get("conceded") or 0)
        # xGA fehlt bei Torhuetern aus Ligen ohne Detailstatistik (Importer
        # setzt None) – dann gibt es auch keine verhinderten Tore. Vorher
        # wurde None zu 0.0 und der Keeper stand mit "verhindert −40,00" da.
        xga_bekannt = p.get("xga") is not None
        xga = float(p.get("xga") or 0.0)
        p["conceded"], p["xga"], p["apps"] = conc, (xga if xga_bekannt else None), int(p.get("apps") or 0)
        p["goals_prevented"] = round(xga - conc, 2) if xga_bekannt else None
        p["gp_p90"] = _p90(xga - conc, m) if xga_bekannt else None
        p["conceded_p90"] = _p90(conc, m)
        # Torwart-Kennzahlen NUR aus dem Export. Der Speicher liefert fuer
        # Torhueter zwar 'conceded' und 'xga', aber unbrauchbar: in der RAM-
        # Kohorte stehen Keeper mit 2970 Minuten und 0 Gegentoren, die
        # Korrelation Gegentore/90 zu Note ist dort exakt 0,00 (im Export
        # -0,85). Damit sind auch die alten gp_adj-Verteilungen aus der Kohorte
        # Rauschen gewesen. Was hier folgt, gilt deshalb nur, wenn die Zeile
        # aus dem Export stammt; sonst None, und die Score-Engine ueberspringt.
        aus_export = (p.get("stat_quelle") == "export" or p.get("source") == "export"
                      or p.get("saves_held") is not None)
        sv = [p.get(k) for k in ("saves_tipped", "saves_parried", "saves_held")]
        saves = sum(int(v or 0) for v in sv) if any(v is not None for v in sv) else None
        p["saves"] = saves
        # Schuesse aufs Tor gegen ihn = Paraden + Gegentore (Elfer inklusive,
        # FM trennt sie im Export nicht heraus)
        sot = (saves + conc) if saves is not None else None
        p["sot_faced"] = sot
        p["save_pct"] = round(100.0 * saves / sot, 1) if sot else None
        held = p.get("saves_held")
        p["held_pct"] = (round(100.0 * int(held) / saves, 1)
                         if (saves and held is not None) else None)
        # Verhinderte Tore je 100 Schuesse und Paradenquote: nur noch zur
        # Anzeige. Beides wiederholt sich zwischen zwei Saisonhaelften nicht
        # (Split-Half r 0,07 bzw. 0,19) und steht in keiner Gewichtung.
        gp_tot = (xga - conc) if (aus_export and p.get("xga") is not None) else None
        p["gp_total"] = None if gp_tot is None else round(gp_tot, 2)
        p["gp_shot"] = round(100.0 * gp_tot / sot, 1) if (gp_tot is not None and sot) else None
        # Note ueber Erwartung (NüE): die Ø-Note eines Torwarts haengt stark an
        # den Gegentoren seiner Mannschaft (Gerade R1, siehe GK_NOTE_A). Was
        # darueber hinausgeht, ist sein eigener Anteil – ueber Saisonhaelften
        # stabil (Split-Half r 0,58), waehrend Paraden und verhinderte Tore es
        # nicht sind (0,02–0,19). Die Schuesse aufs Tor/90 halten die NüE
        # neutral gegenueber dem Beschuss (FM belohnt Paraden kaum). Gerechnet
        # wird bewusst mit den Anzeigewerten (conceded_p90 und rating_adj auf
        # 2 Stellen gerundet) – wie im Abnahmeskript des Datenanalysten; die
        # Abweichung zur ungerundeten Gerade bleibt unter 0,004.
        if (p["is_gk"] and aus_export and p["rating"] > 0 and m >= 90
                and 0.0 < p["conceded_p90"] < 4.0):
            sot90 = (sot or 0) * 90.0 / m
            erw = GK_NOTE_A - GK_NOTE_B * p["conceded_p90"] + GK_NOTE_C * sot90
            p["note_resid"] = round(p["rating_adj"] - erw, 3)
            # Fuers Rechnen zur Mitte geschrumpft, m/(m+1700) – in Profil UND
            # Slot (tactics._wert), bewusste Ausnahme: alle anderen Slot-
            # Kennzahlen gehen roh ein, beim Torwart mit 40 % NüE und
            # Reliabilitaet 0,35 nach 900 Minuten ginge das nicht gut.
            # Angezeigt wird weiter der rohe Wert (note_resid).
            p["note_resid_s"] = round(p["note_resid"] * m / (m + NUE_SHRINK_MIN), 3)
        else:
            p["note_resid"] = p["note_resid_s"] = None
        p["err_p90"] = None if p.get("errors") is None else _p90(p.get("errors"), m)
        # Persoenlichkeit und Medienumgang (sichtbare Beschreibungen, keine
        # verdeckten Werte – siehe charakter.py)
        charakter.anreichern(p)
        # Positionsgruppen (fuer die Positions-Dashboards) + Kurzlabel
        groups, label = pos_info(int(p.get("pos_mask") or 0), p["is_gk"])
        p["pos_groups"], p["pos_label"] = groups, label
        out.append(p)
    return out


# vordefinierte Ranglisten fuer typische Moneyball-Fragen
RANKINGS = {
    "Moneyball-Score": ("score", True),
    "Vereins-DNA (passstark + Ballgewinner)": ("dna", True),
    "Talent-Board (U21, für ihr Alter stark)": ("prospect", True),
    "Für sein Alter stark (alle Jahrgänge)": ("talent", True),
    "Jüngste zuerst": ("age", False),
    "Schnäppchen (Score/Mio €)": ("value_score", True),
    # Anti-Ruf-Signal: Abstand zum leistungsbasierten Schaetzwert (valuemodel).
    # Bleibt leer, solange die Regression kein belastbares Signal findet.
    "Unterbewertet (Fair-Value-Modell)": ("value_delta_pct", True),
    "Höchster Marktwert": ("value_m", True),
    # Persoenlichkeit: sichtbare Beschreibung, uebersetzt in Entwicklung und
    # Mentalitaet (charakter.py). Steht neben dem Score, nicht darin.
    "Charakter (Persönlichkeit)": ("pers_score", True),
    "Höchste Ø-Note": ("rating", True),
    "Torhüter: Note über Erwartung": ("note_resid", True),
    "Beste Torjäger (xG/90)": ("xg_p90", True),
    # Tore minus xG wiederholt sich zwischen zwei Saisonhaelften nicht (Split-
    # Half r 0,12) – keine Qualitaet, sondern Glueck oder Pech. Als Liste fuer
    # Sell-High/Buy-Low nuetzlich, deshalb ohne Qualitaetsbehauptung benannt.
    "Über xG getroffen (Glück?)": ("finishing", True),
    "Unter xG geblieben (Pech?)": ("finishing", False),
    "Kreativität (xA/90)": ("xa_p90", True),
    "Vorlagen/90": ("assists_p90", True),
    "Offensive Beteiligung/90": ("attack_p90", True),
    # Zweikampf-Bestenliste ueber die gewonnenen Duelle/90 (Split-Half IV
    # r 0,75). Die "Zweikampfquote %" stand hier auch – eine Rauschgroesse
    # (r 0,01–0,10) gehoert nicht als Bestenliste heraus.
    "Gewonnene Zweikämpfe/90": ("duels_p90", True),
    "Pressing-Monster (/90)": ("press_p90", True),
    "Progressive Pässe/90": ("prog_p90", True),
    "Schlüsselpässe/90": ("keyp_p90", True),
    "Ballsicher (wenigste Verluste/90)": ("loss_p90", False),
    "Ballgewinner (Recoveries/90)": ("rec_p90", True),
    "Dribbler/90": ("dribbles_p90", True),
    "Passsicherheit %": ("pass_pct", True),
}


def enrich_export(players):
    """Reichert Export-Spieler an – inkl. Marktwert-Effizienz (echtes Moneyball)."""
    out = []
    for p in players:
        p = dict(p)
        m = p.get("minutes") or 0
        g, xg, xa = p.get("goals") or 0, p.get("xg") or 0.0, p.get("xa") or 0.0
        p["finishing"] = round(g - xg, 2)
        p["goals_p90"] = _p90(g, m)
        p["xg_p90"] = _p90(xg, m)
        p["xa_p90"] = _p90(xa, m)
        p["attack_p90"] = round(_p90(g, m) + _p90(xa, m), 2)
        val = p.get("value") or 0
        p["value_m"] = round(val / 1e6, 1) if val else None
        # Schnaeppchen: offensive Produktion pro 90 je 1 Mio Marktwert
        p["value_score"] = round(p["attack_p90"] / (val / 1e6), 3) if val else None
        out.append(p)
    return out


EXPORT_RANKINGS = {
    "Schnäppchen (Leistung/Wert)": ("value_score", True),
    "Über xG getroffen (Glück?)": ("finishing", True),
    "Beste Torjäger (xG/90)": ("xg_p90", True),
    "Kreativität (xA/90)": ("xa_p90", True),
    "Höchste Ø-Note": ("rating", True),
    "Günstigste (Marktwert)": ("value_m", False),
}


def rank(players, key, descending=True, min_minutes=45, limit=None):
    """Rangliste nach einer Kennzahl; blendet Mini-Stichproben aus.

    Spieler ohne Wert in dieser Kennzahl fallen RAUS, statt mit 0 mitzulaufen.
    Frueher sortierte das direkt ueber p.get(key, 0) und ist an None zerbrochen
    (TypeError: '<' not supported between 'NoneType' and 'float'), sobald ein
    Feld leer war – etwa 'value_score' bei jedem Spieler, der laut Export
    'Steht nicht zum Verkauf' ist. Ein 0-Ersatz waere auch inhaltlich falsch:
    in 'Guenstigste (Marktwert)' stuende der Spieler ohne Marktwert dann ganz
    oben, obwohl sein Wert schlicht unbekannt ist.
    """
    pool = [p for p in players
            if (p.get("minutes") or 0) >= min_minutes and p.get(key) is not None]
    pool.sort(key=lambda p: p[key], reverse=descending)
    return pool[:limit] if limit else pool


# ---------------------------------------------------------------- Score-Engine

# Liga-Koeffizienten: Produktion in staerkeren Ligen zaehlt mehr, in
# schwaecheren weniger. Der Anker (Liga Portugal = 1.00) stammt aus der
# Benfica-Zeit und bleibt bewusst stehen – seit dem Wechsel zu Manchester
# United (Sommer, 2. Saison) ist die eigene Liga die Premier League mit 1.20.
# Auf die Rangfolge hat der Anker keinen Einfluss (Perzentile), er bestimmt
# nur, wo eine UNBEKANNTE Liga (=> 1.00) einsortiert wird: knapp unter den
# grossen fuenf, das ist fuer nicht gelistete Ligen die richtige Vermutung.
# Quelle der Liganamen: FM-HTML-Export (EID-Join).
LEAGUE_COEFFS = {
    "Premier League": 1.20, "Spaniens First Division": 1.15,
    "Bundesliga": 1.12, "Serie A": 1.12, "Ligue 1 Uber Eats": 1.05,
    "Portugals Premier League": 1.00, "Liga Portugal": 1.00,
    "Eredivisie": 0.92, "Brasileirão Betano Série A": 0.90,
    "Jupiler Pro League": 0.88, "Sky Bet Championship": 0.85,
    "Argentiniens Primera División": 0.82, "Türkeis Super League": 0.80,
    "Russlands Premier League": 0.78, "Ukraines Premier League": 0.75,
    "The William Hill Premiership": 0.72, "3F Superliga": 0.72,
    "Mexikos First Division": 0.72, "MLS": 0.72,
    "Saudi Arabiens Professional League": 0.70, "Österreichs Bundesliga": 0.68,
    "Schweizer Super League": 0.70, "Griechenlands Super League": 0.68,
    "Kroatiens SuperSport HNL": 0.65, "Polens PKO Ekstraklasa": 0.62,
    "Tschechiens Chance Liga": 0.62, "2. Bundesliga": 0.60,
    "Keuken Kampioen Divisie": 0.60, "Serie B": 0.58, "Ligue 2 BKT": 0.58,
    "Österreichs 1. Liga": 0.55, "Sky Bet League One": 0.55,
}
SHRINK_MIN = 180     # Shrinkage-Prior: ~2 Spiele
# Torwart: Erwartungsgerade der Ø-Note ("R1"), steckt in enrich() ->
# note_resid (Note ueber Erwartung, NüE):
#     Note_erw = GK_NOTE_A − GK_NOTE_B · Gegentore/90 + GK_NOTE_C · Schuesse aufs Tor/90
# Geschaetzt vom Datenanalysten (Auftrag 3, 23.09.2026) auf 159 Keeper-Saisons
# ab 1350 Minuten, OHNE saudische Ligen und Kalenderjahr-Ligen, R² 0,69;
# gerundet 7,442 / 0,681 / 0,039. Die fruehere Gerade (7,652 − 0,681·GT/90)
# war mit fuenf saudischen Keepern geschaetzt und zu steil: sie hob Keeper
# hinter loechrigen Abwehrreihen (NüE gegen Schuesse/90 r +0,26, mit R1 −0,01).
GK_NOTE_A = 7.442012
GK_NOTE_B = 0.681315
GK_NOTE_C = 0.039018
# Stabilisierungspunkt der NüE: nach ~1700 Minuten ist sie zur Haelfte Signal
# (Split-Half, 111 Keeper-Saisons; Reliabilitaet 0,35 nach 900, 0,64 nach
# 3060 Minuten). Geschrumpft wird in Profil UND Slot, siehe enrich().
NUE_SHRINK_MIN = 1700


def league_coeff(league):
    return LEAGUE_COEFFS.get(league, 1.0) if league else 1.0


# Ø-Note je Liga: FM vergibt die Note relativ zum Spielniveau – fast ueberall.
# Gemessen an den Sommer-Exporten (Feldspieler ab 1800 Min): Premier League
# 7,02, Serie A/Bundesliga 6,95, Ligue 1/Portugal 6,93, Eredivisie 6,89 –
# alles innerhalb eines Zehntels. Die beiden saudischen Ligen liegen mit 7,28
# und 7,37 klar darueber (Torhueter 7,48 gegen 6,4–6,85), vermutlich weil FM
# sie ohne Detailstatistik simuliert (0 Paraden, 0 xG). Ohne Abzug standen
# fuenf Saudi-Keeper unter den besten sieben. Die Werte hier sind die
# gemessenen Abstaende zum Schnitt der grossen Ligen; alles andere bleibt 0.
LEAGUE_NOTE_OFFSET = {
    "Saudi Arabiens Professional League": 0.33,
    "Saudi Arabiens First Division League": 0.42,
}


def note_offset(league):
    return LEAGUE_NOTE_OFFSET.get(league, 0.0) if league else 0.0


# Score-Profile: wenige Kennzahlen mit hohem Impact – und BEWUSST OHNE
# Spielidee. Frueher steckte "hohes Pressing + passbasiert" in jedem Profil
# (Pressing 10-25 %, Passquote, progressive Paesse). Seit es Positions-Fit
# (tactics.py, je Slot) und Vereins-DNA (add_dna) gibt, wuerde derselbe Stil
# dreifach zaehlen. Der Score misst deshalb nur noch ERGEBNISSE, die in jedem
# Fussball zaehlen: Tore, Vorlagen, gewonnene Zweikaempfe, Abgefangenes,
# Klaerungen, verhinderte Gegentore – plus FMs Ø-Note, die ohnehin stilneutral
# ist. Ballsicherheit bleibt mit kleinem Gewicht: den Ball nicht zu verlieren
# ist in jedem Stil gut, nur das fruehere Gewicht war Stil.
#
# Drei Achsen, die nichts teilen: Score = wie gut, Fit = passt in den Slot,
# DNA = passt zur Spielidee. Das Talent-Board leitet sich vom Score ab und ist
# damit ebenfalls stilneutral geworden.
#
# press_adj / prog_adj / pass_pct werden in _score_metrics weiterhin berechnet
# (Pressing = ECHTE erfolgreiche Pressingaktionen/90, M+108; progressive
# Paesse = Feld M+102, beide per Kader-Export 7/7 verifiziert) – gebraucht
# werden sie jetzt von Fit und DNA. Format: (metrik, label, gewicht, invertiert)
PROFILES = {
    # Stuermer: Chancen (npxG) tragen, die Tore selbst bleiben als Ergebnis
    # drin. "Eiskaelte" (Tore ueber xG) ist gestrichen: Die Annahme, beim
    # Stuermer sei die Abweichung von xG die Faehigkeit, haelt in diesem
    # Savegame nicht – sie wiederholt sich zwischen zwei Saisonhaelften nicht
    # (Split-Half r 0,06–0,09, nach Vereinswechsel negativ), und xG/90 sagt die
    # Tore der naechsten Haelfte besser vorher als die Tore selbst. Den kleinen
    # echten Abschluss-Anteil tragen die npTore/90 mit. Profil r 0,61 -> 0,69.
    # 35 % Chancen / 25 % Tore / 10 % Schussgenauigkeit / 10 % Zuarbeit /
    # 10 % Note / 5 % Zweikampf / 5 % Schussauswahl
    "st":  [("xg_adj", "npxG/90", .35, False),
            ("goals_adj", "npTore/90", .25, False),
            ("shot_acc", "Schussgenauigkeit %", .10, False),
            ("xa_adj", "xA/90", .10, False),
            ("rating", "Ø-Note", .10, False),
            ("duelwon_adj", "Gewonnene Zweikämpfe/90", .05, False),
            ("long_share", "Anteil Fernschusstore", .05, True)],
    # Fluegel/OM: Erwartungswerte statt Zaehlwerte (Tore und Vorlagen sind
    # Stichprobenrauschen, xG und xA sind Prozess), dazu Ballprogression
    # (die Waehrung des modernen Fluegels), Kreation jenseits der Vorlage
    # (Chancen kreiert korreliert nur 0.49 mit Schluesselpaessen – zwei Masse),
    # Schussauswahl (Anteil Fernschusstore, invertiert) und Ballverluste JE
    # 100 AKTIONEN statt je 90 – der invertierte Fluegel verliert den Ball
    # systembedingt oft, entscheidend ist, wie oft je Ballkontakt.
    "off": [("xg_adj", "npxG/90", .25, False),
            ("xa_adj", "xA/90", .20, False),
            ("rating", "Ø-Note", .15, False),
            ("dribble_adj", "Dribblings/90", .10, False),
            ("prog_adj", "Progressive Pässe/90", .05, False),
            ("keyp_adj", "Schlüsselpässe/90", .05, False),
            ("chance_adj", "Chancen kreiert/90", .05, False),
            ("long_share", "Anteil Fernschusstore", .05, True),
            ("loss_rate", "Ballverluste je 100 Aktionen", .10, True)],
    # Mittelfeld: Kreation als Prozess (xA + Schluesselpaesse statt Vorlagen),
    # Progression zurueck im Profil (Output, nicht Stil – jedes System braucht
    # sie), Pressing als ERFOLGSQUOTE statt Volumen, Zweikampf als gewonnene
    # Duelle/90 (Volumen x Quote), Defensivbeitrag als Komposit aus Abfangen,
    # Klaerungen, Ballgewinnen und Blocks statt nur Abgefangenem.
    # 25 % Kreation / 5 % Tore / 15 % Note / 10 % Progression / 10 % Pressing /
    # 10 % Zweikampf / 15 % Defensiv / 10 % Ball
    "mid": [("xa_adj", "xA/90", .15, False),
            ("keyp_adj", "Schlüsselpässe/90", .10, False),
            ("goals_adj", "npTore/90", .05, False),
            ("rating", "Ø-Note", .15, False),
            ("prog_adj", "Progressive Pässe/90", .10, False),
            ("press_pct", "Pressing-Erfolgsquote %", .10, False),
            ("duelwon_adj", "Gewonnene Zweikämpfe/90", .10, False),
            ("defakt_adj", "Abgefangen+Klärungen/90", .05, False),
            ("rec_adj", "Ballgewinne/90", .05, False),
            ("block_adj", "Blocks/90", .05, False),
            ("loss_p90", "Ballsicherheit", .10, True)],
    # Innenverteidiger: Zweikampf als gewonnene Duelle/90 (Menge x Quote in
    # einer Zahl), Kopfball als Volumen UND Quote (111 Versuche je Halbserie
    # tragen eine Quote, ~36 Bodenduelle nicht), Abfangen, Klaerungen,
    # Ballgewinne als proaktives Verteidigen, Progression mit kleinem Gewicht.
    # Zweikampfquote (Split-Half r 0,03) und Fehler vor Gegentoren (0,17) sind
    # Rauschen und gestrichen; ihr Gewicht geht an die gewonnenen Duelle und
    # an die Note (Variante A des Datenanalysten, Profil r 0,53 -> 0,59, sagt
    # die Note der naechsten Haelfte am besten vorher).
    # 25 % Zweikampf / 20 % Kopfball / 25 % Abfangen+Klaeren+Ballgewinne /
    # 5 % Progression / 20 % Note / 5 % Ball
    "iv":  [("duelwon_adj", "Gewonnene Zweikämpfe/90", .25, False),
            ("headwon_adj", "Gewonnene Kopfbälle/90", .10, False),
            ("header_pct", "Kopfball %", .10, False),
            ("intercept_adj", "Abgefangen/90", .10, False),
            ("clear_adj", "Klärungen/90", .10, False),
            ("rec_adj", "Ballgewinne/90", .05, False),
            ("prog_adj", "Progressive Pässe/90", .05, False),
            ("rating", "Ø-Note", .20, False),
            ("loss_p90", "Ballsicherheit", .05, True)],
    # Aussenverteidiger: Kreation als Prozess (xA + Schluesselpaesse), Flanken
    # als Volumen und Quote (der zentrale AV-Output, vorher Gewicht null),
    # Progression fuer den einrueckenden AV, Sprints als einziges Laufmass,
    # das der Export liefert, Zweikampf als gewonnene Duelle/90, Defensiv-
    # komposit wie beim IV (Abfangen, Ballgewinne, Fehler vor Gegentoren).
    # 25 % Kreation / 15 % Flanken / 10 % Progression / 5 % Sprints /
    # 10 % Zweikampf / 15 % Defensiv / 10 % Note / 5 % Tore / 5 % Ball
    "av":  [("xa_adj", "xA/90", .15, False),
            ("keyp_adj", "Schlüsselpässe/90", .10, False),
            ("cross_adj", "Angekommene Flanken/90", .10, False),
            ("cross_pct", "Flankenquote %", .05, False),
            ("dribble_adj", "Dribblings/90", .05, False),
            ("prog_adj", "Progressive Pässe/90", .05, False),
            ("sprint_adj", "Sprints/90", .05, False),
            ("duelwon_adj", "Gewonnene Zweikämpfe/90", .10, False),
            ("intercept_adj", "Abgefangen/90", .05, False),
            ("rec_adj", "Ballgewinne/90", .05, False),
            ("err_adj", "Fehler vor Gegentoren/90", .05, True),
            ("rating", "Ø-Note", .10, False),
            ("goals_adj", "npTore/90", .05, False),
            ("loss_p90", "Ballsicherheit", .05, True)],
    # Torhueter – zweimal nachgeschaerft. Im Sommer der 2. Saison flog
    # "verhinderte Tore/90" raus (Glueck, kein Koennen). Die Split-Half-
    # Pruefung des Datenanalysten (23.09.2026, 111 Keeper-Saisons ueber drei
    # Saisons, ohne saudische Ligen) zeigte dann: auch der Ersatz "je Schuss"
    # und die Paradenquote wiederholen sich nicht (r 0,07 / 0,19), ebenso die
    # Fehler vor Gegentoren (−0,12). Die frueher genannten 0,87 (Note) und
    # 0,77 (NüE) enthielten fuenf saudische Keeper; ohne sie 0,71–0,79 bzw.
    # 0,50–0,58. Shot-Stopping laesst sich mit den Exportdaten nicht messen.
    #
    # Deshalb tragen nur Note, Note ueber Erwartung (NüE, Gerade R1,
    # geschrumpft – siehe enrich) und die Gegentore/90 (stabil, aber zum
    # grossen Teil Abwehr). Profil r 0,48 -> 0,67. 'Par %' und 'xSv %' aus
    # dem Export bleiben draussen (kaputt), Zu-Null-Spiele auch (Team).
    "tw":  [("rating", "Ø-Note", .45, False),
            ("note_resid", "Note über Erwartung", .40, False),
            ("conceded_adj", "Gegentore/90", .15, True)],
}
# Gewichte muessen je Profil 1.0 ergeben – sonst liegen die Scores der
# Profile auf verschiedenen Skalen und "72" hiesse beim Sechser etwas anderes
# als beim Stuermer. Lieber beim Import auffliegen.
for _prof, _mets in PROFILES.items():
    assert abs(sum(w for _, _, w, _ in _mets) - 1.0) < 1e-9, \
        f"Profil {_prof}: Gewichte ergeben {sum(w for _, _, w, _ in _mets)}"
PROFILE_LABEL = {"st": "Stürmer", "off": "Flügel/OM", "mid": "Mittelfeld",
                 "iv": "Innenverteidiger", "av": "Außenverteidiger",
                 "tw": "Torhüter"}


def _profile(p):
    if p.get("is_gk"):
        return "tw"
    groups = p.get("pos_groups") or ["unk"]
    g = groups[0]
    if g == "def":
        return "iv" if int(p.get("pos_mask") or 0) & (1 << 4) else "av"
    return g if g in ("mid", "off", "st") else None


# --- Quoten: Bayes-Shrinkage -------------------------------------------------
# /90-Raten werden seit jeher mit min/(min+180) zur Mitte gezogen, Quoten
# bisher NICHT: ein Spieler mit 3 Zweikaempfen und 100 % stand ueber einem mit
# 200 und 78 %. Jetzt gilt (gewonnen + k*prior) / (gesamt + k) – bei grossen
# Stichproben aendert sich nichts, bei kleinen zieht es zur gepoolten Quote
# des Positionsprofils. k = 20 heisst: die ersten ~20 Beobachtungen wiegen so
# viel wie der Vorwissen-Anteil. Ohne jede Beobachtung steht der Spieler damit
# auf dem Profilschnitt (Perzentil ~50) statt bei 0 – keine Daten heisst
# "unbekannt", nicht "schlecht".
QUOTE_PRIOR_K = 20
# (Zweikampfquote, Paradenquote und verhinderte Tore je Schuss stehen in
# keinem Profil mehr – Split-Half-Rauschen – und brauchen keinen Prior.)
QUOTEN = {"header_pct": ("headers_won", "headers_total"),
          "pass_pct": ("pass_ok", "pass_try"),
          # Anteil der Tore aus der Distanz (invertiert im Fluegel-Profil):
          # Fernschusstore sind niedrige xG, ein hoher Anteil heisst
          # unhaltbare Abschlussquote statt guter Schussauswahl.
          "long_share": ("long_goals", "goals"),
          # Pressing-ERFOLGSquote: das Volumen ist Stil (Vereins-DNA), die
          # Quote ist Qualitaet – wer anlaeuft, soll auch ankommen.
          "press_pct": ("press_win", "press_try"),
          # Flankenquote (Aussenverteidiger); nur aus dem Export bekannt
          "cross_pct": ("crosses_ok", "crosses_try"),
          # Schussgenauigkeit (Stuermer): Schuesse aufs Tor / Schuesse gesamt
          "shot_acc": ("shots_on", "shots_total")}


def _quote(won, total, prior, k=QUOTE_PRIOR_K):
    if won is None:                          # Kennzahl fehlt (z.B. RAM-Kohorte)
        return None
    won, total = float(won or 0), float(total or 0)
    if prior is None:                        # kein Vorwissen: rohe Quote
        return 100.0 * won / total if total else 0.0
    return 100.0 * (won + k * prior) / (total + k)


def quoten_prior(reference):
    """Gepoolte Quote je (Profil, Kennzahl) als Prior fuer _quote().

    Gepoolt (Summe gewonnen / Summe gesamt), nicht gemittelt: so zaehlt jeder
    Zweikampf gleich viel, und Spieler mit drei Duellen verzerren den Schnitt
    nicht. Je Profil, weil ein Innenverteidiger eine andere Zweikampf-Basis
    hat als ein Fluegel.
    """
    summe = {}
    for r in reference:
        prof = _profile(r)
        if not prof:
            continue
        for key, (w, t) in QUOTEN.items():
            tot = r.get(t) or 0
            # Zeilen ohne den Zaehler (RAM-Kohorte kennt keine Fernschuss-
            # tore) duerfen den Prior nicht als 0 nach unten ziehen.
            if not tot or r.get(w) is None:
                continue
            s = summe.setdefault((prof, key), [0.0, 0.0])
            s[0] += float(r.get(w) or 0)
            s[1] += float(tot)
    return {k: w / t for k, (w, t) in summe.items() if t}


def _score_metrics(p, coeff, priors=None):
    """Kennzahlen fuers Scoring: /90-Raten geshrinkt (min/(min+180)) und mit
    Liga-Koeffizient skaliert; Quoten Bayes-geschrumpft (siehe _quote); Note
    bleibt roh. Angezeigt werden weiterhin die Rohwerte aus enrich() – die
    Schrumpfung steckt nur im Perzentil."""
    prof = _profile(p)
    pri = lambda key: (priors or {}).get((prof, key))
    m = p.get("minutes") or 0
    sh = (m / (m + SHRINK_MIN)) * coeff if m else 0.0
    # Shrinkage OHNE Liga-Koeffizient fuer Negativmasse: ein Fehler vor dem
    # Gegentor ist in jeder Liga derselbe Fehler – mal 1.2 waere er in der
    # Premier League "schlimmer", das ist genau verkehrt herum.
    sh0 = m / (m + SHRINK_MIN) if m else 0.0
    def adj(v):
        return _p90(v or 0, m) * sh
    np_goals = (p.get("goals") or 0) - (p.get("pen_goals") or 0)
    # npxG naehert Elfer-xG mit 0.76 je Elfmetertor an (FM liefert keine
    # Elfer-Versuche); nie unter 0.
    npxg = max(0.0, (p.get("xg") or 0.0) - 0.76 * (p.get("pen_goals") or 0))
    return {
        "goals_adj": adj(np_goals),        # Non-Penalty-Tore (Elfer raus)
        "assists_adj": adj(p.get("assists")),
        "press_adj": adj(p.get("press_win")),
        "intercept_adj": adj(p.get("interceptions")),
        "prog_adj": adj(p.get("prog_passes")),
        "clear_adj": adj(p.get("clearances")),
        # Gegentore/90 nur, wenn die Zahl aus dem Export stammt – der RAM
        # liefert fuer Torhueter Unsinn (siehe enrich, note_resid).
        "conceded_adj": (None if p.get("note_resid") is None
                         else _p90(p.get("conceded") or 0, m) * sh0),
        # Erwartungswerte statt Zaehlwerte: npxG naehert Elfer-xG mit 0.76 je
        # Elfmetertor an (FM liefert keine Elfer-Versuche). xA ohne Abzug.
        "xg_adj": adj(npxg),
        "xa_adj": adj(p.get("xa")),
        # Ballprogression und Kreation
        "dribble_adj": adj(p.get("dribbles")),
        "keyp_adj": adj(p.get("key_passes")),
        # Nur aus dem Export bekannt -> None statt 0, damit die Kohorte die
        # Verteilung nicht mit Nullen flutet (add_scores ueberspringt None).
        "chance_adj": None if p.get("chances") is None else adj(p.get("chances")),
        # Mittelfeld: gewonnene Zweikaempfe/90 sind Volumen x Quote in einer
        # Zahl – die Quote allein belohnte den, der Duellen ausweicht.
        "duelwon_adj": adj(p.get("duels")),
        # Defensivbeitrag: Abfangen + Klaerungen (in RAM und Export gleich
        # bekannt) als eine Zahl; Ballgewinne getrennt, weil sie ein Vielfaches
        # davon sind und die Summe sonst dominieren wuerden.
        "defakt_adj": adj((p.get("interceptions") or 0) + (p.get("clearances") or 0)),
        "rec_adj": adj(p.get("recoveries")),
        # Blocks kennt nur der Export -> None fuer die Kohorte, uebersprungen.
        "block_adj": None if p.get("blocks") is None else adj(p.get("blocks")),
        # Innenverteidiger: gewonnene Kopfballduelle/90 (Volumen x Quote, wie
        # bei den Zweikaempfen) und Fehler vor Gegentoren/90 – nur im Export
        # bekannt, invertiert, ohne Liga-Koeffizient (sh0).
        "headwon_adj": adj(p.get("headers_won")),
        # Aussenverteidiger: angekommene Flanken/90 und Sprints/90 – beides nur
        # im Export, fuer die Kohorte None und uebersprungen.
        "cross_adj": None if p.get("crosses_ok") is None else adj(p.get("crosses_ok")),
        "sprint_adj": None if p.get("sprints") is None else adj(p.get("sprints")),
        "err_adj": (None if p.get("errors") is None
                    else _p90(p.get("errors") or 0, m) * sh0),
        # Ballverluste: bewusst OHNE Shrinkage/Liga-Koeffizient (beides wuerde
        # eine Negativ-Rate faelschlich Richtung "gut" druecken); invertiert.
        "loss_p90": _p90(p.get("losses") or 0, m),
        "header_pct": _quote(p.get("headers_won"), p.get("headers_total"), pri("header_pct")),
        "pass_pct": _quote(p.get("pass_ok"), p.get("pass_try"), pri("pass_pct")),
        # Ballverluste je 100 Aktionen (enrich): fairer als /90 fuer Spieler,
        # die den Ball fordern. None ohne Aktionen -> uebersprungen.
        "loss_rate": p.get("loss_rate"),
        "long_share": _quote(p.get("long_goals"), p.get("goals"), pri("long_share")),
        "press_pct": _quote(p.get("press_win"), p.get("press_try"), pri("press_pct")),
        "cross_pct": _quote(p.get("crosses_ok"), p.get("crosses_try"), pri("cross_pct")),
        "shot_acc": _quote(p.get("shots_on"), p.get("shots_total"), pri("shot_acc")),
        # Torwart: Note ueber Erwartung, in enrich() schon zur Mitte
        # geschrumpft (NUE_SHRINK_MIN) – hier nicht noch einmal.
        "note_resid": p.get("note_resid_s"),
        # ligabereinigt (LEAGUE_NOTE_OFFSET); angezeigt wird weiter die rohe Note
        "rating": p.get("rating_adj") if p.get("rating_adj") is not None else (p.get("rating") or 0),
    }


def _pct(sorted_vals, v):
    n = len(sorted_vals)
    if n < 4:
        return 50.0
    lo = bisect.bisect_left(sorted_vals, v)
    hi = bisect.bisect_right(sorted_vals, v)
    return 100.0 * (lo + 0.5 * (hi - lo)) / n


def add_scores(players, reference=None, leagues=None):
    """Ergaenzt jeden Spieler um score (0-100), score_parts (Tooltip),
    profile_label, league(+coeff). Perzentile werden je Positionsprofil ueber
    die reference-Menge (idealerweise der Pool) gebildet."""
    reference = reference if reference is not None else players
    leagues = leagues or {}

    def lg(p):
        eid = p.get("eid")
        return leagues.get(int(eid)) if eid else None

    # Prior fuer die Quoten-Schrumpfung: aus derselben Referenz wie die
    # Perzentile, sonst wird gegen einen anderen Massstab geschrumpft als
    # verglichen.
    priors = quoten_prior(reference)

    # Verteilungen je (Profil, Metrik) aus der Referenzmenge
    dists = {}
    ref_mets = []
    for r in reference:
        prof = _profile(r)
        if not prof:
            continue
        mets = _score_metrics(r, league_coeff(lg(r)), priors)
        ref_mets.append((r, prof, mets))
        for key, _, _, _ in PROFILES[prof]:
            if mets[key] is not None:
                dists.setdefault((prof, key), []).append(mets[key])
    for v in dists.values():
        v.sort()

    # Score-Verteilung je (Profil, Altersband) – Grundlage fuer das Talentmass.
    # Erst jetzt moeglich, weil dafuer die fertigen Scores der Referenz noetig
    # sind, nicht nur die Rohmetriken.
    age_dists = {}
    for r, prof, mets in ref_mets:
        band = age_band(r.get("age"))
        if not band:
            continue
        s, ws = 0.0, 0.0
        for key, _lab, w, inv in PROFILES[prof]:
            if mets[key] is None:
                continue
            pc = _pct(dists.get((prof, key), []), mets[key])
            s += w * (100.0 - pc if inv else pc)
            ws += w
        if ws >= 0.5:
            age_dists.setdefault((prof, band), []).append(s / ws)
    for v in age_dists.values():
        v.sort()

    for p in players:
        prof = _profile(p)
        league = lg(p)
        coeff = league_coeff(league)
        p["league"] = league
        p["league_coeff"] = round(coeff, 2)
        if not prof:
            p["score"] = None
            p["score_parts"] = []
            p["profile_label"] = "unbekannt"
            continue
        # Torwart ohne Detailstatistik (Liga ohne xG/Paraden, im Save die
        # saudischen): Note und Gegentore sind da, alles andere fehlt. Die
        # Ueberspringen-Regel unten machte daraus einen Score aus 70 % Note –
        # und drei Saudi-Keeper standen mit 89–94 ganz oben. Kein Urteil ist
        # hier ehrlicher als ein halbes.
        if prof == "tw" and p.get("xga") is None and p.get("sot_faced") is None:
            p["score"] = None
            p["score_parts"] = []
            p["profile_label"] = "Torhüter – keine Detailstatistik"
            continue
        mets = _score_metrics(p, coeff, priors)
        # Fehlende Kennzahlen (None) werden uebersprungen und die uebrigen
        # Gewichte hochgerechnet – dieselbe Regel wie in tactics.score_slot.
        # Sonst stuende ein Kohorten-Spieler bei "Chancen kreiert" auf 0 und
        # jeder Export-Spieler mit einem echten Wert automatisch ueber ihm.
        total, wsum, parts = 0.0, 0.0, []
        for key, label, w, inv in PROFILES[prof]:
            if mets[key] is None:
                parts.append({"label": label, "pct": None, "w": w})
                continue
            pct = _pct(dists.get((prof, key), []), mets[key])
            if inv:
                pct = 100.0 - pct
            total += w * pct
            wsum += w
            parts.append({"label": label, "pct": round(pct), "w": w})
        if wsum < 0.5:
            p["score"] = None
            p["score_parts"] = parts
            p["profile_label"] = PROFILE_LABEL[prof]
            continue
        total = total / wsum
        p["score"] = round(total)
        p["score_parts"] = parts
        p["profile_label"] = PROFILE_LABEL[prof]
        # Talent = wie gut ist er FUER SEIN ALTER auf seiner Position.
        # Ein 19-Jaehriger im 90. Perzentil der 19-20-Jaehrigen ist ein
        # Kandidat, derselbe Score bei einem 28-Jaehrigen ist Normalmass.
        band = age_band(p.get("age"))
        pool = age_dists.get((prof, band), []) if band else []
        p["age_band"] = band
        p["talent"] = round(_pct(pool, total)) if len(pool) >= 8 else None
        # Talent-Board: dasselbe Mass, aber nur fuer die Jahrgaenge, um die es
        # dabei geht. Sonst steht in der Rangliste "Talente" ein 31-jaehriger
        # Weltklassetorwart ganz oben – im eigenen Altersband ist er zu Recht
        # im 100. Perzentil, nur ist er eben kein Talent mehr.
        a = p.get("age")
        p["prospect"] = p["talent"] if (a is not None and a <= PROSPECT_AGE) else None
    return players


# ------------------------------------------------------------- Vereins-DNA
# Passt ein Spieler zum Verein – unabhaengig davon, welche Position er spielt?
# Der Positions-Fit (tactics.py) sagt "passt in diesen Slot", die DNA sagt
# "passt zu dem, wie wir spielen wollen". Vorbild ist das Schalker Scouting,
# das jeden Kandidaten gegen ein Vereinsprofil misst. Anders als dort steht
# hier aber KEIN Attribut und kein Charakterwert dahinter (siehe CLAUDE.md),
# sondern nur, was der Spieler auf dem Platz tatsaechlich tut.
#
# Zwei Dimensionen, bewusst nicht mehr:
#  - Passstark:    spielt nach VORN und verliert den Ball trotzdem nicht. Die
#                  reine Passquote allein belohnte den Querpasser mit 92 %,
#                  deshalb wiegen progressive und Schluesselpaesse zusammen mehr
#                  als die Quote.
#  - Ballgewinner: holt den Ball hoch zurueck – erfolgreiches Pressing,
#                  Ballgewinne, Abgefangenes. Das ist der "Kaempfer", aber am
#                  ERGEBNIS gemessen, nicht an der Grätsche.
#
# Perzentile werden je Positionsgruppe gebildet, sonst gewinnt kein Stuermer
# je bei Paessen und kein Fluegel je beim Abfangen. Torhueter bekommen keine
# DNA: Ballgewinne sind fuer sie kein sinnvolles Mass.
DNA = {
    "passstark": ("Passstark", [
        ("prog_p90", "Progressive Pässe/90", .35, False),
        ("keyp_p90", "Schlüsselpässe/90", .25, False),
        ("loss_rate", "Ballverluste je 100 Aktionen", .25, True),
        ("pass_pct", "Passquote %", .15, False)]),
    "ballgewinner": ("Ballgewinner", [
        ("press_p90", "Erfolgr. Pressing/90", .40, False),
        ("rec_p90", "Ballgewinne/90", .35, False),
        ("int_p90", "Abgefangen/90", .25, False)]),
}
# Raten werden mit dem Liga-Koeffizienten skaliert, Quoten und Ballverluste
# nicht – dieselbe Regel wie in tactics.SKALIERBAR, aus denselben Gruenden.
DNA_SKALIERT = {"prog_p90", "keyp_p90", "press_p90", "rec_p90", "int_p90"}
DNA_GRUPPEN = ("def", "mid", "off", "st")
DNA_MIN_MINUTES = 180      # darunter gehoert niemand in die Vergleichsbasis


def _dna_gruppe(p):
    """Positionsgruppe fuer die DNA-Perzentile – Export-Position vor RAM-Maske.

    Die Maske aus dem Speicher zeigt nur, wo der Spieler zuletzt stand, und ist
    bei Kaderspielern oft leer oder falsch: Correia ('V (RL), FV (R)') kam als
    'unbekannt' ohne DNA zurueck, Torwart Restes dagegen MIT einer, weil seine
    Maske Feldspielerbits trug. Dieselbe Reihenfolge wie tactics.player_groups.
    """
    if p.get("is_gk"):
        return None
    pos = p.get("position")
    if pos:
        m = pos_mask_from_string(pos)
        if m:
            g = pos_info(m)[0][0]
            return g if g in DNA_GRUPPEN else None
    g = (p.get("pos_groups") or ["unk"])[0]
    return g if g in DNA_GRUPPEN else None


def _dna_wert(p, key, coeff):
    v = p.get(key)
    if v is None:
        return None
    return float(v) * coeff if key in DNA_SKALIERT else float(v)


def add_dna(players, reference=None, leagues=None):
    """Ergaenzt jeden Spieler um dna (0-100), dna_pass, dna_ball und dna_teile.

    reference: Vergleichsmenge fuer die Perzentile – dieselbe wie bei den
    Scores, sonst liegen die Zahlen auf verschiedenen Skalen.

    Die Gesamt-DNA ist das GEOMETRISCHE Mittel beider Dimensionen, kein
    arithmetisches. Der Unterschied ist der ganze Sinn der Sache: gesucht
    ist, wer BEIDES kann. Ein Spieler mit 95/30 haette im Durchschnitt 62,
    genauso viel wie einer mit 62/62 – geometrisch sind es 53 gegen 62, und
    der ausgewogene liegt vorn. Genau den unterschaetzt der Markt.
    """
    reference = reference if reference is not None else players
    leagues = leagues or {}

    def lg(p):
        eid = p.get("eid")
        return leagues.get(int(eid)) if eid else None

    dists = {}
    for r in reference:
        g = _dna_gruppe(r)
        if not g or (r.get("minutes") or 0) < DNA_MIN_MINUTES:
            continue
        c = league_coeff(lg(r))
        for _, mets in DNA.values():
            for key, _, _, _ in mets:
                v = _dna_wert(r, key, c)
                if v is not None:
                    dists.setdefault((g, key), []).append(v)
    for v in dists.values():
        v.sort()

    for p in players:
        g = _dna_gruppe(p)
        if not g:
            p["dna"] = p["dna_pass"] = p["dna_ball"] = None
            p["dna_teile"] = []
            continue
        c = league_coeff(lg(p))
        dims, teile = {}, []
        for dim, (label, mets) in DNA.items():
            total, wsum = 0.0, 0.0
            for key, mlabel, w, inv in mets:
                v = _dna_wert(p, key, c)
                d = dists.get((g, key))
                if v is None or not d or len(d) < 8:
                    teile.append({"dim": dim, "stat": key, "label": mlabel,
                                  "wert": p.get(key), "pct": None, "w": w})
                    continue
                pc = _pct(d, v)
                if inv:
                    pc = 100.0 - pc
                total += w * pc
                wsum += w
                teile.append({"dim": dim, "stat": key, "label": mlabel,
                              "wert": p.get(key), "pct": round(pc), "w": w})
            dims[dim] = round(total / wsum) if wsum >= 0.5 else None
        a, b = dims.get("passstark"), dims.get("ballgewinner")
        p["dna_pass"], p["dna_ball"] = a, b
        p["dna"] = round((a * b) ** 0.5) if (a is not None and b is not None) else None
        p["dna_teile"] = teile
    return players
