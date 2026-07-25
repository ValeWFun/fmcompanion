# FM Companion

Moneyball-Spielerauswertung für **Football Manager 2024**. Liest die
Saison-Statistiken der Spieler aus dem Arbeitsspeicher des laufenden Spiels,
speichert Snapshots in SQLite und bewertet Spieler nach tatsächlicher Leistung
statt nach Ruf.

**Rein lesend.** Es wird nichts in das Spiel geschrieben und nichts verändert.
Ausgewertet werden ausschließlich Werte, die im Spiel selbst sichtbar sind –
Leistungsdaten, Positionen, Geburtsdaten. Verdeckte Attribute (aktuelle und
potenzielle Fähigkeit, Charakterwerte) werden bewusst **nicht** gelesen: das
Werkzeug soll beim Bewerten von Leistung helfen, nicht das Spiel aushebeln.

## Nutzung

```
python -m pip install -r requirements.txt
python app.py          # startet das Dashboard (FM24 muss laufen)
```

Der Auto-Scan läuft im Hintergrund; „Kader scannen" erzwingt einen Durchlauf.
Über „Import" lassen sich FM-HTML-Exporte (Strg+P) einlesen – nur daraus
kommen Marktwert, Verein und Liga, die im Speicher nicht als Wert vorliegen.

## Was es auswertet

- **Score** je Positionsprofil: gewichtete Perzentile aus Toren, Vorlagen,
  Pressing, progressiven Pässen, Zweikämpfen und Passquote, mit Shrinkage für
  kleine Stichproben und Liga-Koeffizienten.
- **Buy-Low / Sell-High**: Abweichung der Tore von den erwarteten Toren (xG)
  – wer viele Chancen erarbeitet, aber unter Erwartung trifft, ist meist zu
  billig zu haben.
- **Talent**: Perzentil im eigenen Altersband auf der eigenen Position. Ein
  19-Jähriger im 90. Perzentil seines Jahrgangs ist etwas anderes als ein
  28-Jähriger mit demselben Wert.
- **Torhüter**: verhinderte Tore (erwartete minus tatsächliche Gegentore).
- Saison-Historie und Entwicklung je Spieler, Merkliste, Dossier-Karten.

## Struktur

- `fmcompanion/memlib.py` – lesender Speicherzugriff auf `fm.exe`
- `fmcompanion/scanner.py` – Statistik-Records, Namensindex, Geburtsdaten,
  automatische Erkennung der laufenden Saison
- `fmcompanion/moneyball.py` – Kennzahlen, Positionsprofile, Score-Engine
- `fmcompanion/db.py` – SQLite-Snapshots, Namensgedächtnis, Vergleichskohorte
- `fmcompanion/importer.py` – Parser für FM-HTML-Exporte
- `app.py` + `ui/` – PyWebView-Desktop-App

## Grenzen

- Ein Spieler taucht erst auf, wenn FM seinen Namen einmal geladen hat (Kader,
  Scouting-Liste, geöffnetes Profil). Einmal gesehene Namen werden dauerhaft
  gemerkt, die Statistiken aktualisieren sich danach von selbst.
- Marktwert, Verein und Liga gibt es nur über den HTML-Export.
- Das Alter wird aus dem Geburtsjahr gegen ein Bezugsjahr gerechnet, das sich
  an den Export-Altern kalibriert. Ohne neuen Export läuft es über mehrere
  Saisons um ein Jahr pro Saison auseinander.
- Die Feldkarten sind an FM24 verifiziert; ein Spiel-Update kann sie brechen.

Privates Projekt für das eigene Savegame, ohne Verbindung zu Sports
Interactive oder SEGA.
