"""Rotation planning for any number of workers and stations (used when the site is not the 4x4 demo).

The trained PPO agent chooses between the 4! = 24 possible assignments of the demo site. With 12
workers there are 12! = 479 million assignments, with 15 workers 1.3 trillion, so a single
discrete action is no longer possible. Instead, every hour we solve an assignment problem:

    cost[w, s] = (strain_w + rate_s)^2                    spread the strain: a squared cost makes
                                                          giving more to an already strained worker expensive
               + OVER_PENALTY * max(0, strain_w + rate_s - limit)   never push someone over the limit if avoidable
               + MOVE_COST * 2 * (strain_w + rate_s)      moving someone costs about MOVE_COST strain points
               = infinity if worker w is not trained for station s   (the same hard rule as the PPO action mask)

and the Hungarian algorithm (scipy.optimize.linear_sum_assignment) finds the cheapest complete
assignment in milliseconds. rate_s is the strain a station adds per hour: measured from that
station's real posture events when there are enough of them, otherwise estimated from the
station's base risk level.

Only workers who currently have a station take part; they are rotated among the stations that
are currently staffed, so the number of workers and stations always match.
"""
import numpy as np
from scipy.optimize import linear_sum_assignment

from ergosense import config

MOVE_COST = 2.0
OVER_PENALTY = 1e4
INFEASIBLE = 1e12
ROLLOUTS = 200

# strain points added per hour when nothing has been measured yet (from the calibrated demo stations)
BASE_RATE = {"negligible": 5.0, "low": 14.0, "medium": 30.0, "high": 55.0, "very_high": 80.0}


class AssignmentPolicy:
    name = "assignment_optimizer"

    def __init__(self, trained, rates, threshold):
        self.trained = np.asarray(trained, dtype=bool)        # (workers, stations)
        self.rates = np.asarray(rates, dtype=float)           # (stations,)
        self.threshold = float(threshold)

    def choose(self, strain, current):
        """strain: (n,) points, current: (n,) station index per worker -> new station index per worker."""
        projected = strain[:, None] + self.rates[None, :]
        moved = np.arange(len(self.rates))[None, :] != current[:, None]
        cost = projected ** 2 + OVER_PENALTY * np.maximum(projected - self.threshold, 0.0) \
            + MOVE_COST * 2.0 * projected * moved
        cost = np.where(self.trained, cost, INFEASIBLE)
        rows, cols = linear_sum_assignment(cost)
        if cost[rows, cols].max() >= INFEASIBLE:              # no complete trained assignment exists
            return current.copy()
        new = np.empty_like(current)
        new[rows] = cols
        return new


def simulate(policy, strain, assignment, hours_left, rates, stds, rng, rotate=True):
    """One possible future of the shift. Returns final strain per worker."""
    strain, assignment = strain.copy(), assignment.copy()
    for _ in range(hours_left):
        if rotate:
            assignment = policy.choose(strain, assignment)
        added = np.maximum(rates + stds * rng.normal(size=len(rates)), 0.0)
        strain = strain + added[assignment]
    return strain


def suggest(strain, assignment, trained_pairs, hours_elapsed, rates, hours=8, threshold=None):
    """strain {worker: points}, assignment {worker: station}, trained_pairs [(worker, station)],
    rates {station: {"mean", "std", "source"}}. Same result shape as RotationPlanner.suggest."""
    threshold = float(threshold or config.STRAIN_THRESHOLD)
    workers = sorted(assignment)
    stations = sorted(set(assignment.values()))
    if len(workers) < 2:
        raise ValueError("at least two assigned workers are needed to plan a rotation")
    trained = np.zeros((len(workers), len(stations)), dtype=bool)
    for w, s in trained_pairs:
        if w in workers and s in stations:
            trained[workers.index(w), stations.index(s)] = True
    means = np.array([rates[s]["mean"] for s in stations])
    stds = np.array([rates[s]["std"] for s in stations])
    policy = AssignmentPolicy(trained, means, threshold)

    s0 = np.array([float(strain.get(w, 0.0)) for w in workers])
    a0 = np.array([stations.index(assignment[w]) for w in workers])
    hour = min(int(hours_elapsed), hours - 1)
    left = hours - hour

    new = policy.choose(s0, a0)
    plan = {w: stations[new[i]] for i, w in enumerate(workers)}

    rng = np.random.default_rng(1000)
    with_plan = np.array([simulate(policy, s0, a0, left, means, stds, rng) for _ in range(ROLLOUTS)])
    rng = np.random.default_rng(1000)
    without = np.array([simulate(policy, s0, a0, left, means, stds, rng, rotate=False) for _ in range(ROLLOUTS)])

    schedule, s, a = [], s0.copy(), a0.copy()
    for h in range(left):
        a = policy.choose(s, a)
        schedule.append({"hour": hour + h + 1, "assignments": {w: stations[a[i]] for i, w in enumerate(workers)}})
        s = s + means[a]

    return {
        "assignments": plan,
        "policy": policy.name,
        "moves": int(sum(plan[w] != assignment[w] for w in workers)),
        "hours_elapsed": round(float(hours_elapsed), 2),
        "decision_hour": hour + 1,
        "projected_peak_strain": round(float(with_plan.max(axis=1).mean()), 1),
        "projection": {
            "threshold": threshold,
            "rollouts": ROLLOUTS,
            "with_plan": {w: round(float(v), 1) for w, v in zip(workers, with_plan.mean(axis=0))},
            "no_rotation": {w: round(float(v), 1) for w, v in zip(workers, without.mean(axis=0))},
            "peak_with_plan": round(float(with_plan.max(axis=1).mean()), 1),
            "peak_no_rotation": round(float(without.max(axis=1).mean()), 1),
            "prob_anyone_over_limit_with_plan": round(float((with_plan > threshold).any(axis=1).mean()), 3),
            "prob_anyone_over_limit_no_rotation": round(float((without > threshold).any(axis=1).mean()), 3),
            "schedule": schedule,
            "station_profiles": {s: rates[s] for s in stations},
        },
    }
