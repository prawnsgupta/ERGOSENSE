"""Multi-person pose estimation with MediaPipe's PoseLandmarker (Tasks API).

FitCV's legacy `mp.solutions.pose` finds only one person per frame. PoseLandmarker can track
several people at once and still returns, for every person, the same 33 landmarks both in image
coordinates and as 3-D world coordinates in metres - so the REBA code is reused unchanged.

The model file (~9 MB) is not in the repository. It is downloaded once, from Google's official
MediaPipe model storage, the first time a --zones pipeline runs (or with
`python -m ergosense.edge.multipose --download`).
"""
import argparse
import logging
import urllib.request

import numpy as np

from ergosense import config

log = logging.getLogger("edge.multipose")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_full/float16/latest/pose_landmarker_full.task")


def ensure_model(path=None):
    path = path or config.POSE_MODEL_PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading the multi-person pose model to %s ...", path)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(path)
        log.info("model saved (%.1f MB)", path.stat().st_size / 1e6)
    return path


def _arr(landmarks):
    return np.array([[lm.x, lm.y, lm.z, lm.visibility if lm.visibility is not None else 1.0]
                     for lm in landmarks], dtype=np.float32)


class MultiPose:
    """detect(rgb, timestamp_ms) -> list of (image_landmarks (33, 4), world_landmarks (33, 4))."""

    def __init__(self, max_people, model_path=None, min_confidence=0.5):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        self._mp = mp
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(ensure_model(model_path))),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=max_people,
            min_pose_detection_confidence=min_confidence,
            min_pose_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
        )
        self._landmarker = vision.PoseLandmarker.create_from_options(options)
        self._last_ts = -1

    def detect(self, rgb, timestamp_ms):
        timestamp_ms = max(int(timestamp_ms), self._last_ts + 1)     # VIDEO mode needs increasing timestamps
        self._last_ts = timestamp_ms
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        return [(_arr(img), _arr(world)) for img, world in zip(result.pose_landmarks, result.pose_world_landmarks)]

    def close(self):
        self._landmarker.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--download", action="store_true", help="download the model file now")
    if parser.parse_args().download:
        print(ensure_model())
