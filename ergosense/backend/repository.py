"""SQL used by the backend. Each function takes an open psycopg connection (dict rows)."""
from psycopg.types.json import Jsonb

from ergosense.strain import RISK_WEIGHT, is_high_risk, strain_increment


def active_shift(conn):
    return conn.execute(
        "SELECT shift_id, name, started_at, planned_hours, status FROM shifts WHERE status = 'active'"
    ).fetchone()


def get_shift(conn, shift_id):
    return conn.execute(
        "SELECT shift_id, name, started_at, ended_at, planned_hours, status FROM shifts WHERE shift_id = %s",
        (shift_id,),
    ).fetchone()


def resolve_worker(conn, shift_id, station_id, ts):
    """Who was at this station at time ts? Falls back to the current assignment."""
    row = conn.execute(
        """SELECT worker_id FROM shift_assignments
           WHERE shift_id = %s AND station_id = %s
             AND assigned_from <= %s AND (assigned_to IS NULL OR assigned_to > %s)
           ORDER BY assigned_from DESC LIMIT 1""",
        (shift_id, station_id, ts, ts),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT worker_id FROM shift_assignments WHERE shift_id = %s AND station_id = %s AND assigned_to IS NULL",
            (shift_id, station_id),
        ).fetchone()
    return row["worker_id"] if row else None


def ingest_event(conn, event):
    """Store one validated event and update the worker's running strain - in ONE transaction.

    Idempotent: a repeated event_id hits ON CONFLICT DO NOTHING, nothing is returned by the
    INSERT, and the strain is not added a second time. Returns None for duplicates/unknown,
    otherwise {"event": ..., "strain": ...}.
    """
    with conn.transaction():
        shift = active_shift(conn)
        if shift is None:
            return None
        worker_id = event["worker_id"] or resolve_worker(conn, shift["shift_id"], event["station_id"], event["ts"])
        if worker_id is None:
            return None

        inserted = conn.execute(
            """INSERT INTO posture_events (event_id, shift_id, worker_id, station_id, ts, duration_s, reba_score,
                                           risk_level, posture_label, segment_scores, angles, source, edge_seq)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (event_id) DO NOTHING
               RETURNING event_id""",
            (event["event_id"], shift["shift_id"], worker_id, event["station_id"], event["ts"], event["duration_s"],
             event["reba_score"], event["risk_level"], event["posture_label"], Jsonb(event["segment_scores"]),
             Jsonb(event["angles"]) if event["angles"] is not None else None, event["source"], event["seq"]),
        ).fetchone()
        if inserted is None:
            return None                                   # duplicate delivery

        high = is_high_risk(event["risk_level"])
        strain = conn.execute(
            """INSERT INTO shift_strain AS s (shift_id, worker_id, cumulative_strain, high_risk_count,
                                              exposure_s, risky_s, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (shift_id, worker_id) DO UPDATE SET
                   cumulative_strain = s.cumulative_strain + EXCLUDED.cumulative_strain,
                   high_risk_count   = s.high_risk_count + EXCLUDED.high_risk_count,
                   exposure_s        = s.exposure_s + EXCLUDED.exposure_s,
                   risky_s           = s.risky_s + EXCLUDED.risky_s,
                   updated_at        = now()
               RETURNING shift_id, worker_id, cumulative_strain, high_risk_count, exposure_s, risky_s""",
            (shift["shift_id"], worker_id, strain_increment(event["risk_level"], event["duration_s"]),
             1 if high else 0, event["duration_s"], event["duration_s"] if high else 0.0),
        ).fetchone()

    stored = {k: event[k] for k in ("event_id", "station_id", "reba_score", "risk_level", "posture_label",
                                    "segment_scores", "angles", "source", "duration_s")}
    stored["worker_id"] = worker_id
    stored["timestamp"] = event["ts"].isoformat()
    return {"event": stored, "strain": strain}


def workers(conn):
    return conn.execute("SELECT worker_id, display_name, trained_stations FROM workers_v ORDER BY worker_id").fetchall()


def stations(conn):
    return conn.execute(
        "SELECT station_id, name, base_risk_level, description FROM stations ORDER BY station_id"
    ).fetchall()


def training_pairs(conn):
    return conn.execute("SELECT worker_id, station_id FROM worker_training").fetchall()


def current_assignments(conn, shift_id):
    rows = conn.execute(
        "SELECT worker_id, station_id FROM shift_assignments WHERE shift_id = %s AND assigned_to IS NULL",
        (shift_id,),
    ).fetchall()
    return {r["worker_id"]: r["station_id"] for r in rows}


def shift_strain(conn, shift_id):
    return conn.execute(
        """SELECT s.worker_id, w.display_name, s.cumulative_strain, s.high_risk_count, s.exposure_s, s.risky_s,
                  s.updated_at
           FROM shift_strain s JOIN workers w USING (worker_id)
           WHERE s.shift_id = %s ORDER BY s.worker_id""",
        (shift_id,),
    ).fetchall()


def recent_events(conn, worker_id=None, limit=50):
    """Newest events first. With a worker_id this uses the (worker_id, ts DESC) index."""
    sql = """SELECT event_id, worker_id, station_id, ts, duration_s, reba_score, risk_level, posture_label,
                    segment_scores, angles, source
             FROM posture_events {where} ORDER BY ts DESC LIMIT %s"""
    if worker_id:
        return conn.execute(sql.format(where="WHERE worker_id = %s"), (worker_id, limit)).fetchall()
    return conn.execute(sql.format(where=""), (limit,)).fetchall()


def latest_event_per_worker(conn, shift_id):
    """Most recent event of each worker (DISTINCT ON walks the (worker_id, ts DESC) index)."""
    return conn.execute(
        """SELECT DISTINCT ON (worker_id) worker_id, station_id, ts, reba_score, risk_level, posture_label,
                  segment_scores, angles
           FROM posture_events WHERE shift_id = %s
           ORDER BY worker_id, ts DESC""",
        (shift_id,),
    ).fetchall()


def event_count(conn, shift_id=None):
    if shift_id is None:
        return conn.execute("SELECT count(*) AS n FROM posture_events").fetchone()["n"]
    return conn.execute("SELECT count(*) AS n FROM posture_events WHERE shift_id = %s", (shift_id,)).fetchone()["n"]


def station_strain_rates(conn, min_work_s=900):
    """Measured strain points per hour of work at each station, from all stored posture events.

    Only stations with at least min_work_s seconds of observed work are returned; the planner
    falls back to the station's base risk level for the others."""
    weight = " ".join(f"WHEN '{level}' THEN {w}" for level, w in RISK_WEIGHT.items())
    rows = conn.execute(
        f"""SELECT station_id, sum(duration_s) AS work_s,
                   sum(duration_s / 60.0 * CASE risk_level {weight} END) AS strain
            FROM posture_events GROUP BY station_id HAVING sum(duration_s) >= %s""",
        (min_work_s,)).fetchall()
    return {r["station_id"]: {"mean": round(3600.0 * r["strain"] / r["work_s"], 1),
                              "work_h": round(r["work_s"] / 3600.0, 2)} for r in rows}
