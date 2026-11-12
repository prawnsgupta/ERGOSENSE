"""Scoring process: landmarks -> REBA -> smoothing -> one event per interval.

Consumes frames_q (from capture), produces events_q (to the publisher) and display_q (back to
the capture window). Scoring runs in its own process so a slow frame never stalls the camera.
"""
import logging
import queue
import time

from ergosense.edge.proc import child_init, put_drop_oldest
from ergosense.edge.reba import ScoreSmoother, StaticPostureTracker, assess
from ergosense.events import make_event

log = logging.getLogger("edge.scoring")


def run_scoring(frames_q, events_q, display_q, settings, stop_event):
    """Process target. settings: station_id, worker_id, source, interval, demo_speed,
    smooth_window, load_kg, coupling."""
    child_init()
    smoother = ScoreSmoother(window=settings["smooth_window"])
    tracker = StaticPostureTracker()
    interval = settings["interval"]
    last_emit = None
    seq = 0

    while True:
        try:
            item = frames_q.get(timeout=0.5)
        except queue.Empty:
            if stop_event.is_set():
                break
            continue
        if item is None:                                 # capture finished
            break

        now = item["t"]
        result = assess(item["points"], load_kg=settings["load_kg"], coupling=settings["coupling"],
                        activity=tracker.activity(now))
        smoothed = smoother.update(result)
        tracker.update(smoothed.posture_label, now)
        put_drop_oldest(display_q, smoothed.to_event_fields())

        if last_emit is None:
            last_emit = now
            continue
        if now - last_emit >= interval:
            # cap the covered time so a gap with nobody in view is not counted as work
            covered = min(now - last_emit, 2 * interval) * settings["demo_speed"]
            seq += 1
            event = make_event(settings["station_id"], settings["worker_id"], smoothed.to_event_fields(),
                               covered, settings["source"], seq)
            events_q.put(event, timeout=10)              # blocking put: events must not be dropped
            last_emit = now

    events_q.put(None)
    log.info("scoring stopped after %d events", seq)


def run_multi_scoring(frames_q, events_q, display_q, settings, stop_event):
    """Process target for --zones: independent smoothing, timers and events for every station zone."""
    child_init()
    zones = {z["station_id"]: z for z in settings["zones"]}
    state = {}                   # station -> {"smoother", "tracker", "last_emit", "last_seen"}
    interval, seq, infos = settings["interval"], 0, {}

    while True:
        try:
            item = frames_q.get(timeout=0.5)
        except queue.Empty:
            if stop_event.is_set():
                break
            continue
        if item is None:
            break
        now = item["t"]
        for sid, points in item["stations"].items():
            st = state.get(sid)
            if st is None or now - st["last_seen"] > 2.0:      # nobody there for a while: start fresh
                st = state[sid] = {"smoother": ScoreSmoother(window=settings["smooth_window"]),
                                   "tracker": StaticPostureTracker(), "last_emit": now, "last_seen": now}
            st["last_seen"] = now
            z = zones[sid]
            result = assess(points, load_kg=z["load_kg"], coupling=z["coupling"],
                            activity=st["tracker"].activity(now))
            smoothed = st["smoother"].update(result)
            st["tracker"].update(smoothed.posture_label, now)
            infos[sid] = smoothed.to_event_fields()
            if now - st["last_emit"] >= interval:
                covered = min(now - st["last_emit"], 2 * interval) * settings["demo_speed"]
                seq += 1
                events_q.put(make_event(sid, None, infos[sid], covered, settings["source"], seq), timeout=10)
                st["last_emit"] = now
        for sid in list(infos):                                 # forget stations nobody stands at
            if sid not in item["stations"] and now - state[sid]["last_seen"] > 2.0:
                infos.pop(sid)
        put_drop_oldest(display_q, dict(infos))

    events_q.put(None)
    log.info("multi-person scoring stopped after %d events", seq)
