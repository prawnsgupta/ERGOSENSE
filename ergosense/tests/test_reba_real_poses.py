"""REBA on real human poses: FitCV's recorded landmark dataset (image landmarks).

Only the side-view recordings are used; the lateral-raise clips were filmed facing the camera,
where MediaPipe's monocular depth estimate is unreliable (see docs/VIVA_NOTES.md).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ergosense.edge import reba

DATASET = Path(__file__).resolve().parents[2] / "dataset"


def _score_exercise(name, every=6):
    files = sorted((DATASET / name).glob("*.csv"))[::every]
    if not files:
        pytest.skip(f"FitCV dataset for {name} not found")
    scores, trunk, knees, labels = [], [], [], []
    for path in files:
        frames = pd.read_csv(path).drop(columns=["label"]).to_numpy().reshape(-1, 33, 4)
        for frame in frames:
            pts = np.hstack([reba.image_to_metric(frame[:, :3]), frame[:, 3:4]])
            result = reba.assess(pts)
            scores.append(result.score)
            trunk.append(result.angles["trunk_flexion"])
            knees.append(result.angles["knee_flexion"])
            labels.append(result.posture_label)
    return np.array(scores), np.array(trunk), np.array(knees), labels


@pytest.fixture(scope="module")
def recordings():
    return {name: _score_exercise(name) for name in ("BentOverRow", "BicepCurl", "Squat", "Pushup")}


def test_bent_over_row_scores_higher_than_upright_curl(recordings):
    row_scores, row_trunk, _, _ = recordings["BentOverRow"]
    curl_scores, curl_trunk, _, _ = recordings["BicepCurl"]
    assert row_scores.mean() > curl_scores.mean() + 1.5
    assert np.mean(row_trunk > 20) > 0.6           # mostly flexed
    assert np.mean(np.abs(curl_trunk) > 20) < 0.1    # mostly upright


def test_squat_reaches_deep_knee_flexion(recordings):
    _, _, knees, labels = recordings["Squat"]
    assert np.percentile(knees, 90) > 90
    assert "squatting" in labels


def test_pushup_is_labelled_bending(recordings):
    _, _, _, labels = recordings["Pushup"]
    assert labels.count("bending") / len(labels) > 0.5
