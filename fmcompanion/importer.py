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
}
# ganzzahlige Statistik-Felder
_STAT_INT = ["duels", "duels_total", "shots_total", "shots_on", "pass_try",
             "pass_ok", "dribbles", "prog_passes", "press_win", "press_try",
             "interceptions", "key_passes", "clearances", "headers_won",
             "headers_total"]


def _clean(s):
    return re.sub(r"<.*?>", "", s or "").strip()


def _num(s, cast=float):
    s = _clean(s)
    if not s or s in ("-", "N/A"):
        return None
    try:
        return cast(s.replace(",", "."))
    except ValueError:
        return None


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
    if unit in ("Mrd", "Mio", "Tsd", "M", "K", "B"):     # Komma = Dezimal
        num = num.replace(".", "").replace(",", ".")
    else:                                                # reine Zahl: Trenner weg
        num = num.replace(".", "").replace(",", "")
    try:
        return float(num) * mult
    except ValueError:
        return None


def parse_export(path):
    """Liste von Spieler-Dicts aus einem FM-HTML-Export."""
    with open(path, encoding="utf-8", errors="replace") as f:
        html = f.read()
    headers = [_clean(h) for h in re.findall(r"<th>(.*?)</th>", html, re.S)]
    ncol = len(headers)
    if ncol == 0:
        return []
    col = {}
    for field, names in COLUMNS.items():
        for nm in names:
            if nm in headers:
                col[field] = headers.index(nm)
                break
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
            "goals": _num(g(row, "goals"), int),
            "assists": _num(g(row, "assists"), int),
            "xg": _num(g(row, "xg")),
            "xa": _num(g(row, "xa")),
            "minutes": _num(g(row, "minutes"), int),
            "rating": _num(g(row, "rating")),
            "apps": _num(g(row, "apps"), int),
        }
        for f in _STAT_INT:
            p[f] = _num(g(row, f), int)
        lp90 = _num(g(row, "losses_p90"))       # nur /90 im Export -> Total ableiten
        m = p["minutes"] or 0
        p["losses"] = round((lp90 or 0) * m / 90) if m else None
        players.append(p)
    return players
