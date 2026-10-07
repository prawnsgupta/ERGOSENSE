"""The posture event: the only thing the edge sends over the network (never video).

This module is the contract between the edge (publisher) and the backend (subscriber).

    {
      "event_id": "uuid4",            idempotency key - the backend ignores duplicates
      "worker_id": "W04" | null,      null = backend uses whoever is assigned to the station
      "station_id": "S4",
      "timestamp": "2026-10-07T09:15:02.123+00:00",
      "duration_s": 1.0,              exposure time this event covers
      "reba_score": 7, "risk_level": "medium", "posture_label": "bending",
      "segment_scores": {...}, "angles": {...},
      "source": "webcam" | "replay" | "simulated",
      "seq": 42                       per-edge sequence number
    }
"""
import json
import uuid
from datetime import datetime, timezone

from ergosense import config

RISK_LEVELS = ("negligible", "low", "medium", "high", "very_high")
SOURCES = ("webcam", "replay", "simulated")


def topic_for(station_id):
    return f"{config.TOPIC_PREFIX}/{station_id}"


def make_event(station_id, worker_id, reba_fields, duration_s, source, seq, ts=None):
    """Build an event dict. reba_fields comes from RebaResult.to_event_fields()."""
    ts = ts or datetime.now(timezone.utc)
    return {
        "event_id": str(uuid.uuid4()),
        "worker_id": worker_id,
        "station_id": station_id,
        "timestamp": ts.isoformat(timespec="milliseconds"),
        "duration_s": round(float(duration_s), 3),
        **reba_fields,
        "source": source,
        "seq": seq,
    }


def encode(event):
    """Compact JSON bytes (no spaces) - this is what goes over MQTT."""
    return json.dumps(event, separators=(",", ":")).encode("utf-8")


class InvalidEvent(ValueError):
    pass


def decode(payload):
    """Parse and validate an incoming event. Raises InvalidEvent for anything malformed."""
    try:
        event = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidEvent(f"not JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise InvalidEvent("event must be a JSON object")

    for key in ("event_id", "station_id", "timestamp", "reba_score", "risk_level", "posture_label",
                "segment_scores"):
        if key not in event:
            raise InvalidEvent(f"missing field '{key}'")
    try:
        uuid.UUID(str(event["event_id"]))
        event["ts"] = datetime.fromisoformat(event["timestamp"])
    except ValueError as exc:
        raise InvalidEvent(f"bad event_id or timestamp: {exc}") from exc
    if event["ts"].tzinfo is None:
        event["ts"] = event["ts"].replace(tzinfo=timezone.utc)

    score = event["reba_score"]
    if not isinstance(score, int) or not 1 <= score <= 15:
        raise InvalidEvent("reba_score must be an integer 1-15")
    if event["risk_level"] not in RISK_LEVELS:
        raise InvalidEvent(f"unknown risk_level {event['risk_level']!r}")
    if not isinstance(event["segment_scores"], dict):
        raise InvalidEvent("segment_scores must be an object")

    event.setdefault("worker_id", None)
    event["duration_s"] = float(event.get("duration_s", 1.0))
    if event["duration_s"] <= 0 or event["duration_s"] > 3600:
        raise InvalidEvent("duration_s must be in (0, 3600]")
    event["source"] = event.get("source", "webcam")
    if event["source"] not in SOURCES:
        raise InvalidEvent(f"unknown source {event['source']!r}")
    event.setdefault("angles", None)
    event.setdefault("seq", None)
    return event
