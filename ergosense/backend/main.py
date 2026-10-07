"""ERGOSENSE backend: FastAPI app with the MQTT subscriber, REST API, WebSocket and dashboard.

Run from the repo root (venv active):
    python -m uvicorn ergosense.backend.main:app --port 8000
then open http://localhost:8000
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ergosense import config
from ergosense.backend import repository
from ergosense.backend.db import close_pool, get_pool
from ergosense.backend.ingest import EventIngestor
from ergosense.backend.live import LiveHub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-16s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("backend")

hub = LiveHub()


def _on_ingested(result):
    hub.publish_threadsafe({"type": "event", **result})


ingestor = EventIngestor(on_ingested=_on_ingested)


@asynccontextmanager
async def lifespan(app):
    hub.loop = asyncio.get_running_loop()
    get_pool()                     # fail fast if the database is unreachable
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
        return {
            "shift": shift,
            "threshold": config.STRAIN_THRESHOLD,
            "workers": repository.workers(conn),
            "stations": repository.stations(conn),
            "assignments": repository.current_assignments(conn, sid),
            "strain": repository.shift_strain(conn, sid),
            "latest": repository.latest_event_per_worker(conn, sid),
            "events": repository.recent_events(conn, None, 30),
        }


# --- live updates -------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()       # we only push; this just detects disconnects
    except WebSocketDisconnect:
        hub.disconnect(websocket)
