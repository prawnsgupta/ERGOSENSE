-- ERGOSENSE database schema (PostgreSQL 16 + pgvector)
-- Applied by:  python -m ergosense.backend.init_db
-- Every statement is idempotent (IF NOT EXISTS) so the script can be re-run safely.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Master data
-- ---------------------------------------------------------------------------

-- Workers are identified by a pseudonymous id (W01 ...). No camera images are stored anywhere.
CREATE TABLE IF NOT EXISTS workers (
    worker_id     TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stations (
    station_id       TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    base_risk_level  TEXT NOT NULL
        CHECK (base_risk_level IN ('negligible', 'low', 'medium', 'high', 'very_high')),
    description      TEXT
);

-- Which stations each worker is trained for.
-- A junction table instead of a TEXT[] column keeps the schema in 1NF and lets
-- foreign keys guarantee that every trained station actually exists.
CREATE TABLE IF NOT EXISTS worker_training (
    worker_id   TEXT NOT NULL REFERENCES workers(worker_id) ON DELETE CASCADE,
    station_id  TEXT NOT NULL REFERENCES stations(station_id) ON DELETE CASCADE,
    PRIMARY KEY (worker_id, station_id)
);

-- Convenience view that shows trained_stations as an array (what the API returns).
CREATE OR REPLACE VIEW workers_v AS
SELECT w.worker_id,
       w.display_name,
       COALESCE(array_agg(t.station_id ORDER BY t.station_id)
                FILTER (WHERE t.station_id IS NOT NULL), '{}') AS trained_stations
FROM workers w
LEFT JOIN worker_training t ON t.worker_id = w.worker_id
GROUP BY w.worker_id, w.display_name;

-- ---------------------------------------------------------------------------
-- Shifts and who works where
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS shifts (
    shift_id       SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at       TIMESTAMPTZ,
    planned_hours  NUMERIC(4, 1) NOT NULL DEFAULT 8 CHECK (planned_hours > 0),
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed'))
);

-- At most one active shift at a time (partial unique index on a constant expression).
CREATE UNIQUE INDEX IF NOT EXISTS shifts_one_active
    ON shifts ((status)) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS rotation_plans (
    plan_id                SERIAL PRIMARY KEY,
    shift_id               INT NOT NULL REFERENCES shifts(shift_id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    assignments            JSONB NOT NULL,        -- {"W01": "S2", "W02": "S1", ...}
    policy                 TEXT NOT NULL,         -- 'ppo', 'greedy', ...
    projected_peak_strain  DOUBLE PRECISION,
    projection             JSONB,                 -- projected end-of-shift strain with / without the plan
    status                 TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'superseded'))
);
CREATE INDEX IF NOT EXISTS rotation_plans_shift ON rotation_plans (shift_id, created_at DESC);

-- UNIQUE(plan_id): a plan can be approved at most once. SELECT ... FOR UPDATE in the
-- approval transaction is the first line of defence, this constraint is the safety net.
CREATE TABLE IF NOT EXISTS rotation_approvals (
    approval_id  SERIAL PRIMARY KEY,
    plan_id      INT NOT NULL UNIQUE REFERENCES rotation_plans(plan_id),
    approved_by  TEXT NOT NULL,
    approved_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Temporal assignment table: each row says "worker X was at station Y from .. to ..".
-- The current assignment is the row with assigned_to IS NULL. Keeping history lets the
-- backend attribute a late (buffered) event to whoever was at the station at event time.
CREATE TABLE IF NOT EXISTS shift_assignments (
    assignment_id  BIGSERIAL PRIMARY KEY,
    shift_id       INT NOT NULL REFERENCES shifts(shift_id),
    worker_id      TEXT NOT NULL REFERENCES workers(worker_id),
    station_id     TEXT NOT NULL REFERENCES stations(station_id),
    assigned_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_to    TIMESTAMPTZ,
    plan_id        INT REFERENCES rotation_plans(plan_id),   -- NULL for the initial assignment
    CHECK (assigned_to IS NULL OR assigned_to >= assigned_from)
);
-- One current station per worker, and one current worker per station.
CREATE UNIQUE INDEX IF NOT EXISTS shift_assignments_worker_current
    ON shift_assignments (shift_id, worker_id) WHERE assigned_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS shift_assignments_station_current
    ON shift_assignments (shift_id, station_id) WHERE assigned_to IS NULL;
CREATE INDEX IF NOT EXISTS shift_assignments_lookup
    ON shift_assignments (shift_id, station_id, assigned_from);

-- ---------------------------------------------------------------------------
-- Sensor data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS posture_events (
    event_id        UUID PRIMARY KEY,               -- generated at the edge, makes ingest idempotent
    shift_id        INT NOT NULL REFERENCES shifts(shift_id),
    worker_id       TEXT NOT NULL REFERENCES workers(worker_id),
    station_id      TEXT NOT NULL REFERENCES stations(station_id),
    ts              TIMESTAMPTZ NOT NULL,           -- when the edge produced the event
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_s      REAL NOT NULL DEFAULT 1 CHECK (duration_s > 0),   -- exposure time this event covers
    reba_score      SMALLINT NOT NULL CHECK (reba_score BETWEEN 1 AND 15),
    risk_level      TEXT NOT NULL
        CHECK (risk_level IN ('negligible', 'low', 'medium', 'high', 'very_high')),
    posture_label   TEXT NOT NULL,
    segment_scores  JSONB NOT NULL,
    angles          JSONB,
    source          TEXT NOT NULL DEFAULT 'webcam' CHECK (source IN ('webcam', 'replay', 'simulated')),
    edge_seq        BIGINT                          -- per-edge sequence number (ordering diagnostics)
);
-- "Latest events of a worker" (dashboard, GET /events) is an index-only range scan.
CREATE INDEX IF NOT EXISTS posture_events_worker_ts ON posture_events (worker_id, ts DESC);
CREATE INDEX IF NOT EXISTS posture_events_shift_station ON posture_events (shift_id, station_id);
-- Partial index: reports only ever look for high-risk events, which are a small fraction.
CREATE INDEX IF NOT EXISTS posture_events_high_risk
    ON posture_events (shift_id, worker_id) WHERE reba_score >= 8;

-- Running totals per worker per shift, updated in the same transaction as each event insert.
-- Strain rule (see ergosense/strain.py): strain += weight(risk_level) * duration_minutes
CREATE TABLE IF NOT EXISTS shift_strain (
    shift_id           INT NOT NULL REFERENCES shifts(shift_id),
    worker_id          TEXT NOT NULL REFERENCES workers(worker_id),
    cumulative_strain  DOUBLE PRECISION NOT NULL DEFAULT 0,
    high_risk_count    INT NOT NULL DEFAULT 0,      -- events with risk high / very_high
    exposure_s         DOUBLE PRECISION NOT NULL DEFAULT 0,   -- observed working time
    risky_s            DOUBLE PRECISION NOT NULL DEFAULT 0,   -- time spent in high / very_high postures
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (shift_id, worker_id)
);

-- ---------------------------------------------------------------------------
-- RAG knowledge base and generated reports
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    source_file  TEXT NOT NULL,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    chunk_id   TEXT PRIMARY KEY,                    -- e.g. 'reba_reference#3'
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    section    TEXT NOT NULL,
    content    TEXT NOT NULL,
    embedding  vector(384),                         -- all-MiniLM-L6-v2 sentence embedding
    -- Full-text search column, used as a keyword fallback if the embedding model is unavailable.
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', section || ' ' || content)) STORED
);
CREATE INDEX IF NOT EXISTS doc_chunks_embedding ON doc_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS doc_chunks_tsv ON doc_chunks USING gin (tsv);

CREATE TABLE IF NOT EXISTS safety_reports (
    report_id    SERIAL PRIMARY KEY,
    shift_id     INT NOT NULL REFERENCES shifts(shift_id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider     TEXT NOT NULL,                     -- 'ollama', 'openai' or 'template-fallback'
    model        TEXT,
    is_fallback  BOOLEAN NOT NULL,
    content_md   TEXT NOT NULL,
    citations    JSONB NOT NULL,                    -- {"valid": [...], "invalid": [...], "retrieved": [...]}
    file_path    TEXT
);

-- ---------------------------------------------------------------------------
-- Live safety alerts (raised by ergosense/backend/alerts.py while events arrive)
-- ---------------------------------------------------------------------------

-- One row per alert episode: opened when a rule has held long enough, resolved when the
-- condition has cleared, acknowledged when a supervisor confirms they have seen it.
CREATE TABLE IF NOT EXISTS alerts (
    alert_id         BIGSERIAL PRIMARY KEY,
    shift_id         INT NOT NULL REFERENCES shifts(shift_id),
    worker_id        TEXT NOT NULL REFERENCES workers(worker_id),
    station_id       TEXT REFERENCES stations(station_id),
    rule             TEXT NOT NULL,
    level            TEXT NOT NULL CHECK (level IN ('amber', 'red')),
    message          TEXT NOT NULL,
    reba_score       SMALLINT,
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ,
    acknowledged_at  TIMESTAMPTZ,
    acknowledged_by  TEXT
);
-- At most one unresolved alert per worker and rule (the engine's debounce, enforced by the DB too).
CREATE UNIQUE INDEX IF NOT EXISTS alerts_one_open
    ON alerts (shift_id, worker_id, rule) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS alerts_shift_opened ON alerts (shift_id, opened_at DESC);
