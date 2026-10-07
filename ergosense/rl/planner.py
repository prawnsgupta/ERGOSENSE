"""Turns the live shift state from the database into a rotation suggestion.

The trained PPO agent picks the assignment for the next hour. To show the supervisor what
that buys, we also project the end of the shift by simulating the remaining hours many times
(Monte Carlo) twice: once following the policy, once keeping everyone where they are.
Individual sensitivities are unknown for real workers, so the projection uses 1.0.
"""
import logging
import math

import numpy as np

from ergosense import config
from ergosense.rl.env import PERMUTATIONS, RotationEnv
from ergosense.rl.policies import Greedy, NoRotation, PPOPolicy
from ergosense.rl.stations import STATION_IDS, WORKER_IDS, load_profiles

log = logging.getLogger("rl.planner")
ROLLOUTS = 200


class RotationPlanner:
    def __init__(self):
        self.profiles = load_profiles()
        if config.RL_MODEL_PATH.exists():
            self.policy = PPOPolicy()
        else:
            log.warning("no trained PPO model at %s - using the greedy baseline", config.RL_MODEL_PATH)
            self.policy = Greedy()

    @property
    def policy_name(self):
        return self.policy.name

    def _env(self, trained):
        return RotationEnv(profiles=self.profiles, trained=trained, randomize=False)

    def _project(self, env, policy, options, seed):
        """Mean end-of-shift strain per worker (and mean peak) over ROLLOUTS simulated futures."""
        finals = []
        for i in range(ROLLOUTS):
            obs, _ = env.reset(seed=seed + i, options=options)
            done = env.hour >= env.hours
            while not done:
                obs, _, done, _, info = env.step(policy.act(env, obs))
            finals.append(env.strain.copy())
        finals = np.array(finals)
        return finals.mean(axis=0), float(finals.max(axis=1).mean()), float((finals > env.threshold).any(axis=1).mean())

    def _schedule(self, env, options):
        """Hour-by-hour assignments the policy would choose if every hour went as expected."""
        obs, _ = env.reset(seed=0, options={**options, "noise": False})
        schedule = []
        while env.hour < env.hours:
            hour = env.hour
            action = self.policy.act(env, obs)
            schedule.append({"hour": hour + 1, "assignments": {
                WORKER_IDS[w]: STATION_IDS[s] for w, s in enumerate(PERMUTATIONS[action])}})
            obs, _, _, _, _ = env.step(action)
        return schedule

    def suggest(self, strain, assignment, trained_pairs, hours_elapsed):
        """strain: {worker_id: points}, assignment: {worker_id: station_id},
        trained_pairs: [(worker_id, station_id)], hours_elapsed: observed working hours."""
        if sorted(strain) != WORKER_IDS or sorted(assignment.values()) != STATION_IDS:
            raise ValueError(f"the policy was trained for workers {WORKER_IDS} on stations {STATION_IDS}")
        trained = np.zeros((4, 4), dtype=int)
        for worker_id, station_id in trained_pairs:
            trained[WORKER_IDS.index(worker_id), STATION_IDS.index(station_id)] = 1

        env = self._env(trained)
        hour = min(int(math.floor(hours_elapsed)), env.hours - 1)        # the hour we are deciding for
        options = {
            "strain": [strain[w] for w in WORKER_IDS],
            "assignment": [STATION_IDS.index(assignment[w]) for w in WORKER_IDS],
            "hours_elapsed": hour,
            "sensitivity": [1.0] * 4,
        }
        obs, _ = env.reset(seed=0, options=options)
        action = self.policy.act(env, obs)
        new = PERMUTATIONS[action]
        plan = {WORKER_IDS[w]: STATION_IDS[s] for w, s in enumerate(new)}

        with_plan, peak_plan, risk_plan = self._project(env, self.policy, options, seed=1000)
        without, peak_none, risk_none = self._project(env, NoRotation(), options, seed=1000)
        return {
            "assignments": plan,
            "policy": self.policy_name,
            "moves": int(sum(1 for w in WORKER_IDS if plan[w] != assignment[w])),
            "hours_elapsed": round(float(hours_elapsed), 2),
            "decision_hour": hour + 1,
            "projected_peak_strain": round(peak_plan, 1),
            "projection": {
                "threshold": env.threshold,
                "rollouts": ROLLOUTS,
                "with_plan": {w: round(float(v), 1) for w, v in zip(WORKER_IDS, with_plan)},
                "no_rotation": {w: round(float(v), 1) for w, v in zip(WORKER_IDS, without)},
                "peak_with_plan": round(peak_plan, 1),
                "peak_no_rotation": round(peak_none, 1),
                "prob_anyone_over_limit_with_plan": round(risk_plan, 3),
                "prob_anyone_over_limit_no_rotation": round(risk_none, 3),
                "schedule": self._schedule(env, options),
                "station_profiles": {s: self.profiles[s] for s in STATION_IDS},
            },
        }
