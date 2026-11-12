"""ERGOSENSE backend: FastAPI app with the MQTT subscriber, REST API, WebSocket and dashboard.

Run from the repo root (venv active):
    python -m uvicorn ergosense.backend.main:app --port 8000
then open http://localhost:8000
"""
import asyncio
import logging
import threading
from contextlib import asynccontextmanager

import psycopg

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ergosense import config
from ergosense.backend import alerts, repository, rotation
from ergosense.backend.db import close_pool, get_pool
from ergosense.backend.ingest import EventIngestor
from ergosense.backend.live import LiveHub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-16s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("backend")

hub = LiveHub()


def _on_alert_change(kind, alert):
    hub.publish_threadsafe({"type": "alert", "change": kind, "alert": alert})


alert_engine = alerts.AlertEngine(store=alerts.DbAlertStore(get_pool), on_change=_on_alert_change)


def _on_ingested(result):
    hub.publish_threadsafe({"type": "event", **result})
    try:
        alert_engine.process(result["event"], result["strain"])
    except Exception:                       # an alert problem must never stop event ingestion
        log.exception("alert engine failed for event %s", result["event"].get("event_id"))


ingestor = EventIngestor(on_ingested=_on_ingested)


@asynccontextmanager
async def lifespan(app):
    hub.loop = asyncio.get_running_loop()
    get_pool()                     # fail fast if the database is unreachable
    with get_pool().connection() as conn:
        shift = repository.active_shift(conn)
        if shift:
            alert_engine.restore(alerts.open_alerts(conn, shift["shift_id"]))
    ingestor.start()
    log.info("ERGOSENSE backend ready - dashboard at http://%s:%s", config.API_HOST, config.API_PORT)
    yield
    ingestor.stop()
    close_pool()


app = FastAPI(title="ERGOSENSE", version="1.0", lifespan=lifespan,
              description="Industrial worker ergonomic safety system (UN SDG 9)")
app.mount("/static", StaticFiles(directory=config.DASHBOARD_DIR), name="static")


def db():
    return get_pool().connection()


def _active_shift_or_404(conn):
    shift = repository.active_shift(conn)
    if shift is None:
        raise HTTPException(404, "no active shift - run: python -m ergosense.backend.init_db --new-shift")
    return shift


# --- pages and health -----------------------------------------------------------

@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(config.DASHBOARD_DIR / "index.html")


@app.get("/health")
def health():
    status = {"api": "ok", "mqtt_connected": ingestor.connected, "ingest": ingestor.stats}
    try:
        with db() as conn:
            conn.execute("SELECT 1")
            status["database"] = "ok"
            status["events_stored"] = repository.event_count(conn)
    except Exception as exc:
        status["database"] = f"error: {exc}"
    status["rl_model"] = "found" if config.RL_MODEL_PATH.exists() else "missing (greedy fallback)"
    return status


# --- master data ------------------------------------------------------------------

@app.get("/workers")
def list_workers():
    with db() as conn:
        return repository.workers(conn)


@app.get("/stations")
def list_stations():
    with db() as conn:
        return repository.stations(conn)


@app.get("/shifts/active")
def get_active_shift():
    with db() as conn:
        return _active_shift_or_404(conn)


@app.get("/shifts/{shift_id}/strain")
def get_shift_strain(shift_id: int):
    with db() as conn:
        if repository.get_shift(conn, shift_id) is None:
            raise HTTPException(404, f"shift {shift_id} not found")
        return {"shift_id": shift_id, "threshold": config.STRAIN_THRESHOLD,
                "workers": repository.shift_strain(conn, shift_id)}


@app.get("/events")
def list_events(worker_id: str | None = None, limit: int = Query(50, ge=1, le=1000)):
    with db() as conn:
        return repository.recent_events(conn, worker_id, limit)


@app.get("/state")
def dashboard_state():
    """Everything the dashboard needs on first load (later updates arrive over the WebSocket)."""
    with db() as conn:
        shift = _active_shift_or_404(conn)
        sid = shift["shift_id"]
        assignments = repository.current_assignments(conn, sid)
        strain = repository.shift_strain(conn, sid)
        in_shift = set(assignments) | {r["worker_id"] for r in strain}
        return {
            "shift": shift,
            "threshold": config.STRAIN_THRESHOLD,
            "workers": [w for w in repository.workers(conn) if w["worker_id"] in in_shift],
            "stations": repository.stations(conn),
            "assignments": assignments,
            "strain": strain,
            "alerts": alerts.list_alerts(conn, sid),
            "latest": repository.latest_event_per_worker(conn, sid),
            "events": repository.recent_events(conn, None, 30),
        }


# --- safety alerts ---------------------------------------------------------------------

class AckRequest(BaseModel):
    acknowledged_by: str = Field("supervisor", min_length=1, max_length=80)


@app.get("/alerts")
def list_alerts(all: bool = False):
    """Alerts of the active shift: by default those still open or not yet acknowledged."""
    with db() as conn:
        sid = _active_shift_or_404(conn)["shift_id"]
        return alerts.list_alerts(conn, sid, only_attention=not all)


@app.post("/alerts/{alert_id}/ack")
def acknowledge_alert(alert_id: int, body: AckRequest | None = None):
    with db() as conn:
        row = alerts.acknowledge(conn, alert_id, body.acknowledged_by if body else "supervisor")
    if row is None:
        raise HTTPException(404, f"alert {alert_id} not found")
    hub.publish_threadsafe({"type": "alert", "change": "acknowledged", "alert": row})
    return row


# --- live updates -------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()       # we only push; this just detects disconnects
    except WebSocketDisconnect:
        hub.disconnect(websocket)


# --- rotation planning (reinforcement learning) ------------------------------------------

_planner = None
_planner_lock = threading.Lock()


def get_planner():
    """Load the PPO model once, on first use (importing torch takes a few seconds)."""
    global _planner
    with _planner_lock:
        if _planner is None:
            from ergosense.rl.planner import RotationPlanner
            _planner = RotationPlanner()
    return _planner


class ApproveRequest(BaseModel):
    approved_by: str = Field("supervisor", min_length=1, max_length=80)


@app.post("/rotation/suggest", status_code=201)
def rotation_suggest():
    """Run the RL policy on the live shift state and store the result as a proposed plan."""
    with db() as conn:
        try:
            plan = rotation.suggest_plan(conn, get_planner())
        except rotation.PlanConflict as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    hub.publish_threadsafe({"type": "plan", "plan": plan})
    return plan


@app.post("/rotation/{plan_id}/approve")
def rotation_approve(plan_id: int, body: ApproveRequest | None = None):
    """Approve a plan in one database transaction (see rotation.approve_plan)."""
    approved_by = body.approved_by if body else "supervisor"
    with db() as conn:
        try:
            result = rotation.approve_plan(conn, plan_id, approved_by)
        except rotation.PlanNotFound as exc:
            raise HTTPException(404, str(exc))
        except rotation.PlanConflict as exc:
            raise HTTPException(409, str(exc))
        except psycopg.errors.IntegrityError as exc:
            raise HTTPException(409, f"plan violates a database constraint: {exc.diag.message_primary}")
    hub.publish_threadsafe({"type": "approval", **result})
    return result


@app.get("/rotation/plans")
def rotation_plans(shift_id: int | None = None):
    with db() as conn:
        sid = shift_id or _active_shift_or_404(conn)["shift_id"]
        return rotation.list_plans(conn, sid)


# --- safety report (generative AI + RAG) --------------------------------------------------

@app.post("/report/{shift_id}")
def create_report(shift_id: int):
    """Generate the end-of-shift safety report (LLM + RAG, with a template fallback)."""
    from ergosense.assistant.report import generate_report
    with db() as conn:
        if repository.get_shift(conn, shift_id) is None:
            raise HTTPException(404, f"shift {shift_id} not found")
    report = generate_report(shift_id)
    hub.publish_threadsafe({"type": "report", "report_id": report["report_id"], "shift_id": shift_id})
    return report


@app.get("/reports/{report_id}")
def get_report(report_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM safety_reports WHERE report_id = %s", (report_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"report {report_id} not found")
    return row
