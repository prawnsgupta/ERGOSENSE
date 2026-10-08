# Part 4 — Evaluation, Value, Business, Risks (Chapters 17–29)

---

# Chapter 17 — Existing Solutions and How We Compare

**Important honesty note:** we did **not** run or buy any competitor product. The descriptions below come from our general knowledge of these companies' public descriptions. **Every line is TO VERIFY.** Check each company's website before you name it on stage, and never quote a price you have not seen.

| Type | Example names (TO VERIFY) | What they generally do |
|---|---|---|
| Paper/app observation methods | REBA, RULA, NIOSH lifting equation (**EXTERNAL SOURCE:** Hignett & McAtamney 2000; Waters et al. 1993) | An expert fills a score sheet |
| Video AI ergonomics apps | TuMeke; VelocityEHS Industrial Ergonomics | Score postures from phone or uploaded videos |
| Wearable sensors | Soter Analytics; Kinetic | Devices worn on the body measure movement and give alerts |
| Workforce scheduling software | General rostering tools | Plan shifts, usually without posture data |

### Comparison (keep the "TO VERIFY" cells cautious)
| Feature | Paper REBA | Video AI apps | Wearables | **ERGOSENSE** |
|---|---|---|---|---|
| Hardware | Paper or a phone | Phone/camera | Device per worker | One webcam per station + a laptop |
| Continuous through the shift | No | TO VERIFY (often clip-based) | Yes | **Yes** |
| Video leaves the site | No | TO VERIFY (often uploaded) | No video | **No (only ~600-byte scores)** |
| Adds up the load per worker over a shift | Manual | TO VERIFY | Yes (TO VERIFY) | **Yes** |
| Plans job rotation | No | TO VERIFY | TO VERIFY | **Yes (RL, with approval)** |
| Respects who is trained where | No | TO VERIFY | TO VERIFY | **Yes** |
| Works offline / on-premises | Yes | TO VERIFY | TO VERIFY | **Yes** |
| Expert accuracy | **Highest** (trained human) | TO VERIFY | TO VERIFY | **Not validated against experts** |
| Commercial maturity | Mature | Mature products | Mature products | **Student prototype** |

### Where we are weaker (say this honestly)
- Commercial products are tested in real factories. We are not.
- Wearables measure movement directly. A single webcam estimates depth poorly.
- We have no validation against human expert REBA scores.

### Our strongest honest advantage
**The combination:** continuous camera-based REBA, privacy (no video sent), reliable offline-tolerant delivery, per-worker cumulative strain, and data-driven rotation planning with human approval, in one open-source system that runs on a laptop.

---

# Chapter 18 — Numerical Comparison

We have **no measured numbers for competitors**, so we only compare within our own system (against baselines). All numbers below are **OUR MEASUREMENT**.

### 18.1 Rotation policies (500 seeded 8-hour shifts; `python -m ergosense.rl.evaluate`)
| Metric | No rotation | Round-robin | Greedy | **PPO** |
|---|---:|---:|---:|---:|
| Final peak strain (mean ± std) | 691 ± 75 | 532 ± 62 | 437 ± 35 | **400 ± 25** |
| Workers over the limit (of 4) | 1.67 | 1.53 | 1.65 | **0.78** |
| Shifts with anyone over the limit | 100% | 100% | 86% | **48%** |
| Total strain (all workers) | 1513 | 1511 | 1506 | 1502 |
| Workers moved per shift | 0 | 26.5 | 11.9 | **10.1** |

Differences: PPO's peak is **42% lower** than no rotation and **8% lower** than greedy. It has **53% fewer** workers over the limit than greedy and **16% fewer** moves. Total strain is about the same for all, because rotation **shares** the work but cannot reduce it.

### 18.2 Live end-to-end run (`python -m ergosense.e2e_check`)
| Metric | Value |
|---|---:|
| Checks passed | 10 / 10 |
| Approvals during the shift | 4 |
| Actual peak strain at the end of the shift | 362 |
| Peak projected at hour 2 (with policy) | 370 |
| Peak projected at hour 2 (no rotation) | 632 |
| Workers over 400 | 0 |

### 18.3 Network
| Metric | Value |
|---|---:|
| Average event size | about 598–603 bytes |
| Raw 640×480 frame | 921,600 bytes |
| Ratio | about 1,540× smaller |
| Broker outage test | 96 buffered, 192/192 delivered, 0 out of order |
| Backend outage test | 48 queued by the broker, all delivered |
| `localhost` (IPv6) connection stall on Windows (before the fix) | about 10 s per connection |

### 18.4 Other
| Metric | Value |
|---|---:|
| PPO training time (500k steps, CPU) | 3.5 min |
| Evaluation of 4 policies × 500 shifts | about 7 s |
| Model file size | 546 KB |
| Loading-bay calibration from real poses | 81.8 strain/h (simulator 77.4) from 528 events |
| Automated tests | 54 passing (about 16–25 s) |
| RAG chunks | 20 from 3 documents; top-match similarity, e.g. 0.75 for "how often to rotate" |
| Template report generation | about 2–7 s |
| Webcam session test | 116 webcam events at S4, REBA 3–10 |

### NOT MEASURED (and how to measure)
| Metric | How to measure |
|---|---|
| Webcam FPS | Read it from the webcam window footer during a demo |
| End-to-end latency (camera → dashboard) | Log the edge timestamp vs the backend `received_at` (the column already exists): `SELECT avg(received_at - ts) FROM posture_events WHERE source='webcam'` |
| CPU/RAM/GPU usage | Task Manager during a demo, or Python `psutil` |
| REBA accuracy vs an expert | Record clips, have a trained person score them, compare with our scores (agreement %, mean difference) |
| LLM report time | Time the `POST /report` call with Ollama running |
| Energy use | A plug-in power meter |

---

# Chapter 19 — Efficiency

**Efficiency** = doing the job with less time, memory, money or energy.

**Big-O in simple words:** O(n) means "twice as much data → about twice as much work". O(1) means "the same work no matter how much data".

| Area | How efficient | Simple Big-O |
|---|---|---|
| REBA per frame | Fixed maths on 33 points | O(1) per frame |
| Smoothing | Sorts 15 numbers | O(1) (the window is fixed) |
| Network | ~600 B/s instead of a video stream | — |
| Saving one event | One indexed insert + one update | About O(log n) because of the indexes |
| A worker's latest events | Uses the `(worker_id, ts DESC)` index | O(log n + k) |
| Vector search | HNSW index (fast approximate search) | Roughly O(log n) |
| PPO decision | One small neural network pass | O(1); milliseconds (exact time NOT MEASURED) |
| Planner projection | 2 × 200 simulated futures | O(rollouts × hours) |
| Training | 3.5 min on a CPU, no GPU needed | — |
| Energy/cost | One laptop, free software, local LLM | NOT MEASURED in watts |

The biggest CPU cost is MediaPipe on every frame. This is why it has its own process.

---

# Chapter 20 — Feasibility

- **Technical:** **YES.** It is built and runs on one laptop (54 tests, a 10/10 end-to-end check, a webcam run).
- **Economic:** webcams are cheap, and all software is free and open source. **Real factory deployment costs: NOT ESTIMATED.**
- **Operational:** the supervisor only clicks "suggest" and "approve". Cameras need good placement (side-on, whole body visible).
- **Legal:** filming workers needs **consent** and must follow privacy laws (for example, India's DPDP Act 2023 or GDPR in Europe; **TO VERIFY** the exact obligations). We help by never storing or sending video, and by using pseudonymous IDs.
- **Ethical:** risk of being used to **watch or punish** workers. Rotation must stay a human decision, and the data should be used for safety only.
- **Infrastructure:** a local network is enough. No internet is needed after setup (the first run downloads the embedding model).
- **Scalability:**
  - 10 stations: works as it is.
  - 100 stations: one broker is fine. Partition the events table by month. Run more backend copies with MQTT shared subscriptions.
  - 1,000+ stations: a broker cluster, a Postgres replica and partitioning, monitoring, authentication + TLS.
  - "1 million users" does not really fit this product (a user is a factory). The realistic target is thousands of factories, each with its own edge + broker, sending summaries to a central service.
  - **The RL action space grows as n!** (factorial). For more than about 6 workers per group, we would need a new action design (for example, pairwise swaps or per-worker choices).

---

# Chapter 21 — SDG 9 Connection

- **United Nations (UN):** a group of almost all the countries in the world, working together.
- **SDGs:** 17 goals the UN set in 2015 for a better world by 2030.
- **SDG 9:** "Build resilient infrastructure, promote inclusive and sustainable industrialization and foster innovation." (**EXTERNAL SOURCE:** UN SDG 9)
  - **Industry** = factories and production.
  - **Innovation** = new ideas and technology.
  - **Infrastructure** = the basic systems a place needs (networks, buildings, machines).

| SDG 9 area | How our project contributes | Strength |
|---|---|---|
| 9.4 Upgrade industries with clean, efficient technology (**TO VERIFY** the target wording) | A low-cost digital safety system for existing factories, using old webcams | Direct, moderate |
| 9.5 Enhance research and innovation (**TO VERIFY** the wording) | Combines CV, RL and GenAI for ergonomics; open source | Direct, moderate |
| Sustainable industrialization | Healthier workers means a more sustainable workforce | Indirect |
| Resilient infrastructure | Offline-tolerant, local system with no cloud dependency | Indirect, weak–moderate |
| SDG 8.8 (safe work) and SDG 3 (health) | Fewer injuries | **Very direct**. Be honest: these fit very well too. |

**Why it qualifies as SDG 9:** it is industrial infrastructure innovation (a factory sensing + decision system). The *benefit* also strongly supports SDG 8 and SDG 3.

---

# Chapter 22 — Novelty

**Honest summary:** "Our novelty is mainly in the combination and application, not in inventing a new algorithm."

What is genuinely ours:
1. **A complete pipeline** from camera → REBA → cumulative strain → RL rotation → approval → cited report.
2. **RL rotation that respects training constraints**, via action masking, with human approval and an audit trail.
3. **Station-camera attribution in time**: events go to whoever was at that station at that moment, even if delivered late.
4. **Privacy-by-design edge**: only ~600-byte scores ever leave the laptop.
5. **Verified-citation reports**: our code removes sources the LLM invented.
6. **Practical REBA fixes found on real data**: a neck offset, and ignoring side-bend in side views.

Not new: REBA (2000), PPO, MediaPipe, RAG, MQTT. These are all existing tools.

---

# Chapter 23 — Social Impact
- **Direct:** workers (less overload on one body), supervisors (data instead of guessing), safety teams (which station is worst).
- **Indirect:** fewer injury days, longer careers, a better image for the company, and evidence for redesigning workstations.
- **Potential reach:** any factory with repetitive manual work. **A number of people is NOT ESTIMATED.**
- **Possible negative effects:** a feeling of being watched; misuse of the data to judge speed; false alarms or missed risks; too much trust in the AI. **Mitigation:** no video, pseudonymous IDs, human approval, and clear limitation notes.

---

# Chapter 24 — Startup Potential
- **Problem:** manual-handling injuries and unmanaged exposure.
- **Customer (who pays):** factory or warehouse owners, safety (EHS) departments.
- **User:** supervisors, safety officers.
- **Value:** cheap, continuous, private measurement plus a rotation plan plus reports for audits.
- **Fitting business models:** a per-station monthly subscription (software) with an on-premises edge kit (hardware + software). Possibly a one-time setup/consulting fee. **ASSUMPTION:** no pricing research has been done.
- **Not a fit:** consumer freemium apps or ads.

# Chapter 25 — Upscaling
| Stage | What changes | What breaks first |
|---|---|---|
| College prototype | One laptop, Docker | — |
| Pilot (1 factory line) | Real cameras, accuracy check with an ergonomist, login | Camera placement, lighting, accuracy |
| 10 factories | One edge box per line, central server, TLS + passwords, backups | Manual setup and support |
| 100 factories | Cloud or managed Postgres, device management, monitoring, a support team | Database size, updates to edge devices |
| 1,000+ factories | Multi-tenant cloud, broker cluster, data partitioning, security audits, a sales team | Operations and support cost |

Rough team size: 2–3 people in the pilot → 10–20 people at 100 factories. **Costs: NOT ESTIMATED.**

---

# Chapter 26 — Security
**Present (FACT):**
- Ports are bound to 127.0.0.1 only (`docker-compose.yml`).
- Input validation of every event (`events.decode`) and every API body (Pydantic).
- SQL uses parameters (`%s`), which protects against SQL injection.
- No video is stored or sent. Worker IDs are pseudonyms.
- The dashboard's Markdown renderer escapes HTML (stops script injection from report text).

**Missing (FUTURE WORK):** login and roles (who can approve), HTTPS/TLS, MQTT username/password and per-device topic permissions, rate limiting, encrypted backups, audit logging beyond approvals, and protection against **prompt injection** (our documents are our own, which lowers the risk, but there is no defence if someone edits them).

**Simple example:** right now, anyone on the same computer could open the dashboard and approve a plan. In a factory, you would need a supervisor login.

# Chapter 27 — Failure Cases
| What if... | What happens | How we handle it | Future |
|---|---|---|---|
| Network or broker stops | The edge can't send | The outbox stores events on disk and resends in order (**measured: 0 lost**) | Alert if the outbox grows |
| Backend stops | Nobody receives | The broker keeps messages (persistent session; **measured**) | Run 2 backends |
| Database stops | Can't save | Ingest retries with growing waits; the message isn't confirmed, so it isn't lost | A database replica |
| AI model file missing | — | Planner uses the greedy rule | — |
| LLM unavailable | — | Template report, clearly labelled | — |
| Embedding model can't load | — | Keyword search fallback | — |
| Wrong or bad data arrives | — | `decode` rejects it; database constraints block bad values | — |
| Duplicate message | — | Ignored using `event_id` | — |
| Two supervisors approve at once | — | Row lock: one wins, the other gets 409 | — |
| Plan puts two people on one station | — | Unique index fails and the whole transaction rolls back | — |
| LLM invents a source | — | Removed and flagged | Check sentence-level support |
| LLM gives bad advice with a real citation | **Not caught automatically** | A human reads the report | An expert review loop |
| Person not visible / bad lighting | Wrong angles | Visibility check per arm, median smoothing; "waiting" message | Multiple cameras |
| Traffic spike | The queues fill | Frames: the oldest is dropped. Events: the producer waits | Scale the backend |
| Biased simulator | RL may learn wrong habits | Calibration from real data for one station | Calibrate all stations |

# Chapter 28 — Limitations (say these yourself)
1. **REBA from one webcam is an approximation.** Depth is weak, wrist detail is coarse, and the load, grip and shoulder raise must be typed in.
2. **No validation against human experts.** Accuracy is NOT MEASURED.
3. **RL is trained in a simulator.** Only one station is calibrated, using exercise recordings (bent-over rows) as a stand-in for lifting.
4. **The safe limit (400) and the strain weights are design choices**, not medical limits.
5. **Rule-based posture labels; one person per camera.**
6. **The RL design only fits small teams** (4 workers × 4 stations; the action count grows as n!).
7. **No login or encryption**; local only.
8. **An LLM report on our machine has not been confirmed yet** (Ollama model not pulled at last check).
9. **Only one real webcam test session** so far.

# Chapter 29 — Future Work
- **1 week:** pull the Ollama model and save an LLM report; record webcam FPS and latency; film a demo video.
- **1 month:** compare our REBA scores with an expert on 30–50 clips; add a login; calibrate all 4 stations from webcam data.
- **6 months:** a pilot on one real line; multi-camera views; learn each worker's sensitivity; HTTPS/MQTT TLS.
- **1 year:** larger teams with a new action design; cloud dashboard across factories; validation study.
- **3 years:** a product (edge kit + subscription), certifications, and a research publication on RL rotation under training constraints with real data.
