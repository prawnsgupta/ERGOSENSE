"""Draws the skeleton and the live REBA panel on the OpenCV window.

Same idea as FitCV's draw_custom_landmarks (bones as lines, joints as dots, skip landmarks with
low visibility), but the whole skeleton is coloured by the current REBA risk level.
"""
import cv2

# BGR colours, the same palette the web dashboard uses
RISK_COLORS = {
    "negligible": (68, 158, 46),     # green
    "low": (0, 184, 201),            # yellow
    "medium": (0, 140, 240),         # orange
    "high": (49, 49, 224),           # red
    "very_high": (181, 54, 156),     # purple
}
WAITING_COLOR = (180, 180, 180)


def draw_skeleton(image, landmarks, connections, color):
    """landmarks: (33, 4) normalised image coordinates [x, y, z, visibility]."""
    h, w = image.shape[:2]
    for a, b in connections:
        if landmarks[a, 3] < 0.5 or landmarks[b, 3] < 0.5:
            continue
        pa = (int(landmarks[a, 0] * w), int(landmarks[a, 1] * h))
        pb = (int(landmarks[b, 0] * w), int(landmarks[b, 1] * h))
        cv2.line(image, pa, pb, color, 3)
    for x, y, _, vis in landmarks:
        if vis >= 0.5:
            cv2.circle(image, (int(x * w), int(y * h)), 4, (255, 255, 255), -1)


def draw_panel(image, info, status_lines):
    """Translucent panel with the score, risk level, posture and REBA segment scores."""
    overlay = image.copy()
    cv2.rectangle(overlay, (0, 0), (image.shape[1], 118), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.6, image, 0.4, 0, image)

    if info is None:
        cv2.putText(image, "Waiting for a person in view...", (12, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    WAITING_COLOR, 2, cv2.LINE_AA)
    else:
        color = RISK_COLORS[info["risk_level"]]
        cv2.putText(image, f"REBA {info['reba_score']}", (12, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.3, color, 3,
                    cv2.LINE_AA)
        cv2.putText(image, info["risk_level"].replace("_", " ").upper(), (190, 44), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, color, 2, cv2.LINE_AA)
        cv2.putText(image, f"posture: {info['posture_label']}", (12, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 1, cv2.LINE_AA)
        s = info["segment_scores"]
        seg = (f"trunk {s['trunk']}  neck {s['neck']}  legs {s['legs']}  |  upper arm {s['upper_arm']}  "
               f"lower arm {s['lower_arm']}  wrist {s['wrist']}")
        cv2.putText(image, seg, (12, 98), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1, cv2.LINE_AA)

    for i, line in enumerate(status_lines):
        cv2.putText(image, line, (12, image.shape[0] - 12 - 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (240, 240, 240), 1, cv2.LINE_AA)
