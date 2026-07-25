"""Pipeline-Test: scannen -> anreichern -> speichern -> Rankings."""
import sys
from fmcompanion import scanner, moneyball, db

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

players = scanner.scan_players()
players = moneyball.enrich(players)
print(f"Gescannt & angereichert: {len(players)} Spieler")

conn = db.connect()
sid = db.save_snapshot(conn, players, note="Pipeline-Test")
print(f"Snapshot #{sid} gespeichert, DB hat {db.snapshot_count(conn)} Snapshot(s)\n")

for title, (key, desc) in list(moneyball.RANKINGS.items())[:4]:
    print(f"— {title} —")
    for p in moneyball.rank(players, key, desc, limit=5):
        print(f"   {p['name'][:24]:<25} {key}={p.get(key)}   "
              f"({p['goals']}T/{p['xg']}xG, {p['minutes']}min)")
    print()
