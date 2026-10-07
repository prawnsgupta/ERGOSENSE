"""Station risk profiles for the RL simulator: mean and std of strain points added per hour.

The defaults were measured from the station simulator (ergosense/edge/simulate.py, 300
simulated hours per station). `python -m ergosense.rl.calibrate` replaces a station's profile
with real REBA data from the database when enough webcam/replay events exist for it, and
writes the result to station_profiles.json, which is what training and the backend use.
"""
import json

from ergosense import config

STATION_IDS = ["S1", "S2", "S3", "S4"]
WORKER_IDS = ["W01", "W02", "W03", "W04"]

DEFAULT_PROFILES = {
    "S1": {"mean": 13.7, "std": 3.2, "source": "default (station simulator)"},
    "S2": {"mean": 30.3, "std": 5.1, "source": "default (station simulator)"},
    "S3": {"mean": 49.1, "std": 3.8, "source": "default (station simulator)"},
    "S4": {"mean": 77.4, "std": 6.1, "source": "default (station simulator)"},
}

# Which worker is trained for which station (rows W01..W04, columns S1..S4), same as seed.sql.
DEFAULT_TRAINED = [
    [1, 1, 1, 1],
    [1, 1, 0, 1],
    [1, 0, 1, 1],
    [0, 1, 1, 1],
]


def load_profiles(path=None):
    """Station profiles from station_profiles.json, or the defaults if it does not exist."""
    path = path or config.STATION_PROFILES_PATH
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["stations"]
    return DEFAULT_PROFILES


def profile_arrays(profiles):
    """(means, stds) lists in STATION_IDS order."""
    return [profiles[s]["mean"] for s in STATION_IDS], [profiles[s]["std"] for s in STATION_IDS]
