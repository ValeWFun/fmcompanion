"""Taktik-Analyse: wie gut erfuellt ein Spieler die Aufgaben SEINER Position in
DIESER Aufstellung?

Grundlage ist die Rollenanalyse des 4-2-3-1 "Highway Star" (offensiv, Tiki-Taka,
sehr hohe Linie, Gegenpressing) – seit dem Wechsel zu Manchester United (Sommer,
2. Saison) unveraendert uebernommen. Je Position sind die Aufgaben in messbare
Kennzahlen uebersetzt und gewichtet; der Score ist die gewichtete Summe der
Perzentile INNERHALB der Spieler, die diese Position ueberhaupt spielen koennen.

Bewusst dokumentiert, was NICHT messbar ist: die Datenbasis sind Ereigniszaehler
aus dem Speicher, keine Laufwege und keine Positionsdaten. Aufgaben wie
"Raum fuer den Aussenverteidiger freilaufen" erzeugen keine einzige Statistik.
"""
import re

from . import moneyball

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


def player_groups(p):
    """Positionsgruppen eines Spielers. Export schlaegt Speichermaske.

    Das Ergebnis wird am Spieler-Dict gemerkt (`_gruppen`). Grund: slot_dists()
    laeuft je Position einmal ueber die GANZE Vergleichsmenge und ruft dabei
    eligible() -> player_groups() auf. Bei elf Positionen und ~47.000 Spielern
    sind das 515.000 Aufrufe pro Taktikbrett, die alle dasselbe ausrechnen –
    gemessen 4,2 der 10 Sekunden. Der Unterstrich haelt den Schluessel aus der
    pywebview-Spiegelung heraus.
    """
    gemerkt = p.get("_gruppen")
    if gemerkt is not None:
        return gemerkt
    g = groups_from_position(p.get("position"))
    ergebnis = (g, "export") if g else (
        (lambda m: (m, "ram" if m else "unbekannt"))(groups_from_mask(p.get("pos_mask"))))
    p["_gruppen"] = ergebnis
    return ergebnis


# ------------------------------------------------------------- Umschulung
# Ein Ersatz muss die Position nicht schon spielen koennen: in FM laesst sich
# umschulen. Das kostet aber Zeit und faellt je nach Entfernung unterschiedlich
# schwer, deshalb bekommt ein positionsfremder Kandidat einen Abzug auf seinen
# Positionsscore.
#
# Modelliert als GRAPH direkter Nachbarschaften, NICHT als 12x12-Tabelle: von
# Hand gepflegt werden nur die neunzehn Kanten, alles Weitere ist der kuerzeste
# Weg darueber. So ergibt sich "Innenverteidiger auf Sturmspitze" automatisch
# als sehr teuer (25), ohne dass 144 Einzelwerte konsistent gehalten werden
# muessen – und eine Korrektur an einer Kante zieht sich sauber durch.
#
# Der Torwart hat BEWUSST keine einzige Kante. Er ist nicht ein paar Trainings-
# einheiten entfernt, sondern ein anderer Beruf; FM schult dorthin nicht um.
# Dadurch faellt er aus jeder Feldsuche heraus und Feldspieler aus der Torwart-
# suche, ohne dass das an einer weiteren Stelle abgefragt werden muss.
UMSCHULUNG_KANTEN = [
    ("iv", "lv", 6), ("iv", "rv", 6), ("lv", "rv", 9),   # Seitenwechsel teurer
    ("iv", "dm", 7),
    ("lv", "ml", 7), ("rv", "mr", 7),                    # die Linie hoch
    ("dm", "zm", 5),
    ("zm", "ml", 6), ("zm", "mr", 6), ("ml", "mr", 9),
    ("zm", "omz", 6),
    ("ml", "oml", 4), ("mr", "omr", 4),                  # nur eine Linie
    ("oml", "omr", 9),
    ("omz", "oml", 6), ("omz", "omr", 6),
    ("omz", "st", 7),
    ("oml", "st", 8), ("omr", "st", 8),
]
MAX_UMSCHULUNG = 22        # darueber ist es kein Ersatz mehr, sondern ein Umbau


def _umschulung_tabelle():
    """Alle Paare als kuerzester Weg (Floyd-Warshall ueber zwoelf Knoten)."""
    inf = float("inf")
    d = {(a, b): (0.0 if a == b else inf) for a in GROUPS for b in GROUPS}
    for a, b, k in UMSCHULUNG_KANTEN:
        d[(a, b)] = d[(b, a)] = min(d[(a, b)], float(k))
    for m in GROUPS:
        for a in GROUPS:
            if d[(a, m)] == inf:
                continue
            for b in GROUPS:
                if d[(a, m)] + d[(m, b)] < d[(a, b)]:
                    d[(a, b)] = d[(a, m)] + d[(m, b)]
    return {k: v for k, v in d.items() if v < inf}


UMSCHULUNG = _umschulung_tabelle()


def umschulung(von, nach):
    """Billigster Weg von einer Menge Positionsgruppen auf eine andere.

    Gibt (Kosten, Ausgangsgruppe) zurueck – 0 bedeutet, dass der Spieler die
    Position ohnehin schon spielt. (None, None), wenn es keinen Weg gibt.
    """
    best, quelle = None, None
    for a in von or ():
        for b in nach or ():
            k = UMSCHULUNG.get((a, b))
            if k is not None and (best is None or k < best):
                best, quelle = k, a
    return best, quelle


# ---------------------------------------------------------------- Kennzahlen
# label, Richtung (+1 hoch ist gut / -1 niedrig ist gut), Format
METRICS = {
    "gp_p90":       ("Verhinderte Tore/90", +1, "2f"),
    # Torwart, seit dem Nachschaerfen: je Schuss statt je Spiel, dazu der
    # Teil der Note, den die Gegentore der Mannschaft nicht erklaeren
    # (Herleitung in moneyball.PROFILES["tw"])
    "gp_shot":      ("Verhindert je 100 Schüsse", +1, "1f"),
    "save_pct":     ("Paradenquote %", +1, "pct"),
    "note_resid":   ("Note über Erwartung", +1, "2f"),
    "pass_pct":     ("Passquote %", +1, "pct"),
    "rating":       ("Ø-Note", +1, "2f"),
    "xg_p90":       ("xG/90", +1, "2f"),
    "goals_p90":    ("Tore/90", +1, "2f"),
    "xa_p90":       ("xA/90", +1, "2f"),
    "keyp_p90":     ("Schlüsselpässe/90", +1, "2f"),
    "prog_p90":     ("Progressive Pässe/90", +1, "2f"),
    "dribbles_p90": ("Dribblings/90", +1, "2f"),
    "rec_p90":      ("Ballgewinne/90", +1, "2f"),
    "int_p90":      ("Abgefangen/90", +1, "2f"),
    "press_p90":    ("Erfolgr. Pressing/90", +1, "2f"),
    "duel_pct":     ("Zweikampfquote %", +1, "pct"),
    "duels_p90":    ("Zweikämpfe/90", +1, "2f"),
    "header_pct":   ("Kopfballquote %", +1, "pct"),
    "shot_acc":     ("Schussgenauigkeit %", +1, "pct"),
    "finishing":    ("Tore über xG", +1, "2f"),
    "loss_rate":    ("Ballverluste je 100 Aktionen", -1, "1f"),
}

# ---------------------------------------------------------------- Formation
# x/y in Prozent des Spielfelds (0/0 = links oben, eigenes Tor unten).
# Gewichte aus der Rollenanalyse, nach der Gegenpruefung korrigiert:
#  - keine Kennzahl doppelt fuer dieselbe Dimension (pass_pct UND loss_p90)
#  - Quoten immer mit Volumen gepaart (duel_pct + duels_p90)
#  - Ballverluste als Rate je Aktion, nicht je 90 Minuten
#  - kein prog_p90 beim Torwart (misst dort lange Baelle = das Gegenteil)
FORMATION = [
    {
        "key": "tw", "label": "Torwart", "kurz": "TW",
        "rolle": "Mitspielender Torwart", "rolle_kurz": "MTW", "duty": "Verteidigen",
        "x": 50, "y": 91, "gruppen": ["tw"],
        "aufgaben": [
            "Jeden Angriff flach eröffnen, auch unter Druck",
            "Den Raum hinter der sehr hohen Kette absichern – als konservativste "
            "Sweeper-Variante situativ, nicht proaktiv",
            "Letzter Mann, wenn das hohe Pressing überspielt wird",
        ],
        # Shot-Stopping je Schuss und Note über Erwartung statt "verhinderte
        # Tore/90": Letztere waren zwischen den Saisonhälften nicht stabil
        # (r 0,23) und hoben Keeper hinter schwachen Abwehrreihen.
        "gewichte": {"pass_pct": 0.30, "note_resid": 0.25, "gp_shot": 0.20,
                     "rating": 0.15, "save_pct": 0.10},
        "blind": [
            "Ob er wirklich ausrollt oder lang schlägt – Passrichtung und "
            "Passlänge fehlen in den Daten komplett.",
            "Herauslaufen, Eins-gegen-eins und Strafraumbeherrschung – genau "
            "das, was der Torwart hinter einer sehr hohen Kette braucht – gibt "
            "es nicht als eigene Felder; Paraden je Schuss sind die Näherung.",
        ],
    },
    {
        "key": "lv", "label": "Linksverteidiger", "kurz": "LV",
        "rolle": "Außenverteidiger", "rolle_kurz": "AV", "duty": "Angriff",
        "x": 16, "y": 68, "gruppen": ["lv"],
        "aufgaben": [
            "Die gesamte Breite der Mannschaft herstellen – die Flügel ziehen "
            "invertiert nach innen",
            "Hoch aufrücken und den Strafraum mit flachen Hereingaben bedienen",
            "Nach Ballverlust in die 2-2-Restverteidigung zurück",
        ],
        "gewichte": {"xa_p90": 0.20, "keyp_p90": 0.15, "dribbles_p90": 0.15,
                     "prog_p90": 0.15, "rec_p90": 0.10, "duel_pct": 0.10,
                     "duels_p90": 0.05, "loss_rate": 0.10},
        "blind": [
            "Ob geflankt oder zurückgelegt wurde – Hereingaben sind kein eigenes Feld.",
            "Schlüsselpässe und xA enthalten Standards: wer Ecken tritt, sieht "
            "hier deutlich besser aus.",
        ],
    },
    {
        "key": "ivl", "label": "Innenverteidiger links", "kurz": "IV",
        "rolle": "Ballspielender Verteidiger", "rolle_kurz": "BsV", "duty": "Verteidigen",
        "x": 37, "y": 78, "gruppen": ["iv"],
        "aufgaben": [
            "Spielaufbau unter Druck, weil aus der Abwehr herausgespielt wird",
            "Linie halten und geschlossen aufrücken – kein aggressives "
            "Herausrücken, das wäre die Stopper-Aufgabe",
            "Kopfballverteidigung im Strafraum, weil Flanken bewusst zugelassen werden",
        ],
        "gewichte": {"pass_pct": 0.20, "prog_p90": 0.20, "header_pct": 0.15,
                     "int_p90": 0.10, "duel_pct": 0.10, "duels_p90": 0.05,
                     "rec_p90": 0.05, "loss_rate": 0.15},
        "blind": [
            "Ob der Ball diagonal auf die hochstehenden Außenverteidiger kam.",
            "Innenverteidiger führen im Schnitt nur rund anderthalb Bodenduelle "
            "pro Spiel – die Zweikampfquote ist entsprechend wacklig.",
        ],
    },
    {
        "key": "ivr", "label": "Innenverteidiger rechts", "kurz": "IV",
        "rolle": "Ballspielender Verteidiger", "rolle_kurz": "BsV", "duty": "Verteidigen",
        "x": 63, "y": 78, "gruppen": ["iv"],
        "aufgaben": [
            "Spielaufbau unter Druck, weil aus der Abwehr herausgespielt wird",
            "Linie halten und geschlossen aufrücken",
            "Kopfballverteidigung im Strafraum",
        ],
        "gewichte": {"pass_pct": 0.20, "prog_p90": 0.20, "header_pct": 0.15,
                     "int_p90": 0.10, "duel_pct": 0.10, "duels_p90": 0.05,
                     "rec_p90": 0.05, "loss_rate": 0.15},
        "blind": [
            "Ob der Ball diagonal auf die hochstehenden Außenverteidiger kam.",
            "Zu kleine Zweikampf-Grundmenge für eine belastbare Quote.",
        ],
    },
    {
        "key": "rv", "label": "Rechtsverteidiger", "kurz": "RV",
        "rolle": "Außenverteidiger", "rolle_kurz": "AV", "duty": "Angriff",
        "x": 84, "y": 68, "gruppen": ["rv"],
        "aufgaben": [
            "Die gesamte Breite der Mannschaft herstellen",
            "Hoch aufrücken und den Strafraum mit flachen Hereingaben bedienen",
            "Nach Ballverlust in die 2-2-Restverteidigung zurück",
        ],
        "gewichte": {"xa_p90": 0.20, "keyp_p90": 0.15, "dribbles_p90": 0.15,
                     "prog_p90": 0.15, "rec_p90": 0.10, "duel_pct": 0.10,
                     "duels_p90": 0.05, "loss_rate": 0.10},
        "blind": [
            "Ob geflankt oder zurückgelegt wurde.",
            "Standards verfälschen Schlüsselpässe und xA.",
        ],
    },
    {
        "key": "dml", "label": "Sechser links", "kurz": "DM",
        "rolle": "Defensiver Mittelfeldspieler", "rolle_kurz": "DM", "duty": "Unterstützen",
        "x": 38, "y": 55, "gruppen": ["dm", "zm"],
        "aufgaben": [
            "Abschirmen und zirkulieren – NICHT zwischen die Innenverteidiger "
            "abkippen, das wäre Halbverteidiger oder Regista",
            "Zweite Bälle nach dem Gegenpressing aufsammeln",
            "Ballsicher bleiben im engen Zentrum",
        ],
        "gewichte": {"rec_p90": 0.20, "pass_pct": 0.15, "loss_rate": 0.20,
                     "prog_p90": 0.10, "int_p90": 0.10, "press_p90": 0.10,
                     "duel_pct": 0.08, "duels_p90": 0.07},
        "blind": [
            "Die Rolle ist bewusst konservativ – wer den Tiefenpass aus der Sechs "
            "will, braucht eine andere Rolle, nicht einen anderen Spieler.",
            "Erfolgreiches Pressing hängt stark am Ballbesitz des bisherigen Vereins.",
        ],
    },
    {
        "key": "dmr", "label": "Sechser rechts", "kurz": "DM",
        "rolle": "Defensiver Mittelfeldspieler", "rolle_kurz": "DM", "duty": "Unterstützen",
        "x": 62, "y": 55, "gruppen": ["dm", "zm"],
        "aufgaben": [
            "Abschirmen und zirkulieren",
            "Zweite Bälle nach dem Gegenpressing aufsammeln",
            "Ballsicher bleiben im engen Zentrum",
        ],
        "gewichte": {"rec_p90": 0.20, "pass_pct": 0.15, "loss_rate": 0.20,
                     "prog_p90": 0.10, "int_p90": 0.10, "press_p90": 0.10,
                     "duel_pct": 0.08, "duels_p90": 0.07},
        "blind": [
            "Konservative Rolle – Tiefenpässe sind hier nicht vorgesehen.",
            "Pressingwerte sind teamabhängig.",
        ],
    },
    {
        "key": "aml", "label": "Linksaußen", "kurz": "IAS",
        "rolle": "Inverser Außenstürmer", "rolle_kurz": "IAS", "duty": "Unterstützen",
        "x": 18, "y": 33, "gruppen": ["oml", "ml"],
        "aufgaben": [
            "Auf den starken Fuß nach innen ziehen und SELBST abschließen – das "
            "ist der Kern der Rolle, nicht bloß Vorbereiten",
            "Entgegenkommen und zwischen den Linien kombinieren (Unterstützen)",
            "Die Linie für den Außenverteidiger räumen, sie aber halten, wenn "
            "der nicht aufgerückt ist",
        ],
        "gewichte": {"xg_p90": 0.20, "goals_p90": 0.10, "xa_p90": 0.15,
                     "keyp_p90": 0.15, "dribbles_p90": 0.15, "press_p90": 0.10,
                     "loss_rate": 0.15},
        "blind": [
            "Das Freilaufen für den Außenverteidiger – die wertvollste Aktion "
            "dieser Rolle erzeugt null Statistik.",
            "Dribblings kennen keine Zone: das 1-gegen-1 außen sieht identisch "
            "aus wie der Zug ins Zentrum.",
        ],
    },
    {
        "key": "amc", "label": "Zehner", "kurz": "OM",
        "rolle": "Offensiver Mittelfeldspieler", "rolle_kurz": "OM", "duty": "Angriff",
        "x": 50, "y": 36, "gruppen": ["omz"],
        "aufgaben": [
            "Verbindung zwischen Doppelsechs und Sturm",
            "Chancenkreation im engen Zentrum",
            "Späte Strafraumbesetzung als zweite Welle – kein Schattenstürmer, "
            "also keine eigenen Tiefenläufe",
        ],
        "gewichte": {"keyp_p90": 0.22, "xa_p90": 0.20, "xg_p90": 0.15,
                     "dribbles_p90": 0.10, "pass_pct": 0.10, "press_p90": 0.08,
                     "loss_rate": 0.15},
        "blind": [
            "Er teilt den Zwischenlinienraum mit beiden eingerückten Flügeln – "
            "wer dort wirklich anspielbar war, zeigen die Daten nicht.",
            "Standards machen einen großen Teil von Schlüsselpässen und xA aus.",
        ],
    },
    {
        "key": "amr", "label": "Rechtsaußen", "kurz": "IAS",
        "rolle": "Inverser Außenstürmer", "rolle_kurz": "IAS", "duty": "Unterstützen",
        "x": 82, "y": 33, "gruppen": ["omr", "mr"],
        "aufgaben": [
            "Auf den starken Fuß nach innen ziehen und selbst abschließen",
            "Entgegenkommen und zwischen den Linien kombinieren",
            "Die Linie für den Außenverteidiger räumen",
        ],
        "gewichte": {"xg_p90": 0.20, "goals_p90": 0.10, "xa_p90": 0.15,
                     "keyp_p90": 0.15, "dribbles_p90": 0.15, "press_p90": 0.10,
                     "loss_rate": 0.15},
        "blind": [
            "Das Freilaufen für den Außenverteidiger ist nicht messbar.",
            "Dribblings haben keinen Zonenbezug.",
        ],
    },
    {
        "key": "st", "label": "Sturmspitze", "kurz": "ST",
        "rolle": "Stoßstürmer", "rolle_kurz": "StS", "duty": "Angriff",
        "x": 50, "y": 15, "gruppen": ["st"],
        "aufgaben": [
            "Die letzte Linie besetzen und auf der Schulter des Verteidigers in "
            "die Tiefe laufen",
            "Den Strafraum besetzen – sie kommt NICHT entgegen und lässt nicht "
            "prallen, das wären Zielspieler oder hängende Spitze",
            "Chancen verwerten bei wenigen Kontakten",
        ],
        "gewichte": {"xg_p90": 0.28, "goals_p90": 0.20, "finishing": 0.12,
                     "xa_p90": 0.10, "press_p90": 0.10, "shot_acc": 0.07,
                     "duel_pct": 0.08, "duels_p90": 0.05},
        "blind": [
            "Das Anlaufen: der Stoßstürmer hat keinen eingebauten Pressing"
            "auftrag – wenn das wichtig ist, wäre der Pressende Stürmer die Rolle.",
            "Läufe ohne Ball, die Räume öffnen, tauchen in keiner Zahl auf.",
        ],
    },
]

MIN_MINUTES = 180          # darunter sind /90-Raten Rauschen
VERLAESSLICH_MIN = 180     # Bezug der Stichprobenguete: min/(min+180), ~2 Spiele

# ------------------------------------------------------------ Kadererkennung
LIST_RUN = 16              # Laenge des geprueften u32-Laufs
LIST_MIN_NAMED = 11        # so viele davon muessen benannte Spieler sein
MAX_RUN = 96               # Deckel fuer die Lauflaenge (Kadergroesse + Reserve)
MAX_ANKER = 400            # Notbremse: so viele Laeufe je Speicherblock genuegen


def find_squads(regions, names, stat_pids, min_size=11, max_size=60,
                label_names=None):
    """Findet Kaderlisten im Speicher: FM legt Mannschaften als zusammen-
    haengende u32-Arrays von Spieler-IDs ab.

    Erkennungsmerkmal ist NICHT "Array kleiner Zahlen" – danach zu suchen
    liefert Millionen Fehltreffer, weil Spieler-IDs kleine Ganzzahlen sind.
    Entscheidend ist, dass die Werte IDs sind, zu denen es sowohl einen
    Namens- als auch einen Statistik-Record gibt.

    Ueberlappende Fundstellen werden vereinigt: derselbe Kader liegt in vielen
    Kopien und oft unvollstaendig im Speicher; erst die Vereinigung ergibt den
    ganzen Kader.

    names       : NUR die gerade im Speicher stehenden Namen – der Anker lebt
                  von seiner Trennschaerfe. Mit einem grossen Namensgedaechtnis
                  (>10.000 IDs) trifft "11 Spieler-IDs in 16 Werten" auf
                  beliebiges Zahlenmaterial zu; die Suche findet dann Millionen
                  Scheinfunde und kommt nicht mehr zurueck.
    label_names : Zum BESCHRIFTEN der gefundenen IDs. Hier ist das volle
                  Gedaechtnis richtig, weil es nur um die Anzeige geht.
    max_size    : Obergrenze. Nicht jedes ID-Array ist ein Kader – die
                  Scouting- und Suchlisten der Oberflaeche sehen im Speicher
                  genauso aus, sind aber 100+ Eintraege lang und nach Position
                  sortiert (erkennbar an lauter Aussenverteidigern in einer
                  Liste). Ein Kader hat inkl. Jugend hoechstens ~50 Spieler.
    """
    label_names = label_names or names
    import numpy as np
    OBER = 200000          # groesser sind echte Spieler-IDs nicht
    gueltig = [p for p in (set(names) & set(stat_pids)) if 1000 <= p < OBER]
    if len(gueltig) < LIST_MIN_NAMED:
        return []
    # Nachschlagetabelle statt np.isin: isin sortiert bei jedem Durchlauf neu,
    # die Tabelle kostet 200 KB und beantwortet die Frage in konstanter Zeit.
    lut = np.zeros(OBER, np.bool_)
    lut[np.fromiter(gueltig, np.int64)] = True

    roh = {}
    for base, data in regions:
        n = len(data) // 4
        if n < LIST_RUN:
            continue
        v = np.frombuffer(data, np.uint32, count=n)
        # Direkter Tabellenzugriff statt Indexliste: np.nonzero ueber alle
        # plausiblen Werte erzeugt riesige Zwischenarrays, weil der Speicher
        # voller kleiner Ganzzahlen ist.
        hit = lut[np.where(v < OBER, v, 0)]
        if not hit.any():
            continue
        # Der ANKER verlangt viele benannte Spieler (selektiv), das AUSDEHNEN
        # dagegen nur plausible Spieler-IDs. Sonst zerschneidet ein einziges
        # Kadermitglied ohne Namens-Record die Liste – genau daran fehlte in
        # einem Testlauf der Torwart.
        # Nur die TREFFERSTELLEN betrachten statt jede Speicherposition:
        # ein Anker liegt vor, wenn LIST_MIN_NAMED Treffer innerhalb von
        # LIST_RUN Positionen liegen. Das ist O(Treffer) statt O(Region).
        tp = np.nonzero(hit)[0]
        if tp.size < LIST_MIN_NAMED:
            continue
        k = LIST_MIN_NAMED - 1
        spanne = tp[k:] - tp[:tp.size - k]
        if not (spanne <= LIST_RUN - 1).any():
            continue
        plaus = (v >= 1000) & (v < OBER)
        # WICHTIG: bereits abgedeckte Bereiche ueberspringen. Ohne das loest
        # jeder einzelne Treffer innerhalb derselben Liste einen eigenen
        # Durchlauf aus - bei einem grossen Namensgedaechtnis sind das
        # Millionen identischer Laeufe, und die Suche kommt nicht zurueck.
        letztes_ende, geprueft = -1, 0
        for si in np.nonzero(spanne <= LIST_RUN - 1)[0]:
            s = int(tp[si])
            if s < letztes_ende:
                continue
            geprueft += 1
            if geprueft > MAX_ANKER:      # Notbremse gegen Endlosschleifen
                break
            # Ausdehnung BEGRENZEN: ein Kader hat hoechstens ein paar Dutzend
            # Spieler. Ohne Deckel frisst ein Lauf den ganzen Speicherblock,
            # weil "plausible ID" auf sehr viele kleine Ganzzahlen zutrifft –
            # das war die Ursache, warum die Suche nicht zurueckkam.
            a, grenze = s, max(0, s - MAX_RUN)
            while a > grenze and plaus[a - 1]:
                a -= 1
            b, grenze = s + LIST_RUN, min(n, s + LIST_RUN + MAX_RUN)
            while b < grenze and plaus[b]:
                b += 1
            letztes_ende = b
            alle = frozenset(int(x) for x in v[a:b] if 1000 <= int(x) < OBER)
            if len(alle) > max_size:
                continue
            benannt = frozenset(p for p in alle if p in names)
            if len(benannt) >= min_size:
                roh[alle] = roh.get(alle, 0) + 1

    # Vereinigen, was sich stark ueberschneidet (gleiche Mannschaft)
    gruppen = []            # [ [pid-set, kopien] ]
    for pids, kopien in sorted(roh.items(), key=lambda kv: -len(kv[0])):
        for g in gruppen:
            klein = min(len(pids), len(g[0]))
            if klein and len(pids & g[0]) / klein >= 0.5:
                g[0] = g[0] | pids
                g[1] += kopien
                break
        else:
            gruppen.append([set(pids), kopien])

    out = []
    for pids, kopien in gruppen:
        if len(pids) > max_size:
            continue
        benannt = sorted(p for p in pids if p in label_names)
        if len(benannt) >= min_size:
            out.append({"pids": sorted(pids), "kopien": kopien,
                        "benannt": len(benannt),
                        "namen": sorted(label_names[p] for p in benannt)})
    # Nach Fundstellen sortieren: der eigene Kader liegt dauerhaft im Speicher
    # und damit in vielen Kopien, eine einmal geoeffnete Liste nur in wenigen.
    out.sort(key=lambda s: (-s["kopien"], -s["benannt"]))
    return out


def _pct(sorted_vals, v):
    """Perzentil mit Mittelung bei Gleichstaenden (wie moneyball._pct)."""
    import bisect
    n = len(sorted_vals)
    if n < 4:
        return 50.0
    lo = bisect.bisect_left(sorted_vals, v)
    hi = bisect.bisect_right(sorted_vals, v)
    return 100.0 * (lo + 0.5 * (hi - lo)) / n


def eligible(p, slot):
    """Kann der Spieler diese Position spielen? Gibt (ja, Quelle) zurueck."""
    gs, quelle = player_groups(p)
    return bool(gs & set(slot["gruppen"])), quelle


# ------------------------------------------------- Liga und Teamstaerke
# Kennzahlen, die mit dem Liga-Koeffizienten skaliert werden: alles, was eine
# RATE ist. Sechs Dribblings in der Premier League sind mehr wert als sechs in
# der Ekstraklasa. NICHT skaliert werden:
#  - Quoten (Pass-, Zweikampf-, Kopfball-, Schussquote): der Gegner ist staerker,
#    die Quote faellt dadurch von selbst – eine Skalierung zaehlte doppelt.
#  - 'rating': FM vergibt die Note bereits relativ zum Spielniveau.
#  - 'loss_rate' und 'finishing': Negativ- bzw. Differenzmasse. Ein schwacher
#    Wert mal 1.2 wuerde eine schlechte Leistung als noch schlechter ausweisen,
#    obwohl der Massstab genau umgekehrt laufen muesste.
SKALIERBAR = {"gp_p90", "xg_p90", "goals_p90", "xa_p90", "keyp_p90", "prog_p90",
              "dribbles_p90", "rec_p90", "int_p90", "press_p90", "duels_p90"}


def _koeff(p):
    # Der Import steht bewusst OBEN im Modul, nicht hier: _koeff laeuft je
    # Kennzahl und Spieler, bei einem Taktikbrett millionenfach. Als lokaler
    # Import kostete allein die Import-Maschinerie 69 % der Laufzeit von
    # _wert() und machte die Score-Engine 11x langsamer als vorher.
    # Aus demselben Grund gemerkt wie die Positionsgruppen: je Spieler konstant.
    k = p.get("_koeff")
    if k is None:
        k = p["_koeff"] = moneyball.league_coeff(p.get("league"))
    return k


def _wert(p, key):
    """Kennzahl eines Spielers fuer die Positionswertung, ligabereinigt.

    Vorher rechnete score_slot mit den Rohwerten: ein Spieler aus der Eredivisie
    stand mit denselben Dribblings pro 90 gleichauf mit einem aus der Premier
    League. Der Moneyball-Score kannte den Koeffizienten laengst, der
    Positions-Fit nicht – deshalb klafften beide Zahlen bei genau den Spielern
    auseinander, bei denen es darauf ankam.
    """
    # Ø-Note ligabereinigt (moneyball.LEAGUE_NOTE_OFFSET), angezeigt wird die
    # rohe Note – dieselbe Regel wie in der Score-Engine.
    v = p.get("rating_adj", p.get(key)) if key == "rating" else p.get(key)
    if v is None:
        return None
    return float(v) * _koeff(p) if key in SKALIERBAR else float(v)


# Carry-Zuschlag: wer eine schwache Mannschaft traegt, leistet mehr als
# dieselbe Zahl in einer starken. Bezug ist die Ø-Note des Vereins OHNE den
# Spieler selbst – sonst hebt er bei kleinen Kadern seinen eigenen Massstab an.
CARRY_CAP = 4.0            # mehr als +/- 4 Punkte verschiebt der Zuschlag nie
CARRY_K = 6.0              # Punkte je ganzer Note Unterschied (0.5 Note = 3)
CARRY_MIN_KADER = 5        # so viele Spieler braucht ein Verein fuer den Schnitt


def team_strength(players, min_kader=CARRY_MIN_KADER):
    """Verein -> (Ø-Note, Spielerzahl) fuer alle Vereine mit genug Spielern.

    Die Datenbasis sind die importierten Export-Spieler, und die sind eine
    VERZERRTE Stichprobe: gescoutet wurden interessante U27-Spieler, nicht der
    Durchschnitt eines Kaders. Der Schnitt faellt dadurch zu hoch aus und der
    Zuschlag entsprechend zu klein – die Richtung stimmt, die Groesse ist
    konservativ. Vereine unter `min_kader` bekommen gar keinen Wert; sie sind
    im Ergebnis neutral statt falsch.
    """
    summe, anzahl = {}, {}
    for p in players:
        c, r = p.get("club"), p.get("rating")
        if not c or not r:
            continue
        summe[c] = summe.get(c, 0.0) + float(r)
        anzahl[c] = anzahl.get(c, 0) + 1
    return {c: (summe[c] / anzahl[c], anzahl[c])
            for c in summe if anzahl[c] >= min_kader}


def carry_bonus(p, teams):
    """Punkte, die der Carry-Zuschlag auf den Positionsscore legt (oder 0).

    Gibt (Bonus, Vereinsschnitt, Kadergroesse) zurueck, damit die Oberflaeche
    die Herkunft der Zahl zeigen kann statt nur ihr Ergebnis.
    """
    if not teams:
        return 0.0, None, 0
    club, r = p.get("club"), p.get("rating")
    eintrag = teams.get(club) if club else None
    if not eintrag or not r:
        return 0.0, None, 0
    schnitt, n = eintrag
    if n <= 1:
        return 0.0, None, 0
    # eigenen Beitrag herausrechnen
    ohne = (schnitt * n - float(r)) / (n - 1)
    bonus = (float(r) - ohne) * CARRY_K
    return max(-CARRY_CAP, min(CARRY_CAP, bonus)), ohne, n - 1


def slot_dists(slot, reference, min_minutes=MIN_MINUTES):
    """Perzentil-Verteilungen je Kennzahl dieser Position.

    Gebildet NUR ueber Spieler, die die Position auch spielen koennen: ein
    Stuermer gehoert nicht in die Zweikampfverteilung der Innenverteidiger.
    Gibt (Verteilungen, Groesse der Vergleichsbasis) zurueck.
    """
    pool = [r for r in reference
            if (r.get("minutes") or 0) >= min_minutes and eligible(r, slot)[0]]
    dists = {}
    for key in slot["gewichte"]:
        vals = sorted(v for v in (_wert(r, key) for r in pool) if v is not None)
        if len(vals) >= 8:
            dists[key] = vals
    return dists, len(pool)


def score_slot(p, slot, dists, teams=None):
    """Positionsscore eines Spielers samt Aufschluesselung, oder None, wenn
    die Datengrundlage zu duenn ist.

    Bewusst OHNE Positionspruefung: dieselbe Rechnung bewertet auch einen
    positionsfremden Ersatzkandidaten, dessen Abzug erst danach dazukommt.

    teams: Ergebnis von team_strength(). Ist es gesetzt, kommt der Carry-
    Zuschlag auf den fertigen Score – NICHT auf die einzelnen Kennzahlen, sonst
    zaehlte er in jeder Perzentilrechnung erneut mit.
    """
    total, wsum, teile = 0.0, 0.0, []
    for key, w in slot["gewichte"].items():
        v = _wert(p, key)
        label, richtung, fmt = METRICS[key]
        if v is None or key not in dists:
            teile.append({"stat": key, "label": label, "wert": p.get(key),
                          "pct": None, "gewicht": w, "format": fmt})
            continue
        pc = _pct(dists[key], v)
        if richtung < 0:
            pc = 100.0 - pc
        total += w * pc
        wsum += w
        # angezeigt wird der ROHWERT, nicht der ligabereinigte: der Nutzer soll
        # die Zahl aus dem Spiel wiedererkennen. Die Bereinigung steckt im
        # Perzentil daneben.
        teile.append({"stat": key, "label": label, "wert": p.get(key),
                      "pct": round(pc), "gewicht": w, "format": fmt})
    if wsum < 0.5:                # zu wenig Datengrundlage
        return None
    roh = total / wsum
    # Kleine Stichproben werden BEWUSST NICHT weggerechnet. Eine fruehere Fassung
    # hat den Score zur Mitte gedaempft (50 + (roh-50) * min/(min+180)) und damit
    # genau den Fall unsichtbar gemacht, fuer den das Brett da ist: den Ersatz-
    # spieler mit 300 Minuten, der auf seiner Position besser liefert als der
    # Stammspieler mit 2500. Gedaempft blieb der immer hinter ihm.
    #
    # Der Ausreisserschutz ist deshalb rein VISUELL: die Zahl steht so da, wie
    # sie ist, und 'verlaesslich' (0-1) sagt daneben, wie belastbar sie ist.
    # Die Oberflaeche markiert duenne Werte sichtbar – wer sortiert, sieht den
    # Ausreisser oben und daneben, dass er auf 200 Minuten beruht.
    m = float(p.get("minutes") or 0)
    verl = m / (m + VERLAESSLICH_MIN) if m else 0.0
    bonus, teamschnitt, teamn = carry_bonus(p, teams)
    return {"score": max(0, min(100, round(roh + bonus))),
            "score_roh": round(roh), "carry": round(bonus, 1),
            "team_schnitt": None if teamschnitt is None else round(teamschnitt, 2),
            "team_n": teamn, "liga_koeff": round(_koeff(p), 2),
            "verlaesslich": round(verl, 2),
            "teile": sorted(teile, key=lambda t: -t["gewicht"])}


def build_board(squad, reference, min_minutes=MIN_MINUTES, teams=None):
    """Fuer jede Position die passenden Spieler mit Score und Aufschluesselung.

    squad     : Spieler, die bewertet werden (der eigene Kader)
    reference : Vergleichsmenge fuer die Perzentile (Pool + Kohorte)
    teams      : team_strength() fuer den Carry-Zuschlag, oder None
    """
    out = []
    for slot in FORMATION:
        dists, basis = slot_dists(slot, reference, min_minutes)
        kandidaten = []
        for p in squad:
            ok, quelle = eligible(p, slot)
            if not ok:
                continue
            b = score_slot(p, slot, dists, teams)
            if b is None:
                continue
            kandidaten.append({
                "id": p.get("id"), "name": p.get("name"),
                "age": p.get("age"), "minutes": p.get("minutes"),
                "rating": p.get("rating"), "pos_quelle": quelle,
                "club": p.get("club"), "league": p.get("league"),
                "dna": p.get("dna"), "dna_pass": p.get("dna_pass"),
                "dna_ball": p.get("dna_ball"),
                "mb": p.get("mb"),            # Moneyball-Score, vom Aufrufer gesetzt
                # Persoenlichkeit (sichtbare Beschreibung) neben dem Score
                "pers_score": p.get("pers_score"), "pers_label": p.get("pers_label"),
                "pers_stufe": p.get("pers_stufe"), "pers_medien": p.get("pers_medien"),
                "pers_hinweise": p.get("pers_hinweise") or [],
                "foot": p.get("foot"), "info": p.get("info"),
                "pers_stand": p.get("pers_stand"),
                # Ablöseforderung (Euro, meist None) und Eigengewaechs-Status
                "transfer_fee": p.get("transfer_fee"), "homegrown": p.get("homegrown"),
                "homegrown_stand": p.get("homegrown_stand"),
                # PL-Meldestatus bei uns (pl_markieren, vom Aufrufer gesetzt)
                "pl_status": p.get("pl_status"), "pl_grenzfall": p.get("pl_grenzfall"),
                "pl_text": p.get("pl_text"),
                "archetypen": {k: _archetyp_pct(b["teile"], stats)
                               for k, stats in
                               ARCHETYPEN.get(slot["key"], {}).items()},
                "stat_quelle": p.get("stat_quelle")} | b)
        kandidaten.sort(key=lambda k: -k["score"])
        out.append({k: slot[k] for k in
                    ("key", "label", "kurz", "rolle", "rolle_kurz", "duty", "x", "y",
                     "aufgaben", "blind")} | {
            "kandidaten": kandidaten, "vergleichsbasis": basis})
    return out


def startelf(board, pins=None):
    """Beste Elf: jede Position einmal, jeder Spieler einmal.

    Der beste Spieler je Position ist NICHT die Loesung: Borges ist auf beiden
    Fluegeln der Staerkste, Correia auf beiden Aussenbahnen. Ohne Zuordnung
    stuende derselbe Mann doppelt auf dem Platz und die zweite Position waere
    scheinbar besetzt.

    pins: {slot_key: player_id} – manuell gesetzte Spieler. Sie stehen fest,
    die Automatik fuellt die uebrigen Plaetze um sie herum. Ein Pin greift nur,
    wenn der Spieler auf dieser Position Kandidat ist und nicht schon auf einem
    anderen Pin steht; sonst wird er still ignoriert (z.B. nach einem Import,
    in dem er nicht mehr im Kader ist).

    Drei Schritte: erst gierig nach Score vergeben, dann paarweise tauschen
    und freie Spieler einwechseln, solange ein Zug die Summe verbessert – nur unter den automatisch
    vergebenen Plaetzen, die Pins bleiben unangetastet. Bei elf Plaetzen ist
    das in Millisekunden optimal genug und bleibt nachvollziehbar – anders als
    eine ausgewachsene Ungarische Methode, die hier niemand nachrechnen koennte.
    """
    punkte = {s["key"]: {k["id"]: k for k in s["kandidaten"]} for s in board}
    elf, belegt = {}, set()
    for skey, pid in (pins or {}).items():
        k = punkte.get(skey, {}).get(pid)
        if k is None or pid in belegt:
            continue
        elf[skey] = k
        belegt.add(pid)
    fest = set(elf)

    paare = sorted(((k["score"], s["key"], k["id"], k)
                    for s in board for k in s["kandidaten"]),
                   key=lambda t: -t[0])
    for score, skey, pid, k in paare:
        if skey in elf or pid in belegt:
            continue
        elf[skey] = k
        belegt.add(pid)

    verbessert = True
    while verbessert:
        verbessert = False
        keys = [k for k in elf if k not in fest]
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                pa, pb = elf[a], elf[b]
                na, nb = punkte[a].get(pb["id"]), punkte[b].get(pa["id"])
                if na is None or nb is None:
                    continue
                if na["score"] + nb["score"] > pa["score"] + pb["score"]:
                    elf[a], elf[b] = na, nb
                    verbessert = True
        # Einwechseln: ein FREIER Spieler, der auf einem Platz besser ist als
        # der gesetzte. Der Tausch oben bewegt nur Spieler, die schon stehen –
        # nach "de Ligt von der Sechs in die Innenverteidigung, Timber dafuer
        # auf die Sechs" blieb Timber (61) dort stehen, obwohl Mainoo (73) und
        # Wharton (72) auf der Bank sassen. Jeder Zug erhoeht die Summe, die
        # Schleife endet also.
        belegt = {k["id"] for k in elf.values()}
        for a in keys:
            frei = [k for k in punkte[a].values() if k["id"] not in belegt]
            if not frei:
                continue
            bester = max(frei, key=lambda k: k["score"])
            if bester["score"] > elf[a]["score"]:
                belegt.discard(elf[a]["id"])
                elf[a] = bester
                belegt.add(bester["id"])
                verbessert = True
    return elf


# ---------------------------------------------------------- Gesamtzahl
# Drei Achsen, geometrisch gemittelt: Fit (passt in die Rolle), Score (ist
# gut) und Charakter (bringt die Persoenlichkeit mit). Geometrisch wie bei
# der DNA – wer alles mitbringt, liegt vorn, ein Ausreisser nach unten zieht
# spuerbar. Der Charakter ist bewusst NICHT im Fit und nicht im Score: der
# Fit ist ein Perzentil gegen Tausende Vergleichsspieler ohne Persoenlichkeit
# und waere damit nicht mehr vergleichbar, und der Score misst Leistung.
#
# 0,30 ist "relativ grosser Einfluss": bei Fit 80 / Score 80 liegen zwischen
# "Ausgewogen" (50) und "Modellbuerger" (100) 16 Punkte, der Carry-Zuschlag
# darf hoechstens 4 bewegen. Ohne gescoutete Persoenlichkeit faellt der
# Faktor weg – keine Daten heisst unbekannt, nicht schlecht; die Ersatzsuche
# kann Ungescoutete auf Wunsch ausblenden.
CHAR_GEWICHT = 0.30


def gesamt(fit, mb=None, charakter=None):
    """Gesamtzahl 0-100 aus Fit, Moneyball-Score und Charakter.

    Leistung = sqrt(Fit x Score), ohne Score der Fit allein (Ersatzsuche).
    Gesamt = Leistung^(1-CHAR_GEWICHT) x Charakter^CHAR_GEWICHT, ohne
    Charakter die Leistung selbst. Rueckgabe None nur ohne Fit.
    """
    if fit is None:
        return None
    leistung = (float(fit) * float(mb)) ** 0.5 if mb is not None else float(fit)
    if charakter is None:
        return round(leistung)
    c = max(1.0, float(charakter))
    return round(max(0.0, min(100.0, leistung ** (1 - CHAR_GEWICHT) * c ** CHAR_GEWICHT)))


# ---------------------------------------------------------- Ligavergleich
# Wo steht die eigene Elf im Vergleich zur Liga? Je Position der eigene
# Stammspieler (aus der automatischen Elf, jeder Spieler nur einmal) gegen den
# jeweils BESTEN vorhandenen Spieler jedes anderen Vereins. Verglichen wird die
# LEISTUNG = sqrt(Fit x Score) ohne Charakterfaktor – es geht um Staerke auf
# dem Platz, nicht um die Kaderplanung.
#
# Zwei Einschraenkungen, die in die Ausgabe gehoeren statt in eine Fussnote:
#  - Die Datenbasis sind Scoutinglisten, keine Kader. Je Verein stehen oft nur
#    4-13 Spieler in den Daten, bei den Torhuetern eine Handvoll fuer die ganze
#    Liga. Jede Zeile nennt deshalb, wie viele Vereine ueberhaupt Daten haben.
#  - Bei den anderen Vereinen zaehlt derselbe Spieler auf mehreren Positionen
#    (ihr bester Fluegel links UND rechts), bei der eigenen Elf nicht. Der
#    Vergleich ist damit leicht ZU STRENG gegen den eigenen Verein – fuer die
#    Frage "wo muss ich nachlegen" die richtige Richtung des Fehlers.
LIGA_POSITIONEN = [
    ("tw", "Torwart", ["tw"]), ("lv", "Linksverteidiger", ["lv"]),
    ("iv", "Innenverteidiger", ["ivl", "ivr"]), ("rv", "Rechtsverteidiger", ["rv"]),
    ("dm", "Doppelsechs", ["dml", "dmr"]), ("aml", "Linksaußen", ["aml"]),
    ("amc", "Zehner", ["amc"]), ("amr", "Rechtsaußen", ["amr"]),
    ("st", "Sturmspitze", ["st"]),
]


def liga_vergleich(elf, pool, referenz, liga, verein, teams=None,
                   min_minutes=450):
    """Je Position: eigene Elf gegen die Liga.

    elf      : {slot_key: Kandidat aus dem Brett} mit 'fit', 'mb', 'name', 'minutes'
    pool     : angereicherte Spieler MIT Moneyball-Score ('score'), alle Ligen
    referenz : Vergleichsmenge fuer die Perzentile – dieselbe wie im Brett
    liga     : Name der Liga, verein: eigener Verein (wird aus dem Pool genommen)

    Doppelt besetzte Positionen (Innenverteidiger, Doppelsechs) vergleichen das
    Mittel der beiden Stammspieler mit dem Mittel der zwei Besten je Verein;
    Vereine mit nur einem Spieler in den Daten zaehlen dort mit diesem einen.
    """
    return _positionsvergleich(
        elf, pool, referenz,
        lambda p: p.get("league") == liga and p.get("club") != verein,
        teams, min_minutes)


# CL-Niveau: dieselbe Rechnung, aber gegen eine FESTE Liste von 8 Vereinen
# (Einstellung, typischerweise die CL-Viertelfinalisten der letzten Saison).
# Aus den Daten geschaetzt taugte die Auswahl nicht – sie mass unser Scouting,
# nicht die Vereine (Vorpruefung 23.09.: Nottingham Forest vorn, Bayern, Inter
# und Napoli gar nicht dabei). Messlatte ist der Median der Vereinswerte. Unter
# CL_MIN_VEREINE Vereinen mit Daten ist das "keine Aussage"; die Zeile nennt die
# fehlenden, damit klar ist, welchen Kader man noch exportieren muss.
CL_MIN_VEREINE = 6


def cl_vergleich(elf, pool, referenz, vereine, verein, teams=None,
                 min_minutes=450):
    """Je Position: eigene Elf gegen die festgelegten CL-Vergleichsvereine.

    vereine: Vereinsnamen wie im Export; der eigene Verein zaehlt nie mit.
    Zusaetzlich zu liga_vergleich je Zeile: vereine_soll, fehlende_vereine,
    aussage (bool), messlatte (Median oder None) und abstand (eigen − Messlatte).
    """
    ref = {v for v in (vereine or []) if v and v != verein}
    zeilen = _positionsvergleich(elf, pool, referenz,
                                 lambda p: p.get("club") in ref, teams, min_minutes)
    for z in zeilen:
        mit = {v["club"] for v in z["vereine"]}
        werte = sorted(v["wert"] for v in z["vereine"])
        z["vereine_mit_daten"] = len(mit)          # ohne den eigenen Verein
        z["vereine_soll"] = len(ref)
        z["fehlende_vereine"] = sorted(ref - mit)
        z["aussage"] = len(mit) >= CL_MIN_VEREINE
        if z["aussage"]:
            n = len(werte)
            z["messlatte"] = round(werte[n // 2] if n % 2 else
                                   (werte[n // 2 - 1] + werte[n // 2]) / 2, 1)
        else:
            z["messlatte"] = None
        z["abstand"] = (round(z["wert"] - z["messlatte"], 1)
                        if z["messlatte"] is not None and z["wert"] is not None else None)
    return zeilen


def _positionsvergleich(elf, pool, referenz, gehoert_dazu, teams=None,
                        min_minutes=450):
    """Gemeinsamer Kern von Liga- und CL-Vergleich: je Position die eigene Elf
    gegen den jeweils besten Spieler jedes Vereins, der gehoert_dazu(p) erfuellt."""
    slots = {s["key"]: s for s in FORMATION}
    out = []
    for key, label, skeys in LIGA_POSITIONEN:
        slot = slots[skeys[0]]
        dists, _ = slot_dists(slot, referenz)
        je_verein, alle = {}, []
        for p in pool:
            if not gehoert_dazu(p):
                continue
            if (p.get("minutes") or 0) < min_minutes or p.get("score") is None:
                continue
            if not eligible(p, slot)[0]:
                continue
            b = score_slot(p, slot, dists, teams)
            if b is None:
                continue
            wert = gesamt(b["score"], p["score"], None)
            eintrag = {"name": p.get("name"), "club": p.get("club"), "leistung": wert,
                       "fit": b["score"], "score": p["score"], "age": p.get("age"),
                       "minutes": p.get("minutes"), "value": p.get("value")}
            je_verein.setdefault(p["club"], []).append(eintrag)
            alle.append(eintrag)
        n_slots = len(skeys)
        vereine = []
        for club, sp in je_verein.items():
            sp.sort(key=lambda e: -e["leistung"])
            beste = sp[:n_slots]
            vereine.append({"club": club, "spieler": beste,
                            "wert": round(sum(e["leistung"] for e in beste) / len(beste), 1)})
        eigene = []
        for sk in skeys:
            k = elf.get(sk)
            if k is None:
                continue
            eigene.append({"name": k.get("name"), "fit": k.get("fit"), "score": k.get("mb"),
                           "leistung": gesamt(k.get("fit"), k.get("mb"), None),
                           "minutes": k.get("minutes"), "charakter": k.get("charakter")})
        eig = [e["leistung"] for e in eigene if e["leistung"] is not None]
        eigen_wert = round(sum(eig) / len(eig), 1) if eig else None
        vereine.sort(key=lambda v: -v["wert"])
        rang = (1 + sum(1 for v in vereine if v["wert"] > eigen_wert)
                if eigen_wert is not None else None)
        werte = sorted((v["wert"] for v in vereine), reverse=True)
        top4 = werte[3] if len(werte) >= 4 else (werte[-1] if werte else None)
        median = werte[len(werte) // 2] if werte else None
        einzel = sorted(e["leistung"] for e in alle)
        pz = (round(100 * sum(1 for w in einzel if w < eigen_wert) / len(einzel))
              if (einzel and eigen_wert is not None) else None)
        out.append({"key": key, "label": label, "rolle": slot["rolle"],
                    "rolle_kurz": slot["rolle_kurz"],
                    "x": slot["x"] if n_slots == 1 else 50, "y": slot["y"],
                    "eigene": eigene, "wert": eigen_wert, "rang": rang,
                    "vereine_mit_daten": len(vereine) + 1, "liga_spieler": len(alle),
                    "top4": top4, "median": median, "beste": werte[0] if werte else None,
                    "abstand_top4": (None if (top4 is None or eigen_wert is None)
                                     else round(eigen_wert - top4, 1)),
                    "perzentil": pz, "vereine": vereine})
    return out


# ---------------------------------------------------------- Ersatzsuche
GROUP_LABEL = {
    "tw": "Torwart", "iv": "Innenverteidiger", "rv": "Rechtsverteidiger",
    "lv": "Linksverteidiger", "dm": "Sechser", "zm": "Zentrales Mittelfeld",
    "mr": "Rechtes Mittelfeld", "ml": "Linkes Mittelfeld",
    "omz": "Zehner", "omr": "Rechtsaußen", "oml": "Linksaußen",
    "st": "Sturmspitze",
}

# Wonach gesucht wird. Die Schwellen sind absichtlich grob: der Score ist ein
# Perzentilmass, Unterschiede unter ein paar Punkten sind Rauschen.
MODI = {
    "aehnlich":   "Ähnliches Profil, gleiches Niveau",
    "besser":     "Stärker auf dieser Position",
    "juenger":    "Gleiches Niveau, aber jünger",
    "spezialist": "Kann eine Sache besonders gut",
}
TOL_GLEICH = 8             # +/- Punkte, die noch als "gleiches Niveau" gelten
MIN_BESSER = 3             # so viel muss "besser" mindestens besser sein
JUENGER_UM = 2             # Jahre, die "juenger" mindestens juenger ist
JUENGER_TOLERANZ = 5       # so weit darf er dabei im Score abfallen
SPEZ_MIN_PCT = 75          # so stark muss der Spezialist in SEINER Sache sein
SPEZ_TOLERANZ = 12         # so weit darf er dafuer im Gesamtscore abfallen

# ------------------------------------------------------------- Archetypen
# "Wer kann EINE Sache richtig gut?" – eine Teilmenge der Positionsgewichte,
# getrennt bewertet. Bewusst nur Kennzahlen, die im jeweiligen Slot ohnehin
# gewichtet sind: fuer alles andere baut slot_dists gar keine Verteilung, der
# Archetyp haette dann keine Datengrundlage.
#
# Der Sinn: Borges deckt auf dem Fluegel alles ab, aber wenn er fehlt, will man
# vielleicht gezielt einen Vorbereiter statt eines Abschliessers – oder
# umgekehrt, je nachdem, wer sonst noch auf dem Platz steht.
ARCHETYPEN = {
    "tw":  {"paraden": ["gp_shot", "save_pct"], "fussball": ["pass_pct"]},
    "lv":  {"offensiv": ["xa_p90", "keyp_p90", "dribbles_p90"],
            "defensiv": ["duel_pct", "duels_p90", "rec_p90"]},
    "rv":  {"offensiv": ["xa_p90", "keyp_p90", "dribbles_p90"],
            "defensiv": ["duel_pct", "duels_p90", "rec_p90"]},
    "ivl": {"aufbau": ["prog_p90", "pass_pct"], "kopfball": ["header_pct"],
            "zweikampf": ["duel_pct", "duels_p90", "int_p90"]},
    "ivr": {"aufbau": ["prog_p90", "pass_pct"], "kopfball": ["header_pct"],
            "zweikampf": ["duel_pct", "duels_p90", "int_p90"]},
    "dml": {"aufbau": ["prog_p90", "pass_pct"],
            "zerstoerer": ["int_p90", "duel_pct", "duels_p90"],
            "pressing": ["press_p90"]},
    "dmr": {"aufbau": ["prog_p90", "pass_pct"],
            "zerstoerer": ["int_p90", "duel_pct", "duels_p90"],
            "pressing": ["press_p90"]},
    "aml": {"creator": ["xa_p90", "keyp_p90"],
            "finisher": ["xg_p90", "goals_p90"], "dribbler": ["dribbles_p90"]},
    "amr": {"creator": ["xa_p90", "keyp_p90"],
            "finisher": ["xg_p90", "goals_p90"], "dribbler": ["dribbles_p90"]},
    "amc": {"creator": ["xa_p90", "keyp_p90"], "finisher": ["xg_p90"],
            "dribbler": ["dribbles_p90"]},
    "st":  {"finisher": ["xg_p90", "goals_p90", "finishing"],
            "zuarbeiter": ["xa_p90"], "pressing": ["press_p90", "duel_pct"]},
}
ARCHETYP_LABEL = {
    "paraden": "Paradenstark", "fussball": "Fußballspielend",
    "offensiv": "Offensivdrang", "defensiv": "Defensiv sicher",
    "aufbau": "Spielaufbau", "kopfball": "Kopfballstark",
    "zweikampf": "Zweikampfstark", "zerstoerer": "Ballgewinner",
    "pressing": "Pressingstark", "creator": "Vorbereiter",
    "finisher": "Abschließer", "dribbler": "Dribbler",
    "zuarbeiter": "Zuarbeiter",
}
# Jeder Archetyp braucht eine deutsche Beschriftung – fehlt sie, stand vorher
# der rohe Schluessel ("zuarbeiter") als Knopftext in der Oberflaeche. Lieber
# beim Import auffliegen als still im Kontextmenue.
_ohne_label = {k for m in ARCHETYPEN.values() for k in m} - set(ARCHETYP_LABEL)
assert not _ohne_label, f"Archetyp ohne Beschriftung: {sorted(_ohne_label)}"


def archetypen_fuer(slot_key):
    """Verfuegbare Archetypen einer Position, fertig fuer das Kontextmenue."""
    return [{"key": k, "label": ARCHETYP_LABEL.get(k, k),
             "stats": [METRICS[s][0] for s in stats]}
            for k, stats in ARCHETYPEN.get(slot_key, {}).items()]


def _archetyp_pct(teile, stats):
    """Wie stark ist der Spieler in genau diesen Kennzahlen? (0-100)

    Gemittelt wird ueber die Perzentile, gewichtet wie im Positionsscore –
    sonst zoege eine 5-%-Nebenkennzahl den Archetyp genauso stark wie die
    Hauptkennzahl. Fehlt jeder Wert, kommt None zurueck.
    """
    pw = [(t["pct"], t["gewicht"]) for t in teile
          if t["stat"] in stats and t["pct"] is not None]
    if not pw:
        return None
    gsum = sum(w for _, w in pw)
    return round(sum(p * w for p, w in pw) / gsum) if gsum else None


def _aehnlichkeit(teile_a, teile_b):
    """Wie aehnlich sind sich zwei Spieler IM PROFIL dieser Position?

    Verglichen werden die Perzentile der Kennzahlen, gewichtet wie im Score:
    ein Ausreisser bei einer 5-%-Kennzahl faellt kaum ins Gewicht, einer bei
    der Hauptkennzahl sehr wohl. Rueckgabe 0-100, 100 = deckungsgleich.
    Das misst den TYP, nicht die Klasse: zwei durchschnittliche Spieler sind
    sich sehr aehnlich, auch wenn beide nichts taugen.
    """
    pa = {t["stat"]: t["pct"] for t in teile_a if t["pct"] is not None}
    pb = {t["stat"]: t["pct"] for t in teile_b if t["pct"] is not None}
    gew = {t["stat"]: t["gewicht"] for t in teile_a}
    keys = [k for k in pa if k in pb]
    wsum = sum(gew[k] for k in keys)
    if wsum < 0.5:                # zu wenig gemeinsame Kennzahlen
        return None
    abw = sum(gew[k] * abs(pa[k] - pb[k]) for k in keys) / wsum
    return round(max(0.0, 100.0 - abw))


def find_replacements(slot, original, kandidaten, reference,
                      modus="aehnlich", min_minutes=MIN_MINUTES, limit=30,
                      max_umschulung=MAX_UMSCHULUNG, kader_ids=(),
                      archetyp=None, teams=None, nur_charakter=False):
    """Ersatz fuer einen Spieler auf einer bestimmten Position.

    Bewertet wird JEDER Kandidat nach den Gewichten DIESER Position – auch
    einer, der sie noch nicht spielt. Von seinem Score geht dann der
    Umschulungsaufwand ab (siehe UMSCHULUNG_KANTEN). Damit steht ein starker
    Sechser auch dann in der Liste fuer die Acht, wenn er dort noch nie
    gespielt hat, aber hinter einem gleich starken echten Achter.

    original   : der zu ersetzende Spieler (angereichert)
    kandidaten : Suchmenge, ueblicherweise der ganze Pool
    reference  : Vergleichsmenge fuer die Perzentile – MUSS dieselbe sein wie
                 bei build_board, sonst sind die Scores nicht vergleichbar
    kader_ids  : eigene Spieler; sie werden nicht ausgeschlossen, sondern nur
                 markiert – wer intern ersetzen kann, ist die guenstigste Loesung
    nur_charakter : Kandidaten ohne gescoutete Persoenlichkeit auslassen

    Verglichen und sortiert wird die GESAMTZAHL (siehe gesamt()): Fit nach
    Umschulungsabzug, dazu der Charakterfaktor. Der reine Fit bleibt als
    'fit' im Treffer, damit sichtbar ist, woher ein Abstand kommt.
    """
    dists, basis = slot_dists(slot, reference, min_minutes)
    ob = score_slot(original, slot, dists, teams)
    if ob is None:
        return {"ok": False, "error": "Für diesen Spieler reichen die Daten "
                                      "auf dieser Position nicht aus."}
    spez_stats = None
    if modus == "spezialist":
        spez_stats = ARCHETYPEN.get(slot["key"], {}).get(archetyp)
        if not spez_stats:
            return {"ok": False,
                    "error": f"Für diese Position gibt es keinen Archetyp "
                             f"„{archetyp}“."}
    ziel_fit, alter = ob["score"], original.get("age")
    ziel = gesamt(ziel_fit, None, original.get("pers_score"))
    if modus == "juenger" and alter is None:
        return {"ok": False, "error": "Von diesem Spieler ist kein Geburtsdatum "
                                      "bekannt – ohne Alter keine Suche nach "
                                      "jüngeren Alternativen."}
    kader_ids = set(kader_ids or ())
    eigen = original.get("id")
    # Auch ueber die EID ausschliessen: derselbe Spieler taucht im RAM je
    # Wettbewerb mit eigener player_id auf und stuende sonst als sein eigener
    # Ersatz in der Liste.
    eigen_eid = original.get("eid")

    treffer, geprueft = [], 0
    for p in kandidaten:
        pid = p.get("id")
        if pid is None or pid == eigen or not p.get("name"):
            continue
        if eigen_eid and p.get("eid") == eigen_eid:
            continue
        if (p.get("minutes") or 0) < min_minutes:
            continue
        gruppen, quelle = player_groups(p)
        kosten, von = umschulung(gruppen, slot["gruppen"])
        if kosten is None or kosten > max_umschulung:
            continue
        geprueft += 1
        b = score_slot(p, slot, dists, teams)
        if b is None:
            continue
        if nur_charakter and p.get("pers_score") is None:
            continue
        fit = max(0, min(100, round(b["score"] - kosten)))
        score = gesamt(fit, None, p.get("pers_score"))
        a = p.get("age")
        spez = None
        if modus == "besser":
            if score < ziel + MIN_BESSER:
                continue
        elif modus == "juenger":
            if a is None or a > alter - JUENGER_UM or score < ziel - JUENGER_TOLERANZ:
                continue
        elif modus == "spezialist":
            # Der Spezialist darf im Gesamtbild schwaecher sein – dafuer muss er
            # in seiner Sache deutlich herausragen. Ohne die Untergrenze beim
            # Gesamtscore stuenden sonst Totalausfaelle mit einer guten
            # Einzelkennzahl ganz oben.
            spez = _archetyp_pct(b["teile"], spez_stats)
            if spez is None or spez < SPEZ_MIN_PCT or score < ziel - SPEZ_TOLERANZ:
                continue
        else:                     # aehnlich
            if abs(score - ziel) > TOL_GLEICH:
                continue
        aehn = _aehnlichkeit(ob["teile"], b["teile"])
        if modus == "aehnlich" and aehn is None:
            continue
        treffer.append({
            "id": pid, "name": p.get("name"), "age": a,
            "minutes": p.get("minutes"), "rating": p.get("rating"),
            "club": p.get("club"), "league": p.get("league"),
            "value": p.get("value"), "stale": bool(p.get("stale")),
            "stand": p.get("taken_at"), "gruppen": sorted(gruppen),
            "im_kader": pid in kader_ids, "pos_quelle": quelle,
            "score": score, "fit": fit, "score_pos": b["score"],
            "charakter": p.get("pers_score"), "abzug": round(kosten),
            "umschulung": None if not kosten else GROUP_LABEL.get(von, von),
            "aehnlichkeit": aehn, "archetyp_pct": spez,
            "dna": p.get("dna"), "dna_pass": p.get("dna_pass"),
            "dna_ball": p.get("dna_ball"),
            "pers_score": p.get("pers_score"), "pers_label": p.get("pers_label"),
            "pers_stufe": p.get("pers_stufe"), "pers_medien": p.get("pers_medien"),
            "pers_hinweise": p.get("pers_hinweise") or [],
            "foot": p.get("foot"), "info": p.get("info"), "wage": p.get("wage"),
            "pers_stand": p.get("pers_stand"),
            "transfer_fee": p.get("transfer_fee"), "homegrown": p.get("homegrown"),
            "homegrown_stand": p.get("homegrown_stand"),
            "pl_status": p.get("pl_status"), "pl_grenzfall": p.get("pl_grenzfall"),
            "pl_text": p.get("pl_text"),
            "carry": b["carry"], "liga_koeff": b["liga_koeff"],
            "team_schnitt": b["team_schnitt"],
            "delta_score": score - ziel,
            "delta_age": None if (a is None or alter is None) else round(a - alter, 1),
            "verlaesslich": b["verlaesslich"], "teile": b["teile"],
        })

    if modus == "aehnlich":
        treffer.sort(key=lambda t: (-t["aehnlichkeit"], -t["score"]))
    elif modus == "juenger":
        treffer.sort(key=lambda t: (-t["score"], t["age"]))
    elif modus == "spezialist":
        treffer.sort(key=lambda t: (-t["archetyp_pct"], -t["score"]))
    else:
        treffer.sort(key=lambda t: -t["score"])

    return {"ok": True, "modus": modus, "modus_label": MODI.get(modus, modus),
            "archetyp": archetyp,
            "archetyp_label": ARCHETYP_LABEL.get(archetyp) if archetyp else None,
            "slot": {k: slot[k] for k in ("key", "label", "rolle", "rolle_kurz", "duty")},
            "original": {"id": eigen, "name": original.get("name"),
                         "age": alter, "minutes": original.get("minutes"),
                         "score": ziel, "fit": ziel_fit,
                         "charakter": original.get("pers_score"),
                         "teile": ob["teile"],
                         "carry": ob["carry"], "liga_koeff": ob["liga_koeff"],
                         "archetyp_pct": (_archetyp_pct(ob["teile"], spez_stats)
                                          if spez_stats else None)},
            "treffer": treffer[:limit], "geprueft": geprueft,
            "gefunden": len(treffer), "vergleichsbasis": basis,
            "min_minutes": min_minutes, "char_gewicht": CHAR_GEWICHT,
            "nur_charakter": bool(nur_charakter)}


# ------------------------------------------------------------ Meldelisten
# Premier League: hoechstens 25 Spieler ueber 21, davon hoechstens 17 nicht
# heimisch; die Liste schrumpft auf 17 + Heimische, solange es weniger als 8
# sind. U21 sind frei. Champions League, Liste A: 17 + bis zu 8 lokal
# ausgebildete (davon hoechstens 4 "im Land"). Die CL-Zahl ist nur ein
# Richtwert: FM zeigt im Export nicht zuverlaessig den staerksten Status –
# United-Akademiespieler stehen immer mit "(0–21)" da, auch wenn sie das
# UEFA-Fenster ab 15 erfuellen (Mainoo). Massgeblich bleibt der
# Registrierungsbildschirm im Spiel.
PL_MAX_KADER = 25
PL_MAX_NICHT_HEIMISCH = 17
PL_MIN_HEIMISCH = 8
CL_MAX_LOKAL = 8
CL_MAX_LAND = 4
# ab diesem Tag im Jahr gilt ein Export fuer die Saison, die im selben Jahr
# beginnt (Sommer-Exporte liegen bei Tag 134–139, Winter-Exporte bei 361–364
# des Vorjahres bzw. frueh im Jahr; siehe moneyball.bezugsdatum)
SAISON_AB_TAG = 121
PL_TEXT = {"heimisch": "heimisch – kostet keinen Ausländerplatz",
           "braucht_platz": "braucht Ausländerplatz",
           "u21": "U21 – frei",
           "unbekannt": "Eigengewächs-Status unbekannt"}


def saisonstart(ref_year, ref_day=None):
    """Startjahr der Saison, fuer die gemeldet wird.

    Bis Ende April laeuft die Saison des Vorjahres (Winterfenster); ab Mai
    wird fuer die Saison gemeldet, die in diesem Jahr beginnt. Ohne Tag
    (manuelles season_year) gilt das Bezugsjahr selbst.
    """
    if not ref_year:
        return None
    return ref_year if (ref_day is None or ref_day >= SAISON_AB_TAG) else ref_year - 1


def pl_status(p, start, seit=None, ref_year=None):
    """PL-Meldestatus eines Spielers BEI UNS -> (status, grenzfall).

    status: "u21", "heimisch", "braucht_platz" oder "unbekannt".
    - U21: Geburtsjahr >= Saisonstart - 21. Das Geburtsjahr kommt aus dem RAM
      (birth_year); fehlt es, aus dem Export-Alter – das laesst +-1 Jahr offen
      (Geburtstag vor oder nach dem Export). Liegt die Grenze genau dazwischen,
      ist es ein GRENZFALL und zaehlt vorsichtshalber als ueber 21.
    - Heimisch: jeder homegrown-Wert (Verein wie Land). Er bezieht sich auf
      den Verein des Nutzers zum Exportzeitpunkt und zaehlt deshalb nur mit
      homegrown_stand ab `seit` (Vereinswechsel); ein Wert aus der Benfica-
      Zeit sagt ueber United nichts. Ohne verwertbaren Stand: "unbekannt".
    """
    grenze = start - 21 if start else None
    by = p.get("birth_year")
    grenzfall = False
    if grenze is not None and by:
        u21 = int(by) >= grenze
    elif grenze is not None and p.get("age") is not None and ref_year:
        jahre = {ref_year - int(p["age"]) - 1, ref_year - int(p["age"])}
        u21 = min(jahre) >= grenze
        grenzfall = not u21 and max(jahre) >= grenze
    elif p.get("age") is not None:
        # Ohne Bezugsdatum nur das Export-Alter: der Export liegt im Jahr des
        # Saisonstarts oder im Jahr danach, das Geburtsjahr ist +-1 offen.
        # Bis 20 ist ein Spieler sicher U21, ab 23 sicher nicht; 21 und 22
        # sind Grenzfaelle – sonst stuende "Grenzfall" an jedem 28-Jaehrigen.
        a = int(p["age"])
        u21, grenzfall = a <= 20, a in (21, 22)
    else:
        u21, grenzfall = False, True
    if u21:
        return "u21", False
    stand = p.get("homegrown_stand")
    if not stand or (seit and stand < seit):
        return "unbekannt", grenzfall
    return ("heimisch" if p.get("homegrown") else "braucht_platz"), grenzfall


def pl_markieren(players, start, seit=None, ref_year=None):
    """pl_status, pl_grenzfall und pl_text an jeden Spieler haengen (in place)."""
    for p in players:
        st, gf = pl_status(p, start, seit, ref_year)
        p["pl_status"], p["pl_grenzfall"], p["pl_text"] = st, gf, PL_TEXT[st]
    return players


def meldeliste(kader, start, seit=None, ref_year=None):
    """PL-Zaehler und CL-Richtwert fuer den eigenen Kader.

    Grundlage des Winter-Scoutings: jeder Kandidat mit "heimisch" kostet
    keinen der 17 Plaetze fuer Nicht-Heimische.
    """
    pl_markieren(kader, start, seit, ref_year)
    zaehl = {s: sum(1 for p in kader if p["pl_status"] == s) for s in PL_TEXT}
    heim = zaehl["heimisch"]
    nicht = zaehl["braucht_platz"]
    kader_max = PL_MAX_KADER - max(0, PL_MIN_HEIMISCH - heim)
    ueber_21 = heim + nicht + zaehl["unbekannt"]
    # CL Liste A: nur "(15–21)" zaehlt SICHER als UEFA-ausgebildet; "(0–21)"
    # kann es sein (Mainoo erfuellt das Fenster ab 15, FM zeigt trotzdem
    # "0–21"). Daraus eine sichere Untergrenze und eine Obergrenze.
    heimische = [p for p in kader if p["pl_status"] == "heimisch"]

    def art(p, wo, fenster):
        h = (p.get("homegrown") or "").replace("-", "–")   # FM schreibt Halbgeviertstrich
        return wo in h and fenster in h

    verein = sum(1 for p in heimische if art(p, "Verein", "15–21"))
    land = sum(1 for p in heimische if art(p, "Land", "15–21"))
    offen = [p for p in heimische if art(p, "", "0–21")]
    pruefen = len(offen)
    lokal = min(verein + min(land, CL_MAX_LAND), CL_MAX_LOKAL)
    lokal_max = min(verein + sum(1 for p in offen if art(p, "Verein", "0–21"))
                    + min(land + sum(1 for p in offen if art(p, "Land", "0–21")),
                          CL_MAX_LAND), CL_MAX_LOKAL)
    return {
        "saisonstart": start, "u21_ab_jahrgang": start - 21 if start else None,
        "pl": {"nicht_heimisch": nicht, "max_nicht_heimisch": PL_MAX_NICHT_HEIMISCH,
               "heimisch": heim, "u21": zaehl["u21"], "unbekannt": zaehl["unbekannt"],
               "ueber_21": ueber_21, "kader_max": kader_max,
               # Mit unbekanntem Status laesst sich nichts sicher sagen – None
               # statt False, sonst meldete die App einen Verstoss, der nur an
               # fehlenden Daten haengt (vor dem ersten Import mit der Spalte).
               "ok": (None if zaehl["unbekannt"] else
                      nicht <= PL_MAX_NICHT_HEIMISCH and ueber_21 <= kader_max),
               "text": (f"PL: {nicht}/{PL_MAX_NICHT_HEIMISCH} Nicht-Heimische über 21, "
                        f"{heim} Heimische"
                        + (f", {zaehl['unbekannt']} ohne Eigengewächs-Status – "
                           f"Kader-Export mit der Spalte „Status Eigengewächs“ importieren"
                           if zaehl["unbekannt"] else ""))},
        "cl": {"verein_15": verein, "land_15": land, "pruefen_0_21": pruefen,
               "pruefen_namen": [p.get("name") for p in offen],
               # sichere Untergrenze; bis zu liste_a_bis, falls die "(0–21)"-
               # Spieler fuer die UEFA als ausgebildet zaehlen
               "liste_a_max": PL_MAX_NICHT_HEIMISCH + lokal,
               "liste_a_bis": PL_MAX_NICHT_HEIMISCH + lokal_max,
               "text": (f"CL Liste A: sicher {PL_MAX_NICHT_HEIMISCH + lokal}, bis zu "
                        f"{PL_MAX_NICHT_HEIMISCH + lokal_max} Plätze – "
                        f"{', '.join(p.get('name') or '?' for p in offen)} („0–21“) "
                        f"im Registrierungsbildschirm prüfen"
                        if lokal_max > lokal else
                        f"CL Liste A: {PL_MAX_NICHT_HEIMISCH + lokal} Plätze (Richtwert)")},
        "grenzfaelle": [p.get("name") for p in kader if p["pl_grenzfall"]],
        "spieler": [{"id": p.get("id"), "eid": p.get("eid"), "name": p.get("name"),
                     "age": p.get("age"), "birth_year": p.get("birth_year"),
                     "homegrown": p.get("homegrown"), "pl_status": p["pl_status"],
                     "pl_grenzfall": p["pl_grenzfall"], "pl_text": p["pl_text"]}
                    for p in kader],
    }
