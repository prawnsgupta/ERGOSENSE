"""Calibrate station risk profiles from REAL posture events stored in PostgreSQL.

    python -m ergosense.rl.calibrate

Only events from a real pose source count (webcam, or FitCV recordings replayed through the
edge pipeline), never simulated ones. The committed profile for S4 (loading bay) came from:
    python -m ergosense.edge.pipeline --station S4 --replay dataset/BentOverRow --load-kg 8            --replay-fps 90 --interval 0.25 --no-window
(8 kg = the station's typical box weight, entered as REBA's load score like an assessor would). A station with at least MIN_EVENTS such events gets

    mean strain per hour = 60 * time-weighted mean of RISK_WEIGHT[risk_level]

i.e. we assume the recorded movement is representative of an hour of work at that station.
Short recordings cannot show hour-to-hour variation, so its std is taken as 15% of the mean.
Stations without enough real data keep the simulator defaults; the "source" field in
station_profiles.json records which is which.
"""
import json
from datetime import date

import psycopg

from ergosense import config
from ergosense.rl.stations import DEFAULT_PROFILES
from ergosense.strain import RISK_WEIGHT

MIN_EVENTS = 100
CALIBRATED_CV = 0.15


def calibrate(dsn=None):
    weight_case = "CASE risk_level " + " ".join(f"WHEN '{k}' THEN {v}" for k, v in RISK_WEIGHT.items()) + " END"
    with psycopg.connect(dsn or config.DATABASE_URL) as conn:
        rows = conn.execute(
            f"""SELECT station_id, count(*) AS n, sum(duration_s) AS seconds,
                       sum(({weight_case}) * duration_s) / sum(duration_s) AS mean_weight,
                       string_agg(DISTINCT source, ',') AS sources
                FROM posture_events
                WHERE source IN ('webcam', 'replay')
                GROUP BY station_id"""
        ).fetchall()

    profiles = {sid: dict(p) for sid, p in DEFAULT_PROFILES.items()}
    for station_id, n, seconds, mean_weight, sources in rows:
        if station_id not in profiles or n < MIN_EVENTS:
            print(f"{station_id}: only {n} real events (< {MIN_EVENTS}), keeping the default")
            continue
        mean = 60.0 * float(mean_weight)
        profiles[station_id] = {
            "mean": round(mean, 1),
            "std": round(CALIBRATED_CV * mean, 1),
            "source": f"calibrated from {n} real {sources} events, {date.today()}",
        }

    out = {"threshold": config.STRAIN_THRESHOLD, "stations": profiles}
    config.STATION_PROFILES_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    for sid, p in profiles.items():
        print(f"{sid}: {p['mean']:5.1f} +/- {p['std']:4.1f} strain/h   [{p['source']}]")
    print(f"written to {config.STATION_PROFILES_PATH}")
    return profiles


if __name__ == "__main__":
    calibrate()
