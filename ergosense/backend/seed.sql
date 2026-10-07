-- ERGOSENSE seed data: 4 workers, 4 stations, training matrix, one active shift.
-- Safe to re-run: existing rows are left alone (ON CONFLICT / NOT EXISTS guards).

INSERT INTO stations (station_id, name, base_risk_level, description) VALUES
    ('S1', 'Quality inspection', 'low',
     'Visual inspection of finished parts at a waist-high bench, mostly upright posture.'),
    ('S2', 'Packaging line', 'medium',
     'Folding cartons and packing parts, frequent forward reach and some trunk flexion.'),
    ('S3', 'Overhead assembly', 'high',
     'Fastening parts on a hanging conveyor, hands at or above shoulder height.'),
    ('S4', 'Loading bay', 'high',
     'Lifting boxes from floor-level pallets onto a trolley, repeated deep trunk flexion.')
ON CONFLICT (station_id) DO NOTHING;

-- Pseudonymous ids; the names are fictional and only used for the demo dashboard.
INSERT INTO workers (worker_id, display_name) VALUES
    ('W01', 'Asha'),
    ('W02', 'Ravi'),
    ('W03', 'Meena'),
    ('W04', 'Karan')
ON CONFLICT (worker_id) DO NOTHING;

-- Not everyone is trained for every station; the rotation planner must respect this.
INSERT INTO worker_training (worker_id, station_id) VALUES
    ('W01', 'S1'), ('W01', 'S2'), ('W01', 'S3'), ('W01', 'S4'),
    ('W02', 'S1'), ('W02', 'S2'), ('W02', 'S4'),
    ('W03', 'S1'), ('W03', 'S3'), ('W03', 'S4'),
    ('W04', 'S2'), ('W04', 'S3'), ('W04', 'S4')
ON CONFLICT DO NOTHING;

-- One active shift (only created when no shift is active).
INSERT INTO shifts (name, planned_hours)
SELECT 'Shift ' || to_char(now(), 'YYYY-MM-DD HH24:MI'), 8
WHERE NOT EXISTS (SELECT 1 FROM shifts WHERE status = 'active');

-- Initial assignment for the active shift (only if it has none yet).
INSERT INTO shift_assignments (shift_id, worker_id, station_id)
SELECT s.shift_id, a.worker_id, a.station_id
FROM shifts s
CROSS JOIN (VALUES ('W01', 'S1'), ('W02', 'S2'), ('W03', 'S3'), ('W04', 'S4')) AS a(worker_id, station_id)
WHERE s.status = 'active'
  AND NOT EXISTS (SELECT 1 FROM shift_assignments x WHERE x.shift_id = s.shift_id);

-- Zeroed strain rows so every worker shows up on the dashboard from the start.
INSERT INTO shift_strain (shift_id, worker_id)
SELECT s.shift_id, w.worker_id
FROM shifts s CROSS JOIN workers w
WHERE s.status = 'active'
ON CONFLICT DO NOTHING;
