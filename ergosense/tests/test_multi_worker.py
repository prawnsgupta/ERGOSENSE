"""Tests for the multi-worker features: live alert rules, camera zones and the large-site planner."""
import numpy as np
import pytest

from ergosense.backend.alerts import AlertEngine, Rule
from ergosense.edge.zones import assign_people
from ergosense.rl import assign


class FakeStore:
    def __init__(self):
        self.rows, self.next_id = {}, 1

    def open_alert(self, **f):
        row = {"alert_id": self.next_id, **f, "resolved_at": None}
        self.rows[self.next_id] = row
        self.next_id += 1
        return row

    def resolve_alert(self, alert_id):
        self.rows[alert_id]["resolved_at"] = "now"
        return self.rows[alert_id]


def event(risk="negligible", score=1, label="neutral", load=0, duration=1.0, worker="W01"):
    return {"worker_id": worker, "station_id": "ST01", "risk_level": risk, "reba_score": score,
            "posture_label": label, "segment_scores": {"load_force": load}, "duration_s": duration}


def strain(total=0.0, worker="W01"):
    return {"shift_id": 1, "worker_id": worker, "cumulative_strain": total}


def engine(store):
    rules = [Rule("very_high_posture", "red", 5), Rule("lifting_while_bent", "red", 3),
             Rule("high_posture_held", "amber", 30), Rule("strain_over_limit", "red", 0, clears=False),
             Rule("strain_near_limit", "amber", 0, clears=False)]
    return AlertEngine(store=store, threshold=400, warn_fraction=0.8, clear_after_s=10, rules=rules)


def test_one_risky_event_does_not_raise_an_alert():
    store = FakeStore()
    eng = engine(store)
    eng.process(event("very_high", 12, "bending"), strain())
    assert store.rows == {}


def test_held_very_high_posture_raises_one_red_alert_then_resolves():
    store = FakeStore()
    eng = engine(store)
    for _ in range(8):
        eng.process(event("very_high", 12, "bending"), strain())
    red = [r for r in store.rows.values() if r["rule"] == "very_high_posture"]
    assert len(red) == 1 and red[0]["level"] == "red"                  # debounced: only one alert
    for _ in range(12):                                                 # 12 s of safe posture
        eng.process(event(), strain())
    assert red[0]["resolved_at"] is not None


def test_lifting_while_bent_and_strain_alerts():
    store = FakeStore()
    eng = engine(store)
    for _ in range(3):
        eng.process(event("high", 9, "bending", load=1), strain(330))
    rules = {r["rule"] for r in store.rows.values()}
    assert "lifting_while_bent" in rules and "strain_near_limit" in rules
    eng.process(event(), strain(401))
    assert "strain_over_limit" in {r["rule"] for r in store.rows.values()}


def test_workers_are_tracked_independently():
    store = FakeStore()
    eng = engine(store)
    for _ in range(6):
        eng.process(event("very_high", 12, "bending", worker="W01"), strain(worker="W01"))
        eng.process(event(worker="W02"), strain(worker="W02"))
    assert {r["worker_id"] for r in store.rows.values()} == {"W01"}


def _person(cx, size=0.3):
    lm = np.zeros((33, 4), dtype=np.float32)
    lm[:, 3] = 1.0
    lm[:, 0] = cx + np.linspace(-size / 4, size / 4, 33)
    lm[:, 1] = np.linspace(0.5 - size, 0.5 + size, 33)
    lm[[23, 24], 0], lm[[23, 24], 1] = cx, 0.55                          # hips
    return lm


def test_people_are_mapped_to_their_zone_and_passers_by_ignored():
    zones = [{"station_id": "ST01", "rect": [0.0, 0.0, 0.33, 1.0]},
             {"station_id": "ST02", "rect": [0.34, 0.0, 0.66, 1.0]}]
    people = [_person(0.5), _person(0.15), _person(0.9)]               # 0.9 is outside every zone
    assert assign_people(people, zones) == {"ST02": 0, "ST01": 1}


def test_closest_person_wins_a_shared_zone():
    zones = [{"station_id": "ST01", "rect": [0.0, 0.0, 1.0, 1.0]}]
    far, near = _person(0.3, size=0.15), _person(0.6, size=0.4)
    assert assign_people([far, near], zones) == {"ST01": 1}


def test_large_site_planner_respects_training_and_spreads_strain():
    workers = [f"W{i:02d}" for i in range(1, 13)]
    stations = [f"ST{i:02d}" for i in range(1, 13)]
    assignment = dict(zip(workers, stations))
    rates = {s: {"mean": m, "std": 0.15 * m, "source": "test"}
             for s, m in zip(stations, [80, 70, 60, 55, 30, 30, 30, 30, 14, 14, 14, 14])}
    trained = [(w, s) for w in workers for s in stations if s in (assignment[w], "ST09", "ST10", "ST11", "ST12")
               or w in ("W09", "W10", "W11", "W12")]
    strain = {w: (300.0 if i < 4 else 50.0) for i, w in enumerate(workers)}
    result = assign.suggest(strain, assignment, trained, 4.0, rates, threshold=400)
    allowed = set(trained)
    assert all((w, s) in allowed for w, s in result["assignments"].items())
    assert len(set(result["assignments"].values())) == 12
    assert result["moves"] > 0                                           # the strained workers leave heavy stations
    p = result["projection"]
    assert p["peak_with_plan"] < p["peak_no_rotation"]


def test_planner_needs_at_least_two_workers():
    with pytest.raises(ValueError):
        assign.suggest({"W01": 0}, {"W01": "ST01"}, [("W01", "ST01")], 0, {"ST01": {"mean": 10, "std": 1}})
