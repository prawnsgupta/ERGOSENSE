"""Live safety alerts: watches every stored event and flags dangerous situations per worker.

Rules (hold times are WORK seconds, i.e. the sum of event durations, so they follow --demo-speed):

    rule               level  fires when
    very_high_posture  red    REBA 11-15 (very high risk) held for ALERT_VERY_HIGH_HOLD_S
    lifting_while_bent red    bent or twisted (score >= 8) while the station handles a load, held ALERT_LOAD_HOLD_S
    high_posture_held  amber  REBA 8-15 (high or very high) held for ALERT_HIGH_HOLD_S
    strain_over_limit  red    cumulative shift strain reached the safe limit
    strain_near_limit  amber  cumulative shift strain reached ALERT_STRAIN_WARN x the limit

Debounce: a rule must hold continuously before it fires (one noisy event cannot raise an alert),
there is at most one open alert per worker and rule, and a posture alert resolves only after
its condition has been absent for ALERT_CLEAR_S. Strain alerts stay open for the rest of the
shift (strain never goes down within a shift). Acknowledging is separate from resolving: it
only records that a supervisor has seen the alert.

The engine is plain Python with a small `store` object for persistence, so the rules can be
unit tested without a database (see tests/test_alerts.py).
"""
import logging
import threading
from dataclasses import dataclass, field

from ergosense import config

log = logging.getLogger("backend.alerts")

HIGH = ("high", "very_high")
BENT = ("bending", "twisting")


@dataclass
class Rule:
    name: str
    level: str
    hold_s: float
    clears: bool = True          # posture rules resolve themselves; strain rules do not


def default_rules():
    return [
        Rule("very_high_posture", "red", config.ALERT_VERY_HIGH_HOLD_S),
        Rule("lifting_while_bent", "red", config.ALERT_LOAD_HOLD_S),
        Rule("high_posture_held", "amber", config.ALERT_HIGH_HOLD_S),
        Rule("strain_over_limit", "red", 0.0, clears=False),
        Rule("strain_near_limit", "amber", 0.0, clears=False),
    ]


def conditions(event, strain, threshold, warn_fraction):
    """Which rule conditions are true for this event (a dict rule -> bool)."""
    risk = event["risk_level"]
    load = (event.get("segment_scores") or {}).get("load_force", 0) or 0
    total = float(strain["cumulative_strain"]) if strain else 0.0
    return {
        "very_high_posture": risk == "very_high",
        "lifting_while_bent": event["posture_label"] in BENT and load >= 1 and event["reba_score"] >= 8,
        "high_posture_held": risk in HIGH,
        "strain_over_limit": total >= threshold,
        "strain_near_limit": total >= warn_fraction * threshold,
    }


def message_for(rule, event, strain, threshold, held_s):
    label = event["posture_label"].replace("_", " ")
    if rule == "very_high_posture":
        return f"Very high-risk posture ({label}, REBA {event['reba_score']}) for {held_s:.0f} s"
    if rule == "lifting_while_bent":
        return f"Handling a load while {label} (REBA {event['reba_score']}) for {held_s:.0f} s"
    if rule == "high_posture_held":
        return f"High-risk posture held for {held_s:.0f} s (now {label}, REBA {event['reba_score']})"
    total = float(strain["cumulative_strain"])
    if rule == "strain_over_limit":
        return f"Shift strain {total:.0f} has reached the safe limit of {threshold:.0f}"
    return f"Shift strain {total:.0f} is at {100 * total / threshold:.0f}% of the safe limit {threshold:.0f}"


@dataclass
class _Track:
    run_s: float = 0.0            # how long the condition has held without a break
    clear_s: float = 0.0          # how long it has been absent
    alert: dict | None = None     # the open alert row, if any


@dataclass
class AlertEngine:
    store: object                                  # open_alert(**fields) -> row, resolve_alert(alert_id) -> row
    threshold: float = field(default_factory=lambda: config.STRAIN_THRESHOLD)
    warn_fraction: float = field(default_factory=lambda: config.ALERT_STRAIN_WARN)
    clear_after_s: float = field(default_factory=lambda: config.ALERT_CLEAR_S)
    rules: list = field(default_factory=default_rules)
    on_change: object = None                       # called with ("opened" | "resolved", alert row)

    def __post_init__(self):
        self._tracks = {}                          # (shift_id, worker_id, rule) -> _Track
        self._lock = threading.Lock()              # events arrive on paho's thread, acks on the API's

    def restore(self, open_alerts):
        """After a restart: re-attach the alerts that are still open in the database."""
        with self._lock:
            for a in open_alerts:
                self._tracks[(a["shift_id"], a["worker_id"], a["rule"])] = _Track(alert=a)

    def process(self, event, strain):
        """Feed one stored event (as returned by repository.ingest_event). Returns the changes."""
        if strain is None:
            return []
        shift_id, worker_id = strain["shift_id"], event["worker_id"]
        duration = float(event.get("duration_s") or 1.0)
        active = conditions(event, strain, self.threshold, self.warn_fraction)
        changes = []
        with self._lock:
            for rule in self.rules:
                track = self._tracks.setdefault((shift_id, worker_id, rule.name), _Track())
                if active[rule.name]:
                    track.run_s += duration
                    track.clear_s = 0.0
                    if track.alert is None and track.run_s >= rule.hold_s:
                        track.alert = self.store.open_alert(
                            shift_id=shift_id, worker_id=worker_id, station_id=event["station_id"],
                            rule=rule.name, level=rule.level, reba_score=event["reba_score"],
                            message=message_for(rule.name, event, strain, self.threshold, track.run_s))
                        changes.append(("opened", track.alert))
                else:
                    track.run_s = 0.0
                    track.clear_s += duration
                    if track.alert is not None and rule.clears and track.clear_s >= self.clear_after_s:
                        row = self.store.resolve_alert(track.alert["alert_id"])
                        changes.append(("resolved", row or {**track.alert, "resolved": True}))
                        track.alert = None
        for kind, row in changes:
            if kind == "opened":
                log.warning("ALERT %s %s at %s: %s", row["level"].upper(), worker_id, event["station_id"],
                            row["message"])
            if self.on_change:
                self.on_change(kind, row)
        return changes


class DbAlertStore:
    """Persists alerts in PostgreSQL (table `alerts`, see schema.sql)."""

    def __init__(self, pool_getter):
        self._pool = pool_getter

    def open_alert(self, **f):
        with self._pool().connection() as conn:
            row = conn.execute(
                """INSERT INTO alerts (shift_id, worker_id, station_id, rule, level, message, reba_score)
                   VALUES (%(shift_id)s, %(worker_id)s, %(station_id)s, %(rule)s, %(level)s, %(message)s,
                           %(reba_score)s)
                   ON CONFLICT (shift_id, worker_id, rule) WHERE resolved_at IS NULL
                   DO UPDATE SET message = EXCLUDED.message
                   RETURNING *""", f).fetchone()
        return row

    def resolve_alert(self, alert_id):
        with self._pool().connection() as conn:
            return conn.execute("UPDATE alerts SET resolved_at = now() WHERE alert_id = %s RETURNING *",
                                (alert_id,)).fetchone()


# --- queries used by the API -----------------------------------------------------------------

def list_alerts(conn, shift_id, only_attention=True, limit=200):
    """Alerts of a shift, newest first. only_attention: still open OR not yet acknowledged."""
    where = "AND (a.resolved_at IS NULL OR a.acknowledged_at IS NULL)" if only_attention else ""
    return conn.execute(
        f"""SELECT a.*, w.display_name FROM alerts a JOIN workers w USING (worker_id)
            WHERE a.shift_id = %s {where} ORDER BY a.opened_at DESC LIMIT %s""",
        (shift_id, limit)).fetchall()


def open_alerts(conn, shift_id):
    return conn.execute("SELECT * FROM alerts WHERE shift_id = %s AND resolved_at IS NULL", (shift_id,)).fetchall()


def acknowledge(conn, alert_id, by):
    return conn.execute(
        """UPDATE alerts SET acknowledged_at = COALESCE(acknowledged_at, now()),
                             acknowledged_by = COALESCE(acknowledged_by, %s)
           WHERE alert_id = %s RETURNING *""", (by, alert_id)).fetchone()
