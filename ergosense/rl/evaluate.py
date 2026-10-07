"""Compare rotation policies on 500 simulated shifts with fixed seeds.

    python -m ergosense.rl.evaluate

Every policy plays the SAME 500 shifts: episode i always uses seed 10_000 + i, and the
environment draws the same random numbers whatever the policy does, so differences come from
the decisions only (common random numbers). Writes to ergosense/rl/results/:
    episodes.csv            one row per (policy, episode)
    summary.csv             mean and std per policy
    policy_comparison.png   bar chart of the four metrics
"""
import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from ergosense import config  # noqa: E402
from ergosense.rl.env import RotationEnv  # noqa: E402
from ergosense.rl.policies import Greedy, NoRotation, PPOPolicy, RoundRobin, run_episode  # noqa: E402

RESULTS_DIR = config.PACKAGE_DIR / "rl" / "results"
LABELS = {"no_rotation": "No rotation", "round_robin": "Round-robin", "greedy": "Greedy", "ppo": "PPO (ours)"}
METRICS = [
    ("peak_strain", "Final peak worker strain", "strain points"),
    ("workers_over_limit", "Workers over the safe limit", "workers per shift"),
    ("total_strain", "Total strain (all workers)", "strain points"),
    ("moves", "Workers moved", "moves per shift"),
]

# Chart colours: PPO highlighted in blue, baselines neutral (names on the axis carry identity)
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
HIGHLIGHT, BASELINE = "#2a78d6", "#a8a7a2"


def evaluate(episodes=500):
    env = RotationEnv()
    policies = [NoRotation(), RoundRobin(), Greedy()]
    if config.RL_MODEL_PATH.exists():
        policies.append(PPOPolicy())
    else:
        print(f"no trained model at {config.RL_MODEL_PATH} - run python -m ergosense.rl.train first")

    rows = []
    for policy in policies:
        for i in range(episodes):
            info = run_episode(env, policy, seed=10_000 + i)
            rows.append({
                "policy": policy.name, "episode": i,
                "peak_strain": info["peak"],
                "workers_over_limit": int((info["strain"] > env.threshold).sum()),
                "total_strain": float(info["strain"].sum()),
                "total_excess": info["excess"],
                "moves": info["moves"],
                "reward": info["reward"],
            })
    df = pd.DataFrame(rows)
    order = [p.name for p in policies]
    summary = df.groupby("policy").agg(["mean", "std"]).drop(columns="episode").loc[order]
    summary.columns = [f"{m}_{s}" for m, s in summary.columns]
    summary["shifts_with_any_worker_over_limit_pct"] = (
        df.assign(any_over=df.workers_over_limit > 0).groupby("policy")["any_over"].mean().loc[order] * 100)
    return df, summary, order, env.threshold


def plot(summary, order, threshold, path):
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK})
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.4), facecolor=SURFACE)
    names = [LABELS[p] for p in order]
    y = np.arange(len(order))[::-1]
    for ax, (metric, title, unit) in zip(axes.ravel(), METRICS):
        means = summary[f"{metric}_mean"].to_numpy()
        stds = summary[f"{metric}_std"].to_numpy()
        colors = [HIGHLIGHT if p == "ppo" else BASELINE for p in order]
        ax.set_facecolor(SURFACE)
        ax.barh(y, means, height=0.62, color=colors, edgecolor=SURFACE, linewidth=2)
        # counts and strains cannot be negative, so the lower whisker stops at zero
        ax.errorbar(means, y, xerr=[np.minimum(stds, means), stds], fmt="none", ecolor=INK_2, elinewidth=1,
                    capsize=3)
        for yi, m, s in zip(y, means, stds):
            ax.text(m + s + means.max() * 0.02, yi, f"{m:.2f}" if means.max() < 10 else f"{m:.0f}",
                    va="center", fontsize=9, color=INK)
        if metric == "peak_strain":
            ax.axvline(threshold, color=INK_2, linestyle="--", linewidth=1)
            ax.text(threshold + 8, y.max() + 0.62, f"safe limit {threshold:.0f}", ha="left", va="center",
                    fontsize=8, color=INK_2)
            ax.set_ylim(-0.5, y.max() + 0.9)
        ax.set_yticks(y, names)
        ax.set_title(title, loc="left", fontsize=11, fontweight="bold", color=INK)
        ax.set_xlabel(f"{unit} (mean ± std)")
        ax.set_xlim(0, (means + stds).max() * 1.22)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(axis="y", length=0)
    fig.suptitle("Job rotation policies on 500 simulated 8-hour shifts (same seeds for every policy)",
                 x=0.01, ha="left", fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes", type=int, default=500)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    df, summary, order, threshold = evaluate(args.episodes)
    df.to_csv(RESULTS_DIR / "episodes.csv", index=False)
    summary.round(2).to_csv(RESULTS_DIR / "summary.csv")
    plot(summary, order, threshold, RESULTS_DIR / "policy_comparison.png")

    pd.set_option("display.width", 160)
    cols = ["peak_strain_mean", "peak_strain_std", "workers_over_limit_mean", "workers_over_limit_std",
            "total_strain_mean", "total_strain_std", "moves_mean", "shifts_with_any_worker_over_limit_pct"]
    print(summary[cols].round(2).to_string())
    print(f"\nsaved episodes.csv, summary.csv and policy_comparison.png in {RESULTS_DIR}")


if __name__ == "__main__":
    main()
