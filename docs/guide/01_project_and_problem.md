# Part 1 — The Project, the Problem, and How It Works (Chapters 1–8)

---

# Chapter 1 — The Project in One Minute

## 1.1 What problem are we solving?

### A simple story

Imagine Karan. He works in a factory loading bay.
All day he picks up boxes from the floor and puts them on a trolley.
Every time, he bends his back down low.

One bend does not hurt him.
But he bends hundreds of times a day, every day.
After some months, his back starts to hurt.
One day he cannot come to work.

Nobody noticed this coming. Nobody was counting how much his back was working.

**That is the problem:** in factories, workers slowly hurt their bodies by working in bad body positions again and again. Nobody measures it while it is happening.

### Who has this problem?

- **Workers** in factories, warehouses and loading bays. They get hurt.
- **Supervisors.** They decide who works where, but they cannot see who is getting too tired.
- **Factory owners.** They lose workers to injury and pay for it.

### Why does the problem matter?

- Back, shoulder and wrist injuries from work are a very common work health problem in industry. **EXTERNAL SOURCE (TO VERIFY):** organisations like EU-OSHA and the US Bureau of Labor Statistics publish yearly numbers on "work-related musculoskeletal disorders". Look up the latest figure before quoting it.
- These injuries grow slowly. By the time someone feels pain, the damage is already done.

### How big is the problem?

**We do not have our own numbers on this.** Use official statistics from EU-OSHA, ILO, or the US BLS. **TO VERIFY** before you speak.

### What happens if we do nothing?

Workers keep doing the same hard job all day. Some get injured. Factories lose trained people. Nobody learns which station is the most dangerous.

### Why are existing solutions not enough?

- **Paper checklists** (like REBA, explained later): an expert watches a worker and fills a form. It is slow, and it is done only a few times a year.
- **Wearable sensors** (small devices worn on the body): they work, but each worker needs a device, which costs money and can be uncomfortable. **TO VERIFY** prices for specific products.
- **Video AI tools:** some companies analyse uploaded videos. Many work on recorded clips, not live through a whole shift, and they do not plan job rotation. **TO VERIFY** for each product (see Chapter 17).

---

## 1.2 What is our solution?

**Problem →** Workers hurt their bodies slowly, and nobody measures it.

**Our idea →** Use a normal webcam to watch body position, give it a "danger score", add up each worker's tiredness over the shift, and let a learning computer program suggest who should switch jobs, so nobody gets too tired.

**How it works →**
1. A camera watches a worker.
2. The computer finds the body's joints (shoulders, hips, knees...).
3. It measures angles (how much the back is bent, how high the arms are).
4. It turns the angles into a danger score from 1 to 15 (this is called **REBA**).
5. It sends only the small score over the network. **No video is sent.**
6. A database stores every score and adds up each worker's "strain" for the day.
7. A learning program (**Reinforcement Learning**) suggests job swaps every hour.
8. A supervisor approves the swap on a web page.
9. At the end of the day, an AI writer (**Generative AI**) writes a safety report that uses trusted guidance documents.

**Result →** In our simulation tests, the learning program kept the most tired worker much less tired than doing nothing (numbers in Chapter 18).

## 1.3 The project in one sentence

**"A webcam watches how workers bend and lift, the computer adds up how tired each body is getting, and a learning program suggests job swaps so nobody gets hurt."**

## 1.4 The project in 30 seconds (conference ready)

"Factory workers get back and shoulder injuries from bending and lifting all day, and nobody measures it while it happens. ERGOSENSE uses an ordinary webcam to give every posture a standard safety score called REBA. It sends only tiny numbers, never video, to a central database that adds up each worker's strain across the shift. A reinforcement-learning planner then suggests hourly job rotations so nobody crosses a safe limit, and a supervisor approves them. At the end of the shift, an AI writes a safety report where every recommendation is linked to a checked source."

## 1.5 The project in 1 minute (slightly more technical)

"We built on our earlier project FitCV, which already detected the human body with MediaPipe. On the edge laptop, three separate programs run together: one reads the camera, one calculates the REBA score, and one sends the result. They send small JSON messages of about 600 bytes using MQTT, a messaging system made for factory sensors. If the network breaks, messages are saved on disk and sent later, so nothing is lost. A FastAPI server stores them in PostgreSQL and updates each worker's strain in one safe transaction. A PPO reinforcement-learning agent, trained in a simulator of an 8-hour shift, suggests the next hour's job assignment. It respects which worker is trained for which station. Approval happens in a locked database transaction, so two supervisors cannot approve the same plan twice. The report uses RAG: it finds the right paragraphs from our guidance documents and gives them to an LLM. Our code then checks every citation."

## 1.6 The project in 5 minutes (complete high-level)

Say these parts in order:

1. **The problem (45 s).** Bending and lifting injuries build up slowly. Checks are done on paper a few times a year. Supervisors rotate jobs by habit, not by measured load.
2. **The idea (30 s).** Measure continuously with a cheap webcam. Add up the load per worker. Plan rotation with data. Write an evidence-based report.
3. **The edge (1 min).** Camera → MediaPipe finds 33 body points → our code calculates joint angles → the REBA tables give a score from 1 to 15 → a 15-frame median filter removes noise → one small event per second.
4. **The network (45 s).** MQTT with QoS 1 (delivery is confirmed). A disk "outbox" keeps events when the network is down. The backend confirms only after saving. Duplicate messages are ignored using a unique ID. We tested it by switching off the broker: 0 events lost.
5. **The database (30 s).** PostgreSQL tables for workers, stations, shifts, events, strain and plans. Approval is one all-or-nothing transaction with a row lock.
6. **The AI planner (1 min).** An 8-hour shift simulator. The agent picks one of the 24 possible worker-to-station arrangements every hour. Rewards punish going over the limit, a very high "worst worker", and moving people. On 500 test shifts, PPO had the lowest peak strain (400 vs 691 with no rotation).
7. **The report (30 s).** RAG over 3 guidance documents, using a local LLM (Ollama) or an API, with citation checking and a template fallback.
8. **Honesty (30 s).** A webcam REBA score is an approximation. RL results come from simulation. The safe limit is a design choice, not a medical rule.

---

# Chapter 2 — The Real-World Problem

## What is happening today?
In many factories, people do the same physical task for the whole shift: lifting, bending, reaching up, twisting. Safety experts sometimes come and check postures with a paper form. The rest of the time, nobody measures anything.

## Who suffers?
- The worker (pain, injury, lost income).
- The company (lost work days, retraining, compensation).
- Co-workers (they cover extra work).

## Why does it happen? What causes it?
- **Repetition:** the same movement hundreds of times.
- **Bad positions:** a back bent more than 60°, arms above the shoulders, twisting while lifting.
- **Load:** heavy boxes.
- **No recovery:** no change of task, so the same body part keeps working.
- **Unfair spreading of work:** one person always gets the heaviest station.

## What do existing systems do?
1. **Observation methods** like REBA, RULA and the NIOSH lifting equation. These are standard methods, done by an expert on paper or in an app. They give very good knowledge, but only a "snapshot".
2. **Wearable sensors** measure body movement continuously. They work, but they need a device per worker. **TO VERIFY** product details.
3. **Video-based AI apps** score postures from recorded or uploaded videos. **TO VERIFY** product details.
4. **Job rotation by spreadsheet or habit.** The supervisor decides from experience.

## Why are they not enough?
- Paper checks are rare and slow.
- Wearables cost money per worker and must be charged and worn.
- Most tools measure risk but **do not plan who should do what next**.
- Rotation planning usually does not use measured data.

## What gap exists?
Nobody (as far as we checked; see Chapter 17) joins all of these together in one cheap system:
**continuous measurement + adding up the load per worker + data-driven rotation planning + an explained report.**

## How our project fills that gap
- Cheap measurement: a normal webcam.
- Continuous: every second, the whole shift.
- Per-worker strain: the database adds it up.
- Planning: the RL agent suggests who moves.
- Human control: the supervisor approves.
- Explanation: the AI report cites trusted guidance.

**HOW TO SAY THIS TO THE JUDGE:**
"Current tools either measure posture or plan work. They rarely do both. We connect measurement, cumulative load, planning and reporting in one low-cost system."

---

# Chapter 3 — How Our Project Works

## The real path of information (from the code)

```
Worker in front of webcam
   ↓ (light)
Camera → capture program (MediaPipe finds 33 body points)
   ↓ (queue inside the laptop)
Scoring program (angles → REBA score → smoothing)
   ↓ (queue inside the laptop)
Publisher program (saves to a disk "outbox", then sends)
   ↓ (MQTT message over the network, about 600 bytes)
Mosquitto broker (the "post office")
   ↓ (MQTT)
Backend server (FastAPI) – checks the message, saves it
   ↓
PostgreSQL database (stores the event, adds strain)
   ↓ (WebSocket push)
Dashboard in the web browser (live cards, bars, feed)
   ↓ (supervisor clicks "Suggest rotation")
RL planner (trained PPO model) → proposed plan saved in the database
   ↓ (supervisor clicks "Approve")
Approval transaction in the database → new worker-station assignments
   ↓ (end of shift: "Generate report")
Report generator: database facts + retrieved guidance (RAG) → LLM → citation check
   ↓
Report shown on the dashboard + saved as a file and in the database
```

## Explaining every arrow

1. **Worker → camera.** The camera just sees light. No recording is saved.
2. **Camera → capture program.** OpenCV reads each picture (a "frame"). MediaPipe (a ready-made body detector from Google) finds 33 body points. **FACT:** `ergosense/edge/capture.py`.
3. **Capture → scoring (queue).** The points go into a "queue", which is a waiting line inside the laptop. Only the points travel, never the picture. **FACT:** `frames_q` in `pipeline.py`.
4. **Scoring.** Our code measures angles and applies the REBA tables. It smooths over 15 frames and makes one "event" per second. **FACT:** `reba.py`, `scoring.py`.
5. **Scoring → publisher (queue).** The event goes into another waiting line.
6. **Publisher → disk outbox → network.** The event is first saved in a small file database (SQLite). Then it is sent with MQTT. It is deleted from the outbox only after the broker says "got it". **FACT:** `outbox.py`, `publisher.py`.
7. **Broker.** Mosquitto receives messages on the topic `ergosense/events/S4` (S4 = station 4) and passes them to anyone subscribed. It runs in Docker.
8. **Broker → backend.** The backend is subscribed to `ergosense/events/+` (the `+` means "all stations"). It checks the message format. **FACT:** `ingest.py`, `events.py`.
9. **Backend → database.** In one transaction, it saves the event and adds strain to the right worker. If the event has no worker name, the backend looks up who was assigned to that station at that time. **FACT:** `repository.py: ingest_event`.
10. **Backend → dashboard.** It pushes the new event through a WebSocket (an always-open connection). **FACT:** `live.py`, `dashboard/app.js`.
11. **Dashboard → planner.** Clicking the button calls `POST /rotation/suggest`. The planner reads the strain from the database, asks the PPO model, and simulates the future 200 times. **FACT:** `rl/planner.py`, `backend/rotation.py`.
12. **Approve.** `POST /rotation/{id}/approve` runs one locked transaction. **FACT:** `rotation.py: approve_plan`.
13. **Report.** `POST /report/{shift_id}` gathers facts with SQL, retrieves guidance chunks with vector search, asks the LLM, checks the citations, and saves the result. **FACT:** `assistant/report.py`.

## Where each technology is used

| Question | Answer (FACT) |
|---|---|
| Where is Computer Vision? | Steps 2–4 (MediaPipe + our angle code) |
| Which database? | PostgreSQL 16 with the pgvector add-on; also SQLite for the edge outbox |
| Which models? | MediaPipe Pose (ready-made), PPO policy (we trained it), all-MiniLM-L6-v2 (ready-made embedding model), llama3.1:8b through Ollama (ready-made LLM, optional) |
| Where is RL? | Step 11 (the rotation planner) |
| Where is GenAI? | Step 13 (the report) |
| Frontend ↔ backend? | HTTP requests for buttons, a WebSocket for live updates |
| What does the user see? | The webcam window with the score, and a dashboard web page |

## What happens when something goes wrong? (short; details in Chapter 27)
- Network or broker down → events are saved on the laptop's disk and sent later. **OUR MEASUREMENT:** 0 lost.
- Backend down → the broker keeps the messages. **OUR MEASUREMENT:** 48 queued, all delivered.
- No LLM → a template report is made, clearly marked as such.
- No trained model → the greedy rule is used instead.

---

# Chapter 4 — Complete Project Workflow

### A. Simple workflow (zero technical knowledge)
1. A camera looks at a worker.
2. The computer says "this position is dangerous: 8 out of 15".
3. The computer writes it in a notebook (the database).
4. It adds up how tired each worker is.
5. Every hour, a "smart helper" says: "Asha, swap with Karan."
6. The boss says yes or no.
7. At the end of the day, a "robot writer" writes a safety letter.

### B. Technical workflow (for a professor)
MediaPipe world landmarks (33 × [x, y, z, visibility]) → body-frame angles → REBA segment scores → Tables A/B/C + load/coupling/activity → median filter (15 frames) → event JSON (UUID, station, timestamp, duration, score, risk, label, segment scores, angles) → SQLite outbox → MQTT QoS 1 → Mosquitto (persistent session) → paho subscriber (manual ack after commit) → PostgreSQL `INSERT ... ON CONFLICT DO NOTHING` + strain upsert in one transaction → WebSocket broadcast → MaskablePPO policy on a 25-number state → plan + Monte Carlo projection → `SELECT ... FOR UPDATE` approval transaction → RAG (MiniLM + pgvector HNSW) + LLM → citation verification.

### C. Internal workflow (inside each part)
| Component | What happens inside |
|---|---|
| Capture process | read frame → mirror it → MediaPipe → put points in `frames_q` (drop the oldest if full) → draw the window |
| Scoring process | take points → `assess()` → `ScoreSmoother` → `StaticPostureTracker` → every 1 s `make_event()` → `events_q` |
| Publisher process | take event → JSON → `Outbox.append` → `pump()` sends up to 20 at a time → on "got it" `Outbox.remove` |
| Broker | receives on a topic → keeps the message for subscribers (on disk if they are offline) |
| Ingest | `decode()` validates → `ingest_event()` transaction → `client.ack()` → WebSocket broadcast |
| Planner | read strain/assignment/training → build env state → PPO picks action → 200 simulated futures for each option |
| Approval | lock plan → check → update status → close old assignments → insert new ones → audit row |
| Report | SQL facts → topic queries → retrieve chunks → prompt → LLM → verify citations → save |

### D. User workflow (the supervisor)
1. Start Docker, the backend, and the cameras (or the simulator).
2. Open `http://127.0.0.1:8000`.
3. Watch the worker cards and strain bars.
4. About every hour, click **Suggest rotation**, read the plan, and click **Approve** (or ignore it).
5. At the end, click **Generate report** and read it.

### E. Data workflow
Camera pixels (about 900 KB per frame) → 33 points (about 0.5 KB, only inside the laptop) → 1 event per second (about 600 bytes, sent over the network) → 1 row in `posture_events` + an update to the `shift_strain` row → sums and averages for the report.

### F. AI workflow
- **Perception AI** (MediaPipe): picture → body points. Ready-made.
- **Rules** (REBA): angles → score. A formula, not learned.
- **Decision AI** (PPO): strain state → assignment. We trained it.
- **Language AI** (LLM + RAG): facts + guidance → report. Ready-made model, our prompt and checker.

### G. Database workflow
- **Write:** each event is one INSERT, plus a strain update (or nothing if it is a duplicate).
- **Read:** the dashboard uses `/state`, which reads the workers, assignments, strain and latest events.
- **Plan:** one INSERT into `rotation_plans`.
- **Approve:** UPDATE + UPDATE + INSERT + INSERT in one transaction.
- **Report:** GROUP BY queries, plus a vector search on `doc_chunks`, plus one INSERT into `safety_reports`.

### H. Network workflow
| From → To | Protocol | Why |
|---|---|---|
| Edge → broker | MQTT over TCP, port 1883 | Small, reliable sensor messages |
| Broker → backend | MQTT, port 1883 | Subscribe to all stations |
| Backend → Postgres | PostgreSQL protocol over TCP, port 5433 | Database queries |
| Browser → backend | HTTP, port 8000 | Button clicks (REST API) |
| Backend → browser | WebSocket, port 8000 `/ws` | Live push |
| Backend → Ollama | HTTP, port 11434 | LLM request |

### I. Reinforcement Learning workflow
**State → Action → Reward → New State → Learning**
- **State** = what the agent sees: each worker's strain, where each person works, hours left, and how heavy each station is.
- **Action** = choose one of the 24 ways to place 4 workers on 4 stations for the next hour.
- **Reward** = a score. It goes negative if someone goes over the limit, if the most-tired person gets more tired, or if people are moved.
- **New state** = one hour later, everyone's strain has grown.
- **Learning** = after many thousands of practice shifts, PPO adjusts its "brain" (a small neural network) to pick actions that lose fewer points.

### J. Generative AI workflow
**Input** (shift facts from the database) → **Context** (guidance paragraphs found by search) → **Model** (llama3.1:8b, or another API, or the template) → **Generated output** (a Markdown report) → **Our check** (remove fake citations, flag unsupported advice).

---

# Chapter 5 — System Architecture

```
┌──────────────── EDGE LAPTOP ─────────────────┐
│ Webcam → [Capture] → [Scoring] → [Publisher] │ ← three OS processes
│                ↑ display ←┘          │ SQLite outbox
└──────────────────────────────────────┼───────┘
                                       │ MQTT (QoS 1)
                          ┌────────────▼────────────┐
                          │  Mosquitto (Docker)      │
                          └────────────┬────────────┘
┌──────────────── BACKEND (FastAPI, Python) ─────────────────┐
│ MQTT ingest → PostgreSQL ← REST API ← dashboard buttons     │
│ WebSocket hub → dashboard        RL planner (PPO)           │
│ Report generator → retriever (pgvector) → LLM (Ollama/API)  │
└──────────────────────────────┬──────────────────────────────┘
                     ┌─────────▼─────────┐
                     │ PostgreSQL 16 +   │  (Docker)
                     │ pgvector          │
                     └───────────────────┘
```

| Part | Present? | What it is in our project |
|---|---|---|
| Frontend | **YES** | Plain HTML, CSS and JavaScript dashboard + the OpenCV webcam window |
| Backend | **YES** | FastAPI (Python) |
| Database | **YES** | PostgreSQL 16 + pgvector; SQLite outbox at the edge |
| AI models | **YES** | MediaPipe Pose, PPO, MiniLM embeddings, optional LLM |
| Trained ML | **YES (RL only)** | PPO policy. We trained no other ML model for ERGOSENSE. |
| RL | **YES** | `ergosense/rl` |
| GenAI | **YES** | `ergosense/assistant` (the LLM path needs Ollama with the model pulled, or an API key) |
| APIs | **YES** | REST endpoints + WebSocket |
| Network | **YES** | MQTT, HTTP, WebSocket, TCP |
| Cloud | **NO** | Everything is local on purpose. **FUTURE WORK** |
| Authentication | **NO** | No login. **FUTURE WORK** (Chapter 26) |
| Security | **PARTIAL** | Ports open only on 127.0.0.1 (this computer only), input validation, no video leaves the edge |
| Storage | **YES** | Docker volumes for Postgres and Mosquitto; report files |
| Monitoring | **BASIC** | `/health` endpoint and logs. No monitoring dashboards. |

**In very simple words:** the laptop near the worker does the looking and scoring. A post office (the broker) carries small notes. The main program (the backend) writes them in the big notebook (the database), shows them on a screen (the dashboard), asks the smart planner for job swaps, and asks the writer AI for a report.

---

# Chapter 6 — Complete Technology Stack (only what we actually use)

| Technology | What it does | Why we use it | If we didn't use it |
|---|---|---|---|
| Python 3.10 | The programming language for everything except the dashboard | All the AI libraries are in Python; FitCV was in Python | We would rewrite everything |
| MediaPipe 0.10.21 | Finds 33 body points in a picture | Fast on a CPU, no training needed, already used in FitCV | We would need to train our own body detector |
| OpenCV 4.11 | Reads the camera, draws the window | Standard image library | No camera input and no window |
| NumPy | Maths on lists of numbers | Fast angle maths | Slow, messy maths |
| FitCV `features.py` | `calculate_angle` | Reuse of our earlier work | Rewrite the same function |
| paho-mqtt 2.1 | MQTT client in Python | Official, widely used | No MQTT |
| Eclipse Mosquitto 2.0 | MQTT broker | Small, free, standard | Nothing to pass messages through |
| SQLite (Python built-in) | Outbox file at the edge | Survives crashes, no server needed | Events lost when the network breaks |
| FastAPI + Uvicorn | Web server and API | Simple, fast, automatic `/docs` page | Write a server by hand |
| WebSockets | Live push to the browser | No refreshing needed | The dashboard would need to keep asking |
| PostgreSQL 16 | The main database | Transactions, constraints, locks, JSON, vector search | Data could become inconsistent |
| pgvector | Vector search inside Postgres | RAG search in the same database | We would need another tool, like FAISS |
| psycopg 3 + pool | Python ↔ Postgres | Modern driver, connection reuse | No database access |
| Gymnasium 1.4 | The standard way to define an RL "game" | Works with Stable-Baselines3 | Write our own interface |
| Stable-Baselines3 + sb3-contrib 2.9 | PPO and MaskablePPO | Tested algorithms | Write PPO ourselves (risky) |
| PyTorch 2.14 (CPU) | The engine under SB3 and sentence-transformers | Required by them | Nothing trains |
| sentence-transformers + all-MiniLM-L6-v2 | Turns text into 384 numbers | Small, runs on a CPU | No semantic search |
| Ollama + llama3.1:8b (optional) | Local LLM | Free, private, runs on our GPU | Use an API or the template |
| httpx | Calls the LLM over HTTP | Simple | — |
| Matplotlib, pandas | Charts and tables for evaluation | Standard | No result chart |
| Docker + Docker Compose | Runs Postgres and Mosquitto | One command to set up | Manual installs |
| pytest | 54 automated tests | Proves the code works | No proof |
| Git + GitHub Actions file | Version history + CI config | Track changes, auto-test | No history |
| HTML/CSS/JavaScript | Dashboard | No build step, works offline | — |

**Not used (do not claim them):** React, Node.js, Flask, MySQL, MongoDB, Redis, TensorFlow, LangChain, Kubernetes, AWS, any cloud service.

---

# Chapter 7 — Why Did We Choose This Technology?

### Why PostgreSQL? (vs MySQL vs MongoDB)
| Need | PostgreSQL | MySQL | MongoDB |
|---|---|---|---|
| Linked tables (worker ↔ station ↔ shift) | Excellent | Good | Weaker (documents, fewer joins) |
| All-or-nothing transactions with row locks | Yes | Yes | Yes, but less natural |
| Partial unique index ("one current station per worker") | **Yes** | No direct support | Partial |
| JSON column | JSONB, good | JSON | Native |
| Vector search in the same database | **pgvector** | Not standard | Atlas only (cloud) |
| Cost | Free | Free | Free (community) |

**Simple reason:** our data is like a set of connected tables, and we needed safe transactions plus vector search in one place.

### Why MQTT? (vs HTTP vs Kafka)
- **HTTP:** each message needs a request and a reply, and both sides must be online.
- **Kafka:** very powerful, but heavy to run for a small project.
- **MQTT:** tiny messages, made for sensors, built-in delivery confirmation (QoS), and the broker keeps messages for offline receivers.

### Why FastAPI? (vs Flask vs Django)
FastAPI has built-in WebSocket support, checks input data automatically (Pydantic), and makes an automatic `/docs` page. Flask needs add-ons for these. Django is bigger than we need. FitCV already used FastAPI.

### Why PPO? (vs DQN vs Q-table vs plain rules)
- **Q-table:** the strain values are continuous numbers, so there are too many states to fill a table.
- **DQN:** works, but is usually more sensitive to settings.
- **PPO:** stable, standard, and has an "action masking" version for forbidden actions.
- **Rules (greedy):** we compared against one. PPO was better (Chapter 18).

### Why MediaPipe? (vs OpenPose vs YOLO-Pose)
MediaPipe runs in real time on a laptop CPU, gives 3D "world" points in metres, and FitCV already used it with working versions. **We did not measure the alternatives.**

### Why Ollama + Llama 3.1 8B? (vs a cloud API)
It is free, private (data stays on the laptop), and fits the 8 GB GPU. A cloud API is still supported through `.env`.

### Why pgvector? (vs FAISS vs Pinecone)
We have only 20 chunks, so all three are instant. pgvector keeps everything in one database: no extra service and no extra file to keep in sync.

### Why plain HTML/JS? (vs React)
No build tools and fewer things that can break. It works offline. The dashboard is small.

**HOW TO SAY THIS TO THE JUDGE:** "We chose each tool for one job: MQTT for unreliable factory networks, Postgres for safe linked data, PPO for decisions under rules, and a local LLM for privacy. Each choice is explained in our viva notes."

---

# Chapter 8 — How Each Technology Contributes

For each one: **What is it → What does it do here → Where → If removed → Why better.**

1. **MediaPipe** — a ready-made "body finder". It gives 33 points per frame. Used in `capture.py`. Without it there is no posture data, and everything depends on it. It is fast on a CPU.
2. **REBA code (ours)** — a rule book that turns angles into a score from 1 to 15. Used in `reba.py`. Without it, the points have no meaning for safety. It is a published standard method.
3. **Multiprocessing queues** — three workers passing a tray. Used in `pipeline.py`. Without them, a slow network could freeze the camera.
4. **MQTT + Mosquitto** — a post office for small notes. Without it, the edge and the backend must always be online together.
5. **SQLite outbox** — a "to-send" box on the laptop disk. Without it, events are lost in a network cut.
6. **PostgreSQL** — the main notebook with strict rules. Without it there is no history, no strain totals, no safe approvals.
7. **FastAPI** — the "waiter" that takes requests from the browser. Without it, the dashboard cannot talk to anything.
8. **WebSocket** — an open phone line to the browser. Without it, the page is not live.
9. **Gymnasium + PPO** — a practice game and a learning method. Without them there is no smart rotation planning; only the simple rules are left.
10. **MiniLM + pgvector** — a meaning-based search engine. Without it, the LLM has no trusted text to quote.
11. **LLM** — the writer. Without it, the template still writes a plainer report.
12. **Docker** — boxes that run the database and broker with one command. Without it, setup is manual.
13. **pytest** — an automatic checker. Without it, there is no quick proof that changes did not break anything.
