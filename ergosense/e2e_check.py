"""End-to-end check of the whole system in simulate mode (no webcam needed).

Needs docker compose (Postgres + Mosquitto) and the backend running. Then:

    python -m ergosense.e2e_check            # about 2 minutes at the default speed

It starts a NEW shift, then drives the system only through its public interfaces, the way a
supervisor would use it (one rotation decision per hour of work):
  1. simulated station cameras publish the first hour of work over MQTT
  2. POST /rotation/suggest      -> the PPO policy proposes the next hour's assignment
  3. POST /rotation/{id}/approve -> only if somebody actually moves
  4. one more simulated hour (station streams count for whoever is assigned now); repeat 2-4
  5. POST /report/{shift_id}     -> LLM report (or the template fallback)
and prints PASS/FAIL for each step.
"""
import argparse
import subprocess
import sys
import time

import httpx

from ergosense import config
from ergosense.backend.init_db import init_db

API = f"http://{config.API_HOST}:{config.API_PORT}"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}")


def simulate(hours, speed, seed):
    cmd = [sys.executable, "-m", "ergosense.edge.pipeline", "--simulate", "--hours", str(hours),
           "--speed", str(speed), "--rate", "2", "--seed", str(seed)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_events(client, shift_id, expected, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        strain = client.get(f"/shifts/{shift_id}/strain").json()["workers"]
        if sum(w["exposure_s"] for w in strain) >= expected - 1:
            return strain
        time.sleep(0.5)
    return client.get(f"/shifts/{shift_id}/strain").json()["workers"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--speed", type=float, default=600, help="simulated seconds per real second")
    args = parser.parse_args()
    client = httpx.Client(base_url=API, timeout=300)

    print("0. backend health")
    try:
        health = client.get("/health").json()
    except httpx.HTTPError:
        sys.exit(f"backend is not running at {API} - start it first (see README)")
    check("database reachable", health["database"] == "ok")
    check("backend subscribed to MQTT", health["mqtt_connected"])

    print("1. new shift + first simulated hour")
    shift_id = init_db(new_shift=True)
    simulate(1, args.speed, seed=100)
    strain = wait_for_events(client, shift_id, expected=4 * 3600)
    hours = max(w["exposure_s"] for w in strain) / 3600
    check("all events stored", abs(hours - 1) < 0.01, f"{hours:.2f} h observed per worker")

    print("2.-4. hourly loop: suggest rotation (PPO) -> approve if anyone moves -> one more hour")
    first_projection, approvals, rejected_twice = None, 0, None
    for hour in range(2, 9):
        plan = client.post("/rotation/suggest").json()
        projection = plan["projection"]
        first_projection = first_projection or projection
        line = f"       hour {hour}: plan #{plan['plan_id']} ({plan['policy']}) {plan['moves']} move(s)"
        if plan["moves"]:
            approved = client.post(f"/rotation/{plan['plan_id']}/approve", json={"approved_by": "e2e-check"})
            approvals += approved.status_code == 200
            if rejected_twice is None:       # once is enough to prove double approval is blocked
                again = client.post(f"/rotation/{plan['plan_id']}/approve", json={"approved_by": "someone else"})
                rejected_twice = again.status_code == 409
                live = client.get("/state").json()["assignments"]
                check("live assignments match the approved plan", live == plan["assignments"])
            line += " -> approved " + ", ".join(f"{w}:{s}" for w, s in sorted(plan["assignments"].items()))
        print(line)
        simulate(1, args.speed, seed=100 + hour)
        wait_for_events(client, shift_id, expected=4 * hour * 3600)

    check("plans were approved during the shift", approvals > 0, f"{approvals} approval(s)")
    check("second approval of the same plan is rejected with 409", bool(rejected_twice))
    strain = wait_for_events(client, shift_id, expected=4 * 8 * 3600)
    hours = max(w["exposure_s"] for w in strain) / 3600
    check("full shift stored", abs(hours - 8) < 0.01, f"{hours:.2f} h observed per worker")
    peak = max(w["cumulative_strain"] for w in strain)
    for w in strain:
        flag = "  OVER LIMIT" if w["cumulative_strain"] > config.STRAIN_THRESHOLD else ""
        print(f"       {w['worker_id']} {w['display_name']:6s} strain {w['cumulative_strain']:6.1f}{flag}")
    print(f"       actual peak {peak:.0f}; at hour 2 the planner projected {first_projection['peak_with_plan']:.0f} "
          f"with hourly planning and {first_projection['peak_no_rotation']:.0f} with no rotation")
    check("nobody ended above the safe limit", peak <= config.STRAIN_THRESHOLD,
          f"peak {peak:.0f} vs limit {config.STRAIN_THRESHOLD:.0f}")

    print("5. safety report (generative AI + RAG)")
    t0 = time.time()
    report = client.post(f"/report/{shift_id}").json()
    check("report generated", "report_id" in report,
          f"{report.get('provider')} in {time.time() - t0:.0f} s, saved to {report.get('file_path')}")
    check("all citations verified", not report["citations"]["invalid"],
          f"{len(report['citations']['valid'])} valid, {len(report['citations']['invalid'])} removed")
    if report["is_fallback"]:
        print(f"       note: template fallback used ({report.get('llm_error')})")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
