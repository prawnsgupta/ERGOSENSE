# Part 3 — The Code Explained (Chapter 16)

Each block below is real code from our project. "(shortened)" means we removed lines that are not important for understanding.
For each block: **What are we trying to do → Why → What the code does → What happens next.**

Quick words:
- **function** = a named recipe (`def name(...)`).
- **variable** = a labelled box holding a value.
- **class** = a blueprint for an object that holds data plus functions.
- **import** = borrowing code from another file or library.

---

## File: `ergosense/edge/reba.py` — the REBA safety score

### Purpose
Turn 33 body points into a danger score from 1 to 15. This is the heart of the project.

### Block 1 — the REBA tables
```python
TABLE_A = [
    [[1, 2, 3, 4], [1, 2, 3, 4], [3, 3, 5, 6]],
    ...
]
def table_a(trunk, neck, legs):
    return TABLE_A[trunk - 1][neck - 1][legs - 1]
```
- **What:** a copy of the official REBA "Table A" (from Hignett & McAtamney, 2000).
- **Why `- 1`:** REBA scores start at 1, but Python lists start counting at 0.
- **In:** trunk (1–5), neck (1–3), legs (1–4). **Out:** the Group A score.
- **If removed:** no score. **Tested by:** `test_table_spot_checks`.
- Table B (arms and wrist) and Table C (combines A and B) work the same way.

### Block 2 — risk level
```python
def risk_level(score):
    if score <= 1: return "negligible"
    if score <= 3: return "low"
    if score <= 7: return "medium"
    if score <= 10: return "high"
    return "very_high"
```
These are REBA's official "action levels". The result goes into every event, decides the colour on screen, and decides the strain weight.

### Block 3 — segment scores, e.g. the trunk
```python
def trunk_score(flexion, twisted_or_side_bent):
    if abs(flexion) <= 5:
        score = 1
    elif flexion < 0:
        score = 2 if flexion >= -20 else 3
    elif flexion <= 20:
        score = 2
    elif flexion <= 60:
        score = 3
    else:
        score = 4
    return score + (1 if twisted_or_side_bent else 0)
```
- **What:** follows the REBA worksheet box for the trunk.
- `flexion` = how many degrees the back is bent forward (negative = leaning back).
- `<= 5` → upright. This 5° tolerance is **our choice**, to ignore camera noise.
- **+1** if twisted or bent sideways.

### Block 4 — measuring the trunk angle in 3D (`compute_angles`, shortened)
```python
mid_hip = (p[L_HIP] + p[R_HIP]) / 2
mid_sh = (p[L_SHOULDER] + p[R_SHOULDER]) / 2
trunk = mid_sh - mid_hip
lat_h = _unit(_project(hip_axis, UP))
fwd_h = _unit(np.cross(lat_h, UP))
trunk_flexion = math.degrees(math.atan2(np.dot(trunk, fwd_h), np.dot(trunk, UP)))
```
- **What:** find how far the line from the hips to the shoulders tips forward from "straight up".
- `mid_hip`, `mid_sh` = the middle of the hips and the middle of the shoulders.
- `trunk` = an arrow from the hips up to the shoulders.
- `lat_h` = the body's left-right direction (made level). `fwd_h` = "forward" for this body (worked out with a cross product).
- `atan2(forward part, up part)` = the bend angle. 0° = upright, 90° = bent flat.
- **Why this way:** the angle stays the same wherever the person stands in the picture.
- **Tested:** `test_angles_are_recovered_from_constructed_pose` builds a body bent exactly 45° and checks that we measure 45 ± 1.

### Block 5 — real-data fixes (our own findings)
```python
neck_flexion = math.degrees(math.atan2(...)) - NECK_NEUTRAL_OFFSET_DEG   # 15 degrees
if abs(hip_axis[2]) / (abs(hip_axis[0]) + abs(hip_axis[2]) + 1e-9) > SIDE_VIEW_DEPTH_SHARE:
    trunk_side_bend = neck_side_bend = 0.0
```
- Line 1: in a normal head position, the ear is in front of the shoulder (we measured 16° on real recordings), so we subtract 15°.
- Line 2: if the hips point mostly "into" the camera (a side view), side-bending would be measured along depth, which is unreliable. So we ignore it.
- `1e-9` = a tiny number to avoid dividing by zero.

### Block 6 — the full score (`score_angles`, shortened)
```python
score_a = table_a(trunk, neck, legs) + load
...
score_b = tb + coupling
tc = table_c(score_a, score_b)
score = int(min(15, tc + activity))
```
- `load` = 0/1/2 (+1 if there is a sudden jerk) from the kg passed in with `--load-kg`.
- `coupling` = how good the grip is (0–3, passed in).
- `activity` = +1 if the same risky posture is held for more than 60 seconds.
- `min(15, ...)` = the maximum is 15.
- **Out:** a `RebaResult` (score, risk, label, segment scores, angles, side).

### Block 7 — smoothing
```python
class ScoreSmoother:
    def update(self, result):
        self.history.append(result)
        scores = sorted(r.score for r in self.history)
        median = scores[len(scores) // 2]
        representative = next(r for r in reversed(self.history) if r.score == median)
        label = Counter(r.posture_label for r in self.history).most_common(1)[0][0]
        return replace(representative, posture_label=label)
```
- **Why:** the camera sometimes makes one bad frame. One wrong 12 must not trigger an alarm.
- `history` keeps the last 15 frames (about 1 second).
- **Median** = the middle value when sorted. One outlier cannot change it.
- **Label** = the most common label (a majority vote).
- **Tested:** `test_single_noisy_frame_does_not_change_smoothed_score`.

---

## File: `ergosense/edge/pipeline.py` — three programs working together

### Purpose
Run capture, scoring and publishing as separate **OS processes** connected by **bounded queues**.

```python
events_q = mp.Queue(maxsize=1000)
frames_q = mp.Queue(maxsize=30)
display_q = mp.Queue(maxsize=2)
processes.append(mp.Process(target=run_webcam, ...))
processes.append(mp.Process(target=run_scoring, ...))
processes.append(mp.Process(target=run_publisher, ...))
for p in processes:
    p.start()
```
- **Process** = a separate running program with its own memory. Separate processes can use different CPU cores at the same time. Python threads can't fully do this because of the "GIL" (a lock that lets only one thread run Python at a time).
- **Queue** = a waiting line between them. **maxsize** = how many items it can hold, so memory can't grow forever.
- **Analogy:** three cooks with trays between them. If a tray is full, the next cook must wait or throw away the oldest item.

```python
except KeyboardInterrupt:
    stop_event.set()
    for p in processes:
        p.join(timeout=15)
```
Ctrl+C sets a shared "stop" flag. Each process then sends `None` (a "poison pill") down the line, so every queue empties in order before the programs exit.

## File: `ergosense/edge/proc.py`
```python
def put_drop_oldest(q, item):
    try:
        q.put_nowait(item)
    except queue.Full:
        try: q.get_nowait()
        except queue.Empty: pass
        ...
```
For live frames: if the line is full, throw away the oldest frame. An old frame is useless, and the camera must never wait. (Events instead use a normal `put`, which waits, because events must not be lost.)

---

## File: `ergosense/edge/scoring.py` (shortened)
```python
result = assess(item["points"], load_kg=..., coupling=..., activity=tracker.activity(now))
smoothed = smoother.update(result)
...
if now - last_emit >= interval:
    covered = min(now - last_emit, 2 * interval) * settings["demo_speed"]
    event = make_event(station_id, worker_id, smoothed.to_event_fields(), covered, source, seq)
    events_q.put(event, timeout=10)
```
- Scores every frame, but sends **one event per second** (`interval`).
- `covered` = how many seconds of work this event represents. It is capped at 2 seconds so that a gap with nobody in view is not counted as work. `demo_speed` (for example 60) makes 1 real second count as 1 minute, for demos.

---

## File: `ergosense/events.py` — the message format (the "contract")
```python
def make_event(station_id, worker_id, reba_fields, duration_s, source, seq, ts=None):
    return {"event_id": str(uuid.uuid4()), "worker_id": worker_id, "station_id": station_id,
            "timestamp": ts.isoformat(timespec="milliseconds"), "duration_s": ..., **reba_fields,
            "source": source, "seq": seq}
def encode(event):
    return json.dumps(event, separators=(",", ":")).encode("utf-8")
```
- `uuid4()` = a random unique ID, used to catch duplicates.
- `separators=(",", ":")` = no spaces, so the message is smaller.
- `decode()` (not shown) checks everything that arrives: valid ID, score 1–15, known risk level, duration between 0 and 3600 seconds, and so on. Bad messages are rejected. **Tested** with 7 kinds of bad input.

## File: `ergosense/edge/outbox.py` — never lose an event
```python
CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL, topic TEXT NOT NULL, payload BLOB NOT NULL, created_at REAL NOT NULL)
def append(...): INSERT OR IGNORE ...
def peek(after_seq, limit): SELECT ... WHERE seq > ? ORDER BY seq LIMIT ?
def remove(seq): DELETE FROM outbox WHERE seq = ?
```
- Every event is **written to disk first**.
- `seq` keeps the order. `UNIQUE event_id` ignores duplicates.
- It is deleted only when the broker confirms. If the laptop crashes, the events are still in the file.

## File: `ergosense/edge/publisher.py` — the `pump()` loop (shortened)
```python
while acks: seq = self._inflight.pop(mid); self.outbox.remove(seq)
if not self.connected: return
if self._restart: self._inflight.clear(); self._last_sent_seq = 0
for seq, topic, payload in self.outbox.peek(self._last_sent_seq, free):
    info = self.client.publish(topic, payload, qos=1)
    self._inflight[info.mid] = seq
```
1. For each receipt (`mid` = message ID), delete that event from the outbox.
2. If offline, do nothing (events wait on disk).
3. After reconnecting, start again from the oldest unconfirmed event.
4. Send up to `window` (20) events and remember which message ID belongs to which outbox row.

**OUR MEASUREMENT:** broker stopped for about 13 seconds → 96 events waited in the outbox → all 192 were delivered, 0 out of order.

---

## File: `ergosense/backend/ingest.py` — receiving events
```python
self.client = mqtt.Client(..., client_id="ergosense-backend", clean_session=False, manual_ack=True)
...
def _on_message(self, client, userdata, msg):
    event = decode(msg.payload)
    result = self._store_with_retry(event)
    client.ack(msg.mid, msg.qos)   # only after the commit
```
- `clean_session=False` = "post office, keep my mail if I'm away".
- `manual_ack=True` + `ack` after saving = if we crash before saving, the broker sends it again.
- `_store_with_retry` keeps retrying (waiting 1, 2, 4... up to 30 seconds) if the database is down.

## File: `ergosense/backend/repository.py` — `ingest_event` (shortened)
```python
with conn.transaction():
    shift = active_shift(conn)
    worker_id = event["worker_id"] or resolve_worker(conn, shift_id, station_id, event["ts"])
    inserted = conn.execute("INSERT INTO posture_events (...) VALUES (...)
                             ON CONFLICT (event_id) DO NOTHING RETURNING event_id", ...).fetchone()
    if inserted is None:
        return None            # duplicate
    conn.execute("INSERT INTO shift_strain AS s (...) VALUES (...)
                  ON CONFLICT (shift_id, worker_id) DO UPDATE SET
                  cumulative_strain = s.cumulative_strain + EXCLUDED.cumulative_strain, ...")
```
- `with conn.transaction()` = all or nothing.
- `resolve_worker` finds who was assigned to that station **at the time of the event**, so even late, buffered events go to the right person.
- `ON CONFLICT DO NOTHING RETURNING` = if it is a duplicate, nothing comes back, so we skip adding strain. **Tested:** `test_duplicate_event_is_stored_and_counted_once`.
- The second query adds the new strain to the worker's total.

## File: `ergosense/backend/rotation.py` — `approve_plan` (shortened)
```python
with conn.transaction():
    plan = conn.execute("SELECT ... FROM rotation_plans WHERE plan_id = %s FOR UPDATE", ...).fetchone()
    if plan is None: raise PlanNotFound
    if plan["status"] != "proposed": raise PlanConflict("already ...")
    # check training for every worker
    conn.execute("UPDATE rotation_plans SET status = 'approved' WHERE plan_id = %s", ...)
    conn.execute("UPDATE shift_assignments SET assigned_to = now() WHERE ... assigned_to IS NULL", ...)
    for worker_id, station_id in plan["assignments"].items():
        conn.execute("INSERT INTO shift_assignments (...) VALUES (...)", ...)
    conn.execute("INSERT INTO rotation_approvals (plan_id, approved_by) VALUES (%s, %s)", ...)
```
Explained in Chapter 9. `FOR UPDATE` = lock this row until we finish. The API turns `PlanNotFound` into HTTP 404 and `PlanConflict` into HTTP 409.

---

## File: `ergosense/rl/env.py` — the simulated shift (the `step` function, shortened)
```python
new = np.array(PERMUTATIONS[action])
moves = int((new != self.assignment).sum())
noise = self.np_random.normal(0.0, 1.0, size=4)
added = np.maximum(self.means + self.stds * noise, 0.0)
self.strain = self.strain + added[new] * self.sensitivity
cost = (self._excess() - excess_before) + PEAK_WEIGHT * (self.strain.max() - peak_before) \
       + MOVE_COST * moves + penalty
return self._obs(), -cost / 100.0, terminated, False, self._info()
```
- `PERMUTATIONS[action]` = which station each worker goes to.
- `moves` = how many people changed station.
- `noise` = randomness. There is always one draw per station, whatever the action, so every policy faces the same "luck" when given the same seed (a fair comparison).
- `added[new]` = each worker gets their station's strain, times their sensitivity.
- `cost` = the increase in "over the limit" + 0.5 × the increase of the worst worker + 2 × moves.
- The reward is minus the cost ÷ 100.

```python
self.valid = np.array([self.trained[np.arange(4), list(p)].all() for p in PERMUTATIONS])
def action_masks(self): return self.valid.copy()
```
For each of the 24 arrangements, it checks whether every worker is trained for their station. MaskablePPO never picks an invalid one.

## File: `ergosense/rl/policies.py` — the Greedy baseline
```python
worker = int(np.argmax(env.strain))
for station in np.argsort(env.means):          # lightest station first
    if station == assignment[worker]: break
    if not env.trained[worker, station]: continue
    other = int(np.flatnonzero(assignment == station)[0])
    if env.trained[other, assignment[worker]]:
        swap(...); break
```
"Find the most tired worker and swap them onto the lightest station they're allowed to do." This is a simple, sensible rule to beat.

## File: `ergosense/rl/train.py` (shortened)
```python
vec_env = make_vec_env(RotationEnv, n_envs=8, seed=args.seed, env_kwargs={"profiles": profiles})
model = MaskablePPO("MlpPolicy", vec_env, n_steps=256, batch_size=256, n_epochs=10,
                    learning_rate=3e-4, gamma=0.99, gae_lambda=0.95, ent_coef=0.01,
                    policy_kwargs={"net_arch": [128, 128]}, ...)
model.learn(total_timesteps=args.timesteps)
model.save(str(config.RL_MODEL_PATH))
```
8 copies of the game run side by side → 256 steps each → 2,048 experiences → 10 learning passes → repeat until 500,000 steps. `gamma` = how much it cares about the future (0.99 = a lot).

## File: `ergosense/rl/planner.py` — using the trained brain live (shortened)
```python
options = {"strain": [...from database...], "assignment": [...], "hours_elapsed": hour, "sensitivity": [1.0]*4}
obs, _ = env.reset(seed=0, options=options)
action = self.policy.act(env, obs)
with_plan, peak_plan, risk_plan = self._project(env, self.policy, options, seed=1000)
without, peak_none, risk_none = self._project(env, NoRotation(), options, seed=1000)
```
It puts the real database numbers into the game, asks the model, then plays out the rest of the shift 200 times with the policy and 200 times with no rotation, to show the benefit.

---

## File: `ergosense/assistant/report.py` — the citation checker
```python
CITATION = re.compile(r"\[([a-z0-9_]+#\d+)\]")
def check(match):
    cid = match.group(1)
    if cid in retrieved_ids:
        valid.append(cid); return match.group(0)
    invalid.append(cid)
    return f"[citation removed: {cid}]"
text = CITATION.sub(check, text)
```
- A **regex** is a search pattern. This one finds things like `[job_rotation_guidance#3]`.
- If the ID was really retrieved, keep it. If not, replace it and record it as invalid.
- Then every numbered recommendation without a valid citation gets "(no supporting reference — check before acting)".

## File: `ergosense/assistant/retriever.py`
```sql
SELECT c.chunk_id, ..., 1 - (c.embedding <=> %s::vector) AS score
FROM doc_chunks c JOIN documents d USING (doc_id)
ORDER BY c.embedding <=> %s::vector LIMIT %s
```
`<=>` = pgvector's "cosine distance" (how different two meanings are). Smallest distance first = most similar paragraph first. If the embedding model can't load, it falls back to normal keyword search (`ts_rank`).

## File: `ergosense/assistant/llm.py`
```python
for provider in provider_order():          # auto = ["ollama", "openai"]
    if provider == "ollama":
        ready, why = _ollama_ready()        # is the server up and is the model pulled?
        ...
        return {"text": _chat_ollama(system, user), "provider": "ollama", ...}
raise LLMUnavailable("; ".join(problems))
```
It tries each option in order. If none works, it raises an error, and `report.py` catches it and writes the template report instead.

---

## File: `ergosense/backend/main.py` — the API (shortened)
```python
@app.post("/rotation/suggest", status_code=201)
def rotation_suggest():
    with db() as conn:
        plan = rotation.suggest_plan(conn, get_planner())
    hub.publish_threadsafe({"type": "plan", "plan": plan})
    return plan
```
`@app.post(...)` = "when someone sends POST to this address, run this function". `201` = "created". The new plan is also pushed to every open dashboard.

```python
def _on_ingested(result):
    hub.publish_threadsafe({"type": "event", **result})
```
The MQTT callback runs in a different thread from the web server. `publish_threadsafe` safely hands the message to the web server's event loop.

## File: `ergosense/dashboard/app.js`
```js
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onmessage = (msg) => {
  const data = JSON.parse(msg.data);
  if (data.type === "event") { state.latest[ev.worker_id] = ev; ...; renderAll(); }
};
ws.onclose = () => setTimeout(() => connectWebSocket(Math.min(delay * 2, 15000)), delay);
```
It opens the live line. On each message, it updates the worker card, the strain bar and the feed. If the line drops, it tries again after 1, 2, 4... up to 15 seconds.

## File: `ergosense/edge/overlay.py` — the webcam window
`compose()` builds the window: the camera view with the skeleton coloured by risk (risky body parts drawn thicker), plus a side panel with the big score, the 1–15 gauge, body-part bars, angles and a history line, plus a footer with the MQTT status and bandwidth.

## What we wrote vs. what came from libraries
| Ours | From libraries |
|---|---|
| REBA geometry and scoring, smoothing, labels | MediaPipe (body points), OpenCV (camera) |
| Pipeline processes and queue rules, outbox, publisher window | Python multiprocessing, SQLite, paho-mqtt |
| Event format and validation | json, uuid |
| Database schema, all SQL, transactions | PostgreSQL, psycopg, pgvector |
| RL environment, reward, baselines, planner, evaluation | Gymnasium, Stable-Baselines3 (the PPO algorithm itself), PyTorch |
| RAG chunking, queries, prompt, citation check, template | sentence-transformers (MiniLM), Ollama/Llama |
| Dashboard, webcam window, e2e check, 54 tests | FastAPI, matplotlib, pytest |
| Reused from our earlier FitCV project | `features.calculate_angle`, MediaPipe setup, pose dataset |
