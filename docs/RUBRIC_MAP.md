# Rubric map — where each evaluation criterion is covered

Use this as a checklist while preparing each phase review. "Manual" means a team member has to do it.

## Phase I

| Criterion | Evidence in the project | What to show / say |
|---|---|---|
| **Problem definition: clarity** | README "Problem statement" | One sentence: *continuous, privacy-preserving ergonomic risk monitoring and rotation planning for factory workers*. |
| **Problem definition: relevance** | README "Why it matters / SDG 9" | WMSDs are the top occupational health problem; SDG 9.4/9.5 (upgrade industry with clean, innovative technology), SDG 8.8 (safe work). |
| **Problem definition: context** | README "Background", `docs/VIVA_NOTES.md` §0 | Today REBA is done on paper a few times a year; exposure between audits is invisible. |
| **Objectives: specificity / measurable** | README "Objectives" (O1–O5, each with a metric) | Show the table of objectives with the measured result next to each. |
| **Objectives: alignment** | Each objective maps to one component and one mandatory technology | README "How each technology is used". |
| **Objectives: achievability** | All five were met (README "Evaluation results") | — |
| **Methodology: appropriateness** | REBA (standard method), MQTT (IoT standard), PostgreSQL ACID, PPO + masking, RAG | Justifications: `docs/VIVA_NOTES.md`. |
| **Methodology: detail** | `docs/VIVA_NOTES.md`, docstrings at the top of every module | — |
| **Methodology: flow chart** | Mermaid architecture + data-flow diagrams in the README | Render in GitHub or paste into https://mermaid.live to export a PNG for slides (manual). |

## Phase II

| Criterion | Evidence | What to show |
|---|---|---|
| **Execution: adherence** | Built in the planned order; git history shows each step (`git log --oneline`) | — |
| **Execution: timeliness** | Commits dated; working prototype | — |
| **Execution: quality** | 54 automated tests, end-to-end check, CI workflow, pinned requirements | `python -m pytest` live (≈25 s). |
| **Tools: selection** | MediaPipe, MQTT/Mosquitto, PostgreSQL+pgvector, FastAPI, Stable-Baselines3, sentence-transformers, Ollama | README tech table. |
| **Tools: application** | Each tool used for what it is best at (VIVA_NOTES "Why …") | — |
| **Tools: integration** | One running system: edge → broker → backend → DB → RL → dashboard → LLM; `python -m ergosense.e2e_check` | Run the e2e check (≈1 min) or the dashboard demo. |
| **Partial results: clarity / progress / analysis** | REBA validation on real poses, bandwidth numbers, outage tests, RL chart | `ergosense/rl/results/policy_comparison.png`, README results. |

## Phase III

| Criterion | Evidence | What to show |
|---|---|---|
| **Prototype: functionality** | Webcam → live REBA overlay → dashboard → suggest/approve rotation → report | Demo script in README (manual: rehearse once). |
| **Prototype: innovation** | RL rotation under training constraints with human approval; citation-verified RAG report; privacy-by-design edge (no video leaves the device); offline-tolerant delivery | — |
| **Prototype: usability** | Dashboard with colour + text labels, plain-language plan explanation, one-click actions | — |
| **Results & discussion** | README "Evaluation results" + "Discussion"; CSVs in `ergosense/rl/results/` | Explain: rotation spreads but does not remove strain; camera depth limits; sim-to-real. |
| **Report** | README + VIVA_NOTES + generated shift reports | Manual: the college report document (can be assembled from README sections). |
| **Poster & PPT** | — | **Manual**: slides/poster still to be made (can be generated on request). |

> The third rubric screenshot is cut off below "Poster & PPT" (it looks like a further "Notable Outcomes"
> section). Check the full sheet for any extra items such as a paper or competition entry.
