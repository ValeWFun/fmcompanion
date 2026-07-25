"""Moneyball-Bewertung: leitet aus den Rohstatistiken transparente Kennzahlen
ab – Effizienz statt Ruf. Kern ist die Leistung pro 90 Minuten und die
Ueber-/Unterperformance gegenueber den Expected-Werten (xG/xA)."""
import bisect
import re


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
    bekommen. Niedrigstes Bit = Hauptgruppe."""
    mask = 0
    for tok in (s or "").split(","):
        t = tok.strip()
        if not t:
            continue
        head = re.split(r"[ (]", t)[0]
        m = re.search(r"\(([^)]*)\)", t)
        sd = m.group(1) if m else ""
        if head == "TW":
            mask |= 1 << 0
        elif head in ("V", "FV"):
            if "Z" in sd or (head == "V" and not sd):
                mask |= 1 << 4                 # Innenverteidiger
            if "R" in sd or "L" in sd or head == "FV":
                mask |= 1 << 2                 # Aussenverteidiger
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
        # Non-Penalty-Tore: Elfer-Offset noch unverifiziert (kein Elfer im Save)
        # -> pen_goals ist 0, bis der Matcher ihn nach den ersten Elfern pinnt.
        p["np_goals"] = int(p.get("goals") or 0) - int(p.get("pen_goals") or 0)
        # Verlaesslichkeit der Stichprobe (Minuten); < 1 Spiel = wackelig
        p["reliability"] = min(1.0, round(m / 270, 2))  # ~3 Spiele = voll
        # Torhueter: Goals Prevented = erwartete minus tatsaechliche Gegentore
        # (>0 = haelt besser als der Schnitt). Rein aus dem RAM verifiziert.
        p["is_gk"] = bool(p.get("is_gk"))
        conc = int(p.get("conceded") or 0)
        xga = float(p.get("xga") or 0.0)
        p["conceded"], p["xga"], p["apps"] = conc, xga, int(p.get("apps") or 0)
        p["goals_prevented"] = round(xga - conc, 2)
        p["conceded_p90"] = _p90(conc, m)
        # Positionsgruppen (fuer die Positions-Dashboards) + Kurzlabel
        groups, label = pos_info(int(p.get("pos_mask") or 0), p["is_gk"])
        p["pos_groups"], p["pos_label"] = groups, label
        out.append(p)
    return out


# vordefinierte Ranglisten fuer typische Moneyball-Fragen
RANKINGS = {
    "Moneyball-Score": ("score", True),
    "Talent-Board (U21, für ihr Alter stark)": ("prospect", True),
    "Für sein Alter stark (alle Jahrgänge)": ("talent", True),
    "Jüngste zuerst": ("age", False),
    "Schnäppchen (Score/Mio €)": ("value_score", True),
    "Höchster Marktwert": ("value_m", True),
    "Höchste Ø-Note": ("rating", True),
    "Beste Torjäger (xG/90)": ("xg_p90", True),
    "Eiskalt (Tore über xG)": ("finishing", True),
    "Ladehemmung (Tore unter xG)": ("finishing", False),
    "Kreativität (xA/90)": ("xa_p90", True),
    "Vorlagen/90": ("assists_p90", True),
    "Offensive Beteiligung/90": ("attack_p90", True),
    "Ballgewinner (Zweikämpfe/90)": ("duels_p90", True),
    "Zweikampfquote %": ("duel_pct", True),
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
    "Eiskalt (Tore über xG)": ("finishing", True),
    "Beste Torjäger (xG/90)": ("xg_p90", True),
    "Kreativität (xA/90)": ("xa_p90", True),
    "Höchste Ø-Note": ("rating", True),
    "Günstigste (Marktwert)": ("value_m", False),
}


def rank(players, key, descending=True, min_minutes=45, limit=None):
    """Rangliste nach einer Kennzahl; blendet Mini-Stichproben aus."""
    pool = [p for p in players if (p.get("minutes") or 0) >= min_minutes]
    pool.sort(key=lambda p: p.get(key, 0), reverse=descending)
    return pool[:limit] if limit else pool


# ---------------------------------------------------------------- Score-Engine

# Liga-Koeffizienten RELATIV zur eigenen Liga (Portugal = 1.00): Produktion in
# staerkeren Ligen zaehlt mehr, in schwaecheren weniger. Quelle der Liganamen:
# FM-HTML-Export (EID-Join). Unbekannte Liga (z.B. eigener Kader) => 1.00.
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


def league_coeff(league):
    return LEAGUE_COEFFS.get(league, 1.0) if league else 1.0


# Score-Profile: wenige Kennzahlen mit hohem Impact, Spielidee = hohes Pressing
# + passbasiert. Pressing = ECHTE erfolgreiche Pressingaktionen/90 (M+108),
# progressive Paesse = ECHTES Feld M+102 – beide per Kader-Export 7/7
# verifiziert. Format: (metrik, label, gewicht, invertiert)
PROFILES = {
    # Stuermer: 80 % npTore+Vorlagen, 20 % Pressing+Note
    "st":  [("goals_adj", "npTore/90", .55, False),
            ("assists_adj", "Vorlagen/90", .25, False),
            ("press_adj", "Pressing/90", .10, False),
            ("rating", "Ø-Note", .10, False)],
    # Fluegel/OM: zwischen Sturm und Mittelfeld
    "off": [("goals_adj", "npTore/90", .35, False),
            ("assists_adj", "Vorlagen/90", .35, False),
            ("press_adj", "Pressing/90", .15, False),
            ("rating", "Ø-Note", .15, False)],
    # Mittelfeld: 50 % Produktion / 50 % Pressing+Passspiel(+Ballsicherheit)
    "mid": [("assists_adj", "Vorlagen/90", .30, False),
            ("goals_adj", "npTore/90", .15, False),
            ("press_adj", "Pressing/90", .25, False),
            ("loss_p90", "Ballsicherheit", .10, True),
            ("pass_pct", "Pass %", .10, False),
            ("rating", "Ø-Note", .10, False)],
    # Innenverteidiger: 70 % Verteidigen / 30 % progressives Passspiel
    "iv":  [("press_adj", "Pressing/90", .15, False),
            ("duel_pct", "ZWK-Quote", .20, False),
            ("header_pct", "Kopfball %", .15, False),
            ("intercept_adj", "Abgefangen/90", .10, False),
            ("clear_adj", "Klärungen/90", .10, False),
            ("prog_adj", "Progressive Pässe/90", .15, False),
            ("loss_p90", "Ballsicherheit", .10, True),
            ("pass_pct", "Pass %", .05, False)],
    # Aussenverteidiger: offensiver als IV
    "av":  [("assists_adj", "Vorlagen/90", .25, False),
            ("goals_adj", "npTore/90", .10, False),
            ("press_adj", "Pressing/90", .25, False),
            ("duel_pct", "ZWK-Quote", .10, False),
            ("prog_adj", "Progressive Pässe/90", .10, False),
            ("loss_p90", "Ballsicherheit", .10, True),
            ("pass_pct", "Pass %", .05, False),
            ("rating", "Ø-Note", .05, False)],
    # Torhueter: Goals Prevented + wenig kassieren + passbasiert
    "tw":  [("gp_adj", "Verhindert/90", .50, False),
            ("conceded_adj", "Gegentore/90", .20, True),
            ("pass_pct", "Pass %", .15, False),
            ("rating", "Ø-Note", .15, False)],
}
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


def _score_metrics(p, coeff):
    """Kennzahlen fuers Scoring: /90-Raten geshrinkt (min/(min+180)) und mit
    Liga-Koeffizient skaliert; Quoten und Note bleiben unskaliert."""
    m = p.get("minutes") or 0
    sh = (m / (m + SHRINK_MIN)) * coeff if m else 0.0
    def adj(v):
        return _p90(v or 0, m) * sh
    np_goals = (p.get("goals") or 0) - (p.get("pen_goals") or 0)
    return {
        "goals_adj": adj(np_goals),        # Non-Penalty-Tore (Elfer raus)
        "assists_adj": adj(p.get("assists")),
        "press_adj": adj(p.get("press_win")),
        "intercept_adj": adj(p.get("interceptions")),
        "prog_adj": adj(p.get("prog_passes")),
        "clear_adj": adj(p.get("clearances")),
        "gp_adj": adj((p.get("xga") or 0) - (p.get("conceded") or 0)),
        "conceded_adj": adj(p.get("conceded")),
        # Ballverluste: bewusst OHNE Shrinkage/Liga-Koeffizient (beides wuerde
        # eine Negativ-Rate faelschlich Richtung "gut" druecken); invertiert.
        "loss_p90": _p90(p.get("losses") or 0, m),
        "duel_pct": p.get("duel_pct") or 0,
        "header_pct": p.get("header_pct") or 0,
        "pass_pct": p.get("pass_pct") or 0,
        "rating": p.get("rating") or 0,
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

    # Verteilungen je (Profil, Metrik) aus der Referenzmenge
    dists = {}
    ref_mets = []
    for r in reference:
        prof = _profile(r)
        if not prof:
            continue
        mets = _score_metrics(r, league_coeff(lg(r)))
        ref_mets.append((r, prof, mets))
        for key, _, _, _ in PROFILES[prof]:
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
        s = 0.0
        for key, _lab, w, inv in PROFILES[prof]:
            pc = _pct(dists.get((prof, key), []), mets[key])
            s += w * (100.0 - pc if inv else pc)
        age_dists.setdefault((prof, band), []).append(s)
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
        mets = _score_metrics(p, coeff)
        total, parts = 0.0, []
        for key, label, w, inv in PROFILES[prof]:
            pct = _pct(dists.get((prof, key), []), mets[key])
            if inv:
                pct = 100.0 - pct
            total += w * pct
            parts.append({"label": label, "pct": round(pct), "w": w})
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
