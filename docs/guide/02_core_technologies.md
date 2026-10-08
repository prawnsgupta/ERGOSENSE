# Part 2 — Core Technologies Explained from Zero (Chapters 9–15)

---

# Chapter 9 — Database / DBMS

## What is data?
Data is written-down facts. "Karan's posture score was 8 at 10:15" is one piece of data.

## What is a database?
A database is a **very organised cupboard** for data. Each shelf (a **table**) holds one kind of thing. Each box on the shelf (a **row**) is one item. Each label on the box (a **column**) is one property.

## What is a DBMS?
A DBMS (Database Management System) is **the cupboard's keeper**. You ask it: "put this in" or "find me all the high scores of Karan". It does the work safely. Ours is **PostgreSQL**.

## Why does our project need a database?
- To remember every posture event (thousands per shift).
- To keep a running total of strain for each worker.
- To know who works where, and who worked where earlier.
- To store plans and who approved them (an audit trail).
- To store the guidance paragraphs for AI search.
- To store the reports.

## The concepts we actually use

| Concept | Simple meaning | Where we use it |
|---|---|---|
| Table | One shelf for one kind of thing | 12 tables (below) |
| Row | One item | One posture event |
| Column | One property | `reba_score` |
| Primary key | A unique name tag, never repeated | `event_id` (a UUID) |
| Foreign key | "This box points to that box"; it must exist | `posture_events.worker_id → workers` |
| Constraint | A rule the cupboard enforces | `reba_score BETWEEN 1 AND 15` |
| Normalization | Don't store the same fact twice; split lists into their own table | `worker_training` table instead of a list column |
| Index | A book's index, to find things fast | `(worker_id, ts DESC)` |
| Partial index | An index on only some rows | Only high-risk events (`reba_score >= 8`) |
| Partial unique index | "Only one allowed" among some rows | One *current* station per worker |
| Transaction | All steps happen, or none do | Approving a plan; saving an event + strain |
| Row lock (`FOR UPDATE`) | "I'm using this box; wait your turn" | Approving a plan |
| JOIN | Combine two shelves using a shared label | Strain + worker names |
| CRUD | Create, Read, Update, Delete | We create, read and update; delete is only used in reset scripts |
| Upsert (`ON CONFLICT`) | Insert, or update if it already exists | Strain totals |
| View | A saved question that looks like a table | `workers_v` |

## Our complete database (FACT: `ergosense/backend/schema.sql`)

```
workers ─┬─< worker_training >─┬─ stations
         │                     │
         ├─< shift_assignments >┤──> shifts <── rotation_plans ──< rotation_approvals
         │                     │       │
         ├─< posture_events >──┘       │
         └─< shift_strain >────────────┘
documents ─< doc_chunks          shifts ─< safety_reports
```
(`A ─< B` means "one A has many B".)

| Table | Why it exists | Important columns |
|---|---|---|
| `workers` | Who the workers are (pseudonymous) | `worker_id` (W01..), `display_name` |
| `stations` | The workplaces | `station_id` (S1..S4), `name`, `base_risk_level` |
| `worker_training` | Who is allowed to work where | (`worker_id`, `station_id`); both together form the primary key |
| `shifts` | Work periods | `status` ('active'/'closed'), `planned_hours` (8) |
| `shift_assignments` | Who was at which station, from when to when | `assigned_from`, `assigned_to` (empty = still there), `plan_id` |
| `posture_events` | Every score from the cameras | `event_id` (UUID primary key), `ts`, `duration_s`, `reba_score`, `risk_level`, `posture_label`, `segment_scores` (JSON), `source` |
| `shift_strain` | The running total per worker per shift | `cumulative_strain`, `high_risk_count`, `exposure_s`, `risky_s` |
| `rotation_plans` | Plans suggested by the AI | `assignments` (JSON), `policy`, `projected_peak_strain`, `projection`, `status` |
| `rotation_approvals` | Who approved which plan | `plan_id` UNIQUE, `approved_by`, `approved_at` |
| `documents`, `doc_chunks` | Guidance text for RAG | `chunk_id`, `content`, `embedding vector(384)`, `tsv` |
| `safety_reports` | Generated reports | `content_md`, `citations`, `is_fallback`, `provider` |

### Special rules written into the database (FACT)
- `shifts_one_active`: only one shift can be active.
- `shift_assignments_worker_current`: a worker can be at only one station right now.
- `shift_assignments_station_current`: a station can have only one worker right now.
- `rotation_approvals.plan_id UNIQUE`: a plan can be approved only once.

### Why `event_id` is a UUID made at the edge
A UUID is a long random name like `3f2b...`. If the network sends the same event twice, the second copy has the same name, so the database says "already have it" (`ON CONFLICT DO NOTHING`). This is called **idempotency**: doing it twice has the same effect as doing it once.

### The strain rule (FACT: `ergosense/strain.py`)
`strain = strain + weight × minutes`

| Risk level | REBA score | Weight per minute |
|---|---|---|
| negligible | 1 | 0 |
| low | 2–3 | 0.5 |
| medium | 4–7 | 1 |
| high | 8–10 | 2 |
| very high | 11–15 | 4 |

The safe limit is **400 points per shift** (**ASSUMPTION / design choice**, not a medical rule). This equals about 6.7 hours of medium-risk work or 3.3 hours of high-risk work.

### The approval transaction (the most important DB story)
Steps inside one transaction:
1. Lock the plan row (`SELECT ... FOR UPDATE`).
2. Check that the plan is still "proposed", the shift is active, and every worker is trained for their new station.
3. Mark the plan approved, and mark other proposals "superseded".
4. Close all current assignments (set `assigned_to = now()`).
5. Insert the new assignments.
6. Insert the approval record.

If any step fails, **everything goes back** (rollback).
**OUR TEST:** `test_failed_approval_changes_nothing` sends two workers to one station. Step 5 fails, and the test proves the database is exactly as before.
**OUR TEST:** `test_concurrent_approval_waits_on_row_lock` has two "supervisors" approve at the same time. Exactly one wins, and the other gets "already approved".

**HOW TO SAY THIS TO THE JUDGE:** "Approval changes four things at once. If they didn't happen together, we could end up with a plan marked approved but workers still at their old stations. So we use one transaction, and a row lock stops two supervisors approving the same plan."

---

# Chapter 10 — Reinforcement Learning

## What does "learning" mean?
Learning means getting better at something by experience.

## The dog analogy
You teach a dog to sit. When it sits, you give a treat (a **reward**). When it doesn't, no treat. After many tries, the dog sits more often. Nobody showed the dog a book of correct answers. It learned from **rewards**.

**Reinforcement Learning (RL)** is the same idea for a computer: it tries actions, gets points, and slowly prefers actions that give more points.

## The words
| Word | Simple meaning | In our project (FACT: `ergosense/rl/env.py`) |
|---|---|---|
| Agent | The learner (the dog) | The PPO rotation planner |
| Environment | The world it acts in | A simulated 8-hour shift with 4 workers and 4 stations |
| State | What the agent sees right now | 25 numbers: each worker's strain ÷ 400 (4), where each person is, as one-hot (16), hours left ÷ 8 (1), each station's heaviness ÷ 100 (4) |
| Action | A choice | One of the 24 ways to place 4 workers on 4 stations (`Discrete(24)`) |
| Masked action | A forbidden choice | Placing someone at a station they are not trained for. Only 11 of 24 are allowed with our training table. |
| Reward | Points after the action | Minus (increase in strain over the limit) minus 0.5 × (increase of the worst worker's strain) minus 2 × (people moved), all ÷ 100 |
| Episode | One full game | One 8-hour shift = 8 decisions |
| Policy | The agent's habit or brain | A small neural network (128 → 128 neurons) |
| Exploration | Trying new things | PPO picks somewhat random actions while training (entropy bonus 0.01) |
| Exploitation | Using what works | After training we use the most likely action (`deterministic=True`) |
| Training | Practice | 500,000 steps = 62,500 practice shifts |
| Q-value | "How good is this action here?" | **Not used directly.** PPO learns a "value" (how good a state is) and a policy, not a Q-table. |

## What happens in one hour of the simulation
1. The agent picks an assignment.
2. Each station adds random strain: `max(0, Normal(mean, std))`. The means are: S1 13.7, S2 30.3, S3 49.1, S4 81.8 strain per hour (**FACT:** `station_profiles.json`).
3. Each worker's added strain is multiplied by a hidden "sensitivity" between 0.85 and 1.20 (some bodies get tired faster; **ASSUMPTION** we built into the simulator).
4. The reward is calculated.

### Good and bad actions
- **Good:** moving the most tired worker to a lighter station before they cross 400.
- **Bad:** leaving someone on the loading bay all day, or moving everyone every hour for no reason.

### The clever reward design ("telescoping")
Each hour's reward is the *change* in the shift's total cost. So adding up all 8 rewards gives exactly: −(final excess + 0.5 × final peak + 2 × total moves) ÷ 100. **OUR TEST** `test_rewards_add_up_to_the_shift_objective` checks this.

### Why not supervised learning?
Supervised learning needs examples of the correct answer ("in this situation, the right rotation is X"). Nobody has such labelled data. RL learns by trying in a simulator, which we can run millions of times for free.

### Why do we need RL at all, instead of a simple rule?
We tested simple rules. Greedy got a peak of 437 with 1.65 workers over the limit; PPO got 400 with 0.78 (**OUR MEASUREMENT**). The decision depends on time left, every worker's load, training rules and the cost of moving. PPO learned to balance all of these together.

### After training
The model is saved in `ergosense/rl/models/ppo_rotation.zip` (546 KB). The backend loads it. When the supervisor clicks the button, the model looks at the real strain from the database and picks the next hour's assignment. The planner also simulates 200 futures with the plan and 200 without it, to show the expected benefit.

**HOW TO SAY THIS TO THE JUDGE:** "The agent is the rotation planner. The state is each worker's accumulated strain, where people are, and the time left. The action is one of 24 arrangements, with untrained ones blocked. The reward punishes going over the limit, a very tired worst-off worker, and moving people. We trained it on 62,500 simulated shifts in about 3.5 minutes."

---

# Chapter 11 — Generative AI

- **AI:** computers doing things that normally need human thinking.
- **Machine Learning (ML):** AI that learns from examples instead of fixed rules.
- **Deep Learning:** ML using big "neural networks" (many layers of simple maths units).
- **Generative AI:** AI that *creates* new things, like text or images.
- **LLM (Large Language Model):** a generative AI trained on huge amounts of text. It predicts the next word, again and again. Ours is **llama3.1:8b** (8 billion numbers inside it), running in **Ollama** on the laptop.
- **Prompt:** the instructions and question we give the LLM.
- **Context:** the extra information we put in the prompt (shift numbers, guidance paragraphs).
- **Token:** a small piece of a word. LLMs read and write tokens.
- **API:** a doorway where one program asks another program for something. We call Ollama's API at `http://127.0.0.1:11434/api/chat`.

## What our GenAI part does (FACT: `ergosense/assistant/report.py`)
It writes the **end-of-shift safety report** with these sections: Summary, Per-worker findings, Station findings, Recommended actions, Rotation decision explained, and Sources.

## RAG — Retrieval-Augmented Generation
**Analogy:** an open-book exam. Instead of the student answering from memory (which may be wrong), we first find the right pages of the book and give them to the student.

1. Our 3 guidance documents (REBA reference, safe lifting/NIOSH, job rotation) are cut into **20 sections ("chunks")**.
2. Each chunk is turned into 384 numbers (an **embedding**) by the MiniLM model. Similar meaning gives similar numbers.
3. These are stored in Postgres (pgvector).
4. For a report, we ask topic questions (for example "reduce deep trunk bending when lifting boxes from floor-level pallets"), find the closest chunks, and put them in the prompt.
5. The LLM must cite chunk IDs like `[safe_lifting_niosh#5]`.
6. **Our checker** removes any ID that wasn't actually given (written as "citation removed"), and flags recommendations without a valid source.

## Controlling hallucination (made-up facts)
- Low temperature (0.2), which means less "creative" writing.
- "Use ONLY the given data" in the system prompt.
- Numbers come from SQL, not from the model's memory.
- Citation checking (**OUR TEST** with a fake LLM that invents `not_a_real_chunk#7`: it was removed).
- **Limitation:** we check that *the sources exist*. We do **not** automatically check that every sentence matches its source.

## Why GenAI instead of...
| Alternative | Why not only that |
|---|---|
| Fixed rules / template | We do have it as a fallback. But it can't explain in flexible, natural language. |
| Database queries | They give numbers, not explanations. |
| Traditional ML | It classifies; it doesn't write reports. |
| Search only | It gives paragraphs, not a combined, tailored report. |

## Status
- **CURRENTLY IMPLEMENTED:** RAG, prompt, Ollama/OpenAI calls, citation checker, template fallback, tests with a fake LLM.
- **NOT CONFIRMED YET:** a real report written by llama3.1:8b on our machine. Ollama is installed, but the model had not been pulled when we last checked. Every real run so far used the template fallback. Run it and save one before the conference.
- **Cost per request:** with local Ollama, there is no per-request fee (only electricity). Generation time **NOT MEASURED** yet.

---

# Chapter 12 — Computer Networks

## What happens when I click a button?
You click "Suggest rotation" → your browser sends a **request** (a letter) → it travels to the **server** (the kitchen) → the server asks the **database** (the cupboard) → the server sends back a **response** (the meal) → the page shows it.

| Word | Simple meaning | In our project |
|---|---|---|
| IP address | The house address of a computer | `127.0.0.1` = "this same computer" |
| Port | A door number on that house | 8000 (backend), 1883 (MQTT), 5433 (Postgres), 11434 (Ollama) |
| TCP | A careful delivery service that checks every parcel arrives in order | Under MQTT, HTTP and Postgres |
| HTTP | The language of web requests | Dashboard ↔ backend |
| HTTPS | HTTP inside a locked envelope (encrypted) | **Not used** (everything is local). **FUTURE WORK** |
| REST API | A set of URLs that each do one job | `GET /workers`, `POST /rotation/suggest` |
| Client / server | Customer / shop | Browser / FastAPI |
| DNS | The phone book from names to addresses | Not needed (we use IP addresses) |
| Latency | How long one trip takes | **NOT MEASURED** end to end |
| Bandwidth | How much data per second | About 600 bytes per second per camera (**OUR MEASUREMENT**) |
| WebSocket | A phone line that stays open, so the server can talk anytime | Live dashboard updates |
| MQTT | A post office for tiny sensor messages | Edge → backend |
| Broker | The post office building | Mosquitto |
| Topic | The address on the envelope | `ergosense/events/S4` |
| QoS 1 | "Delivered at least once" with a receipt (PUBACK) | Used for all events |
| Persistent session | The post office keeps your mail while you're away | Backend uses `clean_session=False` |

## How our network is built (FACT)
- Edge → broker: MQTT QoS 1, at most 20 messages "in flight". Each is deleted from the outbox only after its receipt.
- Broker → backend: QoS 1. The backend sends its receipt **only after the database has saved** (`manual_ack=True`).
- Duplicates are ignored using the `event_id`.
- Browser ↔ backend: HTTP + WebSocket.
- All ports are bound to `127.0.0.1` (only this laptop can connect).

## Interesting network lesson we found (OUR MEASUREMENT)
On Windows, "localhost" first tried IPv6 (`::1`). Docker listened only on IPv4, so each database connection waited about 10 seconds before falling back. Using `127.0.0.1` fixed it.

## Bandwidth saving (OUR MEASUREMENT)
- Average event: about 598–603 bytes.
- One raw 640×480 colour frame: 921,600 bytes.
- That is about **1,540 times smaller**.
- At 1 event per second vs 15 frames per second of raw video: about 600 B/s vs 13.8 MB/s.

---

# Chapter 13 — Computer Vision

- **Image:** a grid of tiny coloured dots.
- **Pixel:** one dot. A 640×480 image has 307,200 pixels, each with 3 colour numbers.
- **Frame:** one image from a video.
- **Camera:** gives about 15–30 frames per second (our real FPS: **NOT RECORDED**; it is shown in the webcam window, so write it down).
- **Preprocessing:** we mirror the frame (like a mirror) and change the colour order for MediaPipe.
- **Feature:** a useful number taken from the image. For us: joint angles.
- **Model:** MediaPipe Pose, a ready-made deep learning model from Google. **We did not train it.**
- **Prediction:** 33 body points, each with x, y, z and "visibility" (how sure it is).

## What our CV part does (FACT: `capture.py`, `reba.py`)
1. OpenCV reads a frame.
2. MediaPipe gives 33 **world** points (in metres, centred between the hips).
3. We build a "body frame" (which way is up, left and front for this body), so angles don't depend on where the camera is.
4. We measure trunk bending, twist and side-bend; neck bending; upper-arm raise; elbow bend; wrist bend; knee bend; and whether the person stands on one leg.
5. The REBA tables give the score.
6. A median over 15 frames removes one-frame glitches.
7. Posture labels: bending (more than 60°), overhead reach, squatting, twisting, leaning (20–60°, our addition), neutral.

## Real-data lessons (OUR MEASUREMENT on FitCV recordings)
- Front-facing videos gave a fake "trunk lean" of about 39° (depth is hard for one camera).
- Side-on videos gave a fake "side bend" of 25–40°, so we ignore side bend in side view.
- The ear sits about 16° in front of the shoulder line in neutral posture, so we subtract a 15° neutral neck offset.
- **Conclusion:** put the camera side-on or at 45°.

## Why OpenCV and MediaPipe
OpenCV = the standard tool for camera access and drawing. MediaPipe = fast body points without training. Both were already working in FitCV.

---

# Chapter 14 — Machine Learning / Deep Learning (only what applies)

**ML basics:** training data → model → training → prediction → evaluation.

| Concept | Used in ERGOSENSE? |
|---|---|
| Features | YES: joint angles (for REBA, which is a formula, not learned) |
| Labels | **NO** supervised labels in ERGOSENSE |
| Training/validation/test split | Not in the supervised sense. RL is tested on **500 separate seeded shifts** (seeds 10,000–10,499), different from training. |
| Loss / optimizer | YES inside PPO (PPO's own loss, Adam optimizer, learning rate 0.0003) |
| Epoch | PPO: 10 passes over each 2,048-step batch of experience |
| Batch | 256 |
| Overfitting | Risk: PPO fits our simulator. Checked only on new seeds of the same simulator. |
| Accuracy / precision / recall / F1 | **NOT MEASURED for ERGOSENSE.** We have no hand-labelled REBA ground truth. |

**Important:** FitCV (our earlier project) has a supervised classifier (PINN + ST-GCN + Transformer/LSTM) with **in-sample** accuracy, for example 98.44% on 64 Squat windows (**OUR MEASUREMENT**). That number is in-sample (it was tested on its own training data), and that model is **not used** by ERGOSENSE. Do not present it as ERGOSENSE accuracy.

---

# Chapter 15 — Training Our Project

## What gets trained
Only the **PPO rotation policy**. MediaPipe, MiniLM and Llama are pretrained (ready-made). REBA is a formula.

## Currently (FACT / OUR MEASUREMENT)
1. **Data needed:** none collected by hand. The agent practises in a simulator.
2. **Where it comes from:** station profiles. S1–S3 come from our station simulator (`simulate.py`). S4 (loading bay) comes from **528 real events**: FitCV bent-over-row recordings replayed through the full pipeline with an 8 kg load → 81.8 strain/h (the simulator model gave 77.4).
3. **How much:** 500,000 steps = 62,500 simulated shifts.
4. **Cleaning/labelling:** not needed for RL.
5. **Split:** training seeds vs evaluation seeds 10,000–10,499.
6. **Model:** MaskablePPO with an MLP of [128, 128].
7. **Hardware:** laptop CPU (1 thread).
8. **Time:** 3.5 minutes (**OUR MEASUREMENT**).
9. **Parameters:** n_envs 8, n_steps 256, batch 256, n_epochs 10, lr 3e-4, gamma 0.99, gae_lambda 0.95, ent_coef 0.01.
10. **Loss:** PPO clipped objective + value loss + entropy bonus (the SB3 default).
11. **Optimizer:** Adam (the SB3 default).
12. **"Epochs":** 10 per update.
13. **Improving?** After training, a quick check on 200 new shifts: PPO peak 404 vs greedy 441 (`train.py` output).
14. **Testing:** `python -m ergosense.rl.evaluate` → 500 shifts, same seeds for all 4 policies.
15. **Overfitting control:** new seeds; random start strain and sensitivity. **Not tested** on real factory data.
16. **Metrics:** final peak strain, workers over the limit, % of shifts with anyone over, total strain, moves.
17. **After training:** the model is saved to a zip file, loaded by the backend, and used live.

## Larger scale in the future (FUTURE WORK)
- Calibrate every station from real webcam data over weeks.
- Learn each worker's real sensitivity from their data.
- Train with more workers and stations (the action space grows fast: 5 workers = 120 actions, 10 workers = 3.6 million, so a different action design would be needed).
- Validate with ergonomists before trusting it.
