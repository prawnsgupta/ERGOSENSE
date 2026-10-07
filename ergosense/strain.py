"""Strain accumulation rule, shared by the backend, the RL simulator and calibration.

Every posture event covers `duration_s` seconds of work. A worker's cumulative strain grows by

    strain += RISK_WEIGHT[risk_level] * duration_s / 60

so the unit is a "weighted minute": one minute in a medium-risk posture adds 1 point, one
minute in a high-risk posture adds 2 points, and a negligible posture adds nothing.
The weights grow faster than the REBA risk bands because injury risk does too.

With the default safe limit of 400 points per shift, a worker may spend the equivalent of
about 6.7 h in medium-risk postures or 3.3 h in high-risk postures. The limit is a design
parameter of this project (STRAIN_THRESHOLD in .env), not a clinical threshold: it was set
about 17% above the load a perfectly balanced rotation gives on the seeded stations
(~341 points per 8 h), so balancing work keeps everyone safe but a static assignment at the
loading bay (~620 points) does not.
"""

RISK_WEIGHT = {
    "negligible": 0.0,   # REBA 1
    "low": 0.5,          # REBA 2-3
    "medium": 1.0,       # REBA 4-7
    "high": 2.0,         # REBA 8-10
    "very_high": 4.0,    # REBA 11-15
}

HIGH_RISK_LEVELS = ("high", "very_high")


def strain_increment(risk_level, duration_s):
    """Strain points added by one event of the given risk level and duration."""
    return RISK_WEIGHT[risk_level] * duration_s / 60.0


def is_high_risk(risk_level):
    return risk_level in HIGH_RISK_LEVELS
