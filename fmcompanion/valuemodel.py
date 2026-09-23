"""Fair-Value-Modell: schaetzt den Marktwert aus der Leistung und macht die
Abweichung sichtbar.

Der FM-Marktwert folgt dem Ruf – Nationalelf, Verein, Schlagzeilen. Eine
lineare Regression auf log(Marktwert) mit Leistung, Alter, Liga und Position
als Erklaerung mittelt genau diesen Ruf-Anteil weg. Interessant ist deshalb
nicht die Vorhersage selbst, sondern das Residuum: Wer laut Statistik teurer
sein muesste, als der Markt aufruft, ist unterbewertet.

Gegenueber dem bestehenden `value_score` (Offensivproduktion je Mio) ist das
der ehrlichere Massstab – er kontrolliert Alter, Liga und Position und liefert
damit auch fuer Verteidiger und Torhueter sinnvolle Werte.

WICHTIG – das Modell haelt sich selbst zurueck: Es liefert nur dann Zahlen,
wenn der Fit kreuzvalidiert echtes Signal zeigt (MIN_CV_R2). Bleibt er darunter,
gibt score() ein leeres Dict zurueck und die Oberflaeche zeigt "–". Der Grund
ist nicht Vorsicht, sondern Arithmetik: Ohne Signal sagt die Regression jedem
Spieler den Mittelwert voraus, und das Residuum ist dann nur noch der
umgedrehte Marktwert – "billig = unterbewertet". Genau diese Aussage waere als
Modellergebnis getarnter Unsinn und schlechter als gar keine Spalte.

Bewusst nur numpy: kein sklearn, kein pandas.
"""
import math

import numpy as np

from .moneyball import (SHRINK_MIN, league_coeff, pos_info,
                        pos_mask_from_string)

# Wunsch-Mindestminuten fuers Training. Wird die Zeilenzahl dabei zu duenn,
# steigt der Fit die Leiter hinab – mitten in der Saison hat schlicht noch
# niemand 450 Minuten. Spaeter im Jahr greift automatisch wieder die oberste
# Stufe. Siehe _pick_threshold().
MIN_MINUTES = 450
MIN_MINUTES_LADDER = (450, 360, 270, 180, 90)
# Untergrenze an Zeilen je Koeffizient. Darunter beschreibt die Regression nur
# noch das Rauschen der Stichprobe (Residuen gegen null = kein Signal).
ROWS_PER_FEATURE = 6
# Mindest-Guete, ab der das Modell ueberhaupt Zahlen herausgibt. Kreuzvalidiert,
# nicht im Training gemessen: Das Trainings-R² steigt mit jedem Feature, auch
# mit sinnlosen, und ist bei 20 Koeffizienten auf gut 100 Zeilen wertlos.
# Der Plan erwartete 0,4–0,8; 0,20 ist die grosszuegige Untergrenze, unterhalb
# derer die Vorhersage nachweislich schlechter ist als "nimm den Mittelwert".
MIN_CV_R2 = 0.20
# Je Positionsgruppe noetige Trainingszeilen, damit deren Fair Values als
# verlaesslich gelten. Ein Torhueter, dessen Gruppe im Training nie vorkam,
# bekommt sonst eine Zahl, die aus Mittelfeldspielern hochgerechnet ist.
MIN_GROUP_ROWS = 8
# Ridge-Staerken, unter denen die Kreuzvalidierung auswaehlt. Ridge statt
# blankem OLS, weil die Trainingsmenge klein und die Features korreliert sind
# (xG und Tore, Pressing und Zweikaempfe).
ALPHAS = (0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0)
CV_FOLDS = 5

# Rohfelder, ohne die eine Zeile nicht ins Training darf. Fehlen sie, stammt
# der Eintrag aus einem Export ohne Detailspalten – dann waeren die Features
# geraten statt gemessen.
DETAIL_COLS = ("prog_passes", "press_win", "interceptions", "key_passes",
               "clearances", "pass_try", "pass_ok", "duels", "duels_total")

# Positionsgruppen als Dummy-Variablen. "unk" ist bewusst die Basiskategorie
# und bekommt keine eigene Spalte – sonst waeren die Dummies linear abhaengig.
POS_DUMMIES = ("tw", "def", "mid", "off", "st")

FEATURE_NAMES = (
    "tore_p90", "vorlagen_p90", "xg_p90", "xa_p90", "prog_p90", "press_p90",
    "int_p90", "keyp_p90", "clear_p90",
    "pass_pct", "duel_pct", "rating",
    "alter", "alter2", "liga_koeff",
) + tuple("pos_" + g for g in POS_DUMMIES)


def _p90(value, minutes):
    return (value or 0) * 90.0 / minutes if minutes else 0.0


def _group(row):
    """Positionsgruppe eines Export-Spielers aus seinem Positionsstring."""
    pos = (row.get("position") or "").strip()
    mask = pos_mask_from_string(pos)
    groups, _ = pos_info(mask, pos.startswith("TW"))
    return groups[0]


def _features(row):
    """Feature-Zeile eines Spielers; nicht messbare Werte als NaN.

    Die /90-Raten werden mit m/(m+180) geschrumpft – dieselbe Idee wie in der
    Score-Engine: Wer 200 Minuten gespielt hat, darf mit einer Zufallsserie
    nicht dieselbe Aussagekraft bekommen wie ein Stammspieler.
    """
    m = row.get("minutes") or 0
    sh = m / (m + SHRINK_MIN) if m else 0.0

    def rate(col):
        v = row.get(col)
        return math.nan if v is None else _p90(v, m) * sh

    def quote(ok_col, try_col):
        t = row.get(try_col)
        o = row.get(ok_col)
        if t is None or o is None:
            return math.nan
        return 100.0 * o / t if t else 0.0

    age = row.get("age")
    age = float(age) if age is not None else math.nan
    grp = _group(row)
    return [
        rate("goals"), rate("assists"), rate("xg"), rate("xa"),
        rate("prog_passes"), rate("press_win"), rate("interceptions"),
        rate("key_passes"), rate("clearances"),
        quote("pass_ok", "pass_try"), quote("duels", "duels_total"),
        float(row.get("rating") or 0.0),
        age, age * age, league_coeff(row.get("league")),
    ] + [1.0 if grp == g else 0.0 for g in POS_DUMMIES]


def _trainable(row):
    """Taugt die Zeile als Trainingsbeispiel? (Wert, Alter, Detailstatistik)"""
    return ((row.get("value") or 0) > 0
            and row.get("age") is not None
            and (row.get("rating") or 0) > 0
            and all(row.get(c) is not None for c in DETAIL_COLS))


def _pick_threshold(rows):
    """Hoechste Minutenschwelle, bei der noch genug Zeilen uebrig bleiben.

    Der Plan sah feste 450 Minuten vor; in der laufenden Saison erfuellen die
    aber nur eine Handvoll Spieler. Statt an einer leeren Trainingsmenge zu
    scheitern, sucht der Fit die strengste Schwelle, die das Verhaeltnis von
    Zeilen zu Koeffizienten noch traegt.
    """
    need = len(FEATURE_NAMES) * ROWS_PER_FEATURE
    for t in MIN_MINUTES_LADDER:
        n = sum(1 for r in rows if _trainable(r) and (r.get("minutes") or 0) >= t)
        if n >= need:
            return t, n
    # Nichts reicht: die lockerste Stufe zurueckgeben, fit() entscheidet dann,
    # ob die Menge ueberhaupt tragfaehig ist.
    t = MIN_MINUTES_LADDER[-1]
    n = sum(1 for r in rows if _trainable(r) and (r.get("minutes") or 0) >= t)
    return t, n


def _ridge(Z, y, alpha):
    """Ridge-Loesung ueber die erweiterte Matrix.

    Statt (Z'Z + aI)^-1 Z'y wird lstsq auf das um sqrt(a)*I ergaenzte System
    angewandt – numerisch stabiler und weiterhin nur np.linalg.lstsq.
    """
    k = Z.shape[1]
    Za = np.vstack([Z, math.sqrt(alpha) * np.eye(k)])
    ya = np.concatenate([y, np.zeros(k)])
    beta, *_ = np.linalg.lstsq(Za, ya, rcond=None)
    return beta


def _fit_core(X, y, alpha):
    """Standardisieren, zentrieren, Ridge fitten -> (mittel, streuung, beta, y0).

    Ohne z-Standardisierung dominiert Alter² allein durch seine Groessenordnung
    (~600 gegen ~0,5 bei den /90-Raten) saemtliche Koeffizienten.
    """
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-9] = 1.0                 # konstante Spalte neutralisieren
    y0 = float(y.mean())
    beta = _ridge((X - mu) / sd, y - y0, alpha)
    return mu, sd, beta, y0


def _predict_core(core, X):
    mu, sd, beta, y0 = core
    return ((X - mu) / sd) @ beta + y0


def _r2(y, pred):
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return 1.0 - ss_res / ss_tot if ss_tot else 0.0


def _cv_r2(X, y, alpha, folds=CV_FOLDS):
    """Kreuzvalidiertes R². Der Wert im Training selbst ist bei kleiner
    Stichprobe wertlos – er steigt mit jedem Feature, auch mit sinnlosen."""
    n, k = X.shape
    if n < folds * 2:
        return float("nan")
    idx = np.arange(n)
    pred = np.empty(n, dtype=float)
    for f in range(folds):
        te = (idx % folds) == f
        tr = ~te
        if tr.sum() <= k or not te.any():
            return float("nan")
        pred[te] = _predict_core(_fit_core(X[tr], y[tr], alpha), X[te])
    return _r2(y, pred)


class FairValueModel:
    """Gefittetes Modell samt Guetemassen. `usable` entscheidet, ob die Zahlen
    ueberhaupt nach draussen gehen."""

    def __init__(self, core, imput, threshold, n_train, alpha, r2, cv_r2,
                 group_rows):
        self._core = core
        self._imput = imput             # Spaltenmittel fuer fehlende Features
        self.threshold = threshold      # tatsaechlich benutzte Minutenschwelle
        self.n_train = n_train
        self.alpha = alpha
        self.r2 = r2
        self.cv_r2 = cv_r2
        self.group_rows = group_rows    # Trainingszeilen je Positionsgruppe

    @property
    def usable(self):
        return not math.isnan(self.cv_r2) and self.cv_r2 >= MIN_CV_R2

    @property
    def reason(self):
        """Klartext, warum das Modell (nicht) liefert – fuer Log und Anzeige."""
        if self.usable:
            return (f"Fit traegt: R² kreuzvalidiert {self.cv_r2:.2f} aus "
                    f"{self.n_train} Spielern ab {self.threshold} Minuten.")
        if math.isnan(self.cv_r2):
            return ("Kreuzvalidierung nicht moeglich – zu wenige Spieler mit "
                    "Marktwert, Minuten und Detailstatistik.")
        return (f"Kein belastbarer Zusammenhang zwischen Leistung und Marktwert: "
                f"R² kreuzvalidiert {self.cv_r2:.2f}, noetig waeren "
                f"{MIN_CV_R2:.2f}. Mehr gespielte Minuten oder ein breiterer "
                f"Export loesen das von selbst.")

    def predict_log(self, rows):
        X = np.array([_features(r) for r in rows], dtype=float)
        X = np.where(np.isnan(X), self._imput, X)
        return _predict_core(self._core, X)

    def coefficients(self):
        """Koeffizienten auf standardisierter Skala, nach Betrag sortiert."""
        beta = self._core[2]
        return sorted(zip(FEATURE_NAMES, (float(b) for b in beta)),
                      key=lambda kv: -abs(kv[1]))

    def status(self):
        """Kompakter Zustand fuers Frontend / die Diagnose."""
        return {"ok": self.usable, "reason": self.reason,
                "n_train": self.n_train, "threshold": self.threshold,
                "r2": round(self.r2, 3),
                "cv_r2": None if math.isnan(self.cv_r2) else round(self.cv_r2, 3)}


def fit(rows):
    """Fittet das Fair-Value-Modell auf den Export-Spielern.

    Gibt None zurueck, wenn nicht einmal ein Fit moeglich ist. Ein Modell, das
    zwar rechnet, aber kreuzvalidiert kein Signal zeigt, kommt MIT usable=False
    zurueck – damit der Aufrufer den Grund anzeigen kann, statt nur zu schweigen.
    """
    threshold, _ = _pick_threshold(rows)
    train = [r for r in rows
             if _trainable(r) and (r.get("minutes") or 0) >= threshold]
    k = len(FEATURE_NAMES)
    if len(train) < k + 2:
        return None

    X = np.array([_features(r) for r in train], dtype=float)
    y = np.array([math.log(r["value"]) for r in train], dtype=float)
    # Fehlwerte im Training sind durch _trainable() praktisch ausgeschlossen;
    # der Rest faellt auf das Spaltenmittel zurueck.
    imput = np.nanmean(X, axis=0)
    imput = np.where(np.isnan(imput), 0.0, imput)
    X = np.where(np.isnan(X), imput, X)

    best_alpha, best_cv = ALPHAS[0], -math.inf
    for a in ALPHAS:
        cv = _cv_r2(X, y, a)
        if not math.isnan(cv) and cv > best_cv:
            best_alpha, best_cv = a, cv
    core = _fit_core(X, y, best_alpha)
    r2 = _r2(y, _predict_core(core, X))
    groups = {}
    for r in train:
        g = _group(r)
        groups[g] = groups.get(g, 0) + 1
    return FairValueModel(core, imput, threshold, len(train), best_alpha, r2,
                          best_cv if best_cv > -math.inf else float("nan"),
                          groups)


def score(model, rows):
    """Fair Values fuer ALLE Export-Spieler -> {eid: felder}.

    Leeres Dict, solange das Modell nicht traegt (siehe FairValueModel.usable) –
    lieber keine Spalte als eine, die nur den Preis umdreht.

    Spieler unter der Trainingsschwelle bekommen einen Wert, aber mit
    `value_reliable = False` gekennzeichnet; die Oberflaeche zeigt ihn dann
    gedaempft und ohne Einfaerbung. Dasselbe gilt fuer Positionsgruppen, die im
    Training kaum vertreten waren.
    """
    out = {}
    if model is None or not model.usable:
        return out
    usable = [r for r in rows if r.get("eid") and (r.get("value") or 0) > 0
              and r.get("age") is not None]
    if not usable:
        return out
    preds = model.predict_log(usable)
    for r, lp in zip(usable, preds):
        fair = math.exp(float(lp))
        val = float(r["value"])
        reliable = ((r.get("minutes") or 0) >= model.threshold
                    and all(r.get(c) is not None for c in DETAIL_COLS)
                    and (r.get("rating") or 0) > 0
                    and model.group_rows.get(_group(r), 0) >= MIN_GROUP_ROWS)
        out[int(r["eid"])] = {
            "fair_value_m": round(fair / 1e6, 1),
            # positiv = unterbewertet (Statistik erwartet mehr als der Markt)
            "value_delta_pct": round((fair / val - 1.0) * 100.0),
            "value_reliable": bool(reliable),
        }
    return out


def fair_values(rows):
    """Bequemer Einstieg: fitten und scoren in einem Schritt.

    -> ({eid: felder}, model). `model` traegt Schwelle, Guete und Begruendung;
    bei zu duenner Datenlage ist es None und das Dict leer.
    """
    model = fit(rows)
    return score(model, rows), model


# ------------------------------------------------------------- Akzeptanztest
# Laeuft ohne FM24: liest die Export-Tabelle NUR LESEND (mode=ro) und zeigt,
# ob das Modell plausibel sitzt.
if __name__ == "__main__":
    import os
    import sqlite3

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "fmcompanion.db").replace("\\", "/")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in
                conn.execute("SELECT * FROM export_players").fetchall()]
    finally:
        conn.close()

    print(f"Export-Spieler: {len(rows)}")
    vals, model = fair_values(rows)
    if model is None:
        print("Kein Fit moeglich – zu wenige Spieler mit Marktwert, Minuten "
              "und Detailstatistik.")
        raise SystemExit(1)

    print(f"Trainingsmenge: {model.n_train} Zeilen ab {model.threshold} Minuten "
          f"| {len(FEATURE_NAMES)} Features | Ridge-Alpha {model.alpha}")
    print(f"R² im Training   : {model.r2:6.3f}")
    print(f"R² kreuzvalidiert: {model.cv_r2:6.3f}   <- der ehrliche Wert "
          f"(Schwelle {MIN_CV_R2:.2f})")
    print("Trainingszeilen je Positionsgruppe: "
          + ", ".join(f"{g}={n}" for g, n in sorted(model.group_rows.items(),
                                                    key=lambda kv: -kv[1])))
    print(f"\nBefund: {model.reason}")
    print(f"Ausgegebene Fair Values: {len(vals)}")

    print("\nStaerkste Koeffizienten (standardisiert):")
    for name, b in model.coefficients()[:8]:
        print(f"  {name:14s} {b:+7.3f}")

    if not vals:
        print("\nKeine Rangliste: Das Modell haelt sich zurueck. Ohne Signal "
              "waere das Residuum nur der umgedrehte Marktwert – jeder billige "
              "Spieler saehe wie ein Schnaeppchen aus.")
        raise SystemExit(0)

    by_eid = {int(r["eid"]): r for r in rows if r.get("eid")}
    ranked = [(v["value_delta_pct"], by_eid[e], v) for e, v in vals.items()
              if v["value_reliable"]]
    ranked.sort(key=lambda t: -t[0])

    def table(title, items):
        print(f"\n{title}")
        print(f"  {'Spieler':22s} {'Alt':>3s} {'Min':>4s} {'Wert':>7s} "
              f"{'Fair':>7s} {'Delta':>7s}  Position / Liga")
        for d, r, v in items:
            name = (r.get("name") or "?")[:22]
            print(f"  {name:22s} {int(r['age']):3d} "
                  f"{int(r.get('minutes') or 0):4d} "
                  f"{r['value']/1e6:6.1f}M {v['fair_value_m']:6.1f}M "
                  f"{d:+6d}%  {(r.get('position') or '?'):16s} "
                  f"{(r.get('league') or '?')[:24]}")

    table("Top 10 unterbewertet (Statistik erwartet mehr als der Markt):",
          ranked[:10])
    table("Top 10 ueberbewertet (Markt zahlt mehr, als die Leistung hergibt):",
          ranked[-10:][::-1])
