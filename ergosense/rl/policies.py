"""Rotation policies: three hand-written baselines and the trained PPO agent.

Every policy has act(env, obs) -> action index, using the env's current state.
"""
import numpy as np

from ergosense import config
from ergosense.rl.env import PERMUTATIONS


class NoRotation:
    """Everyone stays at the station they started on."""
    name = "no_rotation"

    def act(self, env, obs):
        return env.action_for(env.assignment)


class RoundRobin:
    """Every hour each worker moves on to the next station (S1 -> S2 -> S3 -> S4 -> S1).

    When that exact rotation would put someone on a station they are not trained for, we take
    the valid assignment that agrees with it for the most workers (ties: fewest moves).
    """
    name = "round_robin"

    def act(self, env, obs):
        target = (env.assignment + 1) % 4
        best, best_key = None, None
        for a in np.flatnonzero(env.valid):
            perm = np.array(PERMUTATIONS[a])
            key = ((perm == target).sum(), -(perm != env.assignment).sum())
            if best_key is None or key > best_key:
                best, best_key = a, key
        return int(best)


class Greedy:
    """Move the most strained worker to the lowest-risk station they are trained for,
    swapping with whoever is there (if that person is trained for the vacated station)."""
    name = "greedy"

    def act(self, env, obs):
        assignment = env.assignment.copy()
        worker = int(np.argmax(env.strain))
        for station in np.argsort(env.means):            # lowest risk first
            if station == assignment[worker]:
                break                                     # already at the best station they can do
            if not env.trained[worker, station]:
                continue
            other = int(np.flatnonzero(assignment == station)[0])
            if env.trained[other, assignment[worker]]:
                assignment[worker], assignment[other] = station, assignment[worker]
                break
        return env.action_for(assignment)


class PPOPolicy:
    """The trained MaskablePPO agent (deterministic: always the most likely valid action)."""
    name = "ppo"

    def __init__(self, path=None):
        from sb3_contrib import MaskablePPO
        self.model = MaskablePPO.load(str(path or config.RL_MODEL_PATH), device="cpu")

    def act(self, env, obs):
        action, _ = self.model.predict(obs, action_masks=env.action_masks(), deterministic=True)
        return int(action)


def run_episode(env, policy, seed=None, options=None):
    """Play one shift. Returns the final info dict plus the total reward."""
    obs, info = env.reset(seed=seed, options=options)
    total_reward, done = 0.0, False
    while not done:
        obs, reward, done, _, info = env.step(policy.act(env, obs))
        total_reward += reward
    info["reward"] = total_reward
    return info
