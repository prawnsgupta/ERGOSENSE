"""Event contract validation and the edge's durable outbox (no broker or database needed)."""
import json

import pytest

from ergosense.edge.outbox import Outbox
from ergosense.events import InvalidEvent, decode, encode, make_event

REBA_FIELDS = {"reba_score": 7, "risk_level": "medium", "posture_label": "bending",
               "segment_scores": {"trunk": 4}, "angles": {"trunk_flexion": 70.0}}


def test_event_round_trip():
    event = make_event("S4", None, REBA_FIELDS, 1.0, "webcam", seq=1)
    decoded = decode(encode(event))
    assert decoded["event_id"] == event["event_id"]
    assert decoded["ts"].tzinfo is not None
    assert decoded["worker_id"] is None
    assert len(encode(event)) < 1000            # events must stay small


@pytest.mark.parametrize("change", [
    {"reba_score": 16}, {"reba_score": "7"}, {"risk_level": "extreme"}, {"event_id": "not-a-uuid"},
    {"timestamp": "yesterday"}, {"duration_s": -1}, {"source": "satellite"},
])
def test_invalid_events_are_rejected(change):
    event = make_event("S4", None, REBA_FIELDS, 1.0, "webcam", seq=1)
    event.update(change)
    with pytest.raises(InvalidEvent):
        decode(json.dumps(event).encode())


def test_garbage_payload_is_rejected():
    with pytest.raises(InvalidEvent):
        decode(b"\xff\x00 not json")


def test_outbox_is_fifo_and_survives_restart(tmp_path):
    path = tmp_path / "outbox.db"
    box = Outbox(path)
    for i in range(5):
        box.append(f"id-{i}", "ergosense/events/S1", f"payload-{i}".encode())
    box.append("id-2", "ergosense/events/S1", b"duplicate")      # same event id is ignored
    assert len(box) == 5

    first_two = box.peek(0, 2)
    assert [row[2] for row in first_two] == [b"payload-0", b"payload-1"]
    box.remove(first_two[0][0])                                   # broker acknowledged event 0
    box.close()

    reopened = Outbox(path)                                       # e.g. after a crash / reboot
    assert len(reopened) == 4
    assert [row[2] for row in reopened.peek(0, 10)] == [f"payload-{i}".encode() for i in range(1, 5)]
    reopened.close()
