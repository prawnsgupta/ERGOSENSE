"""REBA (Rapid Entire Body Assessment) scoring from MediaPipe Pose landmarks.

    33 landmarks -> joint angles -> REBA segment scores -> Tables A, B, C -> score 1-15 -> risk level

Also provides a rule-based posture label and a smoother so that one noisy frame does not
raise an alert. Like FitCV's features.py this module only needs numpy, so it can be unit
tested without a camera.

Reference: Hignett, S. & McAtamney, L. (2000). Rapid Entire Body Assessment (REBA).
Applied Ergonomics, 31(2), 201-205.

Coordinate convention (same for MediaPipe world and image landmarks): x to the right of the
image, y DOWN, z away from the camera. World landmarks are in metres with the origin between
the hips. Image landmarks must be rescaled first (see image_to_metric).
"""
import math
from collections import Counter, deque
from dataclasses import dataclass, field, replace

import numpy as np

from features import calculate_angle  # FitCV: angle at vertex b for three 3-D points

# MediaPipe Pose landmark indices used here
L_EAR, R_EAR = 7, 8
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_PINKY, R_PINKY = 17, 18
L_INDEX, R_INDEX = 19, 20
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28

SIDES = {
    "left": dict(shoulder=L_SHOULDER, elbow=L_ELBOW, wrist=L_WRIST, pinky=L_PINKY, index=L_INDEX,
                 hip=L_HIP, knee=L_KNEE, ankle=L_ANKLE),
    "right": dict(shoulder=R_SHOULDER, elbow=R_ELBOW, wrist=R_WRIST, pinky=R_PINKY, index=R_INDEX,
                  hip=R_HIP, knee=R_KNEE, ankle=R_ANKLE),
}

UP = np.array([0.0, -1.0, 0.0])   # y points down in MediaPipe, so "up" is -y

# Thresholds for the REBA "+1 if twisted / side-bent / abducted" adjustments. The REBA sheet
# leaves these to the assessor's judgement; webcam pose is noisy, so we use moderate values.
TWIST_DEG = 20.0
SIDE_BEND_DEG = 15.0
NECK_TWIST_DEG = 25.0
NECK_EXTENSION_DEG = 5.0          # neck counts as "in extension" beyond this many degrees
# The ear sits in front of the shoulder joint even with a neutral head, so the ear-shoulder line
# leans forward of the trunk line. On FitCV's upright side-view recordings (bicep curls) the
# median lean was 16 degrees, so we subtract 15 degrees to call that posture "0 degrees".
NECK_NEUTRAL_OFFSET_DEG = 15.0
ABDUCTION_DEG = 30.0
WRIST_DEVIATION_DEG = 15.0
FOOT_RAISE_RATIO = 0.12           # ankle height difference / leg length => one-legged stance
OVERHEAD_RATIO = 0.10             # wrist above shoulder by this fraction of torso length
# In a side view the left-right body axis points along the camera's depth axis, MediaPipe's
# weakest estimate. On FitCV's side-view recordings upright poses showed a false 25-40 degree
# "side bend" in most frames, so sideways bending is ignored when the view is mostly side-on.
SIDE_VIEW_DEPTH_SHARE = 0.7

RISK_LEVELS = ("negligible", "low", "medium", "high", "very_high")

# ---------------------------------------------------------------------------
# REBA tables (indices are 1-based scores, so we subtract 1 when looking up)
# ---------------------------------------------------------------------------

# TABLE_A[trunk][neck][legs]   trunk 1-5, neck 1-3, legs 1-4
TABLE_A = [
    [[1, 2, 3, 4], [1, 2, 3, 4], [3, 3, 5, 6]],
    [[2, 3, 4, 5], [3, 4, 5, 6], [4, 5, 6, 7]],
    [[2, 4, 5, 6], [4, 5, 6, 7], [5, 6, 7, 8]],
    [[3, 5, 6, 7], [5, 6, 7, 8], [6, 7, 8, 9]],
    [[4, 6, 7, 8], [6, 7, 8, 9], [7, 8, 9, 9]],
]

# TABLE_B[upper_arm][lower_arm][wrist]   upper arm 1-6, lower arm 1-2, wrist 1-3
TABLE_B = [
    [[1, 2, 2], [1, 2, 3]],
    [[1, 2, 3], [2, 3, 4]],
    [[3, 4, 5], [4, 5, 5]],
    [[4, 5, 5], [5, 6, 7]],
    [[6, 7, 8], [7, 8, 8]],
    [[7, 8, 8], [8, 9, 9]],
]

# TABLE_C[score_a][score_b]   both 1-12
TABLE_C = [
    [1, 1, 1, 2, 3, 3, 4, 5, 6, 7, 7, 7],
    [1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 7, 8],
    [2, 3, 3, 3, 4, 5, 6, 7, 7, 8, 8, 8],
    [3, 4, 4, 4, 5, 6, 7, 8, 8, 9, 9, 9],
    [4, 4, 4, 5, 6, 7, 8, 8, 9, 9, 9, 9],
    [6, 6, 6, 7, 8, 8, 9, 9, 10, 10, 10, 10],
    [7, 7, 7, 8, 9, 9, 9, 10, 10, 11, 11, 11],
    [8, 8, 8, 9, 10, 10, 10, 10, 10, 11, 11, 11],
    [9, 9, 9, 10, 10, 10, 11, 11, 11, 12, 12, 12],
    [10, 10, 10, 11, 11, 11, 11, 12, 12, 12, 12, 12],
    [11, 11, 11, 11, 12, 12, 12, 12, 12, 12, 12, 12],
    [12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12],
]


def table_a(trunk, neck, legs):
    return TABLE_A[trunk - 1][neck - 1][legs - 1]


def table_b(upper_arm, lower_arm, wrist):
    return TABLE_B[upper_arm - 1][lower_arm - 1][wrist - 1]


def table_c(score_a, score_b):
    return TABLE_C[min(score_a, 12) - 1][min(score_b, 12) - 1]


def load_force_score(load_kg, shock=False):
    """0 for < 5 kg, 1 for 5-10 kg, 2 for > 10 kg; +1 for shock or rapid build-up of force."""
    score = 0 if load_kg < 5 else (1 if load_kg <= 10 else 2)
    return score + (1 if shock else 0)


def risk_level(score):
    """Map a final REBA score (1-15) to the standard action level."""
    if score <= 1:
        return "negligible"
    if score <= 3:
        return "low"
    if score <= 7:
        return "medium"
    if score <= 10:
        return "high"
    return "very_high"


# ---------------------------------------------------------------------------
# Segment scores from angles (each follows one box of the REBA worksheet)
# ---------------------------------------------------------------------------

def trunk_score(flexion, twisted_or_side_bent):
    """flexion > 0 is forward bending, < 0 is extension (leaning back)."""
    if abs(flexion) <= 5:
        score = 1                      # upright (5 degree tolerance for pose noise)
    elif flexion < 0:
        score = 2 if flexion >= -20 else 3
    elif flexion <= 20:
        score = 2
    elif flexion <= 60:
        score = 3
    else:
        score = 4
    return score + (1 if twisted_or_side_bent else 0)


def neck_score(flexion, twisted_or_side_bent):
    score = 2 if (flexion > 20 or flexion < -NECK_EXTENSION_DEG) else 1
    return score + (1 if twisted_or_side_bent else 0)


def legs_score(knee_flexion, unilateral):
    score = 2 if unilateral else 1
    if knee_flexion > 60:
        score += 2
    elif knee_flexion >= 30:
        score += 1
    return score


def upper_arm_score(elevation, extended, abducted, shoulder_raised=False, supported=False):
    """elevation = angle between upper arm and the trunk's downward axis (0 = hanging)."""
    if extended:
        score = 2 if elevation > 20 else 1
    elif elevation <= 20:
        score = 1
    elif elevation <= 45:
        score = 2
    elif elevation <= 90:
        score = 3
    else:
        score = 4
    score += (1 if abducted else 0) + (1 if shoulder_raised else 0) - (1 if supported else 0)
    return int(np.clip(score, 1, 6))


def lower_arm_score(elbow_flexion):
    return 1 if 60 <= elbow_flexion <= 100 else 2


def wrist_score(deviation, twisted=False):
    return (1 if deviation <= WRIST_DEVIATION_DEG else 2) + (1 if twisted else 0)


# ---------------------------------------------------------------------------
# Geometry: joint angles from landmarks
# ---------------------------------------------------------------------------

def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else np.zeros_like(v)


def _project(v, unit_normal):
    """Project v onto the plane whose normal is unit_normal."""
    return v - np.dot(v, unit_normal) * unit_normal


def _angle_between(u, v):
    cos = np.dot(_unit(u), _unit(v))
    return math.degrees(math.acos(float(np.clip(cos, -1.0, 1.0))))


def _asin_deg(x):
    return math.degrees(math.asin(float(np.clip(x, -1.0, 1.0))))


def image_to_metric(points, width=640, height=480):
    """Rescale normalised image landmarks (x/width, y/height) so all axes share one unit.

    MediaPipe's image z uses roughly the same scale as x, so it gets the same factor.
    """
    out = np.array(points, dtype=float).copy()
    out[:, 0] *= width / height
    out[:, 2] *= width / height
    return out


@dataclass
class PostureAngles:
    trunk_flexion: float
    trunk_side_bend: float
    trunk_twist: float
    neck_flexion: float
    neck_side_bend: float
    neck_twist: float
    knee_flexion: float          # weight-bearing knee(s), REBA uses the worse one
    unilateral: bool
    wrist_height: float          # max over both wrists of (wrist above shoulder) / torso length
    arms: dict = field(default_factory=dict)   # per side: elevation, extended, abduction, elbow, wrist
    visibility: dict = field(default_factory=dict)


def compute_angles(points):
    """Joint angles (degrees) from a (33, 3) or (33, 4) landmark array.

    A body frame is built from the hips and shoulders, so angles do not depend on where the
    camera is, as long as the camera is roughly level (y axis close to gravity).
    """
    pts = np.asarray(points, dtype=float)
    p = pts[:, :3]
    vis = pts[:, 3] if pts.shape[1] > 3 else np.ones(len(pts))

    mid_hip = (p[L_HIP] + p[R_HIP]) / 2
    mid_sh = (p[L_SHOULDER] + p[R_SHOULDER]) / 2
    trunk = mid_sh - mid_hip
    trunk_u = _unit(trunk)
    torso_len = max(np.linalg.norm(trunk), 1e-6)
    hip_axis = p[L_HIP] - p[R_HIP]
    sh_axis = p[L_SHOULDER] - p[R_SHOULDER]

    # Gravity frame at the pelvis: horizontal "left" and "forward" of the person.
    lat_h = _unit(_project(hip_axis, UP))
    fwd_h = _unit(np.cross(lat_h, UP))

    # Trunk flexion is measured in the sagittal plane; side bend is the angle out of it.
    trunk_flexion = math.degrees(math.atan2(np.dot(trunk, fwd_h), np.dot(trunk, UP)))
    trunk_side_bend = abs(_asin_deg(np.dot(trunk_u, lat_h)))
    trunk_twist = _angle_between(_project(sh_axis, trunk_u), _project(hip_axis, trunk_u))

    # Body frame attached to the trunk (used for the neck and the arms).
    lat_b = _unit(_project(hip_axis + sh_axis, trunk_u))
    ant_b = _unit(np.cross(lat_b, trunk_u))

    ear_mid = (p[L_EAR] + p[R_EAR]) / 2
    neck = ear_mid - mid_sh
    neck_flexion = math.degrees(math.atan2(np.dot(neck, ant_b), np.dot(neck, trunk_u))) - NECK_NEUTRAL_OFFSET_DEG
    neck_side_bend = abs(_asin_deg(np.dot(_unit(neck), lat_b)))
    neck_twist = _angle_between(_project(p[L_EAR] - p[R_EAR], trunk_u), _project(sh_axis, trunk_u))
    if abs(hip_axis[2]) / (abs(hip_axis[0]) + abs(hip_axis[2]) + 1e-9) > SIDE_VIEW_DEPTH_SHARE:
        trunk_side_bend = neck_side_bend = 0.0

    arms, visibility = {}, {}
    wrist_height = -math.inf
    for side, j in SIDES.items():
        arm = p[j["elbow"]] - p[j["shoulder"]]
        elevation = _angle_between(arm, -trunk_u)
        hand = (p[j["index"]] + p[j["pinky"]]) / 2
        arms[side] = {
            "elevation": elevation,
            # arm behind the trunk line counts as extension
            "extended": bool(np.dot(_unit(arm), ant_b) < 0 and elevation > 20),
            "abduction": abs(_asin_deg(np.dot(_unit(arm), lat_b))),
            "elbow_flexion": 180.0 - calculate_angle(p[j["shoulder"]], p[j["elbow"]], p[j["wrist"]]),
            "wrist_deviation": 180.0 - calculate_angle(p[j["elbow"]], p[j["wrist"]], hand),
        }
        visibility[side] = float(np.mean(vis[[j["shoulder"], j["elbow"], j["wrist"]]]))
        wrist_height = max(wrist_height, float(np.dot(p[j["wrist"]] - p[j["shoulder"]], UP)) / torso_len)

    knees = {s: 180.0 - calculate_angle(p[j["hip"]], p[j["knee"]], p[j["ankle"]]) for s, j in SIDES.items()}
    leg_len = (np.linalg.norm(p[L_HIP] - p[L_ANKLE]) + np.linalg.norm(p[R_HIP] - p[R_ANKLE])) / 2
    foot_raise = float(np.dot(p[L_ANKLE] - p[R_ANKLE], UP))       # > 0 means left foot is higher
    unilateral = abs(foot_raise) > FOOT_RAISE_RATIO * max(leg_len, 1e-6)
    if unilateral:
        # only the supporting leg's knee counts
        knee_flexion = knees["right"] if foot_raise > 0 else knees["left"]
    else:
        knee_flexion = max(knees.values())

    return PostureAngles(
        trunk_flexion=trunk_flexion, trunk_side_bend=trunk_side_bend, trunk_twist=trunk_twist,
        neck_flexion=neck_flexion, neck_side_bend=neck_side_bend, neck_twist=neck_twist,
        knee_flexion=knee_flexion, unilateral=bool(unilateral), wrist_height=wrist_height,
        arms=arms, visibility=visibility,
    )


# ---------------------------------------------------------------------------
# Posture labels
# ---------------------------------------------------------------------------

# "leaning" (moderate forward flexion) is our addition to the required labels: without it a
# 20-60 degree lean with a load can score REBA "high" yet be labelled "neutral".
LABEL_PRIORITY = ("bending", "overhead_reach", "squatting", "twisting", "leaning")


def posture_flags(angles):
    """All rule-based posture labels that apply to these angles."""
    flags = []
    if angles.trunk_flexion > 60:
        flags.append("bending")
    if angles.wrist_height > OVERHEAD_RATIO:
        flags.append("overhead_reach")
    if angles.knee_flexion > 60 and not angles.unilateral:
        flags.append("squatting")
    if angles.trunk_twist > TWIST_DEG:
        flags.append("twisting")
    if 20 < angles.trunk_flexion <= 60:
        flags.append("leaning")
    return flags


def primary_label(flags):
    for label in LABEL_PRIORITY:
        if label in flags:
            return label
    return "neutral"


# ---------------------------------------------------------------------------
# Full assessment
# ---------------------------------------------------------------------------

@dataclass
class RebaResult:
    score: int
    risk_level: str
    posture_label: str
    segments: dict
    angles: dict
    flags: list
    side: str            # arm side that produced the (worse) Group B score

    def to_event_fields(self):
        """The compact subset that goes into the MQTT event."""
        return {
            "reba_score": self.score,
            "risk_level": self.risk_level,
            "posture_label": self.posture_label,
            "segment_scores": self.segments,
            "angles": self.angles,
        }


def assess(points, **options):
    """Score one frame of landmarks with REBA (see score_angles for the options)."""
    return score_angles(compute_angles(points), **options)


def score_angles(a, load_kg=0.0, shock=False, coupling=0, activity=0,
                 shoulder_raised=False, arm_supported=False, wrist_twisted=False):
    """Apply the REBA worksheet to a set of joint angles.

    load_kg, coupling (0 good .. 3 unacceptable) and activity (0-3) cannot be seen by a webcam,
    so they are passed in by the caller (command-line options in the edge pipeline).
    """
    trunk =trunk_score(a.trunk_flexion, a.trunk_twist > TWIST_DEG or a.trunk_side_bend > SIDE_BEND_DEG)
    neck = neck_score(a.neck_flexion, a.neck_twist > NECK_TWIST_DEG or a.neck_side_bend > SIDE_BEND_DEG)
    legs = legs_score(a.knee_flexion, a.unilateral)
    load = load_force_score(load_kg, shock)
    score_a = table_a(trunk, neck, legs) + load

    # Score both arms and keep the worse one; if one arm is clearly not visible, ignore it.
    sides = [s for s in ("left", "right") if a.visibility[s] >= 0.5] or ["left", "right"]
    best = None
    for side in sides:
        arm = a.arms[side]
        ua = upper_arm_score(arm["elevation"], arm["extended"], arm["abduction"] > ABDUCTION_DEG,
                             shoulder_raised, arm_supported)
        la = lower_arm_score(arm["elbow_flexion"])
        wr = wrist_score(arm["wrist_deviation"], wrist_twisted)
        tb = table_b(ua, la, wr)
        if best is None or tb > best[0]:
            best = (tb, side, ua, la, wr)
    tb, side, ua, la, wr = best
    score_b = tb + coupling

    tc = table_c(score_a, score_b)
    score = int(min(15, tc + activity))

    flags = posture_flags(a)
    segments = {
        "trunk": trunk, "neck": neck, "legs": legs, "table_a": table_a(trunk, neck, legs),
        "load_force": load, "score_a": score_a,
        "upper_arm": ua, "lower_arm": la, "wrist": wr, "table_b": tb,
        "coupling": coupling, "score_b": score_b,
        "table_c": tc, "activity": activity,
    }
    arm = a.arms[side]
    angles = {
        "trunk_flexion": round(a.trunk_flexion, 1),
        "trunk_twist": round(a.trunk_twist, 1),
        "trunk_side_bend": round(a.trunk_side_bend, 1),
        "neck_flexion": round(a.neck_flexion, 1),
        "upper_arm": round(arm["elevation"], 1),
        "elbow_flexion": round(arm["elbow_flexion"], 1),
        "wrist_deviation": round(arm["wrist_deviation"], 1),
        "knee_flexion": round(a.knee_flexion, 1),
    }
    return RebaResult(score=score, risk_level=risk_level(score), posture_label=primary_label(flags),
                      segments=segments, angles=angles, flags=flags, side=side)


# ---------------------------------------------------------------------------
# Temporal helpers
# ---------------------------------------------------------------------------

class ScoreSmoother:
    """Median filter over the last `window` frames, majority vote for the posture label.

    A single bad frame (e.g. a mis-detected arm) cannot move the median, so it cannot
    trigger an alert. The returned result is the most recent frame whose score equals
    the median, so its segment scores stay consistent with the reported score.
    """

    def __init__(self, window=15):
        self.history = deque(maxlen=window)

    def update(self, result):
        self.history.append(result)
        scores = sorted(r.score for r in self.history)
        median = scores[len(scores) // 2]
        representative = next(r for r in reversed(self.history) if r.score == median)
        label = Counter(r.posture_label for r in self.history).most_common(1)[0][0]
        return replace(representative, posture_label=label)

    def reset(self):
        self.history.clear()


class StaticPostureTracker:
    """REBA activity score: +1 if the same non-neutral posture is held for more than 1 minute."""

    def __init__(self, hold_seconds=60.0):
        self.hold_seconds = hold_seconds
        self.label = None
        self.since = None

    def update(self, label, now):
        if label != self.label:
            self.label, self.since = label, now
        return self.activity(now)

    def activity(self, now):
        held = self.label not in (None, "neutral") and now - self.since >= self.hold_seconds
        return 1 if held else 0
