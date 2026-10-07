"""Small LLM client: local Ollama or any OpenAI-compatible chat API, chosen in .env.

LLM_PROVIDER=auto tries Ollama first (free, private, runs on the laptop GPU), then an
OpenAI-compatible endpoint if OPENAI_API_KEY is set. If neither works, LLMUnavailable is raised
and the report falls back to a template, so the demo never breaks.
"""
import logging

import httpx

from ergosense import config

log = logging.getLogger("assistant.llm")


class LLMUnavailable(Exception):
    pass


def _ollama_ready():
    """Is Ollama running and has the configured model been pulled?"""
    try:
        tags = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=2).json()
    except (httpx.HTTPError, ValueError):
        return False, f"Ollama is not running at {config.OLLAMA_URL}"
    names = {m.get("name", "") for m in tags.get("models", [])}
    wanted = config.OLLAMA_MODEL
    if wanted in names or f"{wanted}:latest" in names:
        return True, ""
    return False, f"Ollama is running but model '{wanted}' is not pulled (run: ollama pull {wanted})"


def _chat_ollama(system, user):
    response = httpx.post(
        f"{config.OLLAMA_URL}/api/chat",
        json={
            "model": config.OLLAMA_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            # low temperature: we want a factual report, not creative writing;
            # 8k context so the shift data and all retrieved excerpts fit
            "options": {"temperature": 0.2, "num_ctx": 8192},
        },
        timeout=config.LLM_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _chat_openai(system, user):
    response = httpx.post(
        f"{config.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json={
            "model": config.OPENAI_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        },
        timeout=config.LLM_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def provider_order():
    return {"auto": ["ollama", "openai"], "ollama": ["ollama"], "openai": ["openai"]}.get(config.LLM_PROVIDER, [])


def generate(system, user):
    """Return {"text", "provider", "model"} from the first provider that works."""
    problems = []
    for provider in provider_order():
        try:
            if provider == "ollama":
                ready, why = _ollama_ready()
                if not ready:
                    problems.append(why)
                    continue
                log.info("asking Ollama (%s)...", config.OLLAMA_MODEL)
                return {"text": _chat_ollama(system, user), "provider": "ollama", "model": config.OLLAMA_MODEL}
            if provider == "openai":
                if not config.OPENAI_API_KEY:
                    problems.append("no OPENAI_API_KEY set")
                    continue
                log.info("asking %s (%s)...", config.OPENAI_BASE_URL, config.OPENAI_MODEL)
                return {"text": _chat_openai(system, user), "provider": "openai", "model": config.OPENAI_MODEL}
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            problems.append(f"{provider} failed: {exc}")
    if config.LLM_PROVIDER == "none":
        problems.append("LLM_PROVIDER=none")
    raise LLMUnavailable("; ".join(problems) or f"unknown LLM_PROVIDER '{config.LLM_PROVIDER}'")
