"""RAG chunking, citation verification, LLM provider selection and the full report flow
(with a fake LLM, so no Ollama or API key is needed)."""
import pytest

from ergosense import config
from ergosense.assistant import llm, report
from ergosense.assistant.ingest import DOCS_DIR, chunk_markdown


def test_documents_are_chunked_by_section():
    title, chunks = chunk_markdown((DOCS_DIR / "job_rotation_guidance.md").read_text(encoding="utf-8"),
                                   "job_rotation_guidance")
    assert title.startswith("Job rotation guidance")
    assert [c[0] for c in chunks][:2] == ["job_rotation_guidance#1", "job_rotation_guidance#2"]
    assert all(len(body) > 200 for _, _, body in chunks)


def test_unretrieved_citations_are_removed_and_uncited_actions_flagged():
    text = "\n".join([
        "## Summary", "Things happened [reba_reference#5].",
        "## Recommended actions",
        "1. Raise the pallets [safe_lifting_niosh#5].",
        "2. Buy exoskeletons for everyone [vendor_brochure#1].",    # hallucinated source
        "3. Rotate more often.",                                   # no citation at all
        "## Rotation decision explained", "Because [job_rotation_guidance#3].",
    ])
    retrieved = ["reba_reference#5", "safe_lifting_niosh#5", "job_rotation_guidance#3"]
    checked, valid, invalid = report.verify_citations(text, retrieved)
    assert valid == ["reba_reference#5", "safe_lifting_niosh#5", "job_rotation_guidance#3"]
    assert invalid == ["vendor_brochure#1"]
    assert "[citation removed: vendor_brochure#1]" in checked
    lines = checked.splitlines()
    assert "no supporting reference" not in lines[3]               # action 1 is properly cited
    assert "no supporting reference" in lines[4]                   # its only citation was removed
    assert "no supporting reference" in lines[5]


def test_no_provider_means_llm_unavailable(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "none")
    with pytest.raises(llm.LLMUnavailable):
        llm.generate("system", "user")


def test_auto_mode_uses_ollama_when_the_model_is_pulled(monkeypatch):
    class Response:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

        def raise_for_status(self):
            pass

    sent = {}
    monkeypatch.setattr(config, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(llm.httpx, "get", lambda url, timeout: Response({"models": [{"name": config.OLLAMA_MODEL}]}))

    def fake_post(url, json, timeout):
        sent.update(url=url, body=json)
        return Response({"message": {"content": "hello from ollama"}})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    answer = llm.generate("system prompt", "user prompt")
    assert answer == {"text": "hello from ollama", "provider": "ollama", "model": config.OLLAMA_MODEL}
    assert sent["url"].endswith("/api/chat")
    assert sent["body"]["stream"] is False
    assert sent["body"]["messages"][0] == {"role": "system", "content": "system prompt"}


@pytest.fixture
def report_db(fresh_db):
    """Point the backend pool at the test database and load the RAG documents into it."""
    from ergosense.assistant.ingest import ingest
    from ergosense.backend import db
    db.close_pool()
    db.get_pool(fresh_db)
    ingest(fresh_db)
    yield fresh_db
    db.close_pool()


def test_report_with_fake_llm_verifies_citations_and_is_saved(report_db, monkeypatch):
    from ergosense.backend import repository
    from ergosense.backend.db import get_pool
    from ergosense.events import decode, encode, make_event

    fields = {"reba_score": 9, "risk_level": "high", "posture_label": "bending",
              "segment_scores": {"trunk": 4}, "angles": None}
    with get_pool().connection() as conn:
        for _ in range(5):
            repository.ingest_event(conn, decode(encode(make_event("S4", None, fields, 600, "simulated", 1))))

    def fake_generate(system, user):
        assert "Karan (W04)" in user                   # the shift data reached the prompt
        first_id = user.split("REFERENCE EXCERPTS (cite by id):\n[")[1].split("]")[0]
        text = ("# Shift safety report\n## Summary\nKaran bent a lot.\n## Recommended actions\n"
                f"1. Raise the pallets [{first_id}].\n2. Trust me [not_a_real_chunk#7].\n")
        return {"text": text, "provider": "fake", "model": "fake-1"}

    monkeypatch.setattr(report, "generate", fake_generate)
    result = report.generate_report()
    try:
        assert result["is_fallback"] is False
        assert result["citations"]["invalid"] == ["not_a_real_chunk#7"]
        assert len(result["citations"]["valid"]) == 1
        assert "[citation removed: not_a_real_chunk#7]" in result["content_md"]
        assert "## Sources" in result["content_md"]
        with get_pool().connection() as conn:
            row = conn.execute("SELECT provider, is_fallback FROM safety_reports WHERE report_id = %s",
                               (result["report_id"],)).fetchone()
        assert row == {"provider": "fake", "is_fallback": False}
    finally:
        (config.REPO_ROOT / result["file_path"]).unlink(missing_ok=True)


def test_template_fallback_report(report_db, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "none")
    result = report.generate_report()
    try:
        assert result["is_fallback"] is True
        assert result["provider"] == "template-fallback"
        assert "Template fallback" in result["content_md"]
        assert result["citations"]["invalid"] == []
        assert result["citations"]["valid"]                      # the template cites retrieved chunks
        for section in ("## Summary", "## Per-worker findings", "## Station findings",
                        "## Recommended actions", "## Rotation decision explained"):
            assert section in result["content_md"]
    finally:
        (config.REPO_ROOT / result["file_path"]).unlink(missing_ok=True)
