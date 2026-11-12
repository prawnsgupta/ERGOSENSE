"""Central settings for every ERGOSENSE component, read from the .env file at the repo root."""
import os
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
load_dotenv(REPO_ROOT / ".env")


def _env(name, default):
    """Read an environment variable, treating an empty value as 'not set'."""
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


# Database and broker
DATABASE_URL = _env("DATABASE_URL", "postgresql://ergosense:ergosense@127.0.0.1:5434/ergosense")
MQTT_HOST = _env("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(_env("MQTT_PORT", "1883"))
TOPIC_PREFIX = "ergosense/events"          # edge publishes to ergosense/events/{station_id}

# Strain model
STRAIN_THRESHOLD = float(_env("STRAIN_THRESHOLD", "400"))

# Live alerts (seconds of WORK time, i.e. the sum of event durations; see backend/alerts.py)
ALERT_HIGH_HOLD_S = float(_env("ALERT_HIGH_HOLD_S", "30"))          # high/very high posture held this long -> amber
ALERT_VERY_HIGH_HOLD_S = float(_env("ALERT_VERY_HIGH_HOLD_S", "5"))  # very high posture held this long -> red
ALERT_LOAD_HOLD_S = float(_env("ALERT_LOAD_HOLD_S", "3"))           # bent/twisted while handling load -> red
ALERT_CLEAR_S = float(_env("ALERT_CLEAR_S", "10"))                  # condition gone this long -> alert resolved
ALERT_STRAIN_WARN = float(_env("ALERT_STRAIN_WARN", "0.8"))         # fraction of the strain limit -> amber

# Backend
API_HOST = _env("API_HOST", "127.0.0.1")
API_PORT = int(_env("API_PORT", "8000"))

# LLM
LLM_PROVIDER = _env("LLM_PROVIDER", "auto").lower()
OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "llama3.1:8b")
OPENAI_BASE_URL = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = _env("OPENAI_API_KEY", "")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_S = float(_env("LLM_TIMEOUT_S", "180"))

# RAG
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
RAG_TOP_K = int(_env("RAG_TOP_K", "4"))

# File locations
RL_MODEL_PATH = PACKAGE_DIR / "rl" / "models" / "ppo_rotation.zip"
STATION_PROFILES_PATH = PACKAGE_DIR / "rl" / "station_profiles.json"
POSE_MODEL_PATH = PACKAGE_DIR / "edge" / "models" / "pose_landmarker_full.task"   # multi-person pose (--zones)
REPORTS_DIR = PACKAGE_DIR / "assistant" / "reports"
DASHBOARD_DIR = PACKAGE_DIR / "dashboard"
