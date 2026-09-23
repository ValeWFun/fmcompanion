"""Liest Spieler + Saisonstatistiken direkt aus dem RAM von fm.exe.

Feldkarte relativ zum [xG,xA,xA,xG]-Blockanfang M – verifiziert an
Estevao Desembargador und Florentino Luis. Header-Felder liegen vor M.

Gefunden werden die Records STRUKTURELL (_find_records), nicht mehr ueber
xG-Werte: die Records liegen in Arrays mit Stride 168 und tragen ein festes
Feldmuster. Der frueher benutzte Float-Anker (xG > 0.005) uebersah jeden
Spieler mit exakt 0 xG UND 0 xA, der Torhueter-Anker zusaetzlich fast alle
Keeper (er verlangte ein "Header == 1223", das gar nicht konstant ist –
M-20 ist eine laufende Record-Nummer).
"""
import time
import numpy as np
from . import memlib

# Obergrenze fuer EINEN Wettbewerbs-Record. Mehr als eine volle Liga-Saison
# (38 Spiele a 90 Min) kann ein einzelner Wettbewerb nicht haben – was darueber
# liegt, ist eine abgeschlossene Saison und faellt raus. Frueher standen hier
# 400 Minuten, weil ein Record faelschlich fuer "die Saison" gehalten wurde.
CUR_MAX_MIN = 3600
MAX_PID = 0x0FFFFFFF
STRIDE = 168               # Groesse eines Stat-Records (gueltig M-24..M+142)


def _fields(data, M):
    u16 = lambda o: int.from_bytes(data[M + o:M + o + 2], "little")
    a, xa = np.frombuffer(data[M:M + 8], np.float32)
    xga = float(np.frombuffer(data[M + 16:M + 20], np.float32)[0])
    m12 = u16(-12)             # gepackt: Low-Byte = Tore, High-Byte = Gegentore
    m14 = u16(-14)             # gepackt: Low = Einsätze, High = bewertete Spiele
    rated = m14 >> 8
    return {
        "xg": round(float(a), 2), "xa": round(float(xa), 2),
        "goals": m12 & 0xFF, "conceded": m12 >> 8,
        "apps": m14 & 0xFF, "xga": round(xga, 2), "rated": rated,
        # Ø-Note = Notensumme(M-16)/10 durch BEWERTETE Spiele (Kurzeinsätze ohne
        # Note zählen nicht mit; per Kaderliste massenverifiziert, s. Memory).
        "rating": round(u16(-16) / 10 / rated, 2) if rated else 0.0,
        "pos_mask": u16(-8),
        "minutes": u16(28), "pass_try": u16(32), "pass_ok": u16(34),
        "duels_total": u16(36), "duels": u16(38), "dribbles": u16(40),
        "shots_total": u16(42), "shots_on": u16(44),
        "fouls": u16(46), "fouls_against": u16(48),
        "key_passes": u16(64), "interceptions": u16(70),
        "headers_total": u16(80), "headers_won": u16(82),
        "clearances": u16(98), "prog_passes": u16(102),
        "recoveries": u16(94), "losses": u16(96),
        "press_try": u16(106), "press_win": u16(108),
        "assists": data[M + 130], "yellow": data[M + 135],
    }


def _valid_name_part(s):
    return 2 <= len(s) <= 25 and all(c.isalpha() or c in "-' " for c in s)


def _build_name_index(regions, with_eid=False, hot_out=None):
    """{small_id: name}; mit with_eid=True zusaetzlich {small_id: EID}.
    EID = persistente ID (@vlen-9, verifiziert Colwill 28124573) – Bruecke zum
    FM-HTML-Export (Liga/Marktwert/Alter).

    hot_out (optional, set): sammelt die Basisadressen der Bloecke, in denen
    ueberhaupt Namen lagen. Gemessen sind das 46 von 1467 Bloecken = 5,8 % des
    Speichers; beschraenkt man den naechsten Lauf darauf, dauert der Namensindex
    0,7 s statt 9,8 s bei identischem Ergebnis."""
    idx = {}
    eids = {}
    for base, data in regions:
        n_before = len(idx)
        time.sleep(0)              # GIL kurz abgeben -> GUI bleibt responsiv
        b = np.frombuffer(data, np.uint8)
        L = b.size - 4
        if L < 40:
            continue
        # Maske muss SELEKTIV sein, sonst explodiert die Python-Schleife:
        # Laengen-Praefix (u32 in 2..30) UND erstes Namensbyte = Grossbuchstabe/UTF-8.
        i0, i1, i2, i3, i4 = b[0:L], b[1:L+1], b[2:L+2], b[3:L+3], b[4:L+4]
        cand = ((i0 >= 2) & (i0 <= 30) & (i1 == 0) & (i2 == 0) & (i3 == 0)
                & (((i4 >= 65) & (i4 <= 90)) | (i4 >= 0xC0)))
        # ZWEITE Stufe vektorisiert vorfiltern: dieselbe Bedingung, die die
        # Schleife unten ohnehin prueft (Laengen-Praefix des NACHNAMENS in
        # 2..30 => dessen obere drei Bytes sind 0, erstes Zeichen ein Buchstabe,
        # und pos >= 14 fuer die ID). Verwirft also nur, was die Schleife auch
        # verwerfen wuerde – spart aber den teuren Python-Durchlauf.
        cpos = np.nonzero(cand)[0]
        if cpos.size:
            o2 = cpos.astype(np.int64) + 4 + b[cpos].astype(np.int64)
            keep = (cpos >= 14) & (o2 + 5 < b.size)
            o2c = np.where(keep, o2, 0)
            n0, n1, n2, n3, n4 = (b[o2c], b[o2c + 1], b[o2c + 2],
                                  b[o2c + 3], b[o2c + 4])
            keep &= ((n0 >= 2) & (n0 <= 30) & (n1 == 0) & (n2 == 0) & (n3 == 0)
                     & (((n4 >= 65) & (n4 <= 90)) | ((n4 >= 97) & (n4 <= 122))
                        | (n4 >= 0xC0) | (n4 == 0x20) | (n4 == 0x2D)
                        | (n4 == 0x27)))
            cpos = cpos[keep]
        for _ci, pos in enumerate(cpos):
            if (_ci & 511) == 0:
                time.sleep(0)          # GIL kurz abgeben -> GUI bleibt responsiv
            pos = int(pos)
            L1 = int(b[pos])
            o1 = pos + 4
            try:
                vor = data[o1:o1 + L1].decode("utf-8")
            except Exception:
                continue
            if not _valid_name_part(vor):
                continue
            o2 = o1 + L1
            if o2 + 4 > len(data):
                continue
            L2 = int.from_bytes(data[o2:o2 + 4], "little")
            if not 2 <= L2 <= 30 or pos - 14 < 0:
                continue
            try:
                nach = data[o2 + 4:o2 + 4 + L2].decode("utf-8")
            except Exception:
                continue
            if not _valid_name_part(nach):
                continue
            pid = int.from_bytes(data[pos - 14:pos - 10], "little")
            if 0 < pid <= MAX_PID:
                idx.setdefault(pid, f"{vor} {nach}")
                if with_eid and pid not in eids and pos - 9 >= 0:
                    eid = int.from_bytes(data[pos - 9:pos - 5], "little")
                    if 1000 < eid < 2**31:
                        eids[pid] = eid
        if hot_out is not None and len(idx) > n_before:
            hot_out.add(base)
    return (idx, eids) if with_eid else idx


def _find_records(regions, id_set, counts=None):
    """Findet Stat-Records ueber die RECORD-STRUKTUR statt ueber xG-Werte –
    ein Durchgang fuer Feldspieler UND Torhueter.

    Anker (alles vektorisiert, deshalb schneller als die zwei alten Suchen):
      u16@M-10 == 1                     in allen Ground-Truth-Records konstant
      u16@M+30 == 0                     Minuten sind u16, High-Half stets 0
      f32@M+0 == f32@M+12               gespiegeltes xG-Paar
      f32@M+4 und f32@M+8               beide >= 0 und < 60 – NICHT auf
                                        Gleichheit pruefen, siehe unten
      1 <= Minuten <= 6000, 0 < PID <= MAX_PID
      angekommen <= versucht bei Paessen, Schuessen, Zweikaempfen,
      Kopfbaellen und Pressing (Monotonie echter Zaehlerpaare)

    id_set=None wertet ALLE Records aus (fuer die Vergleichskohorte), ein
    leeres Set zaehlt nur. counts (optional) nimmt Kennzahlen des Laufs auf:
    wie viele Records es insgesamt gibt und fuer wie viele Spieler-IDs – auch
    fuer die, zu denen gerade kein Namens-Record geladen ist.
    """
    out = {}
    all_pids = set()
    n_slots = 0
    apps_hist = np.zeros(64, np.int64)
    for base, data in regions:
        time.sleep(0)              # GIL kurz abgeben -> GUI bleibt responsiv
        ln = len(data)
        if ln < 1024:
            continue
        u16v = np.frombuffer(data, np.uint16, count=ln // 2)
        u32v = np.frombuffer(data, np.uint32, count=ln // 4)
        f32v = u32v.view(np.float32)
        # M ist 4-aligned -> M-10 liegt bei (M-10) % 4 == 2 -> u16-Index ungerade
        c = np.nonzero(u16v == 1)[0]
        c = c[(c & 1) == 1]
        if not c.size:
            continue
        M = c.astype(np.int64) * 2 + 10
        M = M[(M >= 24) & (M + 144 <= ln)]
        if not M.size:
            continue
        h, q = M // 2, M // 4
        ok = u16v[h + 15] == 0                          # M+30 (Minuten-High)
        xg, xa1, xa2, xg2 = f32v[q], f32v[q + 1], f32v[q + 2], f32v[q + 3]
        # M+8 ist KEIN Spiegel von xA. Die frueher hier stehende Bedingung
        # xa1 == xa2 hat rund ein Drittel aller echten Records verworfen –
        # bei Ryerson (Dortmund, 29.10.2025) genau den Bundesliga-Record:
        #   M+0 0.560714 (xG) · M+4 0.321742 (xA) · M+8 0.019950 · M+12 0.560714
        # Gegen die Spielerstatistik im Spiel geprueft: 8 Einsaetze, 581 Min,
        # 1 Tor, xG 0.56, Note 7.11 – der Record ist echt, nur M+8 traegt ein
        # eigenes Feld. In den meisten Records steht dort zufaellig derselbe
        # Wert wie in M+4, deshalb fiel es lange nicht auf. Was M+8 bedeutet,
        # ist offen; solange das so ist, wird darauf nur auf einen sinnvollen
        # Wertebereich geprueft und keine Ordnung unterstellt.
        ok &= (xg == xg2)
        ok &= (xg >= 0) & (xg < 60) & (xa1 >= 0) & (xa1 < 60)
        ok &= (xa2 >= 0) & (xa2 < 60)
        mins = u16v[h + 14]
        ok &= (mins >= 1) & (mins <= 6000)
        pid = u32v[(M - 24) // 4]
        ok &= (pid > 0) & (pid <= MAX_PID)
        ok &= u16v[h + 17] <= u16v[h + 16]              # Paesse an <= versucht
        ok &= u16v[h + 22] <= u16v[h + 21]              # Schuesse aufs Tor <= ges
        ok &= u16v[h + 19] <= u16v[h + 18]              # Zweikaempfe gew <= ges
        ok &= u16v[h + 41] <= u16v[h + 40]              # Kopfball gew <= ges
        # KEIN Pressing-Vergleich: erfolgreiche Pressings duerfen die versuchten
        # ueberschreiten (live belegt an Tzimas 5/4) – die Bedingung hat echte
        # Records gekostet. Die vier Paare oben sind dagegen ausnahmslos monoton.
        #
        # Die Monotonie allein reicht NICHT: Fuellspeicher besteht sie trivial,
        # weil dort beide Werte gleich sind (0/0 oder 0xFFFF/0xFFFF). Live
        # gefunden an "Diogo Prioste": 53 angebliche Records, u.a. 2600 Minuten
        # bei 0 Einsaetzen und 65535/65535 Paessen. Deshalb zusaetzlich der
        # innere Zusammenhang des Records:
        ok &= u16v[h + 16] < 60000                      # kein 0xFFFF-Fuellwert
        ok &= u16v[h + 18] < 60000
        ok &= u16v[h + 21] < 60000
        ok &= u16v[h + 40] < 60000
        m14 = u16v[h - 7]
        apps = (m14 & 0xFF).astype(np.int32)            # Einsaetze (Low-Byte)
        rated = (m14 >> 8).astype(np.int32)             # bewertete Spiele (High)
        ok &= apps >= 1                                 # Minuten ohne Einsatz gibt es nicht
        ok &= rated <= apps                             # bewertet <= gespielt
        ok &= mins >= apps                              # mind. 1 Minute je Einsatz
        ok &= mins <= apps * 130                        # max. ~130 Min je Einsatz
        sel = np.nonzero(ok)[0]
        n_slots += sel.size
        # Einsatz-Histogramm ueber ALLE gueltigen Records mitfuehren (auch die
        # namenlosen) – das ist die Grundlage der Saison-Erkennung und braucht
        # die grosse Stichprobe, siehe detect_season_apps.
        if sel.size:
            apps_hist += np.bincount(np.clip(apps[sel], 0, 63), minlength=64)
        for j in sel:
            p = int(pid[j])
            all_pids.add(p)
            if id_set is not None and p not in id_set:
                continue                    # Record da, aber (noch) kein Name
            r = _fields(data, int(M[j]))
            # Torhueter: xGA ist nur bei Keepern belegt, Feldspieler haben dort
            # exakt 0.0; Bit 0 der Positionsmaske faengt Keeper ohne xGA.
            r["is_gk"] = r["xga"] > 0 or bool(r["pos_mask"] & 1)
            out.setdefault(p, []).append(r)
    if counts is not None:
        counts["records_total"] = n_slots
        counts["pids_total"] = len(all_pids)
        # nur aussagekraeftig, wenn wirklich gefiltert wurde – bei id_set=None
        # (Kohortenlauf) waere "benannt" gleich "alle" und damit irrefuehrend
        if id_set is not None:
            counts["pids_named"] = len(out)
        counts["apps_hist"] = {a: int(n) for a, n in enumerate(apps_hist) if n}
    return out


# --- Legacy-Anker: nicht mehr im Scan benutzt, aber die Vergleichs-Skripte in
# --- research/ messen den neuen Weg gegen sie (anchor_audit, struct_scan, ...).
def _find_stats(regions, id_set):
    """ALT: sucht das aligned [xG,xA,xA,xG]-Muster. Uebersieht jeden Spieler mit
    xG == 0 UND xA == 0. Ersetzt durch _find_records."""
    out = {}
    for base, data in regions:
        time.sleep(0)
        n = len(data) // 4
        if n < 8:
            continue
        f = np.frombuffer(data, np.float32, count=n)
        a, b, c, d = f[:-3], f[1:-2], f[2:-1], f[3:]
        m = (a == d) & (b == c) & (a > 0.005) & (a < 30) & (b >= 0) & (b < 30)
        for i in np.nonzero(m)[0]:
            M = int(i) * 4
            if M - 24 < 0 or M + 136 > len(data):
                continue
            pid = int.from_bytes(data[M - 24:M - 20], "little")
            if pid not in id_set:
                continue
            r = _fields(data, M)
            if not (1 <= r["minutes"] <= 6000) or r["pass_try"] == 0:
                continue
            r["is_gk"] = False
            out.setdefault(pid, []).append(r)
    return out


def _find_gk_stats(regions, id_set):
    """ALT: Torhueter ueber xG==xA==0 plus zwei positive xGA-Floats bei
    M+16/M+20. Die zusaetzliche Bedingung u16@M-20 == 1223 hat fast alle Keeper
    verworfen – M-20 ist keine Konstante, sondern eine laufende Record-Nummer
    (live gemessen: 3 von 543 Keeper-Records trugen 1223). Ersetzt durch
    _find_records."""
    out = {}
    for base, data in regions:
        time.sleep(0)              # GIL kurz abgeben -> GUI bleibt responsiv
        n = len(data) // 4
        if n < 20:
            continue
        f = np.frombuffer(data, np.float32, count=n)
        f0, f1, f4, f5 = f[:-5], f[1:-4], f[4:-1], f[5:]
        m = ((f0 == 0.0) & (f1 == 0.0)
             & (f4 > 0.01) & (f4 < 80) & (f5 > 0.01) & (f5 < 80))
        for g in np.nonzero(m)[0]:
            M = int(g) * 4
            if M - 24 < 0 or M + 136 > len(data):
                continue
            if int.from_bytes(data[M - 20:M - 18], "little") != 1223:
                continue
            pid = int.from_bytes(data[M - 24:M - 20], "little")
            if pid not in id_set:
                continue
            r = _fields(data, M)
            if not (1 <= r["minutes"] <= 6000):
                continue
            r["is_gk"] = True
            out.setdefault(pid, []).append(r)
    return out


PERSON_SIG = (6847, 2023)     # konstantes u16-Paar am Personen-Record
MIN_EID = 100000              # echte FM-UIDs sind gross (z.B. 12.091.814)


def _find_person_records(regions, eids=None, counts=None):
    """{pid: (tag_im_jahr, geburtsjahr)} aus den PERSONEN-Records.

    Eigene Struktur, getrennt vom Namens-Record (dort steht das Geburtsdatum
    nicht) und nur ueber die Spieler-ID verknuepft. Anker ist das konstante
    u16-Paar (6847, 2023) bei X:
        u32 @X-56 = Spieler-ID     u32 @X-52 = EID
        u16 @X+4  = Geburtstag als Tag im Jahr (1-366)
        u16 @X+6  = Geburtsjahr

    Die EID-Pruefung ist ZWINGEND: die Signatur allein trifft ~5.700x, davon
    sind rund 4.600 Zufallstreffer (lieferten z.B. Trubin als Jahrgang 1973).
    Kennen wir den Spieler namentlich, muss die EID exakt zu der aus dem
    Namens-Record passen; sonst muss sie wenigstens die Groessenordnung einer
    echten FM-UID haben – die Fehltreffer tragen dort Kleinstwerte wie 1734.
    """
    out = {}
    checked = strict = 0
    for base, data in regions:
        time.sleep(0)              # GIL kurz abgeben -> GUI bleibt responsiv
        ln = len(data)
        if ln < 128:
            continue
        u16 = np.frombuffer(data, np.uint16, count=ln // 2)
        idx = np.nonzero(u16[:-1] == PERSON_SIG[0])[0]
        if not idx.size:
            continue
        idx = idx[u16[idx + 1] == PERSON_SIG[1]]
        for i in idx:
            X = int(i) * 2
            if X - 56 < 0 or X + 8 > ln:
                continue
            day = int.from_bytes(data[X + 4:X + 6], "little")
            year = int.from_bytes(data[X + 6:X + 8], "little")
            if not (1 <= day <= 366 and 1900 <= year <= 2015):
                continue
            pid = int.from_bytes(data[X - 56:X - 52], "little")
            eid = int.from_bytes(data[X - 52:X - 48], "little")
            checked += 1
            known = (eids or {}).get(pid)
            if known is not None:
                if known != eid:
                    continue
                strict += 1
            elif eid < MIN_EID:
                continue
            out[pid] = (day, year)
    if counts is not None:
        counts["person_hits"] = checked
        counts["person_by_eid"] = strict
        counts["birthdates"] = len(out)
    return out


def detect_season_apps(hist, ceiling=60):
    """NICHT MEHR IM SCAN BENUTZT – die Annahme dahinter war falsch.

    Die Funktion sucht die Kante im Einsatz-Histogramm und hielt sie fuer die
    Grenze zwischen laufender und abgeschlossener Saison. Tatsaechlich liegt
    dort die Grenze zwischen LIGA- und POKAL-Records: FM speichert je
    Wettbewerb (siehe _aggregate). Am 8. Spieltag fiel das nicht auf, weil die
    Liga-Records damals die schaerfste Kante bildeten und zufaellig richtig
    lagen. Im Saisonverlauf wandert die Kante auf den Pokal-Block – am
    12.08.2026 lieferte sie 4 Einsaetze und degradierte jeden Spieler auf
    seinen Ligapokal.

    Bleibt als Anker fuer die Vergleichs-Skripte in research/ stehen.

    Ermittelt, wie viele Einsaetze die LAUFENDE Saison hoechstens hat.

    Idee: in der laufenden Saison kann niemand mehr Einsaetze haben, als
    Spieltage gespielt wurden. Ueber alle ~37.000 Records im RAM ergibt das
    einen dichten Block bei kleinen Einsatzzahlen und dahinter eine Luecke;
    was rechts davon liegt, sind abgeschlossene Saisons (~30-40 Einsaetze).
    Live gemessen am 8. Spieltag: 1..8 Einsaetze zusammen 96 % aller Records,
    dann Absturz von 595 auf 110 (Faktor 5,4) – die schaerfste Kante im
    ganzen Histogramm.

    Warum Einsaetze und nicht Minuten: Minuten streuen je nach Einsatzzeit um
    Faktor 10 (Ergaenzungsspieler 90 Min vs. Stammspieler 720 bei gleichem
    Spieltag), Einsaetze sind durch die Spieltage hart gedeckelt.

    hist: {Einsaetze: Anzahl Records} ueber ALLE Records im RAM – nicht nur
    ueber die benannten. Mit den paar hundert Records geladener Spieler ist die
    Kante nicht zu sehen, die Erkennung liefe ins Leere (erster Versuch waehlte
    so faelschlich 1 Einsatz und degradierte jeden Spieler auf sein kuerzestes
    Spiel). _find_records liefert das Histogramm deshalb ungefiltert mit.

    Gibt None zurueck, wenn keine klare Kante existiert – dann bleibt es beim
    Minuten-Limit.
    """
    if not hist:
        return None
    cnt = [hist.get(a, 0) for a in range(0, ceiling + 2)]
    peak = max(cnt)
    best, best_ratio = None, 0.0
    for a in range(1, ceiling):
        if cnt[a] < 0.01 * peak:            # zu duenn, um eine Kante zu tragen
            continue
        ratio = cnt[a] / max(cnt[a + 1], 1)
        if ratio <= best_ratio:
            continue
        # Nach der Kante muss es duenn BLEIBEN, sonst ist es nur ein Ausreisser
        tail = cnt[a + 1:a + 5]
        if tail and sum(tail) / len(tail) < 0.35 * cnt[a]:
            best, best_ratio = a, ratio
    # Eine echte Kante hebt sich deutlich ab; sonst lieber nichts behaupten
    return best if best_ratio >= 2.0 else None


def _plausible(r):
    # Die Note faengt den Rest ab, den der gelockerte xA-Anker durchlaesst:
    # FM-Noten liegen zwischen 1 und 10. Live gefunden an "Brandon Ramires"
    # (11 Einsaetze auf 15 Minuten, Note 77,60) – Fuellspeicher, der alle
    # Zaehler-Bedingungen besteht, weil dort ueberall 0 steht.
    return (r["pass_ok"] <= r["pass_try"] and r["goals"] <= 25
            and r["shots_on"] <= r["minutes"] // 6 + 3
            and (not r["rated"] or 1.0 <= r["rating"] <= 10.0))


def _is_current(r, limit):
    """Gehoert der Record zur laufenden Saison? Ein einzelner Wettbewerb kann
    nicht mehr als eine volle Liga-Saison umfassen (siehe CUR_MAX_MIN)."""
    return r["minutes"] <= limit


# Zaehler, die sich ueber Wettbewerbe hinweg aufaddieren lassen. Alles andere
# (Note, Positionsmaske, is_gk) wird eigens behandelt.
SUM_FIELDS = ("minutes", "apps", "rated", "goals", "assists", "conceded",
              "xg", "xa", "xga", "pass_try", "pass_ok", "duels_total", "duels",
              "dribbles", "shots_total", "shots_on", "fouls", "fouls_against",
              "key_passes", "interceptions", "headers_total", "headers_won",
              "clearances", "prog_passes", "recoveries", "losses",
              "press_try", "press_win", "yellow")


def _aggregate(recs):
    """Fasst die Records eines Spielers zu SEINER SAISON zusammen.

    FM legt die Statistik JE WETTBEWERB ab, nicht je Saison. An Correia
    (Benfica, 12.08.2026) gegen die Spielerstatistik im Spiel verifiziert:

        Liga 21 Einsaetze/1654 Min/7,64 · CL 7/522/7,59 · Taca 2/180/8,25
        · Ligapokal 4/230/7,70 · Supertaca 1/63/6,80

    Genau diese fuenf Records lagen im Speicher. Die fruehere Fassung nahm den
    GROESSTEN unterhalb einer Saisongrenze und zeigte deshalb 230 Minuten statt
    2649 – bei jedem Spieler, ueber die ganze Auswertung hinweg.

    Deshalb wird summiert. Zwei Faelle brauchen Sonderbehandlung:

    * Manche Spieler tragen zusaetzlich einen GESAMT-Record, der die uebrigen
      schon aufsummiert (an Kouame und Tressoldi gefunden: 16/725 + 2/80 =
      18/805 auf die Minute genau). Wer den mitsummiert, zaehlt doppelt – also
      erkennen und allein verwenden.
    * Die Note ist ein Mittelwert und darf nicht addiert werden. Gewichtet wird
      mit den BEWERTETEN Spielen, denn genau darueber bildet FM sie (siehe
      _fields); Kurzeinsaetze ohne Note zaehlen so auch hier nicht mit.
    """
    recs = [r for r in recs if r.get("minutes")]
    if not recs:
        return None
    if len(recs) > 1:
        gross = max(recs, key=lambda r: r["minutes"])
        rest = [r for r in recs if r is not gross]
        if (abs(gross["minutes"] - sum(r["minutes"] for r in rest)) <= 1
                and abs(gross["apps"] - sum(r["apps"] for r in rest)) <= 1):
            recs = [gross]          # Gesamt-Record, die uebrigen stecken drin
    out = {k: sum(r.get(k) or 0 for r in recs) for k in SUM_FIELDS}
    for k in ("xg", "xa", "xga"):
        out[k] = round(out[k], 2)
    rated = out["rated"]
    out["rating"] = round(sum((r.get("rating") or 0) * (r.get("rated") or 0)
                              for r in recs) / rated, 2) if rated else 0.0
    mask = 0
    for r in recs:                  # mehrere Wettbewerbe = mehrere Positionen
        mask |= r.get("pos_mask") or 0
    out["pos_mask"] = mask
    out["is_gk"] = any(r.get("is_gk") for r in recs) or bool(mask & 1)
    return out


def _cohort_rows(stats, named, limit, min_minutes):
    """Vergleichsdatensaetze der NAMENLOSEN Spieler-IDs: je ID die aufaddierte
    Saison. Fuer Perzentile braucht es keinen Namen – Positionsmaske, Minuten
    und alle Kennzahlen stehen im Record. Damit rechnen die Scores gegen
    Zehntausende statt gegen die Handvoll geladener Spieler.

    Bewusst NUR die laufende Saison: die Score-Engine schrumpft /90-Raten mit
    min/(min+180). Eine Kohorte aus Altsaisonrecords bekaeme fast volles
    Gewicht und wuerde die aktuell bewerteten Spieler systematisch nach unten
    druecken. Die Mindestminuten gelten fuer die SUMME, nicht je Wettbewerb –
    sonst faellt ein Spieler raus, der seine 200 Minuten auf Liga und Pokal
    verteilt hat."""
    out = []
    for pid, recs in stats.items():
        if pid in named:
            continue
        agg = _aggregate([r for r in recs
                          if _is_current(r, limit) and _plausible(r)])
        if agg is None or agg["minutes"] < min_minutes:
            continue
        out.append(dict(agg, id=pid, name=None, eid=None))
    return out


def scan_players(process_name="fm.exe", cur_max_min=None, known=None, meta=None,
                 cohort_min_minutes=None, hot_regions=None, full_sweep=True):
    """Liste von Spieler-Dicts der aktuellen Saison. Wirft, wenn fm.exe fehlt.

    Ein Spieler-Dict ist die SUMME seiner Wettbewerbs-Records (siehe
    _aggregate), nicht ein einzelner Record.

    hot_regions/full_sweep: Der Namensindex kostet 77 % der Scanzeit, obwohl
    Namen nur in ~6 % des Speichers liegen. Mit hot_regions (Basisadressen aus
    einem frueheren Lauf) und full_sweep=False wird nur dort gesucht – 0,7 s
    statt 9,8 s. Regelmaessig muss trotzdem voll gesucht werden, sonst bleiben
    neu belegte Bereiche unentdeckt. meta["hot_regions"] traegt die
    aktualisierte Menge zurueck.

    cur_max_min: Obergrenze JE WETTBEWERBS-RECORD (darueber = Altsaison),
    Standard CUR_MAX_MIN. Jeder Spieler traegt zusaetzlich all_recs = alle
    plausiblen Einzel-Records fuer die Wettbewerbs-Ansicht.

    known: {pid: {"name":…, "eid":…}} bereits FRUEHER gesehener Spieler.
    FM haelt Namens-Records nur fuer gerade geladene Spieler vor, die
    Statistik-Records bleiben dagegen resident. Wer einmal benannt wurde,
    bleibt damit dauerhaft scanbar, auch wenn sein Name nicht mehr im RAM steht.

    meta: optionales Dict, das der Aufrufer fuellen laesst – enthaelt die frisch
    aus dem RAM gelesenen Namen (zum Wegspeichern) und Zaehler des Laufs.
    """
    limit = CUR_MAX_MIN if cur_max_min is None else max(90, int(cur_max_min))
    pm, _ = memlib.attach(process_name)
    regions = list(memlib.read_regions(pm))
    name_src, swept = regions, True
    if hot_regions and not full_sweep:
        sub = [(b, d) for b, d in regions if b in hot_regions]
        if sub:
            name_src, swept = sub, False
    hot_out = set()
    names, eids = _build_name_index(name_src, with_eid=True, hot_out=hot_out)
    if meta is not None:
        meta["names_ram"] = dict(names)
        meta["eids_ram"] = dict(eids)
        meta["names_from_ram"] = len(names)
        # Nach einer Teilsuche die bekannten Bereiche BEHALTEN (ein Block ohne
        # Treffer kann beim naechsten Mal wieder welche haben); die Vollsuche
        # setzt die Menge neu und raeumt damit auf.
        meta["hot_regions"] = hot_out if swept else set(hot_regions) | hot_out
        meta["full_sweep"] = swept
    # frueher gelernte Namen ergaenzen; der RAM hat Vorrang (aktuellere Schreibweise)
    for pid, info in (known or {}).items():
        pid = int(pid)
        if info.get("name"):
            names.setdefault(pid, info["name"])
        if info.get("eid"):
            eids.setdefault(pid, int(info["eid"]))
    idset = set(names)
    counts = {}
    # Fuer die Kohorte werden ALLE Records ausgewertet (kostet ~0.4s mehr),
    # sonst nur die benannten.
    want_cohort = cohort_min_minutes is not None
    stats = _find_records(regions, None if want_cohort else idset, counts)
    born = _find_person_records(regions, eids, counts)
    if meta is not None:
        counts.pop("apps_hist", None)          # Rohhistogramm nicht weiterreichen
        meta.update(counts)
        meta["names_known"] = len(names)
        meta["season_limit_min"] = limit
        if want_cohort:
            rows = _cohort_rows(stats, idset, limit,
                                max(0, int(cohort_min_minutes)))
            for r in rows:                     # Kohorte braucht Alter fuer die
                b = born.get(r["id"])          # altersbereinigten Perzentile
                if b:
                    r["birth_day"], r["birth_year"] = b
            meta["cohort"] = rows

    players = []
    for pid, name in names.items():
        cands = [r for r in stats.get(pid, []) if r["minutes"] > 0 and _plausible(r)]
        # inhaltsgleiche Speicher-Kopien desselben Records entfernen
        uniq, seen = [], set()
        for r in cands:
            key = tuple(sorted(r.items()))
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        current = [r for r in uniq if _is_current(r, limit)]
        rec = _aggregate(current)      # Summe ueber alle Wettbewerbe
        if rec is None:
            continue
        # In der Wettbewerbs-Ansicht kennzeichnen, was in die Summe eingeht.
        for r in uniq:
            r["is_current"] = any(r is c for c in current)
        all_recs = sorted(uniq, key=lambda r: -r["minutes"])[:12]
        b = born.get(pid)
        rec = dict(rec, id=pid, name=name, all_recs=all_recs,
                   eid=eids.get(pid),
                   birth_day=b[0] if b else None, birth_year=b[1] if b else None)
        players.append(rec)
    players.sort(key=lambda r: (-r["xg"], -r["goals"]))
    if meta is not None:
        meta["players"] = len(players)
    return players


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    m = {}
    ps = scan_players(meta=m)
    field = [p for p in ps if not p.get("is_gk")]
    gks = [p for p in ps if p.get("is_gk")]
    print(f"{len(ps)} Spieler ({len(gks)} Torhüter)")
    print(f"RAM: {m['records_total']} Records fuer {m['pids_total']} Spieler-IDs, "
          f"Namen im RAM fuer {m['names_from_ram']}\n")
    for p in field[:20]:
        print(f"{p['name'][:24]:<25} Note {p['rating']:>5.2f}  Tore {p['goals']:>2}  "
              f"Vorl {p['assists']:>2}  xG {p['xg']:>5.2f}  "
              f"ZWK {p['duels']:>2}/{p['duels_total']:>2}  Min {p['minutes']:>3}")
    if gks:
        print("\n-- Torhüter --")
        for p in gks:
            gp = round(p["xga"] - p["conceded"], 2)
            print(f"{p['name'][:24]:<25} Note {p['rating']:>5.2f}  GEG {p['conceded']:>2}  "
                  f"xGA {p['xga']:>5.2f}  Verhind {gp:>+5.2f}  Min {p['minutes']:>4}")
