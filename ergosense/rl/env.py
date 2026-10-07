"""Gymnasium environment: one 8-hour shift, 4 workers, 4 stations, a rotation decision every hour.

State (25 numbers, all scaled to roughly 0..1):
    cumulative strain of each worker / safe threshold          (4)
    current station of each worker, one-hot                    (4 x 4)
    hours remaining / shift length                             (1)
    mean strain per hour of each station / 100                 (4)

Action: Discrete(24) - one of the 4! = 24 ways to place 4 workers on 4 stations
(PERMUTATIONS[a][w] = station of worker w). Permutations that put a worker on a station they
are not trained for are masked out (invalid action masking, used by MaskablePPO).

Dynamics: during each hour every station adds strain ~ max(0, Normal(mean, std)) to whoever
works there, multiplied by that worker's personal sensitivity (0.85-1.20, unknown to the agent;
it only shows up as faster-growing strain).

Reward per hour = -( increase in total strain above the threshold
                     + PEAK_WEIGHT * increase in the highest worker's strain
                     + MOVE_COST * number of workers moved ) / 100
Because each term is an increase, the rewards of a whole shift add up to
    -(total excess at the end + PEAK_WEIGHT * final peak strain + MOVE_COST * total moves) / 100
(plus a constant from the starting state), i.e. exactly the shift-level objective.
The /100 only keeps PPO's value targets near 1.
"""
import itertools

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from ergosense import config
from ergosense.rl.stations import DEFAULT_TRAINED, load_profiles, profile_arrays

PERMUTATIONS = list(itertools.permutations(range(4)))
PEAK_WEIGHT = 0.5          # lambda: how much the single most-strained worker matters
MOVE_COST = 2.0            # strain-equivalent cost of moving one worker (lost productivity)
INVALID_ACTION_PENALTY = 20.0


class RotationEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, profiles=None, trained=None, threshold=None, hours=8, randomize=True):
        super().__init__()
        self.means, self.stds = (np.array(a, dtype=float) for a in profile_arrays(profiles or load_profiles()))
        self.trained = np.array(trained if trained is not None else DEFAULT_TRAINED, dtype=bool)
        self.threshold = float(threshold or config.STRAIN_THRESHOLD)
        self.hours = hours
        self.randomize = randomize                # random start state (training) vs given state
        self.action_space = spaces.Discrete(len(PERMUTATIONS))
        self.observation_space = spaces.Box(0.0, 5.0, shape=(25,), dtype=np.float32)
        self.valid = np.array([self.trained[np.arange(4), list(p)].all() for p in PERMUTATIONS])

    # --- helpers ------------------------------------------------------------------
    def action_masks(self):
        return self.valid.copy()

    def action_for(self, assignment):
        """Action index of an assignment given as a sequence of station indices per worker."""
        return PERMUTATIONS.index(tuple(int(s) for s in assignment))

    def _obs(self):
        one_hot = np.zeros((4, 4))
        one_hot[np.arange(4), self.assignment] = 1.0
        obs = np.concatenate([self.strain / self.threshold, one_hot.ravel(),
                              [(self.hours - self.hour) / self.hours], self.means / 100.0])
        return np.clip(obs, 0.0, 5.0).astype(np.float32)

    def _excess(self):
        return float(np.maximum(self.strain - self.threshold, 0.0).sum())

    def _info(self):
        return {"strain": self.strain.copy(), "assignment": self.assignment.copy(), "hour": self.hour,
                "moves": self.total_moves, "peak": float(self.strain.max()), "excess": self._excess()}

    # --- gym API -------------------------------------------------------------------
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        options = options or {}
        rng = self.np_random
        if self.randomize:
            self.strain = rng.uniform(0, 40, size=4)
            self.sensitivity = rng.uniform(0.85, 1.20, size=4)
            self.assignment = np.array(PERMUTATIONS[rng.choice(np.flatnonzero(self.valid))])
        else:
            self.strain = np.zeros(4)
            self.sensitivity = np.ones(4)
            self.assignment = np.arange(4)
        if "strain" in options:
            self.strain = np.array(options["strain"], dtype=float)
        if "sensitivity" in options:
            self.sensitivity = np.array(options["sensitivity"], dtype=float)
        if "assignment" in options:
            self.assignment = np.array(options["assignment"], dtype=int)
        self.hour = int(options.get("hours_elapsed", 0))
        self.noise = options.get("noise", True)
        self.total_moves = 0
        return self._obs(), self._info()

    def step(self, action):
        penalty = 0.0
        if not self.valid[action]:
            action = self.action_for(self.assignment)          # keep the current assignment
            penalty = INVALID_ACTION_PENALTY
        new = np.array(PERMUTATIONS[action])
        moves = int((new != self.assignment).sum())

        # one draw per station every hour, whatever the action (fair comparison of policies)
        noise = self.np_random.normal(0.0, 1.0, size=4)
        added = np.maximum(self.means + (self.stds * noise if self.noise else 0.0), 0.0)

        excess_before, peak_before = self._excess(), self.strain.max()
        self.assignment = new
        self.strain = self.strain + added[new] * self.sensitivity
        self.hour += 1
        self.total_moves += moves

        cost = (self._excess() - excess_before) + PEAK_WEIGHT * (self.strain.max() - peak_before) \
            + MOVE_COST * moves + penalty
        terminated = self.hour >= self.hours
        return self._obs(), -cost / 100.0, terminated, False, self._info()
