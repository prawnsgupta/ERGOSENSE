"""Draws the edge window: the camera view with a risk-coloured skeleton, plus a side panel.

    +-------------------------------+------------------+
    |                               |  ERGOSENSE  S4   |
    |   camera + skeleton           |  [ 7 ] MEDIUM    |
    |   (bones of risky body parts  |  1 ---|----- 15  |
    |    drawn thicker)             |  trunk ####..    |
    |                               |  angles, history |
    +-------------------------------+------------------+
    | status: MQTT, sent/pending, FPS, bandwidth        |

Same idea as FitCV's draw_custom_landmarks (bones as lines, joints as dots, skip landmarks with
low visibility), but coloured by the REBA risk level. Colours always come with a text label.
"""
from collections import deque

import cv2
import numpy as np

# BGR colours, the same status palette the web dashboard uses
RISK_COLORS = {
    "negligible": (12, 163, 12),     # good      #0ca30c
    "low": (25, 178, 250),           # warning   #fab219
    "medium": (90, 131, 236),        # serious   #ec835a
    "high": (59, 59, 208),           # critical  #d03b3b
    "very_high": (38, 34, 155),      # critical, darker  #9b2226
}
WAITING_COLOR = (170, 170, 170)
BG = (28, 26, 25)
CARD = (42, 40, 38)
INK = (245, 245, 245)
INK_2 = (185, 185, 180)
MUTED = (120, 120, 115)
FONT = cv2.FONT_HERSHEY_SIMPLEX

VIEW_H = 540
PANEL_W = 360
FOOTER_H = 34

# (label, key, worst possible score) for the segment bars
SEGMENTS = [("Trunk", "trunk", 5), ("Neck", "neck", 3), ("Legs", "legs", 4),
            ("Upper arm", "upper_arm", 6), ("Lower arm", "lower_arm", 2), ("Wrist", "wrist", 3)]
# bones that belong to each segment, so risky body parts can be drawn thicker
SEGMENT_BONES = {
    "trunk": [(11, 23), (12, 24), (11, 12), (23, 24)],
    "neck": [(11, 12)],
    "legs": [(23, 25), (25, 27), (24, 26), (26, 28)],
    "upper_arm": [(11, 13), (12, 14)],
    "lower_arm": [(13, 15), (14, 16)],
    "wrist": [(15, 17), (15, 19), (16, 18), (16, 20)],
}
ANGLES = [("Trunk flexion", "trunk_flexion"), ("Neck flexion", "neck_flexion"), ("Upper arm", "upper_arm"),
          ("Elbow", "elbow_flexion"), ("Knee", "knee_flexion")]

_history = deque(maxlen=90)          # recent smoothed scores for the sparkline


def _text(img, s, org, scale=0.5, color=INK, thick=1):
    cv2.putText(img, s, org, FONT, scale, color, thick, cv2.LINE_AA)


def _text_w(s, scale, thick=1):
    return cv2.getTextSize(s, FONT, scale, thick)[0][0]


def _risk_of(score):
    return ("negligible" if score <= 1 else "low" if score <= 3 else "medium" if score <= 7
            else "high" if score <= 10 else "very_high")


def draw_skeleton(image, landmarks, connections, color, highlight=()):
    """landmarks: (33, 4) normalised image coordinates [x, y, z, visibility]."""
    h, w = image.shape[:2]
    hot = {tuple(sorted(b)) for b in highlight}
    for a, b in connections:
        if landmarks[a, 3] < 0.5 or landmarks[b, 3] < 0.5:
            continue
        pa = (int(landmarks[a, 0] * w), int(landmarks[a, 1] * h))
        pb = (int(landmarks[b, 0] * w), int(landmarks[b, 1] * h))
        if tuple(sorted((a, b))) in hot:
            cv2.line(image, pa, pb, (20, 20, 20), 11, cv2.LINE_AA)      # dark outline marks risky parts
            cv2.line(image, pa, pb, color, 7, cv2.LINE_AA)
        else:
            cv2.line(image, pa, pb, color, 3, cv2.LINE_AA)
    for x, y, _, vis in landmarks:
        if vis >= 0.5:
            cv2.circle(image, (int(x * w), int(y * h)), 4, (255, 255, 255), -1, cv2.LINE_AA)


def risky_bones(info):
    """Bones of the body parts scoring in the upper half of their range."""
    if not info:
        return []
    s = info["segment_scores"]
    bones = []
    for _, key, worst in SEGMENTS:
        if s.get(key, 0) >= max(2, worst * 0.6):
            bones += SEGMENT_BONES[key]
    return bones


def _gauge(panel, x, y, w, score):
    """REBA 1-15 scale with the action-level bands and a marker at the current score."""
    bands = [(1, 1, "negligible"), (2, 3, "low"), (4, 7, "medium"), (8, 10, "high"), (11, 15, "very_high")]
    unit = w / 15
    for lo, hi, level in bands:
        x0, x1 = int(x + (lo - 1) * unit), int(x + hi * unit) - 2
        cv2.rectangle(panel, (x0, y), (x1, y + 8), RISK_COLORS[level], -1)
    if score:
        mx = int(x + (score - 0.5) * unit)
        cv2.fillPoly(panel, [np.array([(mx - 7, y - 10), (mx + 7, y - 10), (mx, y - 1)])], INK)
    for v in (1, 4, 8, 11, 15):
        _text(panel, str(v), (int(x + (v - 0.5) * unit) - 4, y + 24), 0.38, MUTED)


def _sparkline(panel, x, y, w, h):
    cv2.rectangle(panel, (x, y), (x + w, y + h), CARD, -1)
    for v in (4, 8, 11):                                     # action-level gridlines
        gy = int(y + h - (v - 1) / 14 * h)
        cv2.line(panel, (x, gy), (x + w, gy), (60, 58, 56), 1)
    if len(_history) < 2:
        return
    pts = [(int(x + i * w / (_history.maxlen - 1)), int(y + h - (s - 1) / 14 * h)) for i, s in enumerate(_history)]
    for (p, q), s in zip(zip(pts, pts[1:]), list(_history)[1:]):
        cv2.line(panel, p, q, RISK_COLORS[_risk_of(s)], 2, cv2.LINE_AA)


def _panel(info, station):
    panel = np.full((VIEW_H, PANEL_W, 3), BG, np.uint8)
    pad = 20
    _text(panel, "ERGOSENSE", (pad, 34), 0.75, INK, 2)
    tag = f"Station {station}"
    _text(panel, tag, (PANEL_W - pad - _text_w(tag, 0.5), 32), 0.5, INK_2)
    cv2.line(panel, (pad, 48), (PANEL_W - pad, 48), (60, 58, 56), 1)

    if info is None:
        _text(panel, "Waiting for a person", (pad, 110), 0.7, WAITING_COLOR, 2)
        _text(panel, "Stand side-on, 2-3 m away,", (pad, 145), 0.5, INK_2)
        _text(panel, "whole body in view.", (pad, 168), 0.5, INK_2)
        return panel

    score, level = info["reba_score"], info["risk_level"]
    color = RISK_COLORS[level]

    # score badge + risk label
    cv2.rectangle(panel, (pad, 64), (pad + 104, 150), color, -1)
    num = str(score)
    cv2.putText(panel, num, (pad + 52 - _text_w(num, 2.0, 4) // 2, 130), FONT, 2.0, (255, 255, 255), 4, cv2.LINE_AA)
    _text(panel, "REBA SCORE", (pad + 122, 84), 0.42, MUTED)
    _text(panel, level.replace("_", " ").upper(), (pad + 122, 114), 0.8, color, 2)
    _text(panel, info["posture_label"].replace("_", " "), (pad + 122, 142), 0.6, INK, 1)

    _gauge(panel, pad, 182, PANEL_W - 2 * pad, score)

    # segment bars
    y = 236
    _text(panel, "BODY PARTS (score / worst)", (pad, y), 0.42, MUTED)
    seg = info["segment_scores"]
    bar_x, bar_w = pad + 92, PANEL_W - 2 * pad - 92 - 34
    for i, (label, key, worst) in enumerate(SEGMENTS):
        yy = y + 22 + i * 24
        v = seg.get(key, 0)
        _text(panel, label, (pad, yy + 9), 0.45, INK_2)
        cv2.rectangle(panel, (bar_x, yy), (bar_x + bar_w, yy + 10), CARD, -1)
        fill = int(bar_w * v / worst)
        cv2.rectangle(panel, (bar_x, yy), (bar_x + fill, yy + 10), RISK_COLORS[_risk_of(v * 15 // worst)], -1)
        _text(panel, f"{v}/{worst}", (bar_x + bar_w + 6, yy + 10), 0.42, INK)

    # key angles in two columns
    y = 414
    _text(panel, "ANGLES", (pad, y), 0.42, MUTED)
    angles = info.get("angles") or {}
    for i, (label, key) in enumerate(ANGLES):
        cx = pad + (i % 2) * 165
        cy = y + 20 + (i // 2) * 20
        val = angles.get(key)
        _text(panel, f"{label}: {val:.0f} deg" if val is not None else f"{label}: -", (cx, cy), 0.42, INK_2)

    # history
    _text(panel, "LAST FEW SECONDS", (pad, 494), 0.42, MUTED)
    _sparkline(panel, pad, 502, PANEL_W - 2 * pad, 28)
    return panel


def _footer(width, status):
    bar = np.full((FOOTER_H, width, 3), (18, 17, 16), np.uint8)
    ok = status["connected"]
    cv2.circle(bar, (16, FOOTER_H // 2), 6, RISK_COLORS["negligible"] if ok else RISK_COLORS["high"], -1)
    link = "MQTT connected" if ok else "MQTT OFFLINE - buffering to disk"
    parts = [link, f"sent {status['sent']}", f"pending {status['pending']}", f"{status['fps']:.0f} FPS"]
    if status["avg_event_bytes"]:
        parts.append(f"event {status['avg_event_bytes']:.0f} B vs frame {status['raw_frame_bytes'] / 1024:.0f} KB "
                     f"- no video leaves this device")
    line = "   |   ".join(parts)
    while len(parts) > 1 and _text_w(line, 0.45) > width - 130:      # drop details that do not fit
        parts.pop()
        line = "   |   ".join(parts)
    _text(bar, line, (30, 22), 0.45, INK if ok else (120, 140, 255))
    _text(bar, "Q = quit", (width - 80, 22), 0.45, MUTED)
    return bar


def compose(frame, landmarks, info, connections, status, station):
    """Build the full window image: camera view + side panel + status footer."""
    if info:
        _history.append(info["reba_score"])
    color = RISK_COLORS[info["risk_level"]] if info else WAITING_COLOR
    if landmarks is not None:
        draw_skeleton(frame, landmarks, connections, color, risky_bones(info))
    h, w = frame.shape[:2]
    view = cv2.resize(frame, (int(w * VIEW_H / h), VIEW_H))
    cv2.rectangle(view, (0, 0), (view.shape[1] - 1, VIEW_H - 1), color, 4)     # risk-coloured frame border
    top = np.hstack([view, _panel(info, station)])
    return np.vstack([top, _footer(top.shape[1], status)])
