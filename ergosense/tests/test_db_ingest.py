"""Idempotent ingest and time-based worker attribution (needs docker compose Postgres)."""
from datetime import datetime, timedelta, timezone

from ergosense.backend import repository
from ergosense.events import decode, encode, make_event

HIGH = {"reba_score": 9, "risk_level": "high", "posture_label": "bending",
        "segment_scores": {"trunk": 4}, "angles": None}


def _event(station="S4", worker=None, ts=None, duration=60.0):
    return decode(encode(make_event(station, worker, HIGH, duration, "simulated", seq=1, ts=ts)))


def _strain(conn, worker_id):
    shift = repository.active_shift(conn)
    rows = {r["worker_id"]: r for r in repository.shift_strain(conn, shift["shift_id"])}
    return rows[worker_id]


def test_duplicate_event_is_stored_and_counted_once(conn):
    event = _event()
    first = repository.ingest_event(conn, event)
    second = repository.ingest_event(conn, dict(event))        # redelivery of the same event
    assert first is not None and second is None
    assert repository.event_count(conn) == 1
    # one minute in a high-risk posture = 2 strain points, counted once
    s = _strain(conn, "W04")
    assert s["cumulative_strain"] == 2.0
    assert s["high_risk_count"] == 1
    assert s["risky_s"] == 60.0


def test_station_events_follow_the_assignment_in_time(conn):
    shift_id = repository.active_shift(conn)["shift_id"]
    # W04 has worked at S4 for the last hour. Move W01 to S4 from "now"; W04 goes to S1.
    with conn.transaction():
        conn.execute("UPDATE shift_assignments SET assigned_from = now() - interval '1 hour' WHERE shift_id = %s",
                     (shift_id,))
    with conn.transaction():
        conn.execute("UPDATE shift_assignments SET assigned_to = now() WHERE shift_id = %s AND assigned_to IS NULL",
                     (shift_id,))
        for worker, station in (("W01", "S4"), ("W02", "S2"), ("W03", "S3"), ("W04", "S1")):
            conn.execute("INSERT INTO shift_assignments (shift_id, worker_id, station_id) VALUES (%s, %s, %s)",
                         (shift_id, worker, station))

    before = datetime.now(timezone.utc) - timedelta(minutes=5)   # a late event buffered at the edge
    after = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert repository.ingest_event(conn, _event(ts=before))["event"]["worker_id"] == "W04"
    assert repository.ingest_event(conn, _event(ts=after))["event"]["worker_id"] == "W01"


def test_explicit_worker_id_wins(conn):
    result = repository.ingest_event(conn, _event(station="S4", worker="W02"))
    assert result["event"]["worker_id"] == "W02"
