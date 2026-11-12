"""Simulated station cameras, for testing and demos without a webcam.

Each station has a posture mix (how often the work involves bending, overhead reaching, ...).
For every event we sample joint angles for one posture and score them with the real REBA
code (reba.score_angles), so a simulated event looks exactly like one from the webcam.

Time is accelerated: with --speed 60 one real second is one minute of work, so an 8-hour
shift takes 8 real minutes. Each event's duration_s says how much work time it covers.

Simulated events carry worker_id = null, like a fixed camera at a station: the backend
attributes them to whoever is assigned to that station. After a rotation is approved the
same station stream automatically counts towards the newly assigned worker.
"""
import logging
import math
import time

import numpy as np

from ergosense.edge import reba
from ergosense.edge.proc import child_init
from ergosense.events import make_event

log = logging.getLogger("edge.simulate")

# Mean and standard deviation (degrees) of each joint angle for each posture.
POSTURE_ANGLES = {
    "neutral": {"trunk": (3, 4), "twist": (5, 4), "neck": (5, 6), "arm": (20, 12), "elbow": (70, 20),
                "wrist": (10, 6), "knee": (8, 5)},
    "bending": {"trunk": (72, 12), "twist": (8, 6), "neck": (-12, 12), "arm": (75, 15), "elbow": (25, 15),
                "wrist": (15, 8), "knee": (25, 15)},
    "overhead_reach": {"trunk": (4, 5), "twist": (8, 6), "neck": (-20, 8), "arm": (125, 20), "elbow": (35, 20),
                       "wrist": (18, 8), "knee": (8, 5)},
    "squatting": {"trunk": (35, 10), "twist": (8, 6), "neck": (10, 8), "arm": (45, 15), "elbow": (60, 20),
                  "wrist": (12, 8), "knee": (100, 15)},
    "twisting": {"trunk": (25, 12), "twist": (35, 8), "neck": (10, 8), "arm": (50, 15), "elbow": (60, 20),
                 "wrist": (15, 8), "knee": (12, 8)},
}

# What the work at each seeded station looks like (see seed.sql for the descriptions).
STATION_MODELS = {
    "S1": {"mix": {"neutral": 0.85, "bending": 0.05, "twisting": 0.10}, "load_kg": (0, 2)},
    "S2": {"mix": {"neutral": 0.55, "bending": 0.20, "twisting": 0.20, "overhead_reach": 0.05}, "load_kg": (1, 6)},
    "S3": {"mix": {"overhead_reach": 0.65, "neutral": 0.15, "twisting": 0.20}, "load_kg": (2, 6)},
    "S4": {"mix": {"bending": 0.50, "squatting": 0.20, "twisting": 0.15, "neutral": 0.15}, "load_kg": (5, 15)},
}


# Site files (sites/*.json) describe each station's work_type; it picks one of the models above.
WORK_TYPE_MODELS = {"inspection": "S1", "packing": "S2", "overhead": "S3", "lifting": "S4"}


def station_models(stations, site=None):
    """Posture model for each simulated station: the demo stations S1-S4, or by work_type from a site file."""
    work_types = {s["station_id"]: s.get("work_type") for s in (site or {}).get("stations", [])}
    models = {}
    for station in stations:
        if station in STATION_MODELS:
            models[station] = STATION_MODELS[station]
        elif work_types.get(station) in WORK_TYPE_MODELS:
            models[station] = STATION_MODELS[WORK_TYPE_MODELS[work_types[station]]]
        else:
            raise SystemExit(f"no posture model for station {station}: pass --site with a work_type for it")
    return models


def sample_angles(rng, posture):
    """Draw one plausible set of joint angles for a posture."""
    g = {name: rng.normal(mean, sd) for name, (mean, sd) in POSTURE_ANGLES[posture].items()}
    arm = float(np.clip(g["arm"], 0, 180))
    arm_info = {
        "elevation": arm,
        "extended": False,
        "abduction": 40.0 if rng.random() < 0.2 else 10.0,
        "elbow_flexion": float(np.clip(g["elbow"], 0, 150)),
        "wrist_deviation": abs(g["wrist"]),
    }
    return reba.PostureAngles(
        trunk_flexion=g["trunk"], trunk_side_bend=abs(rng.normal(5, 5)), trunk_twist=abs(g["twist"]),
        neck_flexion=g["neck"], neck_side_bend=abs(rng.normal(4, 4)), neck_twist=abs(rng.normal(8, 6)),
        knee_flexion=float(np.clip(g["knee"], 0, 150)), unilateral=bool(rng.random() < 0.05),
        wrist_height=-math.cos(math.radians(arm)) * 1.1,     # arm length is about 1.1 torso lengths
        arms={"left": arm_info, "right": arm_info}, visibility={"left": 1.0, "right": 1.0},
    )


def simulate_posture(rng, station_id, intensity=1.0, model=None):
    """One REBA result for a station. intensity > 1 means a busier hour (more risky postures)."""
    model = model or STATION_MODELS[station_id]
    mix = {p: (w if p == "neutral" else w * intensity) for p, w in model["mix"].items()}
    names = list(mix)
    probs = np.array([mix[n] for n in names]) / sum(mix.values())
    posture = names[rng.choice(len(names), p=probs)]
    load = rng.uniform(*model["load_kg"]) if posture != "neutral" else 0.0
    shoulder_raised = posture == "overhead_reach" and rng.random() < 0.7
    return reba.score_angles(sample_angles(rng, posture), load_kg=load, shoulder_raised=shoulder_raised)


def run_simulator(events_q, stations, speed, rate, hours, seed, stop_event, models=None):
    """Process target: publish `rate` events per second per station until `hours` of work."""
    child_init()
    models = models or station_models(stations)
    rng = np.random.default_rng(seed)
    duration_s = speed / rate                       # work time covered by one event
    total_work_s = hours * 3600
    work_s, seq, hour = 0.0, 0, -1
    intensity = {}
    next_tick = time.time()
    log.info("simulating stations %s: %.0f s of work per event, %.1f h shift in %.1f real minutes",
             ",".join(stations), duration_s, hours, total_work_s / speed / 60)

    while not stop_event.is_set() and work_s < total_work_s:
        if int(work_s // 3600) != hour:
            hour = int(work_s // 3600)
            intensity = {s: float(np.clip(rng.normal(1.0, 0.25), 0.5, 1.5)) for s in stations}
            log.info("simulated hour %d/%d  intensity %s", hour + 1, math.ceil(hours),
                     {s: round(v, 2) for s, v in intensity.items()})
        for station in stations:
            result = simulate_posture(rng, station, intensity[station], models[station])
            seq += 1
            events_q.put(make_event(station, None, result.to_event_fields(), duration_s, "simulated", seq))
        work_s += duration_s
        next_tick += 1.0 / rate
        time.sleep(max(0.0, next_tick - time.time()))

    log.info("simulation finished after %.1f h of simulated work (%d events)", work_s / 3600, seq)
    events_q.put(None)
