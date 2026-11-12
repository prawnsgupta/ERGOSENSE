"""Create the ERGOSENSE schema and seed data.

Usage (from the repo root, with the venv active):
    python -m ergosense.backend.init_db              # create tables + seed (idempotent)
    python -m ergosense.backend.init_db --new-shift  # close the active shift, start a fresh one
    python -m ergosense.backend.init_db --reset      # DROP everything and start from scratch
    python -m ergosense.backend.init_db --new-shift --site sites/example_12_workers.json
                                                     # your own workers/stations instead of the 4-worker demo
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import psycopg

from ergosense import config

HERE = Path(__file__).resolve().parent


def run_sql_file(conn, path):
    conn.execute(path.read_text(encoding="utf-8"))


def close_active_shift(conn):
    """End the active shift and its open assignments so the seed can start a new one."""
    row = conn.execute("SELECT shift_id FROM shifts WHERE status = 'active'").fetchone()
    if row is None:
        return None
    shift_id = row[0]
    conn.execute(
        "UPDATE shift_assignments SET assigned_to = now() WHERE shift_id = %s AND assigned_to IS NULL",
        (shift_id,),
    )
    conn.execute("UPDATE shifts SET status = 'closed', ended_at = now() WHERE shift_id = %s", (shift_id,))
    return shift_id


RISK_LEVELS = ("negligible", "low", "medium", "high", "very_high")


def read_site(path):
    """Load and check a site file (see sites/example_12_workers.json)."""
    site = json.loads(Path(path).read_text(encoding="utf-8"))
    stations = {s["station_id"]: s for s in site["stations"]}
    workers = {w["worker_id"]: w for w in site["workers"]}
    problems = []
    for s in stations.values():
        if s.get("base_risk_level", "medium") not in RISK_LEVELS:
            problems.append(f"station {s['station_id']}: base_risk_level must be one of {RISK_LEVELS}")
    for w in workers.values():
        for st in w.get("trained_stations", []):
            if st not in stations:
                problems.append(f"worker {w['worker_id']} is trained for unknown station {st}")
    assignment = site.get("assignment", {})
    if len(set(assignment.values())) != len(assignment):
        problems.append("assignment puts two workers on the same station")
    for wid, st in assignment.items():
        if wid not in workers or st not in stations:
            problems.append(f"assignment {wid} -> {st} uses an unknown worker or station")
        elif st not in workers[wid].get("trained_stations", []):
            problems.append(f"assignment {wid} -> {st}: the worker is not trained for that station")
    if problems:
        raise SystemExit("site file problems:\n  " + "\n  ".join(problems))
    return site


def load_site(conn, site):
    """Upsert the site's stations, workers and training, start a shift if needed and assign everyone."""
    for s in site["stations"]:
        conn.execute(
            """INSERT INTO stations (station_id, name, base_risk_level, description) VALUES (%s, %s, %s, %s)
               ON CONFLICT (station_id) DO UPDATE SET name = EXCLUDED.name,
                   base_risk_level = EXCLUDED.base_risk_level, description = EXCLUDED.description""",
            (s["station_id"], s["name"], s.get("base_risk_level", "medium"), s.get("description")))
    for w in site["workers"]:
        conn.execute(
            """INSERT INTO workers (worker_id, display_name) VALUES (%s, %s)
               ON CONFLICT (worker_id) DO UPDATE SET display_name = EXCLUDED.display_name""",
            (w["worker_id"], w["display_name"]))
        conn.execute("DELETE FROM worker_training WHERE worker_id = %s", (w["worker_id"],))
        for st in w.get("trained_stations", []):
            conn.execute("INSERT INTO worker_training (worker_id, station_id) VALUES (%s, %s)", (w["worker_id"], st))

    shift = conn.execute("SELECT shift_id FROM shifts WHERE status = 'active'").fetchone()
    if shift is None:
        shift = conn.execute(
            "INSERT INTO shifts (name, planned_hours) VALUES ('Shift ' || to_char(now(), 'YYYY-MM-DD HH24:MI'), %s) "
            "RETURNING shift_id", (site.get("planned_hours", 8),)).fetchone()
    shift_id = shift[0]
    has = conn.execute("SELECT 1 FROM shift_assignments WHERE shift_id = %s AND assigned_to IS NULL LIMIT 1",
                       (shift_id,)).fetchone()
    if has:
        print("The active shift already has assignments; kept them (use --new-shift to start from the site file).")
    else:
        for wid, st in site.get("assignment", {}).items():
            conn.execute("INSERT INTO shift_assignments (shift_id, worker_id, station_id) VALUES (%s, %s, %s)",
                         (shift_id, wid, st))
    for w in site["workers"]:
        conn.execute("INSERT INTO shift_strain (shift_id, worker_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (shift_id, w["worker_id"]))


def init_db(dsn=None, reset=False, new_shift=False, site=None):
    dsn = dsn or config.DATABASE_URL
    with psycopg.connect(dsn, autocommit=False) as conn:
        if reset:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        run_sql_file(conn, HERE / "schema.sql")
        # name new shifts in the laptop's local time, not the container's UTC clock
        minutes = int(datetime.now().astimezone().utcoffset().total_seconds() // 60)
        hours, mins = divmod(abs(minutes), 60)
        sign = "+" if minutes >= 0 else "-"
        conn.execute(f"SET TIME ZONE INTERVAL '{sign}{hours:02d}:{mins:02d}' HOUR TO MINUTE")
        if new_shift:
            closed = close_active_shift(conn)
            if closed:
                print(f"Closed shift {closed}.")
        if site:
            load_site(conn, site)
        else:
            run_sql_file(conn, HERE / "seed.sql")
        conn.commit()

        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("workers", "stations", "worker_training", "shifts", "shift_assignments", "posture_events")
        }
        active = conn.execute("SELECT shift_id, name FROM shifts WHERE status = 'active'").fetchone()
    print("Database ready:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"Active shift: {active[0]} ({active[1]})")
    return active[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="drop all tables first (deletes all data)")
    parser.add_argument("--new-shift", action="store_true", help="close the active shift and start a new one")
    parser.add_argument("--site", metavar="FILE", help="load workers/stations from a site JSON file instead of the demo seed")
    args = parser.parse_args()
    init_db(reset=args.reset, new_shift=args.new_shift, site=read_site(args.site) if args.site else None)


if __name__ == "__main__":
    main()
