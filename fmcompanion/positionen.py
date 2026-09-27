"""Positionsgruppen eines Spielers – gemeinsame Grundlage von Score-Engine
(moneyball) und Taktik (tactics).

Bis D19 stand das in tactics.py. moneyball braucht die Gruppen seit D19
selbst (welche Score-Profile erlauben die Positionen eines Spielers?),
und tactics importiert moneyball – deshalb ein eigenes Modul ohne
Abhaengigkeiten. tactics re-exportiert die Namen, bestehende Aufrufer
(tactics.player_groups usw.) bleiben unveraendert.
"""
import re

# ---------------------------------------------------------------- Positionen
# Gruppencodes, in die BEIDE Quellen uebersetzt werden: die Positionsmaske aus
# dem Speicher (nur GESPIELTE Positionen) und der Positionsstring aus dem
# FM-Export (die echte Positionskompetenz, deutlich verlaesslicher).
GROUPS = ("tw", "iv", "rv", "lv", "dm", "zm", "mr", "ml", "omz", "omr", "oml", "st")

# Speicher-Bitmaske -> Gruppen. Bit-Bedeutung per RE ermittelt (siehe Projekt-
# notizen). Links/rechts ist dort nur bei den Aussenverteidigern sauber
# getrennt; Fluegel (Bit 11) und OM (Bit 12/13) kennen keine Seite.
BIT_GROUPS = {
    0: ("tw",), 1: ("rv", "lv"), 2: ("rv",), 3: ("lv",), 4: ("iv",),
    5: ("rv", "lv"), 6: ("dm",), 7: ("dm",), 8: ("mr", "ml"), 9: ("zm",),
    10: ("zm",), 11: ("mr", "ml", "omr", "oml"), 12: ("omz",),
    13: ("omz",), 14: ("st",), 15: ("st",),
}


def groups_from_mask(mask):
    out = set()
    for bit, gs in BIT_GROUPS.items():
        if int(mask or 0) & (1 << bit):
            out.update(gs)
    return out


def groups_from_position(text):
    """FM-Positionsstring -> Gruppen, z.B. 'OM (RL), ST (Z)' -> {omr, oml, st}.
    Das ist die AUSSAGEKRAEFTIGERE Quelle: sie nennt alle Positionen, die der
    Spieler spielen kann, waehrend die Speichermaske nur zeigt, wo er zuletzt
    tatsaechlich stand.

    Mehrere Koepfe mit SCHRAEGSTRICH teilen sich die Seitenangabe in der
    Klammer: 'M/OM (R)' heisst rechtes Mittelfeld UND rechtes Offensivmittel-
    feld. Ohne die Aufteilung passte kein Zweig und der Spieler kam ohne
    Gruppe zurueck (gleicher Fehler wie in moneyball.pos_mask_from_string).
    """
    out = set()
    for teil in (text or "").split(","):
        t = teil.strip()
        if not t:
            continue
        koepfe = re.split(r"[ (]", t)[0].upper()
        m = re.search(r"\(([^)]*)\)", t)
        seiten = (m.group(1) if m else "").upper()
        R, L, Z = "R" in seiten, "L" in seiten, "Z" in seiten
        for kopf in koepfe.split("/"):
            if kopf == "TW":
                out.add("tw")
            elif kopf in ("V", "FV"):
                if Z or (kopf == "V" and not seiten):
                    out.add("iv")
                if R or kopf == "FV" and not seiten:
                    out.add("rv")
                if L:
                    out.add("lv")
            elif kopf == "DM":
                out.add("dm")
            elif kopf == "M":
                if Z or not seiten:
                    out.add("zm")
                if R:
                    out.add("mr")
                if L:
                    out.add("ml")
            elif kopf == "OM":
                if Z or not seiten:
                    out.add("omz")
                if R:
                    out.add("omr")
                if L:
                    out.add("oml")
            elif kopf == "ST":
                out.add("st")
    return out


_GRUPPEN_CACHE = {}


def player_groups(p):
    """Positionsgruppen eines Spielers -> (frozenset, Quelle). Export schlaegt
    Speichermaske.

    Gemerkt je (Position, Maske) in einem Modul-Cache. Grund: slot_dists()
    laeuft je Position einmal ueber die GANZE Vergleichsmenge und ruft dabei
    eligible() -> player_groups() auf. Bei elf Positionen und ~47.000 Spielern
    sind das 515.000 Aufrufe pro Taktikbrett, die alle dasselbe ausrechnen –
    gemessen 4,2 der 10 Sekunden.

    Frueher stand das Ergebnis als `_gruppen` (mit einem set) am Spieler-Dict.
    Seit D19 bekommt jede Tabellenzeile Profile und damit diesen Schluessel –
    und pywebview serialisiert Rueckgabewerte VOLLSTAENDIG, der Unterstrich
    schuetzt nur Attribute des js_api-Objekts. load_saved scheiterte mit
    "Object of type set is not JSON serializable", die App zeigte keine
    Spieler. Deshalb nichts mehr am Dict, und frozenset: niemand darf das
    gemerkte Ergebnis veraendern.
    """
    pos, maske = p.get("position"), p.get("pos_mask")
    schluessel = (pos, maske)
    gemerkt = _GRUPPEN_CACHE.get(schluessel)
    if gemerkt is not None:
        return gemerkt
    g = groups_from_position(pos)
    if g:
        ergebnis = (frozenset(g), "export")
    else:
        m = groups_from_mask(maske)
        ergebnis = (frozenset(m), "ram" if m else "unbekannt")
    _GRUPPEN_CACHE[schluessel] = ergebnis
    return ergebnis
