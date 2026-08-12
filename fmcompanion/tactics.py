"""Taktik-Analyse: wie gut erfuellt ein Spieler die Aufgaben SEINER Position in
DIESER Aufstellung?

Grundlage ist die Rollenanalyse des 4-2-3-1 "Highway Star" (offensiv, Tiki-Taka,
sehr hohe Linie, Gegenpressing). Je Position sind die Aufgaben in messbare
Kennzahlen uebersetzt und gewichtet; der Score ist die gewichtete Summe der
Perzentile INNERHALB der Spieler, die diese Position ueberhaupt spielen koennen.

Bewusst dokumentiert, was NICHT messbar ist: die Datenbasis sind Ereigniszaehler
aus dem Speicher, keine Laufwege und keine Positionsdaten. Aufgaben wie
"Raum fuer den Aussenverteidiger freilaufen" erzeugen keine einzige Statistik.
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
    tatsaechlich stand."""
    out = set()
    for teil in (text or "").split(","):
        t = teil.strip()
        if not t:
            continue
        kopf = re.split(r"[ (]", t)[0].upper()
        m = re.search(r"\(([^)]*)\)", t)
        seiten = (m.group(1) if m else "").upper()
        R, L, Z = "R" in seiten, "L" in seiten, "Z" in seiten
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
    """Positionsgruppen eines Spielers. Export schlaegt Speichermaske."""
    g = groups_from_position(p.get("position"))
    if g:
        return g, "export"
    g = groups_from_mask(p.get("pos_mask"))
    return g, ("ram" if g else "unbekannt")


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
        "rolle": "Mitspielender Torwart", "duty": "Verteidigend",
        "x": 50, "y": 91, "gruppen": ["tw"],
        "aufgaben": [
            "Jeden Angriff flach eröffnen, auch unter Druck",
            "Den Raum hinter der sehr hohen Kette absichern – als konservativste "
            "Sweeper-Variante situativ, nicht proaktiv",
            "Letzter Mann, wenn das hohe Pressing überspielt wird",
        ],
        "gewichte": {"gp_p90": 0.50, "pass_pct": 0.30, "rating": 0.20},
        "blind": [
            "Ob er wirklich ausrollt oder lang schlägt – Passrichtung und "
            "Passlänge fehlen in den Daten komplett.",
            "Paraden, Herauslaufen und Strafraumbeherrschung gibt es nicht als "
            "eigene Felder.",
        ],
    },
    {
        "key": "lv", "label": "Linksverteidiger", "kurz": "LV",
        "rolle": "Außenverteidiger", "duty": "Angreifend",
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
        "rolle": "Ballspielender Verteidiger", "duty": "Verteidigend",
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
        "rolle": "Ballspielender Verteidiger", "duty": "Verteidigend",
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
        "rolle": "Außenverteidiger", "duty": "Angreifend",
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
        "rolle": "Defensives Mittelfeld", "duty": "Unterstützend",
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
        "rolle": "Defensives Mittelfeld", "duty": "Unterstützend",
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
        "rolle": "Invertierter Außenstürmer", "duty": "Unterstützend",
        "x": 18, "y": 33, "gruppen": ["oml", "ml"],
        "aufgaben": [
            "Auf den starken Fuß nach innen ziehen und SELBST abschließen – das "
            "ist der Kern der Rolle, nicht bloß Vorbereiten",
            "Entgegenkommen und zwischen den Linien kombinieren (Unterstützend)",
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
        "rolle": "Offensives Mittelfeld", "duty": "Angreifend",
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
        "rolle": "Invertierter Außenstürmer", "duty": "Unterstützend",
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
        "rolle": "Sturmspitze", "duty": "Angreifend",
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
            "Das Anlaufen: die Sturmspitze hat keinen eingebauten Pressing"
            "auftrag – wenn das wichtig ist, wäre der Pressingstürmer die Rolle.",
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
        vals = sorted(float(r[key]) for r in pool if r.get(key) is not None)
        if len(vals) >= 8:
            dists[key] = vals
    return dists, len(pool)


def score_slot(p, slot, dists):
    """Positionsscore eines Spielers samt Aufschluesselung, oder None, wenn
    die Datengrundlage zu duenn ist.

    Bewusst OHNE Positionspruefung: dieselbe Rechnung bewertet auch einen
    positionsfremden Ersatzkandidaten, dessen Abzug erst danach dazukommt.
    """
    total, wsum, teile = 0.0, 0.0, []
    for key, w in slot["gewichte"].items():
        v = p.get(key)
        label, richtung, fmt = METRICS[key]
        if v is None or key not in dists:
            teile.append({"stat": key, "label": label, "wert": v,
                          "pct": None, "gewicht": w, "format": fmt})
            continue
        pc = _pct(dists[key], float(v))
        if richtung < 0:
            pc = 100.0 - pc
        total += w * pc
        wsum += w
        teile.append({"stat": key, "label": label, "wert": v,
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
    return {"score": round(roh), "verlaesslich": round(verl, 2),
            "teile": sorted(teile, key=lambda t: -t["gewicht"])}


def build_board(squad, reference, min_minutes=MIN_MINUTES):
    """Fuer jede Position die passenden Spieler mit Score und Aufschluesselung.

    squad     : Spieler, die bewertet werden (der eigene Kader)
    reference : Vergleichsmenge fuer die Perzentile (Pool + Kohorte)
    """
    out = []
    for slot in FORMATION:
        dists, basis = slot_dists(slot, reference, min_minutes)
        kandidaten = []
        for p in squad:
            ok, quelle = eligible(p, slot)
            if not ok:
                continue
            b = score_slot(p, slot, dists)
            if b is None:
                continue
            kandidaten.append({
                "id": p.get("id"), "name": p.get("name"),
                "age": p.get("age"), "minutes": p.get("minutes"),
                "rating": p.get("rating"), "pos_quelle": quelle,
                "stat_quelle": p.get("stat_quelle")} | b)
        kandidaten.sort(key=lambda k: -k["score"])
        out.append({k: slot[k] for k in
                    ("key", "label", "kurz", "rolle", "duty", "x", "y",
                     "aufgaben", "blind")} | {
            "kandidaten": kandidaten, "vergleichsbasis": basis})
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
    "aehnlich": "Ähnliches Profil, gleiches Niveau",
    "besser":   "Stärker auf dieser Position",
    "juenger":  "Gleiches Niveau, aber jünger",
}
TOL_GLEICH = 8             # +/- Punkte, die noch als "gleiches Niveau" gelten
MIN_BESSER = 3             # so viel muss "besser" mindestens besser sein
JUENGER_UM = 2             # Jahre, die "juenger" mindestens juenger ist
JUENGER_TOLERANZ = 5       # so weit darf er dabei im Score abfallen


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
                      max_umschulung=MAX_UMSCHULUNG, kader_ids=()):
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
    """
    dists, basis = slot_dists(slot, reference, min_minutes)
    ob = score_slot(original, slot, dists)
    if ob is None:
        return {"ok": False, "error": "Für diesen Spieler reichen die Daten "
                                      "auf dieser Position nicht aus."}
    ziel, alter = ob["score"], original.get("age")
    if modus == "juenger" and alter is None:
        return {"ok": False, "error": "Von diesem Spieler ist kein Geburtsdatum "
                                      "bekannt – ohne Alter keine Suche nach "
                                      "jüngeren Alternativen."}
    kader_ids = set(kader_ids or ())
    eigen = original.get("id")

    treffer, geprueft = [], 0
    for p in kandidaten:
        pid = p.get("id")
        if pid is None or pid == eigen or not p.get("name"):
            continue
        if (p.get("minutes") or 0) < min_minutes:
            continue
        gruppen, quelle = player_groups(p)
        kosten, von = umschulung(gruppen, slot["gruppen"])
        if kosten is None or kosten > max_umschulung:
            continue
        geprueft += 1
        b = score_slot(p, slot, dists)
        if b is None:
            continue
        score = max(0, min(100, round(b["score"] - kosten)))
        a = p.get("age")
        if modus == "besser":
            if score < ziel + MIN_BESSER:
                continue
        elif modus == "juenger":
            if a is None or a > alter - JUENGER_UM or score < ziel - JUENGER_TOLERANZ:
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
            "score": score, "score_pos": b["score"], "abzug": round(kosten),
            "umschulung": None if not kosten else GROUP_LABEL.get(von, von),
            "aehnlichkeit": aehn,
            "delta_score": score - ziel,
            "delta_age": None if (a is None or alter is None) else round(a - alter, 1),
            "verlaesslich": b["verlaesslich"], "teile": b["teile"],
        })

    if modus == "aehnlich":
        treffer.sort(key=lambda t: (-t["aehnlichkeit"], -t["score"]))
    elif modus == "juenger":
        treffer.sort(key=lambda t: (-t["score"], t["age"]))
    else:
        treffer.sort(key=lambda t: -t["score"])

    return {"ok": True, "modus": modus, "modus_label": MODI.get(modus, modus),
            "slot": {k: slot[k] for k in ("key", "label", "rolle", "duty")},
            "original": {"id": eigen, "name": original.get("name"),
                         "age": alter, "minutes": original.get("minutes"),
                         "score": ziel, "teile": ob["teile"]},
            "treffer": treffer[:limit], "geprueft": geprueft,
            "gefunden": len(treffer), "vergleichsbasis": basis,
            "min_minutes": min_minutes}
