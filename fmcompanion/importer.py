"""Liest FM24-HTML-Exporte (Strg+P) ein: Stammdaten + Statistiken je Spieler,
verknuepfbar mit dem RAM ueber die EID. Robustes Parsen von Geldbetraegen."""
import re

# gewuenschte Felder -> moegliche Spaltenueberschriften im Export
COLUMNS = {
    "eid": ["EID", "UID"],
    "name": ["Name"],
    "position": ["Position"],
    "age": ["Alter", "Age"],
    "club": ["Verein", "Club"],
    "league": ["Liga", "Division"],
    "nation": ["Nation"],
    "wage": ["Gehalt", "Wage"],
    "value": ["Transferwert", "Transfer Value", "Value"],
    "goals": ["Tore", "Gls"],
    "assists": ["Vor", "Ast", "Assists"],
    "xg": ["xG"],
    "xa": ["xA"],
    "minutes": ["Min.", "Min", "Mins"],
    "rating": ["Ø Note", "Av Rat", "Durchschnittsnote"],
    "apps": ["Eins", "Apps"],
    # Statistik-Felder (fuer vollwertige Scores der Export-Spieler)
    "duels": ["Gew Zwk"], "duels_total": ["Zwk Z"],
    "shots_total": ["Schüsse"], "shots_on": ["SchT"],
    "pass_try": ["Pas V"], "pass_ok": ["Ps A"], "dribbles": ["Drb"],
    "prog_passes": ["Pr Pässe"], "press_win": ["PrsErf"], "press_try": ["PrsV"],
    "interceptions": ["AbB"], "key_passes": ["EntP(S)"],
    "clearances": ["Klär.", "Klär"], "headers_won": ["Kopf G"],
    "headers_total": ["Kopf V"], "losses_p90": ["Ballverl/90"],
    "recoveries_p90": ["Ballgew/90"],
    # Elfmetertore: ohne sie sind "npTore" schlicht Tore (pen_goals war fest 0)
    "pen_goals": ["11m-Tor"],
    # Torhueter: Gegentore und verhinderte Tore/90 – vorher RAM-only, damit
    # stand jeder gescoutete Keeper mit halbem Profil da. 'Par %' und 'xSv %'
    # BEWUSST NICHT: FM schreibt dort Unsinn in den Export (-1 %, -903 %).
    "conceded": ["GegT"], "gp_p90": ["xG verh/90"],
    # Fluegel/OM-Profil: Chancen kreiert/90 und Tore aus der Distanz
    "chances_p90": ["Ch/90"], "long_goals": ["Tore durch Fernschüsse"],
    "blocks": ["Blk"],                     # geblockte Schuesse, Saisonsumme
    "errors": ["T Feh"],                   # Fehler, die zu Gegentoren fuehrten
    # Aussenverteidiger: Flanken (angekommen / versucht, Saisonsummen) und
    # Sprints (nur als /90 im Export). Torwart: Elfmeter fuers Abzeichen.
    "crosses_ok": ["Fla A"], "crosses_try": ["Fla V"],
    "sprints_p90": ["Sprints/90"],
    "pen_saved": ["Parierte Elfer"], "pen_faced": ["Elfer gesamt"],
    # Paraden, von FM dreigeteilt: 'Sag' = sicher gehalten, 'Spa' = pariert
    # (abgewehrt), 'Sgh' = gehalten/festgehalten. Ihre Summe sind die Paraden;
    # Paraden + Gegentore = Schuesse aufs Tor. Daraus entstehen die
    # Paradenquote und "verhinderte Tore je Schuss" – beides berechnet, weil
    # FMs eigene Spalten 'Par %' und 'xSv %' im Export kaputt sind.
    "saves_tipped": ["Sag"], "saves_parried": ["Spa"], "saves_held": ["Sgh"],
    "clean_sheets": ["Zu-Null-Spiele", "Clean Sheets"],
    # Sichtbare Stammdaten ohne Zahl: die Persoenlichkeits-Beschreibung und
    # der Medienumgang stehen auf dem Spielerprofil; verdeckte Werte dahinter
    # werden NICHT gelesen (siehe charakter.py). Starker Fuss fuer Seiten-
    # fragen (invertierter Fluegel, linker Innenverteidiger), Info fuer die
    # Statuskuerzel (Ver = verletzt, Trn = Transferliste, Unz = unzufrieden).
    # Ablöseforderung: der Preis, den der Verein des Spielers festgesetzt hat
    # (bei eigenen Spielern UNSERE Forderung) – anders als der Transferwert
    # ein echter Preis. Meist "-" = keine Forderung gesetzt.
    "transfer_fee": ["Ablöseforderung", "Asking Price"],
    # Eigengewaechs-Status, bezogen auf den Verein des NUTZERS zum Zeitpunkt
    # des Exports (an Exporten Winter 2 bis Summer 3 nachgemessen: in der
    # Benfica-Zeit tragen Benfica-Akademiespieler bei fremden Vereinen
    # "Ausgebildet im Verein", in der United-Zeit die United-Akademie). Damit
    # sagt die Spalte direkt, ob ein Kandidat bei uns als Eigengewaechs zaehlt
    # – Grundlage der PL-/CL-Meldelisten. Werte: "Ausgebildet im Verein
    # (0–21)", "… im Land (15–21)", "… im Land (0–21)" oder "-".
    "homegrown": ["Status Eigengewächs", "Home-Grown Status"],
    "personality": ["Persönlichkeit", "Personality"],
    "media": ["Medienumgang", "Media Handling"],
    "foot": ["Starker Fuß", "Preferred Foot"],
    "info": ["Info"],
    "height": ["Größe", "Height"],
}
# Textfelder, die roh (bereinigt) uebernommen werden; leere Platzhalter -> None
_TEXT = ["personality", "media", "foot", "info", "homegrown"]
_TEXT_LEER = {"", "-", "Scouting erforderlich", "Unbekannt"}
# ganzzahlige Statistik-Felder
_STAT_INT = ["duels", "duels_total", "shots_total", "shots_on", "pass_try",
             "pass_ok", "dribbles", "prog_passes", "press_win", "press_try",
             "interceptions", "key_passes", "clearances", "headers_won",
             "headers_total", "blocks", "errors",
             "crosses_ok", "crosses_try", "pen_saved", "pen_faced",
             "saves_tipped", "saves_parried", "saves_held", "clean_sheets"]


def _clean(s):
    return re.sub(r"<.*?>", "", s or "").strip()


def _dezimal(s):
    """Zahlstring -> float, unabhaengig vom Zahlformat des Exports.

    FM formatiert Zahlen nach der Spracheinstellung des SPIELS, und die muss
    nicht zur Sprache der Spaltentitel passen: die Exporte aus diesem Savegame
    haben deutsche Ueberschriften ('Tore', 'Ø Note') und englische Zahlen
    ('2,198' Minuten, '6.54' Note). Vorher wurde stur deutsch angenommen und
    jedes Komma zum Dezimalpunkt gemacht – aus 2198 Minuten wurde int('2.198')
    und damit None. Betroffen war jeder Spieler ueber 999 Minuten, also genau
    die Stammspieler; ebenso 'Ps A'/'Pas V' und alle Marktwerte mit Nachkomma
    ('€17.5Mio' wurde zu 175 Mio).

    Erkannt wird deshalb pro Wert am ZULETZT stehenden Trenner: folgen ihm
    genau drei Ziffern, ist es ein Tausendertrenner, sonst ein Dezimaltrenner.
    FM exportiert nie drei Nachkommastellen, damit ist die Regel eindeutig und
    liest beide Formate ('2.198' wie '2,198' -> 2198, '6,54' wie '6.54' -> 6.54).
    """
    t = re.match(r"[+-]?[\d.,]+", (s or "").replace(" ", "").replace("\xa0", ""))
    if not t:
        return None
    t = t.group(0).rstrip(".,")
    letzte = max(t.rfind("."), t.rfind(","))
    if letzte < 0:
        ganz, rest = t, ""
    elif len(t) - letzte - 1 == 3:            # '2,198' / '2.198' -> Tausender
        ganz, rest = t.replace(".", "").replace(",", ""), ""
    else:
        ganz, rest = t[:letzte].replace(".", "").replace(",", ""), t[letzte + 1:]
    try:
        return float(f"{ganz}.{rest}" if rest else ganz)
    except ValueError:
        return None


def _num(s, cast=float):
    s = _clean(s)
    if not s or s in ("-", "N/A"):
        return None
    v = _dezimal(s)
    return None if v is None else cast(v)


def _eins(s):
    """'30 (2)' -> 32. FM schreibt Startelfeinsaetze und Einwechslungen in eine
    Zelle; int() scheiterte daran und liess 'apps' leer."""
    s = _clean(s)
    m = re.match(r"(\d+)(?:\s*\((\d+)\))?", s or "")
    return int(m.group(1)) + int(m.group(2) or 0) if m else None


def parse_money(s):
    """'€157Mio - €189Mio' / '€115.000/W.' / '-' -> int (Euro) oder None."""
    s = _clean(s)
    if not s or s == "-":
        return None
    vals = [_one_money(p) for p in s.split(" - ")]
    vals = [v for v in vals if v is not None]
    return int(sum(vals) / len(vals)) if vals else None


def _one_money(s):
    s = s.replace("€", "").replace("/W.", "").replace("/Wo.", "").strip()
    m = re.search(r"([\d.,]+)\s*(Mrd|Mio|Tsd|K|M|B)?", s)
    if not m:
        return None
    num, unit = m.group(1), m.group(2)
    mult = {"Mrd": 1e9, "B": 1e9, "Mio": 1e6, "M": 1e6,
            "Tsd": 1e3, "K": 1e3, None: 1}.get(unit, 1)
    v = _dezimal(num)               # erkennt Tausender- vs. Dezimaltrenner selbst
    return None if v is None else v * mult


def diagnose(path):
    """Klartext, warum eine Datei keine Spieler liefert – oder None.

    Haeufigster Fall: die Liste wurde mit FMs Standard-Scoutingansicht
    exportiert (Info, Name, Verein, Position, Alter, Transferwert ...) statt
    mit der Statistik-Ansicht. Dann fehlt die EID, ueber die jeder Import
    laeuft, und es gibt auch keine einzige Kennzahl – 'keine Spieler gefunden'
    sagte dazu nichts. Passiert im Winter der 3. Saison mit neun Dateien.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            kopf = [_clean(h) for h in re.findall(r"<th>(.*?)</th>", f.read(), re.S)]
    except OSError as e:
        return f"Datei nicht lesbar: {e}"
    if not kopf:
        return "keine Tabelle in der Datei"
    if not any(n in kopf for n in COLUMNS["eid"]):
        return (f"keine EID-Spalte ({len(kopf)} Spalten: {', '.join(kopf[:6])} …) – "
                f"die Liste wurde mit der Standardansicht exportiert. In FM die "
                f"Statistik-Ansicht mit EID wählen und neu exportieren.")
    if not any(n in kopf for n in COLUMNS["minutes"]):
        return "EID vorhanden, aber keine Statistikspalten – falsche Ansicht exportiert"
    return None


# COLUMNS-Eintraege, die nur als Quelle fuer ein abgeleitetes Feld dienen und
# selbst nicht gespeichert werden.
_NUR_QUELLE = {"eid", "losses_p90", "recoveries_p90", "gp_p90", "chances_p90",
               "sprints_p90"}
# Abgeleitete Felder und ALLE Spalten, aus denen sie entstehen. Fehlt eine
# davon, steht im Feld ein Ersatzwert (xga waeren dann nackte Gegentore) –
# es gilt deshalb als NICHT in der Datei enthalten und wird nicht gespeichert.
_ABGELEITET = {
    "losses": ("losses_p90", "minutes"),
    "recoveries": ("recoveries_p90", "minutes"),
    "xga": ("conceded", "gp_p90", "minutes"),
    "chances": ("chances_p90", "minutes"),
    "sprints": ("sprints_p90", "minutes"),
}


def parse_export(path):
    """Liste von Spieler-Dicts aus einem FM-HTML-Export."""
    return parse_export_felder(path)[0]


def parse_export_felder(path):
    """(Spieler, Felder) aus einem FM-HTML-Export.

    Felder sind die gespeicherten Felder, die die Datei WIRKLICH enthaelt.
    Die Spieler-Dicts tragen immer alle Schluessel – fuer eine fehlende Spalte
    eben None oder einen Ersatzwert –, daran allein laesst sich "Spalte fehlt"
    nicht von "Wert ist leer" unterscheiden. Der Import braucht genau das:
    eine Shortlist mit zehn Spalten darf nur diese zehn Felder aendern, nicht
    alle anderen auf NULL setzen (db.save_export).
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        html = f.read()
    headers = [_clean(h) for h in re.findall(r"<th>(.*?)</th>", html, re.S)]
    ncol = len(headers)
    if ncol == 0:
        return [], set()
    col = {}
    for field, names in COLUMNS.items():
        for nm in names:
            if nm in headers:
                col[field] = headers.index(nm)
                break
    felder = {f for f in col if f not in _NUR_QUELLE}
    felder |= {f for f, quellen in _ABGELEITET.items()
               if all(q in col for q in quellen)}
    cells = re.findall(r"<td>(.*?)</td>", html, re.S)
    rows = [cells[i:i + ncol] for i in range(0, len(cells) - ncol + 1, ncol)]

    def g(row, field):
        i = col.get(field)
        return row[i] if i is not None and i < len(row) else None

    players = []
    for row in rows:
        eid = _clean(g(row, "eid"))
        if not eid.isdigit():
            continue
        p = {
            "eid": int(eid),
            "name": _clean(g(row, "name")),
            "position": _clean(g(row, "position")),
            "age": _num(g(row, "age"), int),
            "club": _clean(g(row, "club")),
            "league": _clean(g(row, "league")),
            "nation": _clean(g(row, "nation")),
            "value": parse_money(g(row, "value")),
            "wage": parse_money(g(row, "wage")),
            "transfer_fee": parse_money(g(row, "transfer_fee")),
            "goals": _num(g(row, "goals"), int),
            "assists": _num(g(row, "assists"), int),
            "xg": _num(g(row, "xg")),
            "xa": _num(g(row, "xa")),
            "minutes": _num(g(row, "minutes"), int),
            "rating": _num(g(row, "rating")),
            "apps": _eins(g(row, "apps")),
        }
        for f in _STAT_INT:
            p[f] = _num(g(row, f), int)
        lp90 = _num(g(row, "losses_p90"))       # nur /90 im Export -> Total ableiten
        m = p["minutes"] or 0
        p["losses"] = round((lp90 or 0) * m / 90) if m else None
        # Ballgewinne genauso: der Export kennt nur die /90-Rate. Bis hierher
        # waren sie RAM-only, und jeder Export-Spieler stand in der Vereins-DNA
        # (Ballgewinner) ohne Wert da.
        rp90 = _num(g(row, "recoveries_p90"))
        p["recoveries"] = round((rp90 or 0) * m / 90) if m else None
        p["pen_goals"] = _num(g(row, "pen_goals"), int) or 0
        # Torhueter: xGA wird aus Gegentoren + verhinderten Toren rekonstruiert,
        # weil die Score-Engine mit (xga - conceded) rechnet. 'xG verh/90' ist
        # FMs eigene Goals-Prevented-Rate; xGP im Export ist derselbe Wert als
        # Saisonsumme (an Restes gegengeprueft).
        p["conceded"] = _num(g(row, "conceded"), int)
        gp90 = _num(g(row, "gp_p90"))
        p["xga"] = (round((p["conceded"] or 0) + (gp90 or 0) * m / 90, 2)
                    if (m and p["conceded"] is not None) else None)
        # Ligen ohne Detailstatistik (im Save: Saudi-Arabien) exportiert FM
        # nicht als '-', sondern als 0: null Paraden bei 40 Gegentoren und
        # 'xG verh/90' exakt 0,00. Ein Torwart mit diesen Nullen stuende in
        # der Paradenquote bei 0 % und bei "verhindert" auf dem Schnitt –
        # beides Aussagen ueber Daten, die es nicht gibt. Deshalb: keine
        # Paraden bei mindestens zehn Gegentoren = keine Detailstatistik,
        # und dann bleiben Paraden UND xGA leer.
        saves = sum((p.get(k) or 0) for k in ("saves_tipped", "saves_parried", "saves_held"))
        if p["conceded"] is not None and p["conceded"] >= 10 and saves == 0:
            for k in ("saves_tipped", "saves_parried", "saves_held"):
                p[k] = None
            p["xga"] = None
        for f in _TEXT:
            t = _clean(g(row, f))
            p[f] = None if t in _TEXT_LEER else t
        # '189 cm' -> 189
        p["height"] = _num(g(row, "height"), int)
        # Chancen kreiert: nur als /90 im Export -> Saisonsumme ableiten
        ch90 = _num(g(row, "chances_p90"))
        p["chances"] = round((ch90 or 0) * m / 90) if (m and ch90 is not None) else None
        p["long_goals"] = _num(g(row, "long_goals"), int)
        # Sprints: einziges Laufmass, das im Export funktioniert ('Lauf/90' ist
        # ueberall 0.0km). Nur als /90 -> Saisonsumme ableiten.
        sp90 = _num(g(row, "sprints_p90"))
        p["sprints"] = round((sp90 or 0) * m / 90) if (m and sp90 is not None) else None
        players.append(p)
    return players, felder
