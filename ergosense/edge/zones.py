"""Workstation zones: one camera watches several workers, each standing in their own zone.

A zones file (made with `python -m ergosense.edge.zone_setup`) lists rectangles in the camera
image, one per station, in normalised coordinates (0..1, top-left origin, after mirroring):

    {
      "camera": 0, "mirror": true,
      "zones": [
        {"station_id": "ST01", "rect": [0.02, 0.10, 0.33, 0.98], "load_kg": 10, "coupling": 1},
        {"station_id": "ST02", "rect": [0.35, 0.10, 0.66, 0.98], "load_kg": 0,  "coupling": 0}
      ]
    }

Identity without faces: whoever stands in a zone is treated as the worker the backend has
assigned to that station (exactly like a one-camera-per-station setup). A person's position is
their hip midpoint, which stays inside the zone while they bend, reach or squat. If two people
are in one zone, the larger one (closest to the camera) is scored; anyone outside every zone
(a passer-by, a supervisor) is ignored.
"""
import json
from pathlib import Path

import numpy as np

L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 11, 12, 23, 24


def load_zones(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    zones = data.get("zones", [])
    if not zones:
        raise SystemExit(f"{path} has no zones - draw them with: python -m ergosense.edge.zone_setup")
    seen = set()
    for z in zones:
        x1, y1, x2, y2 = z["rect"]
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise SystemExit(f"zone {z['station_id']}: rect must be [x1, y1, x2, y2] with 0 <= x1 < x2 <= 1")
        if z["station_id"] in seen:
            raise SystemExit(f"station {z['station_id']} has two zones in {path}")
        seen.add(z["station_id"])
        z.setdefault("load_kg", 0.0)
        z.setdefault("coupling", 0)
    return data


def anchor(image_landmarks, min_vis=0.5):
    """Where a person is: hip midpoint, else shoulder midpoint, else centre of the visible points."""
    lm = np.asarray(image_landmarks)
    for a, b in ((L_HIP, R_HIP), (L_SHOULDER, R_SHOULDER)):
        if lm[a, 3] >= min_vis and lm[b, 3] >= min_vis:
            return (lm[a, :2] + lm[b, :2]) / 2
    visible = lm[lm[:, 3] >= min_vis]
    pts = visible if len(visible) else lm
    return pts[:, :2].mean(axis=0)


def body_area(image_landmarks, min_vis=0.5):
    """Bounding-box area of the visible landmarks (bigger = closer to the camera)."""
    lm = np.asarray(image_landmarks)
    visible = lm[lm[:, 3] >= min_vis]
    pts = visible if len(visible) >= 2 else lm
    span = pts[:, :2].max(axis=0) - pts[:, :2].min(axis=0)
    return float(span[0] * span[1])


def inside(point, rect):
    x1, y1, x2, y2 = rect
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


def assign_people(people_image_landmarks, zones):
    """Map each zone to at most one detected person. Returns {station_id: person_index}.

    People are considered from largest to smallest, so when two stand in the same zone the one
    closest to the camera wins; each person belongs to at most one zone.
    """
    order = sorted(range(len(people_image_landmarks)), key=lambda i: -body_area(people_image_landmarks[i]))
    taken = {}
    for i in order:
        p = anchor(people_image_landmarks[i])
        for z in zones:
            if z["station_id"] not in taken and inside(p, z["rect"]):
                taken[z["station_id"]] = i
                break
    return taken
