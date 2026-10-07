# ERGOSENSE — viva notes

One section per component: **what it does**, **why we chose it**, and **likely questions with short answers**.
Numbers quoted here come from our own runs (see "Key numbers" at the end).

---

## 0. The problem and SDG 9

**What:** Work-related musculoskeletal disorders (back, shoulder, wrist injuries) are the most common
occupational health problem in manufacturing and logistics. Ergonomic risk is normally checked by a
specialist with a paper REBA sheet a few times a year, so day-to-day exposure is invisible.
ERGOSENSE turns an ordinary webcam into a continuous, privacy-preserving posture-risk sensor, tracks each
worker's cumulative strain over a shift, suggests job rotations that keep everyone under a safe limit, and
writes an evidence-based shift report.

**SDG 9 link:** Target 9.4 asks industries to upgrade with clean, efficient technology, and target 9.5 asks
for more research and innovation in industry. ERGOSENSE is low-cost, retrofittable industrial
infrastructure (a webcam, a laptop, open-source software) that makes factories safer and more
productive. It also supports SDG 8.8 (safe working environments).

**Likely questions**
- *Why not just train workers to lift properly?* Training alone has weak evidence. Task design and
  exposure management work better, and we can only manage what we measure.
- *Who is the user?* The shift supervisor (dashboard, approvals, report) and the safety team (trends per station).
- *Is this surveillance?* No video leaves the edge device and no images are stored. Only ~600-byte posture
  scores with a pseudonymous worker id go over the network. Rotations need human approval.

---

## 1. Computer vision (MediaPipe Pose, reused from FitCV)

**What:** `ergosense/edge/capture.py` reads the webcam, flips it into a mirror view and runs MediaPipe Pose
with the same legacy API, settings and pinned versions as FitCV's `main.py`. It sends the 33 **world**
landmarks (metres, origin between the hips) to the scoring process.

**Why:** MediaPipe runs in real time on a laptop CPU and needs no training. FitCV had already solved the
version issues (mediapipe 0.10.21 needs numpy < 2). We reuse FitCV's `features.calculate_angle` for the
elbow, wrist and knee angles, and FitCV's recorded pose dataset as a camera-free test source (`--replay`).
FitCV's ST-GCN classifier is exercise-specific, so we did not need it: REBA is a formula on joint angles.

**Why keypoints instead of video:** each event is ~600 B instead of a 921,600 B raw frame (~1,540x less),
no images exist to leak (privacy by design), and the backend never needs a GPU.

**Likely questions**
- *World vs image landmarks?* World landmarks are 3-D in metres from MediaPipe's 3-D body model, so angles
  don't depend on distance to the camera. Image landmarks are normalised pixels. We use world landmarks
  live, and image landmarks (rescaled by the aspect ratio) for the FitCV recordings.
- *Where should the camera go?* Side-on or at about 45 degrees, with the whole body in view. Monocular depth is the weakest
  axis. On FitCV's front-facing lateral-raise clips the depth error showed up as a fake ~39 degree trunk lean.
  On side-view clips it showed up as a fake 25-40 degree side bend, so side bend is ignored when the view is
  side-on (`SIDE_VIEW_DEPTH_SHARE`).
- *Multiple workers in one frame?* MediaPipe Pose tracks one person. One camera per station is the design.

---

## 2. REBA scoring (`ergosense/edge/reba.py`)

**What:** joint angles → segment scores (trunk, neck, legs, upper arm, lower arm, wrist) → Tables A, B, C →
+ load/force, coupling, activity → final score 1-15 → action level (negligible / low / medium / high / very high).
It also gives rule-based labels (bending > 60 degrees, overhead_reach, squatting, twisting, plus our "leaning" for 20-60 degrees)
and a **median filter over 15 frames** so one bad frame cannot raise an alert.

**Why REBA:** it covers the whole body (RULA focuses on the upper limbs), it is standard and published
(Hignett & McAtamney 2000), and it is purely angle-based, so it maps directly onto pose keypoints. NIOSH needs
load weights and distances a camera cannot see, so it lives in our RAG guidance instead.

**Engineering details we can defend**
- Angles are measured in a **body frame** built from the hip and shoulder lines, not raw image axes.
- Neck neutral offset of 15 degrees: on upright side-view recordings the ear-shoulder line leaned 16 degrees forward.
  Without the offset every neutral head would score "flexed".
- Both arms are scored and the worse one is kept. An arm with visibility < 0.5 is ignored.
- Load (kg), coupling and the shoulder-raised flag are command-line inputs because a camera cannot see them.
- Activity +1 when the same non-neutral posture is held for more than 60 s (`StaticPostureTracker`).

**Likely questions**
- *How did you validate it?* There are 21 unit tests: hand-constructed skeletons for neutral standing (score 1), deep bend
  (medium; high with 15 kg), overhead reach (low-medium per the tables), squat and twist. A test on **real
  human poses** from FitCV also passes: bent-over rows score much higher than upright curls, squats reach
  >90 degrees of knee flexion, and push-ups are labelled bending.
- *Why did a deep bend score only 3 in one test?* That is what REBA's own tables give for a load-free bend
  with a neutral neck (Table C(3,4) = 3). Real bending usually extends the neck and adds load, which gives medium/high.
- *Why median and not mean?* A median ignores outliers completely. A mean would still be dragged up by a spike.

---

## 3. Operating-system concepts: the edge pipeline (`ergosense/edge/pipeline.py`)

**What:** three OS processes: **capture → scoring → publisher**, connected by bounded
`multiprocessing.Queue`s (the classic producer–consumer / bounded-buffer problem).

**Why processes and not threads:** Python's GIL lets only one thread run Python code at a time. Separate
processes run MediaPipe, REBA and network I/O truly in parallel on different cores, and a crash or a slow
network in one stage cannot freeze the camera.

**Back-pressure policy per queue**
- `frames_q` (30) and `display_q` (2) carry live data: when full, the **oldest item is dropped**, because a
  stale frame is worthless and the camera must never block.
- `events_q` (1000) carries data we must keep, so the producer **blocks** instead of dropping.
- Shutdown: Ctrl+C or Q sets a shared `multiprocessing.Event`. Each stage then sends a `None` "poison pill"
  downstream, so queues drain in order. Children ignore SIGINT so the main process controls the order.

**Likely questions**
- *What happens if scoring is slower than the camera?* `frames_q` fills, the oldest frames are dropped, and
  the system stays real-time. We lose frames, not freshness.
- *Deadlock risk?* There are no cycles of blocking waits: live queues never block, and the only blocking
  put (events) is drained by a publisher that never waits on its producers.
- *Why is the OpenCV window in the capture process?* Frames never cross a process boundary (they're big).
  Only the small REBA result goes back through `display_q`.
- *Where is the shared state?* `multiprocessing.Value`s for the status line (connected, pending, bytes).

---

## 4. Computer networks: MQTT, QoS, store-and-forward, WebSockets

**What:** the edge publishes compact JSON events to `ergosense/events/{station_id}` with **QoS 1** on Mosquitto.
The backend subscribes to `ergosense/events/+` with a **persistent session** and **manual acknowledgements**.
The dashboard gets live pushes over a **WebSocket**.

**Why MQTT over HTTP:** it's publish/subscribe (edge and backend don't need to know each other or be up at
the same time), it has a tiny 2-byte header and one long-lived TCP connection instead of a request per event, it
has built-in delivery guarantees (QoS 0/1/2), and the broker can queue messages for offline subscribers. It is
the standard protocol for industrial IoT.

**End-to-end delivery guarantee (at-least-once + idempotency = effectively once)**
1. Edge: every event is first written to a **SQLite outbox** on disk and deleted only after the broker's
   **PUBACK**. There's a sliding window of 20 in-flight messages, and after a reconnect it resends from the head
   in order.
2. Broker: QoS 1 + `clean_session=false` → queues events while the backend is down (persistence on disk).
3. Backend: acks a message **only after the database commit**. A crash in between means redelivery, not loss.
4. Duplicates are harmless: `event_id` (UUID) is the primary key and the strain update is in the same transaction.

**Measured:** with the broker stopped for ~13 s, 96 events were buffered on disk and all 192 were delivered, **0 lost
and 0 out of order**. With the backend stopped, the broker queued 48 events and delivered them on restart
(`session present: True`).

**Likely questions**
- *QoS 0 vs 1 vs 2?* 0 = at most once (fire and forget), 1 = at least once (PUBACK, may duplicate),
  2 = exactly once (4-way handshake, slower). We use 1 + idempotent ingest: the guarantees of 2 at the cost of 1.
- *Why WebSockets for the dashboard?* The server pushes each event as it arrives, with no polling delay and
  no wasted requests. HTTP is used for the request/response actions (suggest, approve, report).
- *Why `127.0.0.1` and not `localhost`?* On Windows `localhost` resolves to IPv6 `::1` first. Docker publishes
  ports on IPv4 only, so every connection stalled until the IPv6 attempt timed out (we measured 10 s).
- *Security?* Ports are bound to 127.0.0.1. For a LAN deployment you would add Mosquitto username/password
  and TLS (port 8883), and give each edge device its own credentials and topic ACL.
- *Bandwidth?* ~600 B/s per camera vs ~13.8 MB/s of raw 15-FPS video, so a 100-camera plant needs ~0.5 Mb/s.

---

## 5. DBMS: PostgreSQL schema, transactions, indexes (`ergosense/backend/schema.sql`)

**What:** tables `workers`, `stations`, `worker_training`, `shifts`, `shift_assignments`, `posture_events`,
`shift_strain`, `rotation_plans`, `rotation_approvals`, `documents`, `doc_chunks`, `safety_reports`.

**Why PostgreSQL:** full ACID transactions and row locks, strong constraints, JSONB for flexible
per-event details, and the **pgvector** extension, so the RAG vectors live in the same database
(one backup, one transaction model, SQL joins between chunks and documents).

**Design points**
- **Normalisation:** training is a junction table `worker_training(worker_id, station_id)` instead of a
  `TEXT[]` column, which keeps 1NF and lets foreign keys guarantee every station exists. The view `workers_v`
  shows it as an array for the API.
- **Temporal table:** `shift_assignments(assigned_from, assigned_to)` keeps history. A late (buffered) event is
  attributed to whoever was at the station **at the event's timestamp**.
- **Constraints as business rules:** partial unique indexes give one active shift, one current station per
  worker and one current worker per station. `UNIQUE(plan_id)` in approvals means a plan is approved once.
  CHECK constraints cover REBA 1-15, risk levels and sources.
- **Indexes:** `(worker_id, ts DESC)` for a worker's latest events (also used by `DISTINCT ON`), a partial index
  on high-risk events (`WHERE reba_score >= 8`) for reports, HNSW (`vector_cosine_ops`) for embeddings, and GIN on a
  generated `tsvector` for the keyword fallback.
- **Strain rule:** `strain += weight(risk_level) * minutes`, with weights 0 / 0.5 / 1 / 2 / 4 for
  negligible / low / medium / high / very high. The safe limit is 400 points per shift (about 6.7 h medium or
  3.3 h high). It's a design parameter set ~17% above what a perfectly balanced rotation gives.

**The approval transaction** (`ergosense/backend/rotation.py::approve_plan`), all-or-nothing:
`SELECT ... FOR UPDATE` on the plan → check status / shift / training → mark approved (and supersede other
proposals) → close current assignments → insert new ones → insert into `rotation_approvals`.

**Likely questions**
- *Prove it is atomic.* `test_failed_approval_changes_nothing`: a plan putting two workers on one station
  fails on the unique index *after* the status update and the closing of assignments, and the test shows the
  database is exactly as before.
- *What does FOR UPDATE do?* It takes a row lock. A second approver's transaction waits until the first commits,
  then re-reads the row, sees "approved" and fails with 409. That's `test_concurrent_approval_waits_on_row_lock`.
- *Isolation level?* Default READ COMMITTED. The row lock is what serialises approvals of the same plan.
- *Why JSONB for segment_scores?* It's display/diagnostic detail with no joins on it. JSONB keeps the event table
  stable if the scoring detail changes.
- *Idempotent ingest in SQL?* `INSERT ... ON CONFLICT (event_id) DO NOTHING RETURNING`: if nothing comes back it's
  a duplicate, and the strain `INSERT ... ON CONFLICT DO UPDATE` is skipped.

---

## 6. System design

**What:** edge (camera + REBA) → broker → backend (ingest, API, WebSocket, planner, reporter) → PostgreSQL;
a dashboard on top. Everything runs locally with Docker Compose + Python.

**Key decisions**
- **Compute at the edge:** pose and REBA run next to the camera. The network carries facts, not pixels.
- **Broker in the middle:** decouples producers from consumers, absorbs outages, and lets more consumers
  (analytics, alarms) subscribe without touching the edge.
- **Station cameras, not worker cameras:** events carry `worker_id = null` and the backend attributes them
  to the station's current (or historical) assignee. After a rotation is approved, the same camera stream counts
  for the new worker automatically.
- **Human in the loop:** the RL planner only *proposes*. A supervisor approves, and that's recorded for audit.
- **Graceful degradation:** no LLM → template report; no PPO model → greedy policy; no embedding model →
  keyword search; no broker → edge buffers to disk; no backend → broker queues.

**Likely questions**
- *How does it scale to 100 stations?* Topics per station, one broker (Mosquitto handles thousands of msg/s),
  and several backend instances using MQTT shared subscriptions (`$share/group/...`). Postgres can partition
  `posture_events` by month, and Postgres `LISTEN/NOTIFY` can fan out to many API servers.
- *Single points of failure?* The broker and the database. In production: a clustered broker (or two Mosquitto
  instances with bridging) and a Postgres replica. The edge outbox already covers broker downtime.
- *Why not cloud?* It's out of scope by design: a local deployment keeps data on-premises and works without internet.

---

## 7. Reinforcement learning (`ergosense/rl/`)

**MDP**
- **State (25 numbers):** each worker's cumulative strain / limit (4), current station one-hot (16), hours
  remaining (1), station mean strain per hour (4).
- **Action:** `Discrete(24)`, one of the 4! permutations of workers to stations. **Action masking** removes
  permutations that put someone on a station they are not trained for (11 of 24 remain valid).
- **Transition:** each hour a station adds `max(0, N(mean, std))` strain, times the worker's hidden sensitivity
  (0.85-1.20). The shift is 8 decisions.
- **Reward per hour:** −(increase in total strain above the limit + 0.5 × increase in the peak worker's
  strain + 2 × workers moved) / 100. The rewards **telescope**: the sum over a shift equals the negative shift
  objective (excess + λ·peak + c·moves), so per-step rewards are dense but optimise exactly the goal.

**Why PPO:** a stable, well-tested on-policy algorithm for discrete actions. It works out of the box in Stable-Baselines3 on a
CPU (trained in ~4 minutes), and **MaskablePPO** (sb3-contrib) supports invalid-action masking. DQN would also
work but is more sensitive to tuning, and tabular Q-learning doesn't fit the continuous strain state.

**Baselines:** no rotation; round-robin (everyone moves to the next station every hour, adjusted for
training); greedy (move the most strained worker to the lightest station they can do).

**Evaluation:** 500 shifts per policy with **the same seeds** for every policy (common random numbers: the
environment draws the same random numbers whatever the policy does), so differences come from decisions only.

**Calibration:** the loading bay's strain per hour comes from **real REBA events in PostgreSQL**: all of FitCV's
bent-over-row recordings replayed through the full pipeline with the station's 8 kg box load (REBA's load is
entered by the assessor). Result: 81.8 strain/h, within 6% of the simulator's hand-built model (77.4).
An earlier calibration gave 79.5 with no load but with false side-bend points; fixing the side-view
side-bend and adding the real load are documented in the git history.

**Likely questions**
- *Why does total strain not go down?* Rotation **redistributes** exposure, it does not remove it. Only
  engineering changes (lift tables, platforms) reduce the total. The report says so explicitly.
- *Why does PPO sometimes keep everyone in place?* Moving costs productivity (switching cost). The policy
  rotates when the expected reduction in peak/excess strain outweighs the move cost, often later in the shift.
- *Sim-to-real gap?* The dynamics are a model. One station is calibrated on real poses and the others use the
  station simulator. Individual sensitivity is unknown in the live system (we use 1.0), and the policy adapts
  through the observed strain.
- *What does the backend run?* `POST /rotation/suggest` builds the state from the database, asks PPO for the next hour,
  and projects end-of-shift strain with 200 Monte Carlo futures, both with the policy and with no rotation.

---

## 8. Generative AI + RAG (`ergosense/assistant/`)

**What:** at the end of a shift, SQL gathers per-worker and per-station facts and the approved rotations.
One retrieval query per relevant topic (lifting, overhead, twisting, rotation, recovery, camera limits)
fetches guidance chunks. The LLM writes a fixed-structure report citing chunk ids, and code then **verifies every
citation**.

**Corpus:** three short documents written in our own words: a REBA reference, safe lifting / NIOSH lifting equation
concepts, and job rotation guidance. They're chunked by `##` section (one idea per chunk, readable citations
like `[safe_lifting_niosh#5]`), embedded with **all-MiniLM-L6-v2** (384-d, runs on CPU), and stored in **pgvector**
with an HNSW index.

**Why RAG:** the LLM's recommendations are grounded in a known, reviewable source instead of its memory,
and every recommendation is traceable to a paragraph.

**Hallucination controls:** a low temperature (0.2), "use only the given data/excerpts" in the system prompt, numbers
supplied as tables, and a post-check: any cited id not in the retrieved set is replaced by
`[citation removed: id]`, and any recommended action left without a valid citation is flagged.

**Providers:** Ollama (local `llama3.1:8b`, private, free, fits the RTX 4060) or any OpenAI-compatible API,
chosen in `.env`. If neither is reachable, a **template fallback** builds the same sections from the same
facts and chunks and says clearly that it is the fallback.

**Likely questions**
- *Why pgvector and not FAISS?* At 20 chunks either is instant. pgvector keeps vectors in the same ACID
  database with SQL joins and no separate index file to keep in sync.
- *Why section-level chunks?* The documents are written as one idea per section, and fixed-size chunks would cut
  ideas in half and make citations meaningless.
- *How do you know retrieval works?* We checked the top hits for typical queries. "Lifting boxes from a floor
  pallet" → *Practical rules for floor-level lifting* (0.56), "how often to rotate" → *How often to rotate* (0.75).
- *What if the LLM invents a source?* `test_report_with_fake_llm_verifies_citations_and_is_saved` feeds a
  fake LLM answer citing `not_a_real_chunk#7`, and the check removes it and records it as invalid.

---

## 9. Dashboard (`ergosense/dashboard/`)

Plain HTML/CSS/JS (no framework, no CDN, works offline), served by FastAPI. Initial state comes from `GET /state`,
then WebSocket messages (`event`, `plan`, `approval`). Risk colours always appear with a text label
(colour-blind safe), strain bars share one scale with a dashed safe-limit line, and the light and dark themes follow the OS.

---

## 10. Testing and evaluation summary

- `python -m pytest`: 54 tests (REBA tables, angles and scenarios; real-pose checks; event validation;
  outbox; idempotent ingest; temporal attribution; approval atomicity and concurrency; RL environment
  (including the Gymnasium checker and reward telescoping); RAG chunking; citation verification; LLM provider
  selection; full report with a fake LLM; template fallback).
- `python -m ergosense.e2e_check`: the full system through its public interfaces, with an hourly
  suggest/approve loop over a simulated 8-hour shift.
- `python -m ergosense.rl.evaluate`: 500-shift comparison, writing a CSV and a chart.

---

## Key numbers

| What | Value |
|---|---|
| Event size vs raw frame | ~600 B vs 921,600 B (≈1,540x smaller) |
| Broker outage test | 96 events buffered, 192/192 delivered, 0 out of order |
| Backend outage test | 48 events queued by broker, all delivered on restart |
| Test suite | 54 tests passing |
| RL final peak strain (500 shifts) | no rotation 691, round-robin 532, greedy 437, **PPO 400** |
| RL workers over limit | 1.67 / 1.53 / 1.65 / **0.78** |
| RL moves per shift | 0 / 26.5 / 11.9 / **10.1** |
| Loading-bay calibration | 81.8 strain/h from 528 real pose events (simulator model 77.4) |
| Live e2e shift (hourly PPO) | peak 362 vs 632 projected with no rotation; nobody over 400 |
| PPO training time | ~3.5 min on a laptop CPU (500k steps) |
