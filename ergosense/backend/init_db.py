"""Create the ERGOSENSE schema and seed data.

Usage (from the repo root, with the venv active):
    python -m ergosense.backend.init_db              # create tables + seed (idempotent)
    python -m ergosense.backend.init_db --new-shift  # close the active shift, start a fresh one
    python -m ergosense.backend.init_db --reset      # DROP everything and start from scratch
"""
import argparse
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


def init_db(dsn=None, reset=False, new_shift=False):
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
    args = parser.parse_args()
    init_db(reset=args.reset, new_shift=args.new_shift)


if __name__ == "__main__":
    main()
