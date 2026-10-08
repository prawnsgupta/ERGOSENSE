# Part 5 — Presentation, Questions, Vocabulary, Revision (Chapters 30–37)

---

# Chapter 30 — Conference Presentation Script (about 8 minutes)

**Opening (20 s).** "Good morning. Imagine lifting boxes from the floor 500 times a day. One lift is fine. The 500th one hurts. Our project, ERGOSENSE, measures that hidden load and helps share it fairly."

**Problem (45 s).** "Back and shoulder injuries from work build up slowly. Today, safety experts check postures with a paper form, a few times a year. Between checks, nobody measures anything, and supervisors rotate jobs by habit."

**Existing solutions (30 s).** "There are paper methods like REBA, video apps, and wearable sensors. They measure risk well, but most don't plan who should do what next, and many send video or need a device per worker."

**Our solution (45 s).** "We use an ordinary webcam. The computer finds the body's joints, calculates the standard REBA safety score, and sends only a tiny number — never video. A database adds up each worker's strain over the shift. Every hour, a learning program suggests job swaps, and a supervisor approves them. At the end, an AI writes a safety report with checked sources."

**Architecture (60 s).** Point at the diagram: "On the edge laptop, three programs run side by side: camera, scoring, sending. They talk to a central server through MQTT, a messaging system made for factory sensors. If the network breaks, messages wait on disk. The server saves everything in PostgreSQL and pushes live updates to the dashboard."

**Technology (45 s).** "Computer vision with MediaPipe. A PostgreSQL database with safe transactions. PPO reinforcement learning. RAG with a local Llama model. Docker to run it all."

**Demo (2–3 min).** Bend → the score goes red. Show the dashboard bars. Stop the broker → "offline, buffering" → start it → events catch up. Click Suggest → show the plan → Approve → try approving again → 409. Generate the report → show the citations.

**Results (45 s).** "On 500 simulated shifts, the worst worker's strain was 691 with no rotation, 437 with a greedy rule, and 400 with our PPO agent, with half as many workers over the limit as greedy and fewer moves. In a live end-to-end run, nobody crossed the limit. Each event is about 600 bytes, around 1,500 times smaller than a video frame, and we lost zero events during a broker outage."

**SDG 9 (30 s).** "It upgrades existing factories with low-cost, innovative technology — SDG 9.4 and 9.5 — and the benefit is safer work, which also supports SDG 8."

**Innovation (20 s).** "The algorithms are standard. Our contribution is the combination: continuous, private measurement plus cumulative strain plus constraint-aware RL rotation with human approval plus verified reports."

**Business (20 s).** "A per-station subscription with an edge kit, sold to factory safety teams."

**Future work and honesty (30 s).** "Webcam REBA is an approximation, and our RL is trained in a simulator. Next, we want to compare against an expert ergonomist and run a pilot on a real line."

**Closing (10 s).** "ERGOSENSE: measure the hidden load, share it fairly, explain it clearly. Thank you."

---

# Chapter 31 — Question and Answer Bank

Format: **S** = short (10–20 s) · **M** = medium (30–60 s) · **D** = deep (technical judge) · **U** = what it really means (so you understand it, not memorise it).

## Basic

**Q1. What is your project?**
- S: A webcam-based system that scores workers' posture risk, tracks their tiredness through the shift, and suggests job rotations.
- M: Add the parts: REBA score, MQTT, database, RL planner, approval, AI report.
- D: Edge CV with REBA → MQTT QoS 1 + outbox → FastAPI/PostgreSQL with idempotent ingest → MaskablePPO planner → transactional approval → RAG report with citation verification.
- U: Watch → score → add up → plan → explain.

**Q2. Why did you make it?** S: Injuries from repeated bending are common and invisible until too late. U: We wanted to measure what nobody measures daily.

**Q3. Who will use it?** S: Factory supervisors and safety officers. Workers benefit.

**Q4. Why is this problem important?** S: It's a major cause of work injury (cite an EU-OSHA or BLS number — **TO VERIFY**). M: It causes lost workdays and cost. It is preventable by sharing load and redesigning stations.

**Q5. What does the user see?** The webcam window (score, risky body parts) and a dashboard (worker cards, strain bars, plans, report).

## Technical

**Q6. Why Python?** S: All the CV and AI libraries we need are in Python, and FitCV was Python. D: MediaPipe, SB3, sentence-transformers, FastAPI.

**Q7. Why PostgreSQL?** S: Linked data, safe transactions, and vector search in one place. D: Partial unique indexes, `FOR UPDATE`, JSONB, pgvector.

**Q8. Why MQTT and not HTTP?** S: MQTT is built for sensors on unreliable networks. M: Small messages, delivery receipts (QoS 1), and the broker stores messages for offline receivers. With HTTP, both sides must be online. D: Persistent session, manual ack after commit, an outbox at the edge.

**Q9. Why three processes?** S: So a slow network never freezes the camera. D: The Python GIL; separate processes use separate cores; bounded queues; drop-oldest for frames, blocking for events.

**Q10. Why FastAPI?** Built-in WebSockets, input checking, automatic docs; FitCV used it.

**Q11. Why WebSocket?** The server pushes updates instantly instead of the page asking again and again.

**Q12. Why Docker?** One command starts the database and broker, the same way on any computer.

**Q13. Why is there no cloud?** On purpose: privacy, works without internet, and cost. Cloud is future work for multiple factories.

**Q14. How do you avoid duplicates?** Each event has a unique ID made at the edge, and the database ignores repeats (`ON CONFLICT DO NOTHING`). Strain is added in the same transaction.

**Q15. What happens if two supervisors approve at the same time?** The row lock (`FOR UPDATE`) makes the second one wait. Then it sees "approved" and gets error 409. There is an automated test for this.

## AI / ML

**Q16. What is AI here?** Three AI pieces: MediaPipe (seeing), PPO (deciding), Llama (writing). The REBA score itself is a formula.

**Q17. How did you train the model?** S: Only the PPO planner is trained: 500,000 simulated steps in 3.5 minutes on a CPU. U: The other models were already trained by their makers.

**Q18. What data did you use?** S: FitCV's recorded human pose dataset (real people), to test REBA and to calibrate the loading bay (528 events), plus a simulator. We used no labelled factory dataset.

**Q19. How accurate is it?** S: **We have not measured REBA accuracy against a human expert yet. I don't want to give a made-up number.** M: We checked that the angles are correct on constructed skeletons (within 1°) and that real recordings behave sensibly: bent-over rows score higher than upright curls, squats show more than 90° knee bend. An expert comparison is our next step.

**Q20. Is the FitCV 98% accuracy yours?** S: That's FitCV's exercise classifier, measured in-sample (on its own training data). It is not part of ERGOSENSE's scoring, so we don't claim it.

## Reinforcement Learning

**Q21. Why RL?** S: Rotation is a sequence of decisions over the day, with trade-offs. RL learns that from practice. Our tests show it beats simple rules.

**Q22. What is the agent / state / action / reward?** Agent = the planner. State = 25 numbers (strain, positions, hours left, station load). Action = 1 of 24 arrangements (only valid ones allowed). Reward = −(increase over the limit + 0.5 × increase of the worst worker + 2 × moves) ÷ 100.

**Q23. Why not supervised learning?** There are no examples of "correct rotations" to learn from. A simulator lets the agent practise.

**Q24. Are you using RL just because the syllabus requires it?** S: It was required, but we tested whether it earns its place. PPO cut the number of workers over the limit by half compared with a greedy rule, and used fewer moves. If it hadn't helped, we would have said so.

**Q25. Why PPO?** Stable, standard, and it supports action masking (sb3-contrib MaskablePPO). A Q-table can't handle continuous strain values.

**Q26. How do you know it isn't just memorising?** It was evaluated on 500 shifts with seeds it never trained on, with random starting strain and hidden worker sensitivity. **Limitation:** it is still the same simulator.

**Q27. What is action masking?** Blocking forbidden choices, such as putting a worker at a station they're not trained for. Only 11 of the 24 are allowed.

## Generative AI

**Q28. Why GenAI?** To turn numbers into a readable report with explanations. The numbers come from SQL; the AI only writes.

**Q29. Which model and why?** Llama 3.1 8B via Ollama: free, local (private), fits an 8 GB GPU. Any OpenAI-compatible API also works.

**Q30. What if it gives a wrong answer?** S: Citations it invents are removed and flagged. A human reads the report; it advises, it doesn't decide. **Limitation:** we don't automatically check that each sentence matches its source.

**Q31. How do you control hallucinations?** RAG (we give it the right paragraphs), low temperature, "use only this data", numbers from SQL, and the citation checker.

**Q32. Cost per request?** Local Ollama: no fee per request. Time per report: **NOT MEASURED yet.**

**Q33. Did you actually run the LLM?** Answer truthfully on the day. As of writing, the LLM path was tested with a fake LLM and the template fallback; a real Llama report had not yet been generated on our machine.

## DBMS

**Q34. What are your tables?** Workers, stations, worker_training, shifts, shift_assignments, posture_events, shift_strain, rotation_plans, rotation_approvals, documents, doc_chunks, safety_reports.

**Q35. Primary and foreign keys?** Primary key = a unique ID (e.g. `event_id`). Foreign key = must point to an existing row (e.g. an event's `worker_id` must exist in `workers`).

**Q36. Why normalization?** Training is a separate table instead of a list column. Each fact is stored once, and the database can check that the stations exist.

**Q37. How do you handle large data?** Indexes now. Partitioning events by month and summary tables later.

**Q38. Why the history table for assignments?** So an event that arrives late is given to whoever was at the station at that time.

## Networking

**Q39. How does the frontend talk to the backend?** HTTP for buttons and a WebSocket for live updates.

**Q40. What happens when the network fails?** The edge saves events to disk and sends them later in order. Tested: 0 lost.

**Q41. Why no HTTPS?** Everything is on one computer (127.0.0.1). For a real network, we would add TLS. That is future work.

**Q42. What is QoS 1?** "At least once": the receiver must send a receipt, or the sender tries again. Duplicates are possible, and we ignore them using the ID.

## SDG

**Q43. Why SDG 9?** It upgrades existing industry with low-cost innovative technology (targets 9.4, 9.5).

**Q44. Why not SDG 3 or 8?** Those fit the *benefit* very well too (health, safe work). We chose 9 because the project itself is industrial innovation and infrastructure. We mention 8.8 as well.

**Q45. Where exactly is SDG 9 in your project?** A cheap digital sensing and decision layer added to existing factories; open-source innovation.

## Startup

**Q46. Who pays?** Factory owners and safety departments.

**Q47. Business model?** A per-station subscription plus an edge kit. **Pricing is not researched yet.**

**Q48. Competitors?** Paper REBA/RULA, video ergonomics apps, wearable sensors (**names TO VERIFY**). Our difference: continuous, private, and it plans rotation.

**Q49. Why choose you?** No wearables, no video upload, rotation planning included, runs locally. But we are a prototype; they are proven.

**Q50. How much would it cost?** **Not estimated.** The hardware is a webcam and a small computer per line; the software is free.

## Research

**Q51. What is novel?** The combination and the application, not a new algorithm (Chapter 22).

**Q52. What research did you study?** REBA (Hignett & McAtamney 2000), the revised NIOSH equation (Waters et al. 1993), and PPO (Schulman et al. 2017 — **TO VERIFY** the citation details).

**Q53. What would you publish?** "Constraint-aware RL job rotation from camera-based REBA", after a real-data validation.

## Difficult judge questions

**Q54. "Isn't this just an AI wrapper?"** S: No. Most of the work is our own code: REBA geometry, a reliable network pipeline, the database design with transactions, the RL environment and evaluation. The LLM is a small final step.

**Q55. "Your RL works in a simulator. So what?"** S: True, and we say so. One station is calibrated on real pose data, and the simulator's model matched it within 6%. A real pilot is the next step.

**Q56. "What happens with 1 million users?"** S: The realistic unit is factories, not users. Each factory runs its own edge and broker; a central service would need partitioning, broker clusters and authentication. The RL design itself must change for big teams.

**Q57. "What part did you actually build?"** Everything in the `ergosense/` folder: about 30 Python modules, SQL, the dashboard, and 54 tests. Libraries: MediaPipe, PPO's algorithm, the LLM, the database engine.

**Q58. "Biggest weakness?"** No expert validation of the webcam REBA scores yet.

**Q59. "If I give you ₹10 lakh?"** An expert validation study, a pilot line with 4 cameras and edge boxes, security (login and TLS), and real-data calibration of every station.

**Q60. "Why would it fail?"** Inaccurate scores in messy factory lighting, workers rejecting cameras, or supervisors ignoring the suggestions.

**Q61. "What would you change if you started again?"** Collect real factory clips with expert scores from day one, and design the RL action space to scale beyond 4 workers.

**Q62. "Why should we trust the safe limit of 400?"** S: You shouldn't treat it as medical. It is a configurable design parameter, set about 15% above a perfectly balanced workload. A real deployment would set it with ergonomists.

---

# Chapter 32 — "Why not X?"
| Our choice | Alternative | Why ours makes more sense here |
|---|---|---|
| Python | C++/Java | AI libraries; speed comes from libraries written in C |
| PostgreSQL | MongoDB | Linked data + transactions + constraints + pgvector |
| PostgreSQL | MySQL | Partial unique indexes, pgvector, JSONB |
| pgvector | FAISS/Pinecone | One database, transactional, no extra service |
| MQTT | HTTP/REST | Offline tolerance, tiny messages, broker storage |
| MQTT | Kafka | Much lighter for a small deployment |
| PPO | DQN / Q-table | Stable; masking; continuous state |
| RL | Optimisation solver (e.g. integer programming) | A solver needs known future loads. RL handles uncertainty. *A solver is a fair future comparison we did not do.* |
| MediaPipe | OpenPose / YOLO-Pose | Real-time on a CPU, 3D world points, reused from FitCV (alternatives NOT MEASURED) |
| Local Llama | Cloud GPT | Privacy, no fee, offline. A cloud API is still supported. |
| Plain JS | React | Small UI, no build step |
| Local | Cloud | Privacy, cost, no internet needed |

# Chapter 33 — "What if?"
- **10× more stations:** one broker is fine; add backend copies; partition events.
- **Database down:** ingest retries; the broker keeps messages unconfirmed; nothing is lost.
- **Model inaccurate:** the supervisor approval is the safety net; retrain on real data.
- **Slow internet:** not needed. It runs on the local network, and events are small.
- **Wrong user input:** validation rejects it; constraints block impossible states.
- **The AI says something harmful:** the report is advice only, a human reads it, and the sources are verified. Add expert review.
- **A competitor copies us:** our value would be in validation, data and deployment, not secret code.
- **LLM API price rises:** we use a local model; the template fallback always exists.
- **No cloud allowed:** we already run fully on-premises.

---

# Chapter 34 — Project Vocabulary
| Word | One sentence | Like I'm 3 | In our project |
|---|---|---|---|
| Algorithm | Step-by-step method | A recipe | REBA tables, PPO |
| API | A doorway for programs to ask each other | A waiter | `POST /rotation/suggest` |
| Backend | The hidden program doing the work | The kitchen | FastAPI server |
| Frontend | What the user sees | The menu and plate | Dashboard |
| Client / server | The asker / the answerer | Customer / shop | Browser / FastAPI |
| Database | Organised storage | A cupboard | PostgreSQL |
| Table / row / column | Shelf / item / label | — | `posture_events` |
| Primary / foreign key | Unique tag / pointer to another tag | Name sticker / "see box 5" | `event_id`, `worker_id` |
| Transaction | All-or-nothing group of changes | Pay and get the toy, or neither | Plan approval |
| Index | Fast lookup | A book index | `(worker_id, ts DESC)` |
| Model | A trained program that predicts | A trained student | PPO, MediaPipe |
| Training / inference | Learning / using | Studying / exam | `train.py` / planner |
| Dataset | A collection of examples | A photo album | FitCV pose CSVs |
| Framework / library | Ready-made code to build on | A Lego set | FastAPI, SB3 |
| Endpoint | One API address | One counter in the shop | `/health` |
| Request / response | Question / answer | Letter / reply | — |
| Latency | Delay | Waiting time | NOT MEASURED |
| Throughput | Amount per second | Plates per minute | ~1 event/s/camera |
| Bandwidth | Data per second the line can carry | Pipe width | ~600 B/s used |
| Scalability | Handling more load | Feeding a bigger party | Chapter 20 |
| Container / Docker | A packaged program that runs anywhere | A lunchbox | Postgres, Mosquitto |
| AI / ML / DL | Smart programs / learn from data / big neural networks | — | — |
| RL | Learning from rewards | Dog treats | Rotation planner |
| GenAI / LLM | AI that writes / big text model | A storyteller | Llama report |
| RAG | Give the AI the right pages first | An open-book exam | Guidance chunks |
| Embedding | Meaning as numbers | A smell fingerprint for text | 384 numbers |
| CV | Computers seeing | Eyes | MediaPipe |
| Pose / landmark | Body points | Dots on joints | 33 points |
| REBA | Standard posture danger score | A danger meter, 1–15 | `reba.py` |
| MQTT / broker / topic / QoS | Sensor messaging / post office / address / delivery promise | — | Mosquitto |
| WebSocket | Always-open connection | A phone call | `/ws` |
| Idempotent | Twice = once | Pressing a lift button twice | `event_id` |
| Outbox | Saved-to-send list | A letter tray | SQLite file |
| Process / queue | A running program / a waiting line | A cook / a tray | Pipeline |
| Edge | Computing near the sensor | A helper at the machine | The laptop by the camera |
| SDG | UN goal | A world to-do list | SDG 9 |
| Strain | Our tiredness points | A tiredness jar | `shift_strain` |
| Policy | The agent's decision rule | Its habit | PPO network |
| Episode | One full game | One day | One shift |
| Mask | Forbidden moves | "You can't sit there" | Training rules |

# Chapter 35 — Explain It Like I'm 3
Imagine a toy factory. Some jobs are easy, like looking at toys. Some are hard, like lifting heavy boxes from the floor.
A camera is the factory's eyes. It looks at how people bend. When someone bends a lot, a little meter goes red.
The meter's number goes into a big notebook. The notebook adds up how tired each person is getting, like filling a jar with marbles.
When someone's jar gets too full, a clever helper says: "Let's swap! You do the easy job for a while."
The helper learned this by playing a pretend factory game thousands of times, getting a gold star when nobody's jar overflowed.
A grown-up boss says "OK" before anyone swaps.
At the end of the day, a robot writer writes a letter: "Today the box job was the hardest. Here's how to make it easier." It only uses facts from a trusted book.
The camera never sends pictures, only the meter numbers. So nobody's face goes anywhere.

---

# Chapter 36 — Revision Sheet

### 10 things to remember
1. Problem: slow injuries from bending/lifting; nobody measures them daily.
2. Webcam → MediaPipe → REBA score 1–15.
3. Only ~600-byte scores are sent, never video.
4. MQTT QoS 1 + disk outbox → 0 events lost.
5. PostgreSQL adds strain per worker; limit 400.
6. PPO picks 1 of 24 assignments every hour; untrained ones are blocked.
7. PPO peak 400 vs 691 (none), 437 (greedy).
8. Approval = one transaction + row lock.
9. Report = RAG + LLM + citation check + template fallback.
10. Biggest weakness: no expert validation yet; RL trained in a simulator.

### 20 numbers
| # | Number | Meaning |
|---|---|---|
| 1 | 33 | MediaPipe body points |
| 2 | 1–15 | REBA range |
| 3 | 15 frames | Smoothing window |
| 4 | 1 per s | Events per camera |
| 5 | ~600 B | Event size |
| 6 | 921,600 B | Raw frame |
| 7 | ~1,540× | Bandwidth reduction |
| 8 | 400 | Safe strain limit |
| 9 | 0/0.5/1/2/4 | Strain weights per minute |
| 10 | 24 / 11 | Possible / allowed assignments |
| 11 | 25 | State numbers |
| 12 | 500k / 3.5 min | Training steps / time |
| 13 | 500 | Evaluation shifts |
| 14 | 691/532/437/400 | Peak by policy |
| 15 | 1.67/1.53/1.65/0.78 | Workers over the limit |
| 16 | 0 | Events lost in the outage test (192/192) |
| 17 | 81.8 vs 77.4 | Calibrated vs simulated loading bay strain/h |
| 18 | 528 | Real pose events used for calibration |
| 19 | 54 | Automated tests |
| 20 | 362 vs 632 | Live e2e peak vs projected no-rotation |

### 20 design reasons (one line each)
MQTT for flaky networks · outbox for crashes · UUID for duplicates · ack after commit · processes because of the GIL · drop-oldest for live frames · blocking puts for events · Postgres for transactions · a junction table for 1NF · a temporal assignments table for late events · partial unique indexes for business rules · `FOR UPDATE` for double approvals · PPO for stability · masking for training rules · a telescoping reward · common random numbers for fair tests · pgvector for one DB · section chunks for readable citations · a local LLM for privacy · a template for reliability.

### 20 dangerous questions — do NOT make up an answer
REBA accuracy % · camera FPS · end-to-end latency · CPU/GPU use · LLM report time · cost per factory · price · market size · number of injuries prevented · competitor prices · competitor accuracy · injury statistics (unless verified) · real-factory results · user count · energy use · RL performance on real workers · legal compliance details · expert validation · LLM sentence accuracy · scalability numbers beyond our tests.
For all of these say: **"We haven't measured that yet; here is how we would."**

---

# Chapter 37 — Final 5-Minute Revision
- **Problem:** repeated bending and lifting slowly injure workers; checks are rare and on paper.
- **Solution:** a webcam REBA score → cumulative strain → RL rotation → approval → cited report.
- **Users:** supervisors and safety teams; workers benefit.
- **Workflow:** camera → 3 edge processes → MQTT → FastAPI → PostgreSQL → dashboard / planner / report.
- **Tech:** Python, MediaPipe, OpenCV, MQTT/Mosquitto, FastAPI, PostgreSQL + pgvector, Gymnasium + MaskablePPO, MiniLM, Ollama Llama 3.1 8B, Docker.
- **AI:** MediaPipe sees; REBA is a formula; PPO decides; Llama writes.
- **RL:** state = strain + positions + time + station load; action = 1 of 24; reward = −(over limit + 0.5 × worst + 2 × moves).
- **GenAI:** RAG on 20 chunks; citations verified; fallback template.
- **DBMS:** 12 tables; idempotent ingest; locked approval transaction.
- **Networking:** QoS 1, outbox, persistent session, WebSocket; ~600 B events; 0 lost.
- **SDG 9:** low-cost innovation for existing industry (9.4, 9.5); also SDG 8.8.
- **Novelty:** the combination and application, not a new algorithm.
- **Results:** PPO peak 400 vs 691 / 437; 0.78 vs 1.65 workers over the limit; e2e peak 362, nobody over.
- **Efficiency:** O(1) per frame; tiny network use; trains in 3.5 min on a CPU.
- **Feasibility:** runs on one laptop; needs consent, a login and TLS for real use.
- **Startup:** per-station subscription + edge kit for EHS teams.
- **Limitations:** webcam approximation, no expert validation, simulator RL, 4×4 only, no auth.
- **Future:** expert validation, pilot line, calibrate all stations, security, a scalable RL design.

---

# IF THE JUDGE ASKS ME ANYTHING

1. **Stay calm.** Take a breath. It is fine to pause for 2 seconds.
2. **Understand the question.** Repeat it in your own words: "So you're asking whether the RL would still work with more workers?"
3. **If you know:** give the short answer first, then one supporting fact or number, then stop.
4. **If you partly know:** say what you know and mark where your knowledge ends: "The part I can confirm is X; beyond that I'd need to check."
5. **If you don't know:** say so. "I don't know that yet. I'd find out by ___."
6. **Never invent:** no made-up numbers, papers or product claims.
7. **Redirect to what we built:** "What we did implement for that is ___, for example our outbox test with zero lost events."
8. **Admit limits professionally:** a limitation stated by you sounds like maturity. One found by the judge sounds like hiding.

### Useful sentences
- "I have not measured that yet, so I don't want to give you a made-up number."
- "This is part of our future work rather than the current implementation."
- "The current prototype handles this case, but we would need additional infrastructure to support it at large scale."
- "That number comes from our simulation, not from a real factory."
- "Good question. Our honest answer is that the webcam score is an approximation, and expert validation is our next step."
- "We tested exactly that: when we stopped the broker, 96 events were buffered and all 192 arrived, in order."
- "I'm not certain about that competitor's details, so I won't guess. What I can say about our system is..."
