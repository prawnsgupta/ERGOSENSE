"""Rotation plan approval: atomicity, double-approval protection and the RL suggestion path."""
import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ergosense import config
from ergosense.backend import repository
from ergosense.backend.rotation import PlanConflict, approve_plan, suggest_plan

SEED_ASSIGNMENT = {"W01": "S1", "W02": "S2", "W03": "S3", "W04": "S4"}


def _make_plan(conn, assignments):
    shift_id = repository.active_shift(conn)["shift_id"]
    row = conn.execute(
        "INSERT INTO rotation_plans (shift_id, assignments, policy) VALUES (%s, %s, 'test') RETURNING plan_id",
        (shift_id, Jsonb(assignments)),
    ).fetchone()
    conn.commit()
    return row["plan_id"], shift_id


def _snapshot(conn, plan_id, shift_id):
    """Everything an approval is allowed to change."""
    return {
        "status": conn.execute("SELECT status FROM rotation_plans WHERE plan_id = %s", (plan_id,)).fetchone()["status"],
        "assignments": repository.current_assignments(conn, shift_id),
        "assignment_rows": conn.execute("SELECT count(*) AS n FROM shift_assignments").fetchone()["n"],
        "approvals": conn.execute("SELECT count(*) AS n FROM rotation_approvals").fetchone()["n"],
    }


def test_approval_updates_plan_assignments_and_audit_log(conn):
    new = {"W01": "S4", "W02": "S1", "W03": "S3", "W04": "S2"}
    plan_id, shift_id = _make_plan(conn, new)
    result = approve_plan(conn, plan_id, "supervisor-1")

    after = _snapshot(conn, plan_id, shift_id)
    assert result["status"] == "approved"
    assert after["status"] == "approved"
    assert after["assignments"] == new
    assert after["approvals"] == 1
    assert after["assignment_rows"] == 8           # 4 closed (history) + 4 new


def test_failed_approval_changes_nothing(conn):
    """Atomicity: W01 and W02 are both sent to S1. The plan status update and the closing of the
    old assignments run first, then the second INSERT violates the 'one worker per station'
    unique index. The whole transaction must roll back as if nothing had happened."""
    bad = {"W01": "S1", "W02": "S1", "W03": "S3", "W04": "S4"}
    plan_id, shift_id = _make_plan(conn, bad)
    before = _snapshot(conn, plan_id, shift_id)

    with pytest.raises(psycopg.errors.UniqueViolation):
        approve_plan(conn, plan_id, "supervisor-1")

    after = _snapshot(conn, plan_id, shift_id)
    assert after == before
    assert after["status"] == "proposed"
    assert after["assignments"] == SEED_ASSIGNMENT
    assert after["approvals"] == 0


def test_untrained_worker_is_rejected(conn):
    plan_id, shift_id = _make_plan(conn, {"W01": "S1", "W02": "S3", "W03": "S2", "W04": "S4"})
    with pytest.raises(PlanConflict, match="W02 is not trained for S3"):
        approve_plan(conn, plan_id, "supervisor-1")
    assert _snapshot(conn, plan_id, shift_id)["status"] == "proposed"


def test_plan_cannot_be_approved_twice(conn):
    plan_id, _ = _make_plan(conn, {"W01": "S2", "W02": "S1", "W03": "S3", "W04": "S4"})
    approve_plan(conn, plan_id, "supervisor-1")
    with pytest.raises(PlanConflict, match="already approved"):
        approve_plan(conn, plan_id, "supervisor-2")


def test_concurrent_approval_waits_on_row_lock(fresh_db, conn):
    """Two supervisors click Approve at the same moment. Alice's transaction holds the row lock
    from SELECT ... FOR UPDATE; Bob's approval must wait, then see 'approved' and fail."""
    plan_id, _ = _make_plan(conn, {"W01": "S2", "W02": "S1", "W03": "S3", "W04": "S4"})
    outcome = {}

    def bob():
        with psycopg.connect(fresh_db, row_factory=dict_row) as conn_b:
            try:
                approve_plan(conn_b, plan_id, "bob")
                outcome["bob"] = "approved"
            except PlanConflict as exc:
                outcome["bob"] = str(exc)

    with psycopg.connect(fresh_db, row_factory=dict_row) as conn_a:
        with conn_a.transaction():                      # keep Alice's transaction (and lock) open
            approve_plan(conn_a, plan_id, "alice")
            thread = threading.Thread(target=bob)
            thread.start()
            time.sleep(0.7)
            assert thread.is_alive(), "Bob should be blocked by Alice's row lock"
        thread.join(timeout=10)                         # Alice committed, Bob continues

    assert "already approved" in outcome["bob"]
    approvals = conn.execute("SELECT approved_by FROM rotation_approvals WHERE plan_id = %s", (plan_id,)).fetchall()
    assert [a["approved_by"] for a in approvals] == ["alice"]


@pytest.mark.skipif(not config.RL_MODEL_PATH.exists(), reason="PPO model not trained yet")
def test_suggest_then_approve_with_the_trained_policy(conn):
    from ergosense.rl.planner import RotationPlanner

    shift_id = repository.active_shift(conn)["shift_id"]
    # 3 hours in: Karan (W04, loading bay) is far ahead of everyone else
    for worker, strain in (("W01", 40.0), ("W02", 90.0), ("W03", 150.0), ("W04", 240.0)):
        conn.execute("UPDATE shift_strain SET cumulative_strain = %s, exposure_s = 3 * 3600 "
                     "WHERE shift_id = %s AND worker_id = %s", (strain, shift_id, worker))
    conn.commit()

    plan = suggest_plan(conn, RotationPlanner())
    assert plan["status"] == "proposed"
    assert plan["decision_hour"] == 4
    assert plan["assignments"]["W04"] != "S4"            # the most strained worker leaves the loading bay
    projection = plan["projection"]
    assert projection["peak_with_plan"] < projection["peak_no_rotation"]

    approve_plan(conn, plan["plan_id"], "supervisor-1")
    assert repository.current_assignments(conn, shift_id) == plan["assignments"]
