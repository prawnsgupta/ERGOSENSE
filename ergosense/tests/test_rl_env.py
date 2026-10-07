"""Checks on the rotation environment and the baseline policies."""
import numpy as np
from gymnasium.utils.env_checker import check_env

from ergosense.rl.env import MOVE_COST, PEAK_WEIGHT, PERMUTATIONS, RotationEnv
from ergosense.rl.policies import Greedy, NoRotation, RoundRobin, run_episode
from ergosense.rl.stations import DEFAULT_PROFILES


def make_env(**kw):
    return RotationEnv(profiles=DEFAULT_PROFILES, threshold=400, **kw)


def test_passes_gymnasium_checker():
    check_env(make_env(), skip_render_check=True)


def test_action_space_is_all_permutations_and_mask_respects_training():
    env = make_env()
    assert len(PERMUTATIONS) == 24
    env.reset(seed=0)
    for a in np.flatnonzero(env.action_masks()):
        perm = PERMUTATIONS[a]
        assert all(env.trained[w, perm[w]] for w in range(4))
    assert 0 < env.action_masks().sum() < 24     # some assignments are ruled out by training


def test_episode_lasts_one_shift():
    env = make_env()
    env.reset(seed=1)
    steps, done = 0, False
    while not done:
        _, _, done, _, _ = env.step(env.action_for(env.assignment))
        steps += 1
    assert steps == 8


def test_rewards_add_up_to_the_shift_objective():
    env = make_env()
    obs, info = env.reset(seed=3)
    start_excess, start_peak = info["excess"], info["peak"]
    policy, total, done = RoundRobin(), 0.0, False
    while not done:
        obs, reward, done, _, info = env.step(policy.act(env, obs))
        total += reward
    objective = (info["excess"] - start_excess) + PEAK_WEIGHT * (info["peak"] - start_peak) + MOVE_COST * info["moves"]
    assert np.isclose(total, -objective / 100)


def test_same_seed_gives_same_shift():
    env = make_env()
    a = run_episode(env, Greedy(), seed=42)
    b = run_episode(env, Greedy(), seed=42)
    assert np.allclose(a["strain"], b["strain"])


def test_baselines_only_choose_valid_assignments():
    env = make_env()
    for policy in (NoRotation(), RoundRobin(), Greedy()):
        obs, _ = env.reset(seed=7)
        done = False
        while not done:
            action = policy.act(env, obs)
            assert env.action_masks()[action]
            obs, _, done, _, _ = env.step(action)


def test_greedy_moves_the_most_strained_worker_to_lighter_work():
    env = make_env(randomize=False)
    # W04 (index 3) is the most strained and works at S4, the heaviest station.
    obs, _ = env.reset(options={"strain": [50, 60, 70, 300], "assignment": [0, 1, 2, 3]})
    new = PERMUTATIONS[Greedy().act(env, obs)]
    assert new[3] != 3                            # W04 left the loading bay
    assert env.means[new[3]] < env.means[3]


def test_rotation_beats_no_rotation_on_peak_strain():
    env = make_env()
    seeds = range(100, 160)
    none = np.mean([run_episode(env, NoRotation(), seed=s)["peak"] for s in seeds])
    greedy = np.mean([run_episode(env, Greedy(), seed=s)["peak"] for s in seeds])
    assert greedy < none - 100
