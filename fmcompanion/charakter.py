"""Persoenlichkeit aus dem FM-Export: was sie ueber einen Spieler sagt.

Ausgewertet wird NUR, was im Spiel selbst auf dem Spielerprofil steht – die
Persoenlichkeits-Beschreibung ("Perfektionist", "Ausgewogen") und der
Medienumgang ("Besonnen", "Forsch"). Die verdeckten Charakterwerte dahinter
(Professionalitaet, Entschlossenheit, Ehrgeiz, Druckresistenz, Temperament,
Loyalitaet) werden weder aus dem Speicher gelesen noch geschaetzt; die Tabelle
unten uebersetzt lediglich die sichtbare Beschreibung in zwei grobe Achsen.
Das ist derselbe Wissensstand, den jeder Trainer im Spiel hat.

Warum zwei Achsen und nicht eine Zahl:

 - Entwicklung (0-100): wie stark die Beschreibung auf Professionalitaet und
   Entschlossenheit hindeutet. In FM entscheiden genau diese beiden ueber
   Trainingsfortschritt und darueber, ob ein 20-Jaehriger sein Potenzial
   erreicht. Fuer junge Zugaenge die wichtigere Achse.
 - Mentalitaet (0-100): Entschlossenheit, Druckresistenz, Temperament – ob
   ein Spieler im grossen Spiel und nach Rueckschlaegen liefert und ob er die
   Kabine ruhig laesst. Fuer fertige Spieler die wichtigere Achse.

Die Gesamtzahl 'pers_score' mischt beide nach Alter: mit 20 zaehlt die
Entwicklung 60 %, mit 30 die Mentalitaet 70 %.

Was die Zahlen NICHT sind: Der Charakter ist kein Bestandteil des Moneyball-
Scores. Eine Pruefung an 505 Spielern mit bekannter Persoenlichkeit (Sommer,
zweite Saison) zeigte innerhalb EINER Saison keinen messbaren Zusammenhang
zwischen Beschreibung und Ø-Note (Gruppenmittel 6,96–7,12, alles innerhalb
des Rauschens). Der Wert der Persoenlichkeit liegt in der Entwicklung, in der
Konstanz ueber Jahre und im Kabinenrisiko – das misst keine Saisonstatistik.
Deshalb steht sie NEBEN dem Score, nicht darin, und wiegt in Scouting-
Berichten und Ersatzlisten als eigene Spalte.

Die deutschen Beschriftungen sind die von FM24 (aus den Exporten gelesen);
Beschreibungen, die dort noch nicht vorkamen, sind nach der englischen
Fassung uebersetzt und werden zusaetzlich ueber Schluesselwoerter erkannt,
damit eine unbekannte Variante nicht still als 'unbekannt' durchfaellt.
"""

# Beschreibung -> (Entwicklung, Mentalitaet, Hinweis oder None)
# Die Werte bilden die Reihenfolge ab, in der FM die Beschreibungen vergibt:
# Modellbuerger ueber Musterprofi ueber Professionell ueber "relativ ...".
PERSOENLICHKEITEN = {
    # Professionalitaet: der Treiber fuer Training und Entwicklung
    "Modellbürger": (100, 100, None),
    "Vorbildlicher Profi": (100, 90, None),
    "Musterprofi": (100, 90, None),
    "Professionell": (90, 75, None),
    "Perfektionist": (90, 60, "hohe Ansprüche, niedriges Temperament – "
                              "reagiert auf Rückschläge gereizt"),
    "Relativ professionell": (75, 65, None),
    # Entschlossenheit / Wille
    "Eiserner Wille": (80, 95, None),
    "Antriebig": (80, 85, None),
    "Konsequent": (75, 80, None),
    "Zielstrebig": (70, 80, None),
    "Relativ zielstrebig": (60, 65, None),
    "Energisch": (65, 70, None),
    "Belastbar": (55, 80, "hält Druck stand"),
    # Fuehrung
    "Charismatischer Anführer": (80, 95, "Kapitänsmaterial"),
    "Geborener Anführer": (75, 95, "Kapitänsmaterial"),
    "Führungspersönlichkeit": (65, 85, "Kapitänsmaterial"),
    "Anführer": (65, 85, "Kapitänsmaterial"),
    # Ehrgeiz: gut fuer die Entwicklung, aber wechselwillig – bei einem
    # Verein von der Groesse Man Utd kein Fluchtrisiko, wohl aber Anspruch
    # auf Spielzeit
    "Sehr ehrgeizig": (65, 60, "will spielen und will nach oben – "
                               "auf der Bank wird er unruhig"),
    "Ehrgeizig": (60, 60, None),
    "Relativ ehrgeizig": (55, 55, None),
    # Loyalitaet
    "Hingebungsvoll": (60, 70, "bleibt auch in schlechten Phasen"),
    "Sehr loyal": (55, 65, None),
    "Loyal": (52, 60, None),
    "Relativ loyal": (50, 55, None),
    # Neutral
    "Ausgewogen": (50, 50, None),
    "Fair": (50, 55, None),
    "Relativ fair": (50, 55, None),
    "Ehrlich": (50, 55, None),
    "Realist": (45, 50, None),
    "Unbeschwert": (45, 55, "nimmt es leicht – auch das Training"),
    "Frohnatur": (45, 55, None),
    # Temperament und Charakterrisiken
    "Launisch": (40, 30, "Temperament: Leistung schwankt mit der Laune"),
    "Temperamentvoll": (40, 35, "Temperament: Karten- und Kabinenrisiko"),
    "Aufbrausend": (35, 25, "Temperament: Karten- und Kabinenrisiko"),
    "Unbeständig": (35, 25, "Temperament: Leistung schwankt"),
    "Wankelmütig": (40, 40, "wenig loyal, wechselwillig"),
    "Unfair": (45, 40, "Kartenrisiko"),
    # Fehlender Antrieb – die eigentlichen Warnsignale
    "Ohne Ehrgeiz": (30, 40, "kein Antrieb, sich zu verbessern"),
    "Leicht entmutigt": (30, 25, "bricht nach Rückschlägen ein"),
    "Wenig zielstrebig": (30, 30, "wenig Entschlossenheit"),
    "Lässig": (25, 45, "wenig professionell – Training leidet"),
    "Nachlässig": (10, 40, "unprofessionell – entwickelt sich kaum"),
    "Rückgratlos": (35, 15, "knickt unter Druck ein"),
    "Wenig Selbstvertrauen": (40, 25, "knickt unter Druck ein"),
}

# Schluesselwort -> Eintrag, falls die Beschreibung nicht woertlich bekannt ist
# (andere Sprachdatei, neue Variante). Reihenfolge = Prioritaet.
_SCHLUESSEL = [
    ("modellb", "Modellbürger"), ("musterprofi", "Musterprofi"),
    ("vorbild", "Vorbildlicher Profi"), ("perfektion", "Perfektionist"),
    ("relativ professionell", "Relativ professionell"),
    ("professionell", "Professionell"), ("eisern", "Eiserner Wille"),
    ("antrieb", "Antriebig"), ("konsequent", "Konsequent"),
    ("relativ zielstrebig", "Relativ zielstrebig"),
    ("wenig zielstrebig", "Wenig zielstrebig"), ("zielstrebig", "Zielstrebig"),
    ("energisch", "Energisch"), ("belastbar", "Belastbar"),
    ("charismat", "Charismatischer Anführer"), ("geboren", "Geborener Anführer"),
    ("führung", "Führungspersönlichkeit"), ("anführer", "Anführer"),
    ("sehr ehrgeizig", "Sehr ehrgeizig"), ("relativ ehrgeizig", "Relativ ehrgeizig"),
    ("ohne ehrgeiz", "Ohne Ehrgeiz"), ("ehrgeiz", "Ehrgeizig"),
    ("hingebung", "Hingebungsvoll"), ("sehr loyal", "Sehr loyal"),
    ("relativ loyal", "Relativ loyal"), ("loyal", "Loyal"),
    ("ausgewogen", "Ausgewogen"), ("relativ fair", "Relativ fair"),
    ("unfair", "Unfair"), ("fair", "Fair"), ("ehrlich", "Ehrlich"),
    ("realist", "Realist"), ("unbeschwert", "Unbeschwert"),
    ("frohnatur", "Frohnatur"), ("launisch", "Launisch"),
    ("temperament", "Temperamentvoll"), ("aufbrausend", "Aufbrausend"),
    ("unbeständig", "Unbeständig"), ("wankelm", "Wankelmütig"),
    ("entmutigt", "Leicht entmutigt"), ("lässig", "Lässig"),
    ("nachlässig", "Nachlässig"), ("rückgrat", "Rückgratlos"),
    ("selbstvertrauen", "Wenig Selbstvertrauen"),
]

# Medienumgang -> (Abzug/Zuschlag Mentalitaet, Hinweis oder None).
# Er beschreibt Temperament und Kontroversitaet gegenueber der Presse – ein
# 'forscher' Spieler redet, wenn er unzufrieden ist, und zieht Karten:
# in den Sommer-Exporten fouls/90 1,36 gegen 1,02 im Schnitt.
MEDIEN = {
    "Besonnen": (+3, None),
    "Stoisch": (+3, None),
    "Medienfreundlich": (0, None),
    "Verschlossen": (0, None),
    "Scheu": (0, None),
    "Forsch": (-8, "redet offen mit der Presse – Unruheherd, wenn er unzufrieden ist"),
    "Unberechenbar": (-10, "unberechenbar gegenüber den Medien – Kabinenrisiko"),
    "Aufbrausend": (-10, "aufbrausend – Karten- und Kabinenrisiko"),
    "Kontrovers": (-10, "kontrovers – Kabinenrisiko"),
    "Kurz angebunden": (-5, "kurz angebunden – reagiert gereizt auf Kritik"),
}
UNBEKANNT = ("Scouting erforderlich", "Unbekannt", "", None)

# Stufen fuer die Anzeige. Bewusst Worte statt Metalle: es geht um ein
# Urteil, nicht um eine Belohnung.
STUFEN = [(80, "Vorbild"), (65, "stark"), (50, "solide"), (35, "schwach"),
          (0, "Risiko")]


def _eintrag(label):
    """Bekannter Eintrag zur Beschreibung, sonst Schluesselwortsuche, sonst None."""
    if not label or label in UNBEKANNT:
        return None, None
    t = label.strip()
    if t in PERSOENLICHKEITEN:
        return t, PERSOENLICHKEITEN[t]
    low = t.lower()
    for wort, ziel in _SCHLUESSEL:
        if wort in low:
            return ziel, PERSOENLICHKEITEN[ziel]
    return None, None


def _medien(text):
    """Medienumgang kann mehrere Eintraege tragen ('Scheu, Verschlossen')."""
    if not text or text in UNBEKANNT:
        return 0, [], False
    abzug, hinweise = 0, []
    for teil in str(text).split(","):
        t = teil.strip()
        a, h = MEDIEN.get(t, (0, None))
        abzug += a
        if h:
            hinweise.append(h)
    return abzug, hinweise, True


def stufe(score):
    if score is None:
        return None
    for grenze, name in STUFEN:
        if score >= grenze:
            return name
    return STUFEN[-1][1]


def bewerten(label, medien=None, alter=None):
    """Persoenlichkeit + Medienumgang (+ Alter) -> Felder fuers Spieler-Dict.

    Rueckgabe immer mit allen Schluesseln; ohne bekannte Beschreibung sind die
    Zahlen None und pers_label ist None – 'unbekannt' ist eine Aussage, keine
    Null. Der Medienumgang allein ergibt keinen Score (er ist zu duenn), er
    verschiebt nur die Mentalitaet, wenn es eine Beschreibung gibt.
    """
    name, eintrag = _eintrag(label)
    m_abzug, m_hinweise, m_bekannt = _medien(medien)
    out = {"pers_label": name, "pers_roh": (label or None) if label not in UNBEKANNT else None,
           "pers_medien": (medien or None) if medien not in UNBEKANNT else None,
           "pers_dev": None, "pers_ment": None, "pers_score": None,
           "pers_stufe": None, "pers_hinweise": list(m_hinweise)}
    if eintrag is None:
        return out
    dev, ment, hinweis = eintrag
    ment = max(0, min(100, ment + m_abzug))
    if hinweis:
        out["pers_hinweise"].insert(0, hinweis)
    # Altersmischung: jung = Entwicklung zaehlt, fertig = Mentalitaet zaehlt
    if alter is None or alter <= 23:
        w_dev = 0.6
    elif alter <= 27:
        w_dev = 0.45
    else:
        w_dev = 0.3
    score = round(w_dev * dev + (1 - w_dev) * ment)
    out.update(pers_dev=dev, pers_ment=ment, pers_score=score,
               pers_stufe=stufe(score))
    return out


def anreichern(p):
    """Persoenlichkeitsfelder an ein Spieler-Dict haengen (in place)."""
    p.update(bewerten(p.get("personality"), p.get("media"), p.get("age")))
    return p


# Alle Eintraege muessen eine Stufe erreichen koennen; Tippfehler in der
# Tabelle fielen sonst erst in der Oberflaeche auf.
for _k, (_d, _m, _h) in PERSOENLICHKEITEN.items():
    assert 0 <= _d <= 100 and 0 <= _m <= 100, f"Persönlichkeit {_k}: Werte außerhalb 0-100"
for _w, _z in _SCHLUESSEL:
    assert _z in PERSOENLICHKEITEN, f"Schlüsselwort {_w} zeigt auf unbekannten Eintrag {_z}"
