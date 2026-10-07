# Webcam demo checklist (Windows)

Run everything in the repo folder `D:\Downloads\College\EL\ERGOSENSE`. Use **VS Code terminals** or
**PowerShell windows**. Every terminal needs the virtual environment activated first:

```
cd D:\Downloads\College\EL\ERGOSENSE
.venv\Scripts\Activate.ps1
```

## Before the demo (once, ~5 min)
1. Start **Docker Desktop** and wait until it says *Engine running*.
2. Terminal 1: `docker compose up -d`, then `docker compose ps`. Both containers must show `(healthy)`.
3. Terminal 1: `python -m pytest`. You should see 54 passed.
4. (Optional, for a real LLM report) Start Ollama, check `ollama list` shows `llama3.1:8b`.
5. Place the laptop/webcam **side-on or at about 45°** to where you will stand, 2–3 m away, so that the whole body
   from head to feet is in view. Light from the front, plain background, a box to "lift".

## The demo
| # | Where | Command / action | What to point out |
|---|---|---|---|
| 1 | Terminal 1 | `python -m ergosense.backend.init_db --new-shift` | fresh shift, 4 workers, 4 stations |
| 2 | Terminal 1 | `python -m uvicorn ergosense.backend.main:app --port 8000` (leave running) | "subscribed to ergosense/events/+" |
| 3 | Browser | open http://127.0.0.1:8000 | both pills green: Live + MQTT |
| 4 | Terminal 2 | `python -m ergosense.edge.pipeline --simulate --stations S1,S2,S3 --speed 60` | 3 simulated station cameras; feed + bars move |
| 5 | Terminal 3 | `python -m ergosense.edge.pipeline --station S4 --demo-speed 60 --load-kg 8` | OpenCV window: skeleton coloured by REBA risk |
| 6 | In front of the camera | stand upright → bend to the box → reach overhead → squat → twist | score, risk colour and posture label change; Karan (W04, S4) card follows |
| 7 | OpenCV window | read the bottom lines | ~600 B per event vs ~900 KB per frame; **no video leaves the laptop**; FPS (note it for the report) |
| 8 | Terminal 4 | `docker compose stop mosquitto` … wait 10 s … `docker compose start mosquitto` | edge shows OFFLINE + pending count, then catches up; nothing lost |
| 9 | Dashboard | after ~1 simulated hour: **Suggest rotation** | who moves where, projected strain with vs without rotation |
| 10 | Dashboard | **Approve plan** | assignments swap; S4 camera now counts for the new worker |
| 11 | Browser | http://127.0.0.1:8000/docs → `POST /rotation/{plan_id}/approve` with the same id | **409** = transaction + row lock prevent double approval |
| 12 | Dashboard | keep suggesting every simulated hour until 8 h | nobody crosses the dashed 400 line |
| 13 | Dashboard | **Generate report** | sections, verified citations, Sources; fallback banner if Ollama is off |
| 14 | Explorer | `ergosense\rl\results\policy_comparison.png` | PPO vs no rotation / round-robin / greedy on 500 shifts |

Stop everything with **Q** in the OpenCV window and **Ctrl+C** in each terminal. `docker compose stop` stops the containers.

## If something goes wrong
- *Camera does not open*: close other apps using the webcam (Teams, Zoom, browser), or try `--camera 1`.
- *Skeleton jumps / wrong scores*: step back so feet and head are in view; turn side-on; improve lighting.
- *Dashboard shows "Cannot load state"*: the backend is not running (terminal 1) or Docker is down.
- *No webcam at all*: replace step 5 with `python -m ergosense.edge.pipeline --station S4 --replay dataset/BentOverRow`
  (real recorded poses) or `--video your_clip.mp4`.
- *Report says "Template fallback"*: Ollama is not running or the model is not pulled (`ollama pull llama3.1:8b`).
- *Port 8000 in use*: `--port 8001` in step 2 and set `API_PORT=8001` in `.env`.
