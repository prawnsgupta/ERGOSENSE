"""Hand-constructed MediaPipe-style world landmarks for unit tests.

make_pose() builds all 33 landmarks in metres (x right, y down, z away from the camera) for a
person facing the camera, from a few intuitive joint angles. Segment lengths are typical
adult proportions; the exact values do not matter because REBA only uses angles.
"""
import math

import numpy as np

from ergosense.edge.reba import NECK_NEUTRAL_OFFSET_DEG

UP =np.array([0.0, -1.0, 0.0])
DOWN = -UP
FWD = np.array([0.0, 0.0, -1.0])     # towards the camera = the person's front
LEFT = np.array([1.0, 0.0, 0.0])     # person's left appears on the image right


def _unit(v):
    return v / np.linalg.norm(v)


def _rotate(v, axis, deg):
    """Rodrigues rotation of v about a unit axis."""
    t = math.radians(deg)
    return v * math.cos(t) + np.cross(axis, v) * math.sin(t) + axis * np.dot(axis, v) * (1 - math.cos(t))


def _perpendicular(direction, preferred, fallback):
    """Unit vector perpendicular to `direction`, as close as possible to `preferred`."""
    perp = preferred - np.dot(preferred, direction) * direction
    if np.linalg.norm(perp) < 1e-6:
        perp = fallback - np.dot(fallback, direction) * direction
    return _unit(perp)


def make_pose(trunk_flexion=0.0, trunk_side_bend=0.0, trunk_twist=0.0, neck_flexion=0.0,
              shoulder_flexion=(0.0, 0.0), shoulder_abduction=(5.0, 5.0), elbow_flexion=(10.0, 10.0),
              wrist_flexion=(0.0, 0.0), knee_flexion=(0.0, 0.0), raise_left_foot=0.0,
              visibility=(1.0, 1.0)):
    """Return a (33, 4) array [x, y, z, visibility]. Tuples are (left, right)."""
    p = np.zeros((33, 3))
    rad = math.radians

    # Pelvis at the origin, hips along the person's left-right axis.
    p[23], p[24] = 0.1 * LEFT, -0.1 * LEFT

    # Trunk: lean forward by trunk_flexion, sideways by trunk_side_bend.
    trunk_u = math.cos(rad(trunk_flexion)) * UP + math.sin(rad(trunk_flexion)) * FWD
    trunk_u = _unit(trunk_u * math.cos(rad(trunk_side_bend)) + LEFT * math.sin(rad(trunk_side_bend)))
    mid_sh = 0.5 * trunk_u
    lat_sh = _unit(_rotate(LEFT - np.dot(LEFT, trunk_u) * trunk_u, trunk_u, trunk_twist))
    ant = _unit(np.cross(lat_sh, trunk_u))
    p[11], p[12] = mid_sh + 0.18 * lat_sh, mid_sh - 0.18 * lat_sh

    # Head: ears slightly in front of the shoulders (anatomical offset), tilted by neck_flexion.
    head = neck_flexion + NECK_NEUTRAL_OFFSET_DEG
    neck_u = _unit(math.cos(rad(head)) * trunk_u + math.sin(rad(head)) * ant)
    face_fwd = _unit(math.cos(rad(head)) * ant - math.sin(rad(head)) * trunk_u)
    ear_mid = mid_sh + 0.22 * neck_u
    p[7], p[8] = ear_mid + 0.075 * lat_sh, ear_mid - 0.075 * lat_sh
    nose = ear_mid + 0.1 * face_fwd
    p[0] = nose
    for i, s in ((1, 1), (2, 1), (3, 1), (4, -1), (5, -1), (6, -1)):      # eyes
        p[i] = nose + 0.03 * neck_u + s * 0.03 * lat_sh
    p[9], p[10] = nose - 0.04 * neck_u + 0.02 * lat_sh, nose - 0.04 * neck_u - 0.02 * lat_sh

    # Arms.
    for k, (sign, sh, el, wr, pk, ix, th) in enumerate(((1, 11, 13, 15, 17, 19, 21), (-1, 12, 14, 16, 18, 20, 22))):
        side_dir = sign * lat_sh
        fl, ab = rad(shoulder_flexion[k]), rad(shoulder_abduction[k])
        arm_u = _unit(-trunk_u * math.cos(fl) * math.cos(ab) + ant * math.sin(fl) * math.cos(ab)
                      + side_dir * math.sin(ab))
        p[el] = p[sh] + 0.30 * arm_u
        perp = _perpendicular(arm_u, ant, trunk_u)
        fore_u = math.cos(rad(elbow_flexion[k])) * arm_u + math.sin(rad(elbow_flexion[k])) * perp
        p[wr] = p[el] + 0.27 * fore_u
        hand_perp = _perpendicular(fore_u, ant, trunk_u)
        hand_u = math.cos(rad(wrist_flexion[k])) * fore_u + math.sin(rad(wrist_flexion[k])) * hand_perp
        p[ix] = p[wr] + 0.09 * hand_u + 0.02 * side_dir
        p[pk] = p[wr] + 0.09 * hand_u - 0.02 * side_dir
        p[th] = p[wr] + 0.05 * hand_u + 0.03 * ant

    # Legs: knee flexion split between hip (thigh forward) and shank (back), like a squat.
    for k, (hip, knee, ankle, heel, toe) in enumerate(((23, 25, 27, 29, 31), (24, 26, 28, 30, 32))):
        half = rad(knee_flexion[k]) / 2
        thigh_u = math.cos(half) * DOWN + math.sin(half) * FWD
        shank_u = math.cos(half) * DOWN - math.sin(half) * FWD
        p[knee] = p[hip] + 0.45 * thigh_u
        p[ankle] = p[knee] + 0.43 * shank_u
        if k == 0 and raise_left_foot:
            p[ankle] = p[ankle] - raise_left_foot * DOWN
        p[heel] = p[ankle] + 0.04 * DOWN - 0.04 * FWD
        p[toe] = p[ankle] + 0.05 * DOWN + 0.15 * FWD

    vis = np.ones((33, 1))
    vis[[11, 13, 15, 17, 19, 21]] = visibility[0]
    vis[[12, 14, 16, 18, 20, 22]] = visibility[1]
    return np.hstack([p, vis])
