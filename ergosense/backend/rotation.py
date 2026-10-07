"""Rotation plans: create a suggestion from the RL planner, and approve it in one transaction."""
from psycopg.types.json import Jsonb

from ergosense.backend import repository


class PlanNotFound(Exception):
    pass


class PlanConflict(Exception):
    """The plan cannot be approved (already approved/superseded, shift closed, untrained worker...)."""


def hours_elapsed(strain_rows):
    """Observed working time of the shift so far = the longest exposure of any worker."""
    return max((r["exposure_s"] for r in strain_rows), default=0.0) / 3600.0


def suggest_plan(conn, planner):
    """Ask the planner for the next assignment and store it as a 'proposed' plan."""
    shift = repository.active_shift(conn)
    if shift is None:
        raise PlanConflict("there is no active shift")
    sid = shift["shift_id"]
    strain_rows = repository.shift_strain(conn, sid)
    elapsed = hours_elapsed(strain_rows)
    if elapsed >= float(shift["planned_hours"]):
        raise PlanConflict("the shift is already over - nothing left to plan")

    strain = {w["worker_id"]: 0.0 for w in repository.workers(conn)}
    strain.update({r["worker_id"]: r["cumulative_strain"] for r in strain_rows})
    assignment = repository.current_assignments(conn, sid)
    trained = [(r["worker_id"], r["station_id"]) for r in repository.training_pairs(conn)]
    result = planner.suggest(strain, assignment, trained, elapsed)

    with conn.transaction():
        # a newer suggestion replaces any older one that was never approved
        conn.execute("UPDATE rotation_plans SET status = 'superseded' WHERE shift_id = %s AND status = 'proposed'",
                     (sid,))
        row = conn.execute(
            """INSERT INTO rotation_plans (shift_id, assignments, policy, projected_peak_strain, projection)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING plan_id, shift_id, created_at, status""",
            (sid, Jsonb(result["assignments"]), result["policy"], result["projected_peak_strain"],
             Jsonb(result["projection"])),
        ).fetchone()
    return {**row, **result, "current_assignments": assignment,
            "current_strain": {w: round(v, 1) for w, v in strain.items()}}


def approve_plan(conn, plan_id, approved_by):
    """Approve a plan atomically. Either ALL of these happen or NONE of them do:

      1. lock the plan row (SELECT ... FOR UPDATE) so two supervisors cannot approve it at once
      2. mark the plan approved (and any other open proposal of the shift superseded)
      3. close the current worker-station assignments and open the new ones
      4. record who approved it in rotation_approvals

    If anything fails (e.g. a constraint violation), the transaction rolls back and the
    database is exactly as it was before.
    """
    with conn.transaction():
        plan = conn.execute(
            "SELECT plan_id, shift_id, assignments, status FROM rotation_plans WHERE plan_id = %s FOR UPDATE",
            (plan_id,),
        ).fetchone()
        if plan is None:
            raise PlanNotFound(f"plan {plan_id} does not exist")
        if plan["status"] != "proposed":
            raise PlanConflict(f"plan {plan_id} is already {plan['status']}")
        shift = repository.get_shift(conn, plan["shift_id"])
        if shift["status"] != "active":
            raise PlanConflict(f"shift {shift['shift_id']} is closed")

        trained = {(r["worker_id"], r["station_id"]) for r in repository.training_pairs(conn)}
        for worker_id, station_id in plan["assignments"].items():
            if (worker_id, station_id) not in trained:
                raise PlanConflict(f"{worker_id} is not trained for {station_id}")

        conn.execute("UPDATE rotation_plans SET status = 'approved' WHERE plan_id = %s", (plan_id,))
        conn.execute(
            "UPDATE rotation_plans SET status = 'superseded' WHERE shift_id = %s AND status = 'proposed'",
            (plan["shift_id"],),
        )
        conn.execute(
            "UPDATE shift_assignments SET assigned_to = now() WHERE shift_id = %s AND assigned_to IS NULL",
            (plan["shift_id"],),
        )
        for worker_id, station_id in plan["assignments"].items():
            # the partial unique indexes reject two workers on one station (whole transaction rolls back)
            conn.execute(
                "INSERT INTO shift_assignments (shift_id, worker_id, station_id, plan_id) VALUES (%s, %s, %s, %s)",
                (plan["shift_id"], worker_id, station_id, plan_id),
            )
        approval = conn.execute(
            "INSERT INTO rotation_approvals (plan_id, approved_by) VALUES (%s, %s) RETURNING approval_id, approved_at",
            (plan_id, approved_by),
        ).fetchone()
    return {"plan_id": plan_id, "shift_id": plan["shift_id"], "status": "approved",
            "assignments": plan["assignments"], "approved_by": approved_by, **approval}


def list_plans(conn, shift_id):
    return conn.execute(
        """SELECT p.plan_id, p.created_at, p.assignments, p.policy, p.projected_peak_strain, p.status,
                  a.approved_by, a.approved_at
           FROM rotation_plans p LEFT JOIN rotation_approvals a USING (plan_id)
           WHERE p.shift_id = %s ORDER BY p.created_at DESC""",
        (shift_id,),
    ).fetchall()
