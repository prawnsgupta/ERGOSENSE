"""Train the rotation policy with MaskablePPO (PPO + invalid-action masking) on CPU.

    python -m ergosense.rl.train                      # ~500k steps, about 4 minutes on a laptop CPU
    python -m ergosense.rl.train --timesteps 100000   # quicker, slightly weaker policy

Why PPO: a stable, widely used on-policy algorithm that works out of the box with a discrete
action space; the masking variant (sb3-contrib) never wastes samples on assignments that put
a worker on a station they are not trained for.
"""
import argparse
import json
import time

import numpy as np
import torch
from sb3_contrib import MaskablePPO
from stable_baselines3.common.env_util import make_vec_env

from ergosense import config
from ergosense.rl.env import RotationEnv
from ergosense.rl.policies import Greedy, PPOPolicy, run_episode
from ergosense.rl.stations import load_profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timesteps", type=int, default=500_000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    torch.set_num_threads(1)            # tiny network: one thread is faster than many
    profiles = load_profiles()
    print("station profiles (strain points per hour):")
    for sid, p in profiles.items():
        print(f"  {sid}: mean {p['mean']:.1f}, std {p['std']:.1f}  [{p['source']}]")

    vec_env = make_vec_env(RotationEnv, n_envs=8, seed=args.seed, env_kwargs={"profiles": profiles})
    model = MaskablePPO(
        "MlpPolicy", vec_env,
        n_steps=256, batch_size=256, n_epochs=10, learning_rate=3e-4,
        gamma=0.99, gae_lambda=0.95, ent_coef=0.01,
        policy_kwargs={"net_arch": [128, 128]},
        seed=args.seed, verbose=0, device="cpu",
    )

    start = time.time()
    model.learn(total_timesteps=args.timesteps, progress_bar=False)
    minutes = (time.time() - start) / 60
    config.RL_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(config.RL_MODEL_PATH))

    # quick check against the greedy baseline on 200 unseen shifts
    env = RotationEnv(profiles=profiles)
    ppo, greedy = PPOPolicy(), Greedy()
    seeds = range(50_000, 50_200)
    ppo_peak = np.mean([run_episode(env, ppo, seed=s)["peak"] for s in seeds])
    greedy_peak = np.mean([run_episode(env, greedy, seed=s)["peak"] for s in seeds])
    print(f"trained {args.timesteps:,} steps in {minutes:.1f} min -> {config.RL_MODEL_PATH}")
    print(f"mean final peak strain on 200 test shifts: PPO {ppo_peak:.1f} vs greedy {greedy_peak:.1f}")

    meta = {"timesteps": args.timesteps, "train_minutes": round(minutes, 2), "seed": args.seed,
            "threshold": config.STRAIN_THRESHOLD, "profiles": profiles}
    config.RL_MODEL_PATH.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
