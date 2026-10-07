"""Unit tests for the REBA module using hand-constructed landmark sets.

Expected ranges were worked out by hand from the REBA tables (see comments in each test).
"""
import pytest

from ergosense.edge import reba
from ergosense.edge.reba import ScoreSmoother, StaticPostureTracker, assess, compute_angles
from ergosense.tests.pose_factory import make_pose


# --- tables -----------------------------------------------------------------

def test_table_spot_checks():
    assert reba.table_a(1, 1, 1) == 1
    assert reba.table_a(3, 2, 2) == 5
    assert reba.table_a(5, 3, 4) == 9
    assert reba.table_b(1, 1, 1) == 1
    assert reba.table_b(3, 2, 1) == 4
    assert reba.table_b(6, 2, 3) == 9
    assert reba.table_c(1, 1) == 1
    assert reba.table_c(4, 4) == 4
    assert reba.table_c(6, 1) == 6
    assert reba.table_c(12, 12) == 12
    assert reba.table_c(15, 15) == 12          # scores above 12 are clamped


def test_risk_levels_and_load():
    assert [reba.risk_level(s) for s in (1, 2, 3, 4, 7, 8, 10, 11, 15)] == [
        "negligible", "low", "low", "medium", "medium", "high", "high", "very_high", "very_high"]
    assert reba.load_force_score(4) == 0
    assert reba.load_force_score(5) == 1
    assert reba.load_force_score(10) == 1
    assert reba.load_force_score(12) == 2
    assert reba.load_force_score(12, shock=True) == 3


# --- geometry ---------------------------------------------------------------

def test_angles_are_recovered_from_constructed_pose():
    pose = make_pose(trunk_flexion=45, neck_flexion=25, shoulder_flexion=(60, 30),
                     shoulder_abduction=(0, 0), elbow_flexion=(80, 80), knee_flexion=(40, 40),
                     wrist_flexion=(20, 0))
    a = compute_angles(pose)
    assert a.trunk_flexion == pytest.approx(45, abs=1)
    assert a.neck_flexion == pytest.approx(25, abs=1)
    assert a.arms["left"]["elevation"] == pytest.approx(60, abs=1)
    assert a.arms["right"]["elevation"] == pytest.approx(30, abs=1)
    assert a.arms["left"]["elbow_flexion"] == pytest.approx(80, abs=1)
    assert a.arms["left"]["wrist_deviation"] == pytest.approx(20, abs=1)
    assert a.knee_flexion == pytest.approx(40, abs=1)
    assert a.trunk_twist == pytest.approx(0, abs=1)


def test_twist_and_side_bend_are_measured():
    a = compute_angles(make_pose(trunk_twist=35, trunk_side_bend=25))
    assert a.trunk_twist == pytest.approx(35, abs=2)
    assert a.trunk_side_bend == pytest.approx(25, abs=2)


# --- the three required scenarios -------------------------------------------

def test_neutral_standing_is_negligible_or_low():
    # trunk 1, neck 1, legs 1 -> A=1; upper arm 1, lower arm 2 (elbow almost straight), wrist 1 -> B=1
    # Table C(1, 1) = 1
    r = assess(make_pose())
    assert r.posture_label == "neutral"
    assert 1 <= r.score <= 3
    assert r.risk_level in ("negligible", "low")
    assert (r.segments["trunk"], r.segments["neck"], r.segments["legs"]) == (1, 1, 1)
    assert r.segments["upper_arm"] == 1


def test_deep_bend_is_at_least_medium():
    # Trunk flexed 90 deg, arms hanging towards the floor (80 deg relative to the trunk) and the
    # head lifted to look ahead (neck extended 30 deg relative to the trunk), as people really bend.
    # trunk 4, neck 2, legs 1 -> A = Table A(4,2,1) = 5; upper arm 3, lower arm 2, wrist 1 -> B = 4
    # Table C(5, 4) = 5 -> medium
    pose = make_pose(trunk_flexion=90, neck_flexion=-30, shoulder_flexion=(80, 80), knee_flexion=(20, 20))
    r = assess(pose)
    assert r.posture_label == "bending"
    assert r.segments["trunk"] == 4
    assert r.segments["neck"] == 2
    assert 4 <= r.score <= 10
    assert r.risk_level in ("medium", "high")

    # The same posture while lifting 15 kg: A = 5 + 2 = 7 -> Table C(7, 4) = 8 -> high.
    heavy = assess(pose, load_kg=15)
    assert heavy.segments["load_force"] == 2
    assert heavy.score > r.score
    assert heavy.risk_level == "high"


def test_load_free_bend_with_neutral_neck_matches_table():
    # REBA itself rates this mildly: A = Table A(4,1,1) = 3, B = 4, Table C(3, 4) = 3 (low).
    # The label still flags the bend so the dashboard can show it.
    r = assess(make_pose(trunk_flexion=90, shoulder_flexion=(80, 80), knee_flexion=(20, 20)))
    assert r.posture_label == "bending"
    assert r.score == 3


def test_deep_bend_with_bent_knees_and_twist_is_high():
    # trunk 4+1 (twist), neck 1, legs 1+1 (knees 45) -> A = Table A(5,1,2) = 6, +2 load = 8
    # B = 4 -> Table C(8, 4) = 9 -> high
    pose = make_pose(trunk_flexion=90, trunk_twist=30, shoulder_flexion=(80, 80), knee_flexion=(45, 45))
    r = assess(pose, load_kg=12)
    assert r.score >= 8
    assert r.risk_level in ("high", "very_high")


def test_overhead_reach():
    # Arms straight up (170 deg), looking up (neck extension).
    # trunk 1, neck 2, legs 1 -> A=1; upper arm 4, lower arm 2, wrist 1 -> B=5; Table C(1, 5) = 3
    pose = make_pose(shoulder_flexion=(170, 170), shoulder_abduction=(0, 0), neck_flexion=-15)
    r = assess(pose)
    assert r.posture_label == "overhead_reach"
    assert r.segments["upper_arm"] == 4
    assert r.segments["neck"] == 2
    assert 2 <= r.score <= 7

    # If the assessor also marks the shoulders as raised, the upper arm becomes 5 -> B = 7 -> C = 4.
    raised = assess(pose, shoulder_raised=True)
    assert raised.score >= 4
    assert raised.risk_level == "medium"


# --- other labels -------------------------------------------------------------

def test_squatting_label_and_leg_score():
    r = assess(make_pose(trunk_flexion=30, knee_flexion=(100, 100)))
    assert r.posture_label == "squatting"
    assert r.segments["legs"] == 3                 # bilateral (1) + knees > 60 deg (+2)


def test_twisting_label_and_trunk_adjustment():
    r = assess(make_pose(trunk_twist=35))
    assert r.posture_label == "twisting"
    assert r.segments["trunk"] == 2                # upright (1) + twisted (+1)


def test_one_legged_stance_raises_leg_score():
    r = assess(make_pose(raise_left_foot=0.2))
    assert r.segments["legs"] >= 2


def test_invisible_arm_is_ignored():
    # Right arm raised but not visible: only the left (hanging) arm should be scored.
    pose = make_pose(shoulder_flexion=(0, 170), visibility=(1.0, 0.1))
    r = assess(pose)
    assert r.side == "left"
    assert r.segments["upper_arm"] == 1


# --- smoothing ---------------------------------------------------------------

def test_single_noisy_frame_does_not_change_smoothed_score():
    smoother = ScoreSmoother(window=15)
    neutral = assess(make_pose())
    spike = assess(make_pose(trunk_flexion=90, shoulder_flexion=(80, 80)), load_kg=15)
    for _ in range(10):
        smoother.update(neutral)
    out = smoother.update(spike)
    assert out.score == neutral.score
    assert out.posture_label == "neutral"


def test_sustained_change_does_change_smoothed_score():
    smoother = ScoreSmoother(window=15)
    neutral = assess(make_pose())
    bend = assess(make_pose(trunk_flexion=90, shoulder_flexion=(80, 80)))
    for _ in range(15):
        smoother.update(neutral)
    for _ in range(8):                               # more than half the window
        out = smoother.update(bend)
    assert out.score == bend.score
    assert out.posture_label == "bending"


def test_static_posture_adds_activity_point():
    tracker = StaticPostureTracker(hold_seconds=60)
    assert tracker.update("bending", now=0.0) == 0
    assert tracker.update("bending", now=59.0) == 0
    assert tracker.update("bending", now=61.0) == 1
    assert tracker.update("neutral", now=62.0) == 0
    assert tracker.update("neutral", now=200.0) == 0     # neutral never counts


def test_image_coordinates_are_rescaled():
    pose = make_pose(trunk_flexion=40)[:, :3]
    squashed = pose.copy()
    squashed[:, 0] /= 640 / 480                       # what MediaPipe image coords look like
    squashed[:, 2] /= 640 / 480
    restored = reba.image_to_metric(squashed, 640, 480)
    assert compute_angles(restored).trunk_flexion == pytest.approx(40, abs=0.5)
