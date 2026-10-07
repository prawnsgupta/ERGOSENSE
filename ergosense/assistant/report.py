"""End-of-shift safety report: shift data (SQL) + retrieved guidance (RAG) -> LLM -> verified Markdown.

    python -m ergosense.assistant.report [shift_id]       # default: the active shift

Steps
  1. gather_facts     - per-worker and per-station numbers and approved rotation plans from PostgreSQL
  2. retrieve_context - one retrieval query per relevant topic (lifting, overhead work, rotation ...)
  3. LLM              - asked for a fixed set of sections, citing excerpt ids like [job_rotation_guidance#3]
  4. verify_citations - every cited id must be one we actually retrieved; anything else is removed
                        and listed, and recommendations left without a valid citation are flagged
  5. save             - safety_reports table + a Markdown file in assistant/reports/
If no LLM is reachable, a template builds the same sections from the same facts and chunks.
"""
import json
import logging
import re
import sys
from datetime import datetime

from psycopg.types.json import Jsonb

from ergosense import config
from ergosense.assistant.llm import LLMUnavailable, generate
from ergosense.assistant.retriever import retrieve
from ergosense.backend import repository
from ergosense.backend.db import get_pool

log = logging.getLogger("assistant.report")
CITATION = re.compile(r"\[([a-z0-9_]+#\d+)\]")
LIFTING_LABELS = {"bending", "leaning", "squatting"}
MAX_CHUNKS = 10

SYSTEM_PROMPT = """You are an occupational health and safety assistant. You write short, factual
end-of-shift ergonomics reports for a factory supervisor.
Rules:
- Use ONLY the shift data and the reference excerpts you are given. Do not invent numbers.
- Every recommended action must end with the id of the excerpt that supports it in square
  brackets, for example [job_rotation_guidance#3]. Only use ids from the excerpt list.
- Refer to workers by name and id, e.g. "Karan (W04)". Be concise and use plain English.
- Posture scores come from a webcam and are approximate; do not present them as a medical finding."""


# ---------------------------------------------------------------------------
# 1. facts from the database
# ---------------------------------------------------------------------------

def gather_facts(conn, shift_id):
    shift = repository.get_shift(conn, shift_id)
    threshold = config.STRAIN_THRESHOLD
    strain = {r["worker_id"]: r for r in repository.shift_strain(conn, shift_id)}

    per_worker = {r["worker_id"]: r for r in conn.execute(
        """SELECT worker_id, max(reba_score) AS peak_reba,
                  sum(reba_score * duration_s) / sum(duration_s) AS mean_reba
           FROM posture_events WHERE shift_id = %s GROUP BY worker_id""", (shift_id,)).fetchall()}
    risky_postures = conn.execute(
        """SELECT worker_id, posture_label, sum(duration_s) / 60 AS minutes
           FROM posture_events WHERE shift_id = %s AND reba_score >= 8
           GROUP BY worker_id, posture_label ORDER BY minutes DESC""", (shift_id,)).fetchall()
    worker_stations = conn.execute(
        """SELECT worker_id, station_id, sum(duration_s) / 60 AS minutes
           FROM posture_events WHERE shift_id = %s GROUP BY worker_id, station_id ORDER BY minutes DESC""",
        (shift_id,)).fetchall()

    workers = []
    for w in repository.workers(conn):
        wid = w["worker_id"]
        s = strain.get(wid, {})
        value = float(s.get("cumulative_strain", 0.0))
        stats = per_worker.get(wid, {})
        workers.append({
            "worker": f"{w['display_name']} ({wid})",
            "worker_id": wid,
            "strain": round(value, 1),
            "percent_of_limit": round(100 * value / threshold),
            "over_limit": value > threshold,
            "observed_h": round(float(s.get("exposure_s", 0.0)) / 3600, 2),
            "high_risk_min": round(float(s.get("risky_s", 0.0)) / 60, 1),
            "peak_reba": stats.get("peak_reba"),
            "mean_reba": round(float(stats["mean_reba"]), 1) if stats.get("mean_reba") is not None else None,
            "main_risky_postures": [f"{r['posture_label']} ({float(r['minutes']):.0f} min)"
                                    for r in risky_postures if r["worker_id"] == wid][:3],
            "stations_worked": [f"{r['station_id']} ({float(r['minutes']):.0f} min)"
                                for r in worker_stations if r["worker_id"] == wid],
        })

    station_rows = conn.execute(
        """SELECT s.station_id, s.name, sum(e.duration_s) / 60 AS minutes,
                  sum(e.reba_score * e.duration_s) / sum(e.duration_s) AS mean_reba,
                  sum(e.duration_s) FILTER (WHERE e.risk_level IN ('high', 'very_high')) / sum(e.duration_s)
                      AS high_share
           FROM stations s JOIN posture_events e ON e.station_id = s.station_id AND e.shift_id = %s
           GROUP BY s.station_id, s.name ORDER BY s.station_id""", (shift_id,)).fetchall()
    station_postures = conn.execute(
        """SELECT station_id, posture_label, sum(duration_s) / 60 AS minutes
           FROM posture_events WHERE shift_id = %s AND posture_label <> 'neutral'
           GROUP BY station_id, posture_label ORDER BY minutes DESC""", (shift_id,)).fetchall()
    stations = [{
        "station": f"{r['station_id']} {r['name']}",
        "station_id": r["station_id"],
        "observed_min": round(float(r["minutes"])),
        "mean_reba": round(float(r["mean_reba"]), 1),
        "high_risk_share_pct": round(100 * float(r["high_share"] or 0)),
        "main_postures": [p["posture_label"] for p in station_postures if p["station_id"] == r["station_id"]][:3],
    } for r in station_rows]

    plans = []
    for p in conn.execute(
            """SELECT p.plan_id, p.policy, p.assignments, p.projection, a.approved_by, a.approved_at
               FROM rotation_plans p JOIN rotation_approvals a USING (plan_id)
               WHERE p.shift_id = %s ORDER BY a.approved_at""", (shift_id,)).fetchall():
        proj = p["projection"] or {}
        plans.append({
            "plan_id": p["plan_id"], "policy": p["policy"], "approved_by": p["approved_by"],
            "approved_at": p["approved_at"].astimezone().strftime("%H:%M"),
            "assignments": p["assignments"],
            "projected_peak_with_plan": proj.get("peak_with_plan"),
            "projected_peak_no_rotation": proj.get("peak_no_rotation"),
        })

    return {
        "shift": {"id": shift_id, "name": shift["name"], "planned_hours": float(shift["planned_hours"]),
                  "observed_hours": round(max((w["observed_h"] for w in workers), default=0), 2),
                  "status": shift["status"]},
        "strain_limit": threshold,
        "workers": workers,
        "stations": stations,
        "approved_rotations": plans,
        "suggested_plans": conn.execute("SELECT count(*) AS n FROM rotation_plans WHERE shift_id = %s",
                                        (shift_id,)).fetchone()["n"],
    }


# ---------------------------------------------------------------------------
# 2. retrieval
# ---------------------------------------------------------------------------

def build_queries(facts):
    """(topic, query) pairs chosen from what actually happened in the shift."""
    labels = {p.split(" (")[0] for w in facts["workers"] for p in w["main_risky_postures"]}
    labels |= {p for s in facts["stations"] for p in s["main_postures"]}
    queries = [("action_levels", "what REBA action levels mean and how urgently to act on high scores")]
    if labels & LIFTING_LABELS:
        queries.append(("lifting", "reduce deep trunk bending when lifting boxes from floor-level pallets"))
    if "overhead_reach" in labels:
        queries.append(("overhead", "overhead work with arms raised above shoulder height"))
    if "twisting" in labels:
        queries.append(("twisting", "avoid twisting the trunk while lifting and handling loads"))
    queries.append(("rotation", "job rotation between heavy and light tasks and how often to rotate"))
    if any(w["over_limit"] for w in facts["workers"]):
        queries.append(("recovery", "rest breaks and recovery for workers with high cumulative exposure"))
    queries.append(("monitoring", "monitoring exposure per station and engineering improvements"))
    queries.append(("camera", "webcam pose estimation accuracy and limits of automatic REBA from a camera"))
    return queries


def retrieve_context(facts):
    """Returns (chunks, best_chunk_per_topic). Chunks are de-duplicated and capped."""
    chunks, best = {}, {}
    for topic, query in build_queries(facts):
        results = retrieve(query, k=3)
        if results:
            best.setdefault(topic, results[0]["chunk_id"])
        for r in results:
            if r["chunk_id"] not in chunks or r["score"] > chunks[r["chunk_id"]]["score"]:
                chunks[r["chunk_id"]] = r
    keep = set(best.values())                         # every topic keeps its best chunk
    ranked = sorted(chunks.values(), key=lambda r: -r["score"])
    selected = [r for r in ranked if r["chunk_id"] in keep]
    selected += [r for r in ranked if r["chunk_id"] not in keep][:max(0, MAX_CHUNKS - len(selected))]
    return selected, best


# ---------------------------------------------------------------------------
# 3. prompt
# ---------------------------------------------------------------------------

def _table(rows, columns):
    head = "| " + " | ".join(title for _, title in columns) + " |"
    sep = "|" + "---|" * len(columns)
    body = []
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            cells.append(", ".join(value) if isinstance(value, list) else ("" if value is None else str(value)))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([head, sep, *body])


def build_prompt(facts, chunks):
    shift = facts["shift"]
    lines = [
        "SHIFT DATA",
        f"Shift: {shift['name']} (#{shift['id']}), planned {shift['planned_hours']:.0f} h, "
        f"observed so far {shift['observed_hours']:.1f} h.",
        f"Safe cumulative strain limit: {facts['strain_limit']:.0f} points per worker per shift "
        "(1 minute in a medium-risk posture = 1 point, high-risk = 2, very high = 4).",
        "", "Workers:",
        _table(facts["workers"], [("worker", "Worker"), ("strain", "Strain"), ("percent_of_limit", "% of limit"),
                                  ("high_risk_min", "High-risk min"), ("peak_reba", "Peak REBA"),
                                  ("mean_reba", "Mean REBA"), ("main_risky_postures", "Main risky postures"),
                                  ("stations_worked", "Stations worked")]),
        "", "Stations:",
        _table(facts["stations"], [("station", "Station"), ("observed_min", "Observed min"),
                                   ("mean_reba", "Mean REBA"), ("high_risk_share_pct", "% time high risk"),
                                   ("main_postures", "Main postures")]),
        "", f"Rotation plans suggested by the RL planner: {facts['suggested_plans']}. Approved:",
        json.dumps(facts["approved_rotations"], indent=1) if facts["approved_rotations"] else "none",
        "", "REFERENCE EXCERPTS (cite by id):",
    ]
    for c in chunks:
        lines += [f"[{c['chunk_id']}] {c['title']} - {c['section']}", c["content"], ""]
    lines += [
        "TASK",
        f"Write the report in Markdown, starting with '# Shift safety report - {shift['name']}', followed by "
        "exactly these sections:",
        "## Summary  (3-4 sentences)",
        "## Per-worker findings  (one bullet per worker)",
        "## Station findings  (one bullet per station)",
        "## Recommended actions  (numbered list, 3-6 items, each ending with an excerpt id in square brackets)",
        "## Rotation decision explained  (why the approved rotation helps, using the projected peak numbers; "
        "if none was approved, say so and what the supervisor should consider)",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. citation check
# ---------------------------------------------------------------------------

def verify_citations(text, retrieved_ids):
    """Keep citations of retrieved chunks; replace any other id with a visible removal marker.

    Returns (text, valid_ids, invalid_ids). Also flags numbered/bulleted recommendations under
    'Recommended actions' that are left without any valid citation.
    """
    retrieved_ids = set(retrieved_ids)
    valid, invalid = [], []

    def check(match):
        cid = match.group(1)
        if cid in retrieved_ids:
            if cid not in valid:
                valid.append(cid)
            return match.group(0)
        invalid.append(cid)
        return f"[citation removed: {cid}]"

    text = CITATION.sub(check, text)

    out, in_actions = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            in_actions = line.lower().startswith("## recommended actions")
        elif in_actions and re.match(r"^\s*(\d+\.|[-*])\s+", line):
            if not any(m.group(1) in retrieved_ids for m in CITATION.finditer(line)):
                line += " *(no supporting reference - check before acting)*"
        out.append(line)
    return "\n".join(out), valid, invalid


def sources_section(valid, chunks):
    by_id = {c["chunk_id"]: c for c in chunks}
    lines = ["", "## Sources", ""]
    for cid in valid:
        c = by_id[cid]
        lines.append(f"- [{cid}] {c['title']} - *{c['section']}* (`ergosense/assistant/docs/{c['doc_id']}.md`)")
    if not valid:
        lines.append("- (no verified citations)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# template fallback
# ---------------------------------------------------------------------------

def template_report(facts, best, reason):
    """Deterministic report from the same facts, citing the best retrieved chunk per topic."""
    shift, limit = facts["shift"], facts["strain_limit"]
    workers = sorted(facts["workers"], key=lambda w: -w["strain"])
    over = [w for w in workers if w["over_limit"]]
    top = workers[0] if workers else None
    stations = sorted(facts["stations"], key=lambda s: -s["high_risk_share_pct"])
    cite = lambda topic: f" [{best[topic]}]" if topic in best else ""   # noqa: E731

    lines = [
        f"# Shift safety report - {shift['name']}",
        "",
        "> **Template fallback.** No LLM was reachable, so this report was assembled from a fixed template "
        f"using the same shift data and retrieved guidance. Reason: {reason}",
        "",
        "## Summary",
        "",
    ]
    if top:
        lines.append(
            f"During {shift['observed_hours']:.1f} h of observed work, {len(over)} of {len(workers)} workers "
            f"exceeded the safe cumulative strain limit of {limit:.0f} points. The most strained worker was "
            f"{top['worker']} with {top['strain']:.0f} points ({top['percent_of_limit']}% of the limit)."
            + (f" {stations[0]['station']} was the riskiest station: {stations[0]['high_risk_share_pct']}% of its "
               f"observed time was at REBA 8 or above (high risk), which calls for prompt action."
               if stations else "") + cite("action_levels"))
    lines += ["", "## Per-worker findings", ""]
    for w in workers:
        postures = ", ".join(w["main_risky_postures"]) or "no high-risk postures recorded"
        lines.append(f"- **{w['worker']}**: {w['strain']:.0f} strain points ({w['percent_of_limit']}% of the limit"
                     f"{', over the limit' if w['over_limit'] else ''}), {w['high_risk_min']:.0f} min in high-risk "
                     f"postures, peak REBA {w['peak_reba']}, mean {w['mean_reba']}. High-risk postures: {postures}. "
                     f"Stations: {', '.join(w['stations_worked']) or '-'}.")
    lines += ["", "## Station findings", ""]
    for s in facts["stations"]:
        lines.append(f"- **{s['station']}**: mean REBA {s['mean_reba']}, {s['high_risk_share_pct']}% of "
                     f"{s['observed_min']} observed minutes at high risk; main postures: "
                     f"{', '.join(s['main_postures']) or 'neutral'}.")

    actions = []
    if "lifting" in best:
        actions.append("Raise floor-level loads at the stations with bending and squatting (pallet stands, lift "
                       "tables or levellers) so lifts start between knee and waist height, and keep loads close "
                       "to the body." + cite("lifting"))
    if "overhead" in best:
        actions.append("Bring overhead work down to between elbow and shoulder height with adjustable fixtures or "
                       "platforms, and limit continuous time with the arms raised." + cite("overhead"))
    if "twisting" in best:
        actions.append("Rearrange the start and end points of handling tasks so they are in front of the worker "
                       "and the feet turn the body instead of the spine." + cite("twisting"))
    actions.append("Keep rotating workers between heavy and light stations at natural breaks, moving people off "
                   "the heaviest station before they approach the strain limit." + cite("rotation"))
    if over and "recovery" in best:
        actions.append(f"Give {', '.join(w['worker'] for w in over)} a lighter task mix and extra recovery breaks "
                       "for the rest of the shift and the next one." + cite("recovery"))
    if stations and "monitoring" in best:
        actions.append(f"Treat {stations[0]['station']} as the first candidate for engineering improvements, "
                       "since it dominates the exposure of everyone who works there." + cite("monitoring"))
    actions.append("Have a trained assessor confirm the webcam-based scores before redesigning any task, as "
                   "camera-based REBA is an approximation." + cite("camera"))
    lines += ["", "## Recommended actions", ""] + [f"{i}. {a}" for i, a in enumerate(actions, 1)]

    lines += ["", "## Rotation decision explained", ""]
    if facts["approved_rotations"]:
        for p in facts["approved_rotations"]:
            moves = ", ".join(f"{w} -> {s}" for w, s in sorted(p["assignments"].items()))
            peak = ""
            if p["projected_peak_with_plan"] is not None:
                peak = (f" Before approval the planner projected an end-of-shift peak strain of "
                        f"{p['projected_peak_with_plan']:.0f} points with the plan versus "
                        f"{p['projected_peak_no_rotation']:.0f} without rotation.")
            lines.append(f"- Plan #{p['plan_id']} ({p['policy'].upper()} policy) was approved by {p['approved_by']} "
                         f"at {p['approved_at']}: {moves}.{peak}")
        lines.append("")
        lines.append("The reinforcement-learning policy was trained to keep every worker below the strain limit "
                     "and to lower the strain of the most loaded worker, while paying a small cost for every "
                     "move, so it rotates people only when the expected benefit outweighs the disruption. "
                     "Rotation shares exposure but does not remove it." + cite("rotation"))
    else:
        lines.append("No rotation plan was approved during this shift. The supervisor should consider using the "
                     "planner's suggestion before the most strained worker reaches the limit." + cite("rotation"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. orchestration
# ---------------------------------------------------------------------------

def generate_report(shift_id=None):
    with get_pool().connection() as conn:
        if shift_id is None:
            shift_id = repository.active_shift(conn)["shift_id"]
        facts = gather_facts(conn, shift_id)

    chunks, best = retrieve_context(facts)
    retrieved_ids = [c["chunk_id"] for c in chunks]

    try:
        answer = generate(SYSTEM_PROMPT, build_prompt(facts, chunks))
        text, provider, model, is_fallback, llm_error = answer["text"], answer["provider"], answer["model"], False, None
    except LLMUnavailable as exc:
        log.warning("no LLM available (%s) - using the template fallback", exc)
        text, provider, model, is_fallback, llm_error = template_report(facts, best, str(exc)), \
            "template-fallback", None, True, str(exc)

    text, valid, invalid = verify_citations(text.strip(), retrieved_ids)
    footer = [sources_section(valid, chunks), "",
              f"*Citation check: {len(valid)} verified, {len(invalid)} removed "
              f"(cited ids must be among the {len(retrieved_ids)} retrieved excerpts). "
              f"Generated {datetime.now():%Y-%m-%d %H:%M} by {provider}{f' ({model})' if model else ''}.*"]
    content = text + "\n" + "\n".join(footer) + "\n"

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS_DIR / f"shift_{shift_id}_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text(content, encoding="utf-8")
    citations = {"valid": valid, "invalid": invalid, "retrieved": retrieved_ids}
    with get_pool().connection() as conn:
        row = conn.execute(
            """INSERT INTO safety_reports (shift_id, provider, model, is_fallback, content_md, citations, file_path)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING report_id, created_at""",
            (shift_id, provider, model, is_fallback, content, Jsonb(citations),
             path.relative_to(config.REPO_ROOT).as_posix()),
        ).fetchone()
    return {"report_id": row["report_id"], "created_at": row["created_at"], "shift_id": shift_id,
            "provider": provider, "model": model, "is_fallback": is_fallback, "llm_error": llm_error,
            "content_md": content, "citations": citations, "file_path": path.relative_to(config.REPO_ROOT).as_posix()}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    shift_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    report = generate_report(shift_id)
    print(report["content_md"])
    print(f"--> saved as report {report['report_id']} in {report['file_path']}")


if __name__ == "__main__":
    main()
