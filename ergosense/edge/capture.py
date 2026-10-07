"""Capture process: produces landmark frames for the scoring process.

Two sources:
  * webcam - OpenCV camera -> MediaPipe Pose (same legacy API and settings as FitCV's main.py);
             a recorded video file goes through exactly the same code (--video)
  * replay - FitCV's recorded landmark CSVs (dataset/<Exercise>/*.csv), real human poses
             played back at the recording frame rate, so the pipeline can run without a camera

This process also owns the OpenCV window: frames never leave this process. The scoring
process sends back only the small REBA result to draw on the window.
"""
import logging
import time

import cv2
import numpy as np
import pandas as pd

from ergosense.edge.overlay import RISK_COLORS, WAITING_COLOR, draw_panel, draw_skeleton
from ergosense.edge.proc import child_init, latest, put_drop_oldest
from ergosense.edge.reba import image_to_metric

log = logging.getLogger("edge.capture")
WINDOW = "ERGOSENSE edge - press Q to quit"


def _status_lines(status, fps):
    link = "MQTT connected" if status["connected"].value else "MQTT OFFLINE - buffering"
    return [
        f"{link}  |  sent {status['sent'].value}  pending {status['pending'].value}",
        f"{fps:4.1f} FPS  |  event ~{status['avg_event_bytes'].value:.0f} B vs raw frame "
        f"{status['raw_frame_bytes'].value / 1024:.0f} KB",
    ]


def _show(image, image_landmarks, info, status, fps, stop_event):
    color = RISK_COLORS[info["risk_level"]] if info else WAITING_COLOR
    if image_landmarks is not None:
        draw_skeleton(image, image_landmarks, _connections(), color)
    draw_panel(image, info, _status_lines(status, fps))
    cv2.imshow(WINDOW, image)
    if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
        stop_event.set()


_CONNECTIONS = None


def _connections():
    """The 35 MediaPipe Pose bones (the list FitCV's config.POSE_CONNECTIONS also uses)."""
    global _CONNECTIONS
    if _CONNECTIONS is None:
        import mediapipe as mp
        _CONNECTIONS = list(mp.solutions.pose.POSE_CONNECTIONS)
    return _CONNECTIONS


def run_webcam(camera_id, frames_q, display_q, status, stop_event, show_window=True):
    """Process target: webcam (int index) or video file (path) -> MediaPipe -> world landmarks."""
    child_init()
    import mediapipe as mp

    pose = mp.solutions.pose.Pose(min_detection_confidence=0.5, min_tracking_confidence=0.5)
    is_file = isinstance(camera_id, str)
    cap = cv2.VideoCapture(camera_id)
    if not cap.isOpened():
        log.error("cannot open %s (try --camera 1, or close other apps using the webcam)", camera_id)
        stop_event.set()
        frames_q.put(None)
        return
    frame_period = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 30) if is_file else 0.0
    log.info("%s %s opened", "video" if is_file else "camera", camera_id)

    fps, last, n = 0.0, time.time(), 0
    info = None
    while not stop_event.is_set():
        started = time.time()
        ok, frame = cap.read()
        if not ok:
            if is_file:
                log.info("end of video")
                break
            log.warning("camera returned no frame")
            time.sleep(0.05)
            continue
        frame = cv2.flip(frame, 1)                       # mirror view, like FitCV
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        results = pose.process(rgb)
        now = time.time()

        image_landmarks = None
        if results.pose_world_landmarks is not None:
            world = np.array([[lm.x, lm.y, lm.z, lm.visibility] for lm in results.pose_world_landmarks.landmark])
            image_landmarks = np.array([[lm.x, lm.y, lm.z, lm.visibility] for lm in results.pose_landmarks.landmark])
            put_drop_oldest(frames_q, {"t": now, "points": world.astype(np.float32)})

        # bandwidth bookkeeping: what sending video would have cost
        n += 1
        if n % 30 == 1:
            status["raw_frame_bytes"].value = frame.nbytes
            status["jpeg_frame_bytes"].value = len(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])[1])
        fps = 0.9 * fps + 0.1 / max(now - last, 1e-3)
        last = now

        info = latest(display_q) or info
        if show_window:
            _show(frame, image_landmarks, info, status, fps, stop_event)
        if is_file:                                       # play recordings at their real speed
            time.sleep(max(0.0, frame_period - (time.time() - started)))

    cap.release()
    cv2.destroyAllWindows()
    put_drop_oldest(frames_q, None)
    log.info("capture stopped")


def load_replay_frames(paths):
    """Yield (33, 4) image-landmark arrays from FitCV CSV recordings."""
    for path in paths:
        frames = pd.read_csv(path).drop(columns=["label"]).to_numpy(dtype=np.float32)
        for row in frames.reshape(-1, 33, 4):
            yield row


def run_replay(paths, fps, loops, frames_q, display_q, status, stop_event, show_window=True):
    """Process target: play FitCV recordings through the pipeline as if they came from a camera."""
    child_init()
    status["raw_frame_bytes"].value = 640 * 480 * 3      # the recordings came from a 640x480 webcam
    log.info("replaying %d FitCV recordings at %d FPS (%s)", len(paths), fps, "loop" if loops == 0 else f"{loops}x")
    info = None
    loop = 0
    while not stop_event.is_set() and (loops == 0 or loop < loops):
        loop += 1
        for landmarks in load_replay_frames(paths):
            if stop_event.is_set():
                break
            now = time.time()
            # image landmarks: rescale x/z by the aspect ratio so angles are not distorted
            points = np.hstack([image_to_metric(landmarks[:, :3]), landmarks[:, 3:4]])
            put_drop_oldest(frames_q, {"t": now, "points": points})
            info = latest(display_q) or info
            if show_window:
                canvas = np.zeros((480, 640, 3), dtype=np.uint8)
                _show(canvas, landmarks, info, status, fps, stop_event)
            time.sleep(max(0.0, 1.0 / fps - (time.time() - now)))
    if show_window:
        cv2.destroyAllWindows()
    put_drop_oldest(frames_q, None)
    log.info("replay finished")
