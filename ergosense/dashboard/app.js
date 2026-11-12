// ERGOSENSE dashboard: loads the current state once from /state, then stays live over a WebSocket.
"use strict";

const RISK_LABEL = { negligible: "Negligible", low: "Low", medium: "Medium", high: "High", very_high: "Very high" };
const MAX_FEED_ROWS = 40;

const state = {
  shift: null, threshold: 400, workers: {}, stations: {}, assignments: {},
  strain: {}, latest: {}, plan: null, eventsSeen: 0, alerts: {},
};
const LEVEL_RANK = { red: 2, amber: 1 };

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.setAttribute("style", v);
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function riskBadge(level) {
  // colour + text label: the colour never carries the meaning alone
  return el("span", { class: "badge" },
    el("span", { class: "swatch", style: `background: var(--risk-${level})` }), RISK_LABEL[level] || level);
}

function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function stationName(id) {
  return state.stations[id] ? `${id} · ${state.stations[id].name}` : id;
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `${response.status} ${response.statusText}`);
  return data;
}

// ---------------------------------------------------------------- rendering

function renderHeader() {
  if (!state.shift) return;
  $("shift-name").textContent = `${state.shift.name} (#${state.shift.shift_id})`;
  const hours = Math.max(0, ...Object.values(state.strain).map((s) => s.exposure_s || 0)) / 3600;
  $("shift-progress").textContent = `${hours.toFixed(1)} h of ${Number(state.shift.planned_hours)} h observed`;
}

function renderCards() {
  const root = $("worker-cards");
  root.replaceChildren();
  for (const id of Object.keys(state.workers).sort()) {
    const w = state.workers[id];
    const ev = state.latest[id];
    const active = Object.values(state.alerts).filter((a) => a.worker_id === id && !a.resolved_at);
    const worst = active.sort((a, b) => LEVEL_RANK[b.level] - LEVEL_RANK[a.level])[0];
    const card = el("div", { class: worst ? `card flag-${worst.level}` : "card" },
      el("div", { class: "card-top" },
        el("span", { class: "card-name" }, `${w.display_name}`),
        el("span", { class: "muted small" }, id)),
      el("div", { class: "card-station" }, `at ${stationName(state.assignments[id])}`),
      worst ? el("div", { class: `card-alert ${worst.level}` }, `⚠ ${worst.message}`) : null);
    if (ev) {
      const s = ev.segment_scores;
      card.append(
        el("div", { class: "score-row" }, el("span", { class: "score" }, ev.reba_score), riskBadge(ev.risk_level)),
        el("div", { class: "posture" }, "Posture: ", el("strong", {}, ev.posture_label.replace("_", " "))),
        el("div", { class: "segments" },
          ...[["Trunk", s.trunk], ["Neck", s.neck], ["Legs", s.legs], ["U.arm", s.upper_arm],
            ["L.arm", s.lower_arm], ["Wrist", s.wrist]].map(([n, v]) => el("div", { class: "seg" }, el("b", {}, v), n))),
        el("div", { class: "muted small", style: "margin-top:6px" }, `updated ${fmtTime(ev.timestamp || ev.ts)}`));
    } else {
      card.append(el("div", { class: "waiting" }, "No posture data yet"));
    }
    root.append(card);
  }
}

function renderStrain() {
  const root = $("strain-bars");
  root.replaceChildren();
  const values = Object.values(state.strain).map((s) => s.cumulative_strain);
  const scale = Math.max(state.threshold * 1.25, ...values.map((v) => v * 1.08));
  $("strain-legend").textContent =
    `Dashed line = safe limit (${state.threshold} points). 1 min in a medium-risk posture = 1 point, high = 2.`;
  for (const id of Object.keys(state.workers).sort()) {
    const s = state.strain[id] || { cumulative_strain: 0, risky_s: 0 };
    const v = s.cumulative_strain;
    const over = v > state.threshold;
    const value = el("span", { class: "bar-value" }, `${v.toFixed(0)} / ${state.threshold}`,
      over ? el("span", { class: "over-tag" }, "  over limit") : null,
      el("br"), `${((s.risky_s || 0) / 60).toFixed(0)} min high-risk`);
    root.append(el("div", { class: "bar-row" },
      el("span", { class: "bar-label" }, `${state.workers[id].display_name} (${id})`),
      el("div", { class: "bar-track", title: `${v.toFixed(1)} strain points` },
        el("div", { class: over ? "bar-fill over" : "bar-fill", style: `width:${(100 * v / scale).toFixed(2)}%` }),
        el("div", { class: "limit-line", style: `left:${(100 * state.threshold / scale).toFixed(2)}%` })),
      value));
  }
}

function feedRow(ev) {
  const worker = state.workers[ev.worker_id];
  return el("tr", { class: "fresh" },
    el("td", {}, fmtTime(ev.timestamp || ev.ts)),
    el("td", {}, worker ? `${worker.display_name} (${ev.worker_id})` : ev.worker_id),
    el("td", {}, ev.station_id),
    el("td", {}, el("strong", {}, ev.reba_score), " ", riskBadge(ev.risk_level)),
    el("td", {}, ev.posture_label.replace("_", " ")),
    el("td", { class: "muted" }, ev.source));
}

function addToFeed(ev) {
  const body = $("feed-body");
  body.prepend(feedRow(ev));
  while (body.children.length > MAX_FEED_ROWS) body.lastChild.remove();
  state.eventsSeen += 1;
  $("feed-count").textContent = `${state.eventsSeen} events received live`;
}

function renderPlan(plan, note) {
  const root = $("plan");
  root.replaceChildren();
  if (!plan) return;
  const p = plan.projection;
  const scale = Math.max(p.threshold * 1.2, ...Object.values(p.no_rotation), ...Object.values(p.with_plan));
  const pct = (v) => `${(100 * v / scale).toFixed(1)}%`;

  root.append(el("div", { class: "plan-summary" },
    el("strong", {}, `Plan #${plan.plan_id}`), ` · policy ${plan.policy.toUpperCase()} · decision for hour ${plan.decision_hour} · `,
    `${plan.moves} worker(s) move · status `, el("strong", {}, plan.status)));
  root.append(el("p", { class: "small" },
    `Projected end-of-shift peak strain: `, el("strong", {}, p.peak_with_plan.toFixed(0)),
    ` with this policy vs `, el("strong", {}, p.peak_no_rotation.toFixed(0)), ` if nobody rotates. `,
    `Chance that someone ends above the limit: ${(100 * p.prob_anyone_over_limit_with_plan).toFixed(0)}% vs `,
    `${(100 * p.prob_anyone_over_limit_no_rotation).toFixed(0)}% (${p.rollouts} simulated futures, `,
    `assuming you ask for a new suggestion every hour as in the schedule below).`));
  if (plan.moves === 0) {
    root.append(el("p", { class: "small" },
      "No change is needed for the next hour: moving people now would cost more than it saves. ",
      "The policy expects to rotate later in the shift (see the schedule below)."));
  }

  const table = el("table", { class: "plan-table" },
    el("tr", {}, el("th", {}, "Worker"), el("th", {}, "Now"), el("th", {}, "Next hour"),
      el("th", {}, "Strain now"), el("th", {}, "Projected end of shift")));
  for (const id of Object.keys(plan.assignments).sort()) {
    const now = (plan.current_assignments || {})[id];
    const next = plan.assignments[id];
    const moved = now && now !== next;
    table.append(el("tr", {},
      el("td", {}, `${state.workers[id]?.display_name || id} (${id})`),
      el("td", {}, now ? stationName(now) : "–"),
      el("td", { class: moved ? "moved" : "" }, moved ? `→ ${stationName(next)}` : `${stationName(next)} (stays)`),
      el("td", {}, plan.current_strain ? plan.current_strain[id].toFixed(0) : "–"),
      el("td", {}, el("div", { class: "mini-bars", title: `no rotation ${p.no_rotation[id]} · with plan ${p.with_plan[id]}` },
        el("div", { class: "mini" }, el("span", { class: "none", style: `width:${pct(p.no_rotation[id])}` }),
          el("div", { class: "limit-line", style: `left:${pct(p.threshold)}` })),
        el("div", { class: "mini" }, el("span", { class: "plan", style: `width:${pct(p.with_plan[id])}` }),
          el("div", { class: "limit-line", style: `left:${pct(p.threshold)}` }))),
        el("span", { class: "small muted" }, `${p.no_rotation[id].toFixed(0)} → ${p.with_plan[id].toFixed(0)}`))));
  }
  root.append(table, el("div", { class: "legend" },
    el("span", {}, el("i", { style: "background: var(--baseline)" }), "no rotation"),
    el("span", {}, el("i", { style: "background: var(--accent)" }), "with policy"),
    el("span", {}, "dashed line = safe limit")));

  if (p.schedule && p.schedule.length) {
    const sched = el("details", {}, el("summary", {}, "Rest-of-shift schedule the policy expects to follow"));
    const t = el("table", { class: "plan-table" }, el("tr", {}, el("th", {}, "Hour"),
      ...Object.keys(plan.assignments).sort().map((w) => el("th", {}, state.workers[w]?.display_name || w))));
    for (const step of p.schedule) {
      t.append(el("tr", {}, el("td", {}, step.hour),
        ...Object.keys(plan.assignments).sort().map((w) => el("td", {}, step.assignments[w]))));
    }
    sched.append(t);
    root.append(sched);
  }

  if (plan.status === "proposed") {
    const who = el("input", { type: "text", id: "approver", value: "Shift supervisor", "aria-label": "Approved by" });
    const btn = el("button", { class: "btn primary" }, "Approve plan");
    btn.addEventListener("click", () => approvePlan(plan.plan_id, who.value, btn));
    root.append(el("div", { class: "approve-row" }, el("span", { class: "small muted" }, "Approved by"), who, btn));
  }
  if (note) root.append(el("p", { class: note.ok ? "note-ok" : "note-err" }, note.text));
}

function renderReport(report) {
  const meta = $("report-meta");
  meta.replaceChildren(
    `Report #${report.report_id} · ${report.provider}${report.model ? " (" + report.model + ")" : ""} · `,
    `${report.citations.valid.length} verified citation(s)`,
    report.citations.invalid.length ? `, ${report.citations.invalid.length} removed (not in retrieved context)` : "",
    report.file_path ? ` · saved to ${report.file_path}` : "");
  const article = $("report");
  article.innerHTML = "";
  if (report.is_fallback) {
    article.append(el("div", { class: "banner" },
      "Template fallback: no LLM was reachable, so this report was assembled from a fixed template. ",
      "Install Ollama (see README) or set an API key in .env for an LLM-written report."));
  }
  const body = el("div");
  body.innerHTML = window.renderMarkdown(report.content_md);    // renderer escapes all HTML first
  article.append(body);
}

function renderAlerts() {
  const root = $("alerts");
  const list = Object.values(state.alerts)
    .filter((a) => !a.resolved_at || !a.acknowledged_at)
    .sort((a, b) => (LEVEL_RANK[b.level] - LEVEL_RANK[a.level]) || (new Date(b.opened_at) - new Date(a.opened_at)));
  const open = list.filter((a) => !a.resolved_at);
  $("alerts-summary").textContent = list.length
    ? `${open.filter((a) => a.level === "red").length} red, ${open.filter((a) => a.level === "amber").length} amber active · ${list.length} need attention`
    : "No alerts - all workers within limits";
  root.replaceChildren(...list.map((a) => {
    const btn = a.acknowledged_at ? el("span", { class: "muted small" }, `seen by ${a.acknowledged_by}`)
      : el("button", { class: "btn" }, "Acknowledge");
    if (!a.acknowledged_at) btn.addEventListener("click", () => ackAlert(a.alert_id, btn));
    const name = state.workers[a.worker_id]?.display_name || a.display_name || a.worker_id;
    return el("div", { class: `alert-row ${a.level}${a.resolved_at ? " resolved" : ""}` },
      el("span", { class: `alert-level ${a.level}` }, a.level.toUpperCase()),
      el("div", { class: "alert-body" },
        el("strong", {}, `${name} (${a.worker_id}) · ${a.station_id || ""}`),
        el("div", { class: "small" }, a.message),
        el("div", { class: "muted small" }, `since ${fmtTime(a.opened_at)}`,
          a.resolved_at ? ` · cleared ${fmtTime(a.resolved_at)}` : " · still happening")),
      btn);
  }));
}

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    osc.frequency.value = 880;
    osc.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.25);
  } catch (e) { /* audio blocked until the page is clicked once */ }
}

async function ackAlert(alertId, btn) {
  btn.disabled = true;
  try {
    const row = await api("POST", `/alerts/${alertId}/ack`, { acknowledged_by: "Shift supervisor" });
    state.alerts[row.alert_id] = { ...state.alerts[row.alert_id], ...row };
    renderAlerts();
  } catch (err) { btn.disabled = false; }
}

function renderAll() {
  renderAlerts();
  renderHeader();
  renderCards();
  renderStrain();
}

// ---------------------------------------------------------------- actions

async function loadState() {
  const s = await api("GET", "/state");
  state.shift = s.shift;
  state.threshold = s.threshold;
  state.workers = Object.fromEntries(s.workers.map((w) => [w.worker_id, w]));
  state.stations = Object.fromEntries(s.stations.map((st) => [st.station_id, st]));
  state.assignments = s.assignments;
  state.strain = Object.fromEntries(s.strain.map((r) => [r.worker_id, r]));
  state.latest = Object.fromEntries(s.latest.map((e) => [e.worker_id, e]));
  state.alerts = Object.fromEntries((s.alerts || []).map((a) => [a.alert_id, a]));
  $("feed-body").replaceChildren(...s.events.map(feedRow));
  renderAll();
}

async function suggestRotation() {
  const btn = $("suggest-btn");
  btn.disabled = true;
  btn.textContent = "Planning…";
  try {
    state.plan = await api("POST", "/rotation/suggest");
    renderPlan(state.plan);
  } catch (err) {
    renderPlan(null);
    $("plan").append(el("p", { class: "note-err" }, `Could not plan: ${err.message}`));
  } finally {
    btn.disabled = false;
    btn.textContent = "Suggest rotation";
  }
}

async function approvePlan(planId, approvedBy, btn) {
  btn.disabled = true;
  try {
    const result = await api("POST", `/rotation/${planId}/approve`, { approved_by: approvedBy || "supervisor" });
    state.plan.status = "approved";
    state.assignments = result.assignments;
    renderPlan(state.plan, { ok: true, text: `Approved by ${result.approved_by}. New assignments are live.` });
    renderCards();
  } catch (err) {
    renderPlan(state.plan, { ok: false, text: `Approval failed: ${err.message}` });
  }
}

async function generateReport() {
  const btn = $("report-btn");
  btn.disabled = true;
  btn.textContent = "Generating… (an LLM can take a minute)";
  $("report-meta").textContent = "";
  try {
    const report = await api("POST", `/report/${state.shift.shift_id}`);
    renderReport(report);
  } catch (err) {
    $("report-meta").replaceChildren(el("span", { class: "note-err" }, `Report failed: ${err.message}`));
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate report";
  }
}

async function refreshHealth() {
  try {
    const h = await api("GET", "/health");
    const pill = $("mqtt-status");
    pill.className = "pill " + (h.mqtt_connected ? "ok" : "bad");
    pill.lastChild.textContent = h.mqtt_connected ? "MQTT: connected" : "MQTT: disconnected";
  } catch (err) { /* the WebSocket pill already shows that the backend is down */ }
}

// ---------------------------------------------------------------- live updates

function connectWebSocket(delay = 1000) {
  const pill = $("ws-status");
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => {
    pill.className = "pill ok";
    pill.lastChild.textContent = "Live: connected";
    delay = 1000;
    loadState().catch(() => {});          // catch up on anything missed while disconnected
  };
  ws.onclose = () => {
    pill.className = "pill bad";
    pill.lastChild.textContent = "Live: reconnecting";
    setTimeout(() => connectWebSocket(Math.min(delay * 2, 15000)), delay);
  };
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.type === "event") {
      const ev = data.event;
      state.latest[ev.worker_id] = ev;
      state.strain[ev.worker_id] = { ...state.strain[ev.worker_id], ...data.strain };
      addToFeed(ev);
      renderAll();
    } else if (data.type === "alert") {
      const a = data.alert;
      state.alerts[a.alert_id] = { ...state.alerts[a.alert_id], ...a };
      if (data.change === "opened" && a.level === "red") beep();
      renderAlerts();
      renderCards();
    } else if (data.type === "plan") {
      state.plan = data.plan;
      renderPlan(state.plan);
    } else if (data.type === "approval") {
      state.assignments = data.assignments;
      if (state.plan && state.plan.plan_id === data.plan_id) state.plan.status = "approved";
      renderPlan(state.plan, { ok: true, text: `Plan #${data.plan_id} approved by ${data.approved_by}.` });
      renderCards();
    }
  };
}

$("suggest-btn").addEventListener("click", suggestRotation);
$("report-btn").addEventListener("click", generateReport);
loadState().catch((err) => { $("shift-name").textContent = `Cannot load state: ${err.message}`; });
connectWebSocket();
refreshHealth();
setInterval(refreshHealth, 10000);
