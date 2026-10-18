"use strict";
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  dashboard: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  network:
    "M12 4v5m0 6v5M4 12h5m6 0h5 M12 9l3 3-3 3-3-3z M10 2h4v3h-4z M2 10h3v4H2z M19 10h3v4h-3z M10 19h4v3h-4z",
  layers: "m3 7 9-5 9 5-9 5-9-5z m0 5 9 5 9-5 M3 17l9 5 9-5",
  box: "m3 7 9-5 9 5v10l-9 5-9-5V7z m0 0 9 5 9-5 M12 12v10 M8 5l9 5",
  history: "M3 11a9 9 0 1 1 2 7 M3 4v7h7 M12 7v5l3 2",
  settings:
    "M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3z M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  chevron: "m6 9 6 6 6-6",
  pulse: "M2 12h5l3-8 4 16 3-8h5",
  close: "m6 6 12 12 M6 18 18 6",
  refresh:
    "M20 10a8 8 0 0 0-14-4L3 9m0-6v6h6 M4 14a8 8 0 0 0 14 4l3-3m0 6v-6h-6",
  check: "m5 12 4 4 10-10",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 7v5l3 2",
  alert: "m12 3 10 18H2L12 3z M12 9v5m0 3v1",
  file: "M6 2h8l4 4v16H6V2z M14 2v5h5 M9 12h6m-6 4h6",
  machine: "M3 20V8l6 4V7l6 4V3h6v17H3z M7 16h2m4 0h2m3 0h1",
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.file}"/></svg>`;
function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = icon(el.dataset.icon);
  });
}
const scenarios = {
  default: [
    "Machine outage",
    "A stopped embroidery machine and the customer commitments downstream.",
  ],
  cascade: [
    "Cascading dependency",
    "Follow a longer chain of connected jobs and delivery promises.",
  ],
  scarce_operator: [
    "Scarce operator",
    "See how a reduced operator shift limits recovery capacity.",
  ],
  supplier_delay: [
    "Supplier delay memory",
    "A previous two-day delivery changes material assumptions.",
  ],
  impossible: [
    "Impossible capacity",
    "Explore a disruption with no feasible recovery in the horizon.",
  ],
  repeat: [
    "Repeated disruption",
    "Inspect retained incident context and supplier knowledge.",
  ],
};
const savedScenario = localStorage.getItem("factory-scenario");
const state = {
  scenario: scenarios[savedScenario] ? savedScenario : "default",
  token: "",
  factory: "",
  hourlyReview: null,
  hourlyImpact: null,
  epoch: 0,
  board: null,
  incident: null,
  selected: 0,
  view: "overview",
  mode: "schedule",
  version: "current",
  taskFilter: "all",
  memoryFilter: "all",
  graphMode: "graph",
  selectedNode: null,
  stale: false,
  busy: 0,
  refreshing: false,
  refreshVersion: 0,
  boardFilter: "all",
};
class ScopeChanged extends Error {}
async function api(path, body) {
  const epoch = state.epoch;
  const headers = {
    "Content-Type": "application/json",
    "X-Scenario": state.scenario,
  };
  if (state.factory) headers["X-Factory"] = state.factory;
  if (state.token) headers.Authorization = "Bearer " + state.token;
  const response = await fetch("/api/" + path, {
    headers,
    ...(body !== undefined
      ? { method: "POST", body: JSON.stringify(body) }
      : {}),
  });
  const raw = await response.text();
  if (epoch !== state.epoch) throw new ScopeChanged();
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    throw new Error(
      "The service returned an unreadable response. Try refreshing the floor.",
    );
  }
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail
              .slice(0, 3)
              .map((e) => e.loc.slice(1).join(".") + ": " + e.msg)
              .join("; ")
          : "Check the entered values and try again.",
    );
  return data;
}
async function threadKey() {
  const scope = state.scenario + ":" + state.factory;
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(state.token || "local-demo"),
  );
  return (
    "factory-review:" +
    scope +
    ":" +
    Array.from(new Uint8Array(hash))
      .slice(0, 12)
      .map((n) => n.toString(16).padStart(2, "0"))
      .join("")
  );
}
function currentPlan() {
  return state.incident?.plans?.[state.selected] || null;
}
const pending = () => Boolean(state.incident?.awaiting_approval);
const machineName = (id) => {
  const m = state.board?.machines.find((m) => m.id === id);
  return m ? m.name.split("•")[0].trim() : id;
};
const jobName = (id) => state.board?.jobs.find((j) => j.id === id)?.name || id;
const planName = (plan) =>
  plan.strategy === "standard" ? "Standard capacity" : "Allow overtime";
const badge = (label, tone = "") =>
  `<span class="badge ${tone}">${esc(label)}</span>`;
function notice(message, type = "success", action = "") {
  const el = $("notice");
  el.className = "notice " + (type === "success" ? "" : type);
  el.hidden = false;
  el.innerHTML = `<span>${esc(message)}</span><span class="notice-actions">${action === "reanalyse" ? `<button class="text-button underlined" data-action="reanalyse">${pending() ? "Reanalyse" : "New analysis"}</button>` : ""}<button class="icon-button" data-action="dismiss" aria-label="Dismiss message">${icon("close")}</button></span>`;
  el.setAttribute("role", type === "error" ? "alert" : "status");
  el.setAttribute("aria-live", type === "error" ? "assertive" : "polite");
}
function fail(error) {
  if (!(error instanceof ScopeChanged))
    notice(
      error.message || "Unable to reach the service. Check your connection.",
      "error",
      state.stale ? "reanalyse" : "",
    );
}
function loading(button, yes, label) {
  if (!button) return;
  button.disabled = yes;
  button.innerHTML = yes
    ? `<span class="busy-indicator"><i class="spinner"></i>${esc(label)}</span>`
    : esc(label);
}
function empty(
  title,
  copy,
  action = "incident",
  label = "Set up an incident",
  symbol = "network",
) {
  return `<div class="empty-state"><div class="empty-icon">${icon(symbol)}</div><h3>${esc(title)}</h3><p>${esc(copy)}</p>${action ? `<button class="button dark" data-action="${action}">${esc(label)} ${icon("arrow")}</button>` : ""}</div>`;
}
function setView(view, scroll = false) {
  if (view === "review" && state.board?.hourly_mode) view = "operations";
  if (
    ![
      "overview",
      "investigate",
      "review",
      "procurement",
      "history",
      "operations",
    ].includes(view)
  )
    return;
  state.view = view;
  document
    .querySelectorAll(".view")
    .forEach((el) => (el.hidden = el.id !== "view-" + view));
  document.querySelectorAll("[data-view]").forEach((el) => {
    const active = el.dataset.view === view;
    el.classList.toggle("active", active);
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  renderActionbar();
  if (view === "operations")
    document.dispatchEvent(new CustomEvent("factory:operations"));
  if (scroll)
    document.querySelector(".workflow-nav").scrollIntoView({
      block: "start",
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
}
function setConnection(online) {
  $("connection").className = "connection " + (online ? "online" : "offline");
  $("connection").innerHTML =
    `<i></i><span>${online ? "Floor connected" : "Connection offline"}</span>`;
}
async function refresh({ quiet = false, force = false } = {}) {
  if (state.refreshing && !force) return;
  const epoch = state.epoch,
    request = ++state.refreshVersion;
  state.refreshing = true;
  try {
    const board = await api("board");
    if (epoch !== state.epoch || request !== state.refreshVersion) return;
    const changed = JSON.stringify(board) !== JSON.stringify(state.board);
    state.board = board;
    if (board.hourly_mode && state.incident?.awaiting_approval)
      state.incident = null;
    setConnection(true);
    $("revision").textContent =
      "SHOP REVISION " + String(board.revision).padStart(2, "0");
    if (pending() && currentPlan()?.revision !== board.revision) {
      const newly = !state.stale;
      state.stale = true;
      if (newly)
        notice(
          "Shop facts changed. Reanalyse before approving this recovery.",
          "info",
          "reanalyse",
        );
    }
    if (changed) renderAll();
  } catch (error) {
    if (!(error instanceof ScopeChanged) && request === state.refreshVersion) {
      setConnection(false);
      if (!quiet) {
        fail(error);
        $("board").innerHTML = empty(
          "Floor unavailable",
          "Check the local service or open settings to enter your shop access token.",
          "settings",
          "Open settings",
          "alert",
        );
        $("kpis").innerHTML = "";
      }
    }
  } finally {
    if (epoch === state.epoch && request === state.refreshVersion)
      state.refreshing = false;
  }
}
function impactStats() {
  const hourly = state.hourlyReview?.plans?.[state.hourlyReview.selected || 0];
  if (hourly) {
    const affected = (state.board?.jobs || []).filter((j) =>
      hourly.affected.includes(j.id),
    );
    return {
      commitments: new Set(affected.map((j) => j.order_id).filter(Boolean))
        .size,
      late: hourly.assignments.filter(
        (a) =>
          a.start_hour + a.hours >
          ((state.board?.jobs || []).find((j) => j.id === a.job)?.due_hour ??
            Infinity),
      ).length,
      open: (state.board?.tasks || []).filter(
        (t) => !["received", "cancelled"].includes(t.status),
      ).length,
    };
  }
  const plan = currentPlan();
  return {
    commitments: new Set((plan?.impact || []).map((r) => r[2]).filter(Boolean))
      .size,
    late:
      plan?.late_jobs?.length ??
      (state.board?.jobs || []).filter((j) => j.day > j.due).length,
    open: (state.board?.tasks || []).filter(
      (t) => !["received", "cancelled"].includes(t.status),
    ).length,
  };
}
function renderKpis() {
  const { commitments, late, open } = impactStats();
  const hourly = Boolean(state.hourlyReview);
  const waiting = pending() || state.hourlyReview?.status === "pending";
  const items = [
    [
      "Affected commitments",
      state.incident || hourly ? commitments : "—",
      state.incident || hourly ? "connected promises" : "analyse an outage",
      "network",
      hourly ? "operations" : "investigate",
      commitments > 0,
    ],
    [
      "Late jobs",
      late,
      currentPlan() || hourly ? "in selected recovery" : "on the current floor",
      "clock",
      hourly ? "operations" : "review",
      late > 0,
    ],
    [
      "Pending review",
      waiting ? 1 : 0,
      waiting ? "your decision needed" : "no open decision",
      "layers",
      hourly ? "operations" : "review",
      waiting,
    ],
    [
      "Material requests",
      open,
      "open handoffs",
      "box",
      "procurement",
      open > 0,
    ],
  ];
  $("kpis").innerHTML = items
    .map(
      ([label, n, unit, symbol, view, alert]) =>
        `<button class="kpi ${alert ? "alert" : ""}" data-view="${view}" ${label === "Late jobs" && !hourly ? 'data-kpi="late"' : ""}><span><span class="kpi-label">${label}</span><span class="kpi-value">${n} <span class="kpi-unit">${unit}</span></span></span><span class="kpi-symbol">${icon(symbol)}</span></button>`,
    )
    .join("");
}
function boardJobs() {
  let jobs = (state.board?.jobs || []).map((j) => ({ ...j }));
  if (state.version === "before" && currentPlan()) {
    const before = currentPlan().before || [];
    jobs = jobs
      .filter((j) => before.some((a) => a.job === j.id))
      .map((j) => ({ ...j, ...before.find((a) => a.job === j.id) }));
  }
  if (state.version === "proposed" && currentPlan())
    jobs = jobs.map((j) => ({
      ...j,
      ...(currentPlan().assignments.find((a) => a.job === j.id) || {}),
    }));
  if (state.boardFilter === "late") jobs = jobs.filter((j) => j.day > j.due);
  return jobs;
}
function renderBoard() {
  const board = state.board;
  if (!board) return;
  updateSegments("schedule-mode", "button", "mode", state.mode);
  updateSegments("schedule-version", "button", "version", state.version);
  const jobs = boardJobs();
  $("schedule-version").hidden = !currentPlan();
  $("schedule-caption").textContent =
    (state.version === "proposed"
      ? "Selected recovery preview"
      : state.version === "before"
        ? "Before recovery · affected jobs only"
        : "Current production assignments") +
    (state.boardFilter === "late" ? " · late jobs only" : "");
  if (state.mode === "table") {
    $("board").innerHTML =
      `<div class="table-wrap"><table class="data-table mobile-cards"><thead><tr><th>Job</th><th>Machine</th><th>Scheduled</th><th>Due</th><th>Status</th></tr></thead><tbody>${jobs.map((j) => `<tr><td data-label="Job"><strong>${esc(j.id)} / ${esc(j.name)}</strong><small>${j.hours} work hours</small></td><td data-label="Machine">${esc(machineName(j.machine))}</td><td data-label="Scheduled">Day ${j.day}</td><td data-label="Due">Day ${j.due}</td><td data-label="Status">${badge(j.day > j.due ? "Late" : "On schedule", j.day > j.due ? "orange" : "green")}</td></tr>`).join("")}</tbody></table></div>${!jobs.length ? '<p class="empty-copy">No jobs match this filter.</p>' : ""}`;
    return;
  }
  const plan = currentPlan();
  const days = Math.min(
    30,
    Math.max(
      4,
      ...jobs.map((j) => Math.max(j.day, j.due) + 1),
      plan ? plan.until + 1 : 0,
    ),
  );
  const outage = state.incident?.plans?.[0];
  $("board").innerHTML =
    `<div class="schedule-scroll"><div class="machine-schedule" style="--days:${days};min-width:${Math.max(560, days * 68 + 110)}px"><div class="schedule-axis"><span></span><div class="axis-days">${Array.from({ length: days }, (_, d) => `<div>DAY ${String(d).padStart(2, "0")}</div>`).join("")}</div></div>${board.machines
      .map((m) => {
        const machineJobs = jobs
          .filter((j) => j.machine === m.id)
          .sort((a, b) => a.day - b.day || a.id.localeCompare(b.id));
        const rows = {};
        machineJobs.forEach((j) => {
          j.displayRow = rows[j.day] || 0;
          rows[j.day] = j.displayRow + 1;
        });
        const height = Math.max(
          87,
          Math.max(0, ...Object.values(rows)) * 33 + 18,
        );
        return `<div class="machine-lane"><div class="machine-name">${esc(machineName(m.id))}<small>${esc(m.id)} / ${esc(m.kind)}</small></div><div class="lane-grid" style="min-height:${height}px">${outage?.machine === m.id ? `<div class="outage-shade" style="--start:${outage.day};--length:${outage.until - outage.day + 1}" aria-label="Outage days ${outage.day} through ${outage.until}"></div>` : ""}${machineJobs.map((j) => `<span class="due-mark" style="--due:${j.due}" title="${esc(j.id)} due by day ${j.due}"></span><button class="job-bar ${j.day > j.due ? "late" : ""} ${state.version === "proposed" ? "proposed" : ""}" style="--day:${j.day};--hours:${j.hours};--row:${j.displayRow}" data-job="${esc(j.id)}" aria-label="${esc(j.id + " " + j.name)}, ${esc(machineName(m.id))}, day ${j.day}, ${j.hours} hours, due day ${j.due}${j.day > j.due ? ", late" : ""}" title="${esc(j.name)} · ${j.hours}h · due day ${j.due}">${esc(j.id)} · ${j.hours}h</button>`).join("")}</div></div>`;
      })
      .join("")}</div></div>`;
}
function activityItems() {
  const b = state.board;
  if (!b) return [];
  return [
    ...b.memories.map((m) => ({
      ...m,
      category: m.kind || "episodic",
      title: Number.isFinite(m.observed_repair_hours)
        ? "Repair observation recorded"
        : m.kind === "semantic"
          ? "Supplier knowledge updated"
          : m.kind === "procedural"
            ? "Recovery procedure available"
            : m.agent === "procurement"
              ? "Procurement handoff drafted"
              : "Recovery decision retained",
    })),
    ...b.decisions.map((d, i) => ({
      ...d,
      id: "decision-" + i,
      category: "decisions",
      title:
        d.outcome === "approved" ? "Recovery approved" : "Recovery rejected",
      summary:
        d.outcome === "approved"
          ? "A reviewed recovery was applied to the production floor."
          : "The proposal was rejected. Production assignments were retained.",
      source: d.plan,
      created: 0,
    })),
  ].sort((a, b) => (b.created || 0) - (a.created || 0));
}
function memorySummary(memory) {
  if (Number.isFinite(memory.observed_repair_hours))
    return `Recorded repair: ${memory.observed_repair_hours} hours. This sourced observation can inform a later repair estimate.`;
  if (Number.isFinite(memory.observed_lead))
    return `Recorded supplier lead: ${memory.observed_lead} ${memory.observed_lead === 1 ? "day" : "days"}. This delivery observation is available to future recovery planning.`;
  if (Number.isFinite(memory.confirmed_lead))
    return `The supplier expects delivery after ${memory.confirmed_lead} ${memory.confirmed_lead === 1 ? "day" : "days"}. The material has not been received.`;
  return memory.summary || "A sourced shop event was recorded.";
}
function timeLabel(timestamp) {
  return timestamp
    ? new Date(timestamp * 1000).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Recorded decision";
}
function renderOverview() {
  const plan = currentPlan();
  const stats = impactStats();
  $("brief").innerHTML = plan
    ? `<div class="eyebrow">${pending() ? "RECOVERY READY FOR REVIEW" : "LATEST RECOVERY"}</div><h2>${pending() ? "THE NEXT MOVE<br>IS YOURS." : "CONTEXT KEPT.<br>FLOOR MOVING."}</h2><p>${stats.commitments} connected commitment${stats.commitments === 1 ? "" : "s"}. ${plan.assignments.length} jobs in the recovery. ${plan.late_jobs.length ? plan.late_jobs.length + " jobs remain late — review the trade-offs." : "No late jobs in this proposal."}</p><button class="text-button light" data-view="${pending() ? "review" : "history"}">${pending() ? "Compare recovery plans" : "Inspect decision memory"} ${icon("arrow")}</button><div class="brief-grid-art"><span>CONTEXT</span><i></i><span>DECISION</span><i></i><span>ACTION</span></div>`
    : `<div class="eyebrow">NEXT BEST MOVE</div><h2>SEE THE WHOLE<br>CHAIN REACTION.</h2><p>Start with an outage. Follow the connections from the machine to the customer promise.</p><button class="text-button light" data-action="incident">Set up an incident ${icon("arrow")}</button><div class="brief-grid-art"><span>MACHINE</span><i></i><span>JOB</span><i></i><span>PROMISE</span></div>`;
  const recent = activityItems().slice(0, 3);
  if (state.board?.hourly_mode) {
    const r = state.hourlyReview;
    $("brief").innerHTML =
      `<div class="eyebrow">${r?.status === "pending" ? "HOURLY RECOVERY / PENDING REVIEW" : "HOURLY FACTORY OPERATIONS"}</div><h2>EVERY HOUR.<br>EVERY DECISION.</h2><p>${r ? `${r.affected_jobs} jobs in the latest ${r.event.kind === "restoration" ? "post-repair review" : "breakdown analysis"}. Review the cost assumptions, order exposure and maintenance outcome.` : "Model a breakdown in hours, compare its financial exposure and coordinate the repair."}</p><button class="text-button light" data-view="operations">Open exposure & repair ${icon("arrow")}</button><div class="brief-grid-art"><span>IMPACT</span><i></i><span>COST</span><i></i><span>RECOVERY</span></div>`;
  }
  $("recent-activity").innerHTML = recent.length
    ? recent
        .map(
          (m) =>
            `<div class="activity-mini-item"><span class="activity-pin"></span><div><h3>${esc(m.title)}</h3><p>${esc(memorySummary(m))}</p><small>${esc(timeLabel(m.created))}</small></div></div>`,
        )
        .join("")
    : '<p class="empty-copy">A clean slate. Decisions and deliveries will appear here as the shop works.</p>';
}
function investigationPlan() {
  return state.board?.hourly_mode ? state.hourlyImpact : currentPlan();
}
function graphData() {
  const plan = investigationPlan();
  if (!plan) return { nodes: [], edges: [], chains: [] };
  const nodes = new Map(),
    edges = new Map();
  const chains = (plan.impact_paths || []).map((row) => [
    plan.machine,
    ...row[0].slice().reverse(),
    ...(row[1] ? [row[1], row[2]] : []),
  ]);
  chains.forEach((chain) =>
    chain.forEach((id, index) => {
      if (!nodes.has(id))
        nodes.set(id, {
          id,
          level: index,
          type:
            index === 0
              ? "machine"
              : state.board?.jobs.some((j) => j.id === id)
                ? "job"
                : index === chain.length - 1 && !/^K\d+$/.test(id)
                  ? "customer"
                  : "commitment",
        });
      else nodes.get(id).level = Math.max(nodes.get(id).level, index);
      if (index) {
        const key = chain[index - 1] + "|" + id;
        edges.set(key, { from: chain[index - 1], to: id });
      }
    }),
  );
  const max = Math.max(1, ...[...nodes.values()].map((n) => n.level));
  const levels = {};
  for (const n of nodes.values()) (levels[n.level] ??= []).push(n);
  for (const group of Object.values(levels))
    group.forEach((n, i) => {
      n.x = 9 + (n.level / max) * 82;
      n.y = (100 * (i + 1)) / (group.length + 1);
      n.label =
        n.type === "machine"
          ? machineName(n.id)
          : n.type === "job"
            ? jobName(n.id)
            : n.type === "commitment"
              ? "Delivery " + n.id
              : n.id;
    });
  return { nodes: [...nodes.values()], edges: [...edges.values()], chains };
}
function renderGraph() {
  const plan = investigationPlan();
  if (!plan) {
    $("investigation-content").innerHTML = empty(
      "Find the chain reaction.",
      "Run an outage analysis to connect machines, jobs, dependencies and customer commitments.",
    );
    return;
  }
  const graph = graphData();
  const chosen = graph.nodes.find((n) => n.id === state.selectedNode);
  const activeChains = chosen
    ? graph.chains.filter((c) => c.includes(chosen.id))
    : graph.chains;
  const active = new Set(activeChains.flat());
  const nodeContent = graph.nodes
    .map(
      (n) =>
        `<button class="graph-node ${n.type} ${chosen?.id === n.id ? "selected" : ""} ${!active.has(n.id) ? "dim" : ""}" data-node="${esc(n.id)}" style="left:${n.x}%;top:${n.y}%" aria-pressed="${chosen?.id === n.id}" aria-label="${esc(n.type + " " + n.label)}"><small>${esc(n.type)}</small><strong>${esc(n.label)}</strong></button>`,
    )
    .join("");
  const edgeContent = graph.edges
    .map((edge) => {
      const a = graph.nodes.find((n) => n.id === edge.from),
        b = graph.nodes.find((n) => n.id === edge.to);
      const highlighted =
        chosen &&
        activeChains.some((c) =>
          c.some((id, i) => id === edge.from && c[i + 1] === edge.to),
        );
      return `<path class="graph-edge ${chosen ? (highlighted ? "highlight" : "dim") : ""}" d="M${a.x * 10},${a.y * 3.5} C${(a.x + b.x) * 5},${a.y * 3.5} ${(a.x + b.x) * 5},${b.y * 3.5} ${b.x * 10},${b.y * 3.5}"/>`;
    })
    .join("");
  const selectedJob =
    chosen && state.board?.jobs.find((j) => j.id === chosen.id);
  $("investigation-content").innerHTML =
    `<div class="graph-layout"><section class="panel graph-panel"><div class="graph-toolbar"><span class="eyebrow">${graph.nodes.length} ENTITIES / ${graph.edges.length} CONNECTIONS</span><div class="segmented"><button data-graph-mode="graph" class="${state.graphMode === "graph" ? "active" : ""}" aria-pressed="${state.graphMode === "graph"}">Graph view</button><button data-graph-mode="list" class="${state.graphMode === "list" ? "active" : ""}" aria-pressed="${state.graphMode === "list"}">Path list</button></div></div>${state.graphMode === "graph" ? `<div class="graph-scroll"><div class="graph-canvas" style="min-width:${Math.max(650, (Math.max(...graph.nodes.map((n) => n.level)) + 1) * 145)}px"><svg class="graph-svg" viewBox="0 0 1000 350" preserveAspectRatio="none" aria-hidden="true">${edgeContent}</svg>${nodeContent}</div></div>` : `<div class="path-list">${graph.chains.map((c) => `<div class="path-row">${c.map((id) => `<span>${esc(graph.nodes.find((n) => n.id === id)?.label || id)}</span>`).join('<b aria-label="leads to">→</b>')}</div>`).join("")}</div>`}<div class="graph-caption"><span>Connections come from the reviewed graph context.</span><span>Machine → work → customer promise</span></div></section><aside class="graph-inspector"><div class="eyebrow">${chosen ? "SELECTED ENTITY" : "EVIDENCE INSPECTOR"}</div><h3>${esc(chosen?.label || "Nothing happens in isolation.")}</h3><p>${chosen ? `This ${esc(chosen.type)} appears on ${activeChains.length} impact path${activeChains.length === 1 ? "" : "s"} in the selected recovery. Related connections are highlighted.` : "Select a node to see the connected work and promises behind it. Every node can be reached with the keyboard."}</p>${chosen ? `<div class="inspector-row"><span>Entity</span><strong>${esc(chosen.id)}</strong></div><div class="inspector-row"><span>Role</span><strong>${esc(chosen.type)}</strong></div>${selectedJob ? `<div class="inspector-row"><span>Scheduled / due</span><strong>Day ${selectedJob.day} / ${selectedJob.due}</strong></div><button class="text-button underlined" data-job="${esc(selectedJob.id)}">Inspect job details</button>` : ""}<button class="text-button underlined" data-action="clear-node">Clear selection</button>` : `<div class="inspector-row"><span>Affected jobs</span><strong>${new Set((plan.impact || []).map((r) => r[1])).size}</strong></div><div class="inspector-row"><span>Customer promises</span><strong>${graph.nodes.filter((node) => node.type === "commitment").length}</strong></div>`}</aside></div><div class="tool-strip">${(state.incident?.investigation || plan.tool_trace || []).map((t) => `<article class="tool-card"><div class="eyebrow">${esc(t.agent)} / EVIDENCE TOOL</div><h3>${esc({ trace_impact: "Trace connected impact", recall_incidents: "Recall previous incidents", check_capacity: "Check qualified capacity", check_materials: "Check material readiness" }[t.tool] || t.tool)}</h3><p>${esc(t.reason || t.provider || "Graph context tool")}${t.rows !== undefined ? " · " + t.rows + " evidence rows" : ""}</p></article>`).join("")}</div>`;
}
function renderReview() {
  const incident = state.incident,
    plan = currentPlan();
  if (!plan) {
    $("review-content").innerHTML = empty(
      "Make the next move an informed one.",
      "Analyse an outage to compare constrained recovery options before anything changes on the floor.",
    );
    renderActionbar();
    return;
  }
  const memory = (plan.memory_evidence || []).filter(
    (m) => m.observed_lead !== undefined,
  );
  $("review-content").innerHTML =
    `<div class="section-title"><div><div class="eyebrow">REVIEW THE TRADE-OFFS. OWN THE DECISION.</div><h2>A recovery you can stand behind.</h2><p>${esc(machineName(plan.machine))} outage · day ${plan.day}${plan.until !== plan.day ? "–" + plan.until : ""} · ${plan.assignments.length} jobs to reschedule.</p></div>${badge(state.stale ? "REANALYSIS REQUIRED" : pending() ? "HUMAN APPROVAL REQUIRED" : incident.result?.status === "rejected" ? "PROPOSAL REJECTED" : "REVIEW COMPLETED", state.stale ? "orange" : pending() ? "dark" : "green")}</div><div class="review-banner"><div><div class="eyebrow">RECOMMENDATION / ${esc(incident.recommendation?.provider || "POLICY")}</div><p>${esc(incident.recommendation?.reason || "Compare the alternatives and review material assumptions.")}</p></div><button class="text-button" data-view="investigate">Inspect graph evidence ${icon("arrow")}</button></div>${memory.length ? `<div class="memory-influence"><h3>${icon("history")} Remembered supplier context is in this recovery.</h3><p>${memory.map((m) => `${esc(memorySummary(m))} Planning considers an observed lead of ${m.observed_lead} day${m.observed_lead === 1 ? "" : "s"} for ${esc(m.supplier)}.`).join(" ")} A past delivery is evidence, not a guarantee of the next one.</p><button class="text-button underlined" data-action="memory-used">See the source in memory</button></div>` : ""}<div class="plan-grid">${incident.plans
      .map((p, i) => {
        const customers = new Set(
          (p.impact || [])
            .filter((r) => p.late_jobs.includes(r[1]))
            .map((r) => r[3])
            .filter(Boolean),
        );
        const recommended = incident.recommendation?.hash === p.hash;
        return `<article class="plan-card ${state.selected === i ? "selected" : ""}"><div class="plan-card-head"><div><div class="eyebrow">OPTION ${String(i + 1).padStart(2, "0")}${recommended ? " / RECOMMENDED" : ""}</div><h3>${esc(planName(p))}</h3></div><label class="plan-choice"><input type="radio" name="selected-plan" aria-label="Select ${esc(planName(p))}" value="${i}" ${state.selected === i ? "checked" : ""} ${!pending() ? "disabled" : ""}> Select</label></div><div class="comparison-row"><span>Late jobs</span><strong class="${p.late_jobs.length ? "plan-late" : ""}">${p.late_jobs.length}<small>of ${p.assignments.length}</small></strong></div><div class="comparison-row"><span>Customers with late jobs</span><strong>${customers.size}</strong></div><div class="comparison-row"><span>Planned operator overtime</span><strong>${p.metrics?.operator_overtime_hours ?? 0}<small>hours</small></strong></div><div class="comparison-row"><span>New material requests</span><strong>${p.procurement.length}</strong></div><div class="comparison-row"><span class="metric-info" tabindex="0" title="Sum of late days multiplied by each job’s priority. Lower is better among feasible plans.">Priority-weighted delay ⓘ</span><strong>${p.metrics?.weighted_delay ?? "—"}</strong></div><div class="plan-section"><h4>Schedule changes</h4>${p.assignments
          .map((a) => {
            const old = p.before?.find((b) => b.job === a.job);
            return `<div class="job-change"><strong>${esc(a.job)} / ${esc(machineName(a.machine))}</strong><span>D${old?.day ?? "?"} → D${a.day}${p.late_jobs.includes(a.job) ? " · LATE" : ""}</span></div>`;
          })
          .join(
            "",
          )}</div><div class="plan-section"><h4>Material assumptions</h4><p>${p.procurement.length ? p.procurement.map((t) => `${t.quantity} units for ${esc(t.job)}, expected on day ${t.arrival_day}.`).join(" ") + " Supplier arrival must be confirmed." : "No new procurement request in this plan. Existing reservations and incoming supplies still constrain readiness."}</p></div><div class="plan-section"><button class="text-button underlined" data-plan-proof="${i}">Why this plan is feasible ${icon("arrow")}</button></div></article>`;
      })
      .join(
        "",
      )}</div><details class="technical"><summary>Technical evidence & exact proposal identifiers</summary><p class="field-help">The server verifies the exact selected proposal again before applying changes.</p><pre>${esc(JSON.stringify(incident, null, 2))}</pre></details>`;
  renderActionbar();
}
function renderActionbar() {
  const el = $("review-actionbar"),
    plan = currentPlan();
  const visible = state.view === "review" && pending() && Boolean(plan);
  el.hidden = !visible;
  document.body.classList.toggle("has-actionbar", visible);
  if (!visible) return;
  el.innerHTML = `<div class="actionbar-copy"><span class="eyebrow">${state.stale ? "SHOP CONTEXT CHANGED" : "YOUR DECISION / EXACT PLAN"}</span><strong>${esc(planName(plan))} · ${plan.late_jobs.length} late job${plan.late_jobs.length === 1 ? "" : "s"} · ${plan.metrics?.operator_overtime_hours || 0}h overtime</strong></div><div class="actionbar-buttons">${state.stale ? '<button class="button primary" data-action="reanalyse">Reanalyse outage</button>' : '<button class="button secondary" data-action="reject">Reject proposal</button><button class="button primary" data-action="approve">Review & approve ' + icon("arrow") + "</button>"}</div>`;
}
function renderHistory() {
  let items = activityItems();
  if (state.memoryFilter === "used") {
    const ids = new Set(
      (
        (state.board?.hourly_mode
          ? state.hourlyReview?.plans?.[state.hourlyReview.selected || 0]
          : currentPlan()
        )?.memory_evidence || []
      ).map((m) => m.id),
    );
    items = items.filter((m) => ids.has(m.id));
  } else if (state.memoryFilter !== "all")
    items = items.filter((m) => m.category === state.memoryFilter);
  const used = new Set(
    (
      (state.board?.hourly_mode
        ? state.hourlyReview?.plans?.[state.hourlyReview.selected || 0]
        : currentPlan()
      )?.memory_evidence || []
    ).map((m) => m.id),
  );
  $("memory").innerHTML = items.length
    ? `<div class="activity-feed">${items.map((m) => `<article class="activity-item"><div class="activity-type-icon">${icon(m.category === "semantic" ? "box" : m.category === "procedural" ? "file" : m.category === "decisions" ? "check" : "history")}</div><div class="activity-card"><div class="activity-card-top">${badge({ semantic: Number.isFinite(m.observed_repair_hours) ? "REPAIR KNOWLEDGE" : "SUPPLIER KNOWLEDGE", procedural: "PROCEDURE", episodic: "INCIDENT MEMORY", decisions: "DECISION" }[m.category] || m.category, used.has(m.id) ? "green" : "")}<time ${m.created ? `datetime="${new Date(m.created * 1000).toISOString()}"` : ""}>${esc(timeLabel(m.created))}</time></div><h3>${esc(m.title)}</h3><p>${esc(memorySummary(m))}</p>${m.observed_lead !== undefined ? `<div class="inspector-row"><span>Observed delivery lead</span><strong>${m.observed_lead} days</strong></div>` : ""}${used.has(m.id) ? `<div class="memory-influence"><p>${m.observed_lead !== undefined ? "This observation is included in the selected recovery’s material-readiness calculation." : "This sourced context was retrieved for the selected recovery review."}</p><button class="text-button underlined" data-view="review">Inspect the recovery ${icon("arrow")}</button></div>` : ""}<details><summary>Source & provenance</summary><div class="source-detail">Source: ${esc(m.source || "Graph decision record")}<br>Record: ${esc(m.id)}${m.valid_until ? "<br>Context valid until: " + esc(new Date(m.valid_until * 1000).toLocaleDateString()) : ""}</div></details></div></article>`).join("")}</div>`
    : empty(
        "Nothing in this view yet.",
        "Decisions and sourced observations appear as the shop works. Try another filter or start an incident.",
        "",
        "",
        "history",
      );
}
function renderMaterials() {
  const b = state.board;
  if (!b) return;
  let tasks = b.tasks;
  if (state.taskFilter === "open")
    tasks = tasks.filter((t) => !["received", "cancelled"].includes(t.status));
  else if (state.taskFilter !== "all")
    tasks = tasks.filter((t) => t.status === state.taskFilter);
  const labels = {
    pending: "Pending worker",
    claimed: "Worker processing",
    drafted: "Draft prepared",
    confirmed: "Arrival expected",
    received: "Received",
    cancelled: "Cancelled",
    failed: "Needs attention",
  };
  $("tasks").innerHTML = tasks.length
    ? `<div class="task-grid">${tasks.map((t) => `<article class="task-card"><div class="task-card-top">${badge(labels[t.status] || t.status, ["failed", "pending"].includes(t.status) ? "orange" : t.status === "received" ? "green" : "")}<span>${esc(t.owner || "Procurement")}</span></div><h3>${esc(t.draft || "Material request awaiting preparation")}</h3><p>${t.status === "received" ? "Receipt recorded. Material is reserved for its linked job." : t.status === "cancelled" ? "This request is closed. Reanalyse to review remaining material needs." : "An internal request only. No external order has been sent."}</p><dl class="task-details"><div><dt>Quantity</dt><dd>${t.quantity} units</dd></div><div><dt>${t.status === "received" ? "Received" : "Expected"} arrival</dt><dd>Day ${t.arrival_day}</dd></div></dl><div class="task-card-actions">${!["received", "cancelled"].includes(t.status) ? `<button class="button dark" data-feedback="${esc(t.id)}">Record supplier update</button><button class="text-button" data-cancel-task="${esc(t.id)}">Cancel request</button>` : '<span class="badge">CLOSED REQUEST</span>'}${t.status === "failed" ? `<button class="button secondary" data-retry="${esc(t.id)}">Retry worker</button>` : ""}</div></article>`).join("")}</div>`
    : empty(
        "No requests to chase.",
        "Approved recoveries create material handoffs here when stock is short.",
        "",
        "",
        "box",
      );
  $("reservations").innerHTML = b.reservations.length
    ? `<section class="panel"><div class="table-wrap"><table class="data-table mobile-cards"><thead><tr><th>Job / material</th><th>Quantity</th><th>Status</th><th>Action</th></tr></thead><tbody>${b.reservations.map((r) => `<tr><td data-label="Job / material"><strong>${esc(r.job)} / ${esc(r.material)}</strong><small>${esc(jobName(r.job))}</small></td><td data-label="Quantity">${r.quantity} units</td><td data-label="Status">${badge(r.status, r.status === "reserved" ? "green" : "")}</td><td data-label="Actions">${r.status === "reserved" ? `<button class="button secondary" data-reservation="${esc(r.id)}" data-consume="false">Release</button> <button class="button secondary" data-reservation="${esc(r.id)}" data-consume="true">Consume</button>` : "—"}</td></tr>`).join("")}</tbody></table></div></section>`
    : empty(
        "Every unit accounted for.",
        "Reserved inventory will appear after a recovery is approved or a receipt is recorded.",
        "",
        "",
        "box",
      );
}
function renderAll() {
  if (!state.board) return;
  const hourlyMode = Boolean(state.board.hourly_mode);
  $("incident-form").hidden = hourlyMode;
  $("hourly-quickstart").hidden = !hourlyMode;
  document
    .querySelectorAll(
      ".rail-item[data-view='review'], .workflow-nav [data-view='review']",
    )
    .forEach((el) => {
      el.hidden = hourlyMode;
    });
  if (state.board.hourly_mode && !state.busy) {
    $("analyze").textContent = "Open hourly recovery →";
    $("day").disabled = true;
    $("until").disabled = true;
    $("incident-hint").textContent =
      "This factory uses hourly exposure and repair planning.";
  } else if (!state.busy) {
    $("analyze").textContent = "Find recovery plans →";
    $("day").disabled = false;
    $("until").disabled = false;
    $("incident-hint").textContent =
      "Use relative days; day 0 is the demo reference day.";
  }
  const options = state.board.machines
    .map(
      (m) =>
        `<option value="${esc(m.id)}">${esc(machineName(m.id))} / ${esc(m.kind)}</option>`,
    )
    .join("");
  if ($("machine").innerHTML !== options) {
    const value = $("machine").value;
    $("machine").innerHTML = options;
    if (state.board.machines.some((m) => m.id === value))
      $("machine").value = value;
  }
  renderKpis();
  renderBoard();
  renderOverview();
  renderGraph();
  renderReview();
  renderMaterials();
  renderHistory();
  $("review-dot").hidden = !pending();
  $("review-count").hidden = !pending();
  $("review-count").textContent = state.incident?.plans?.length || 0;
}
function openDrawer(title, eyebrow, html) {
  $("drawer-title").textContent = title;
  $("drawer-eyebrow").textContent = eyebrow;
  $("drawer-body").innerHTML = html;
  hydrateIcons($("drawer"));
  if (!$("drawer").open) $("drawer").showModal();
}
function openSettings() {
  openDrawer(
    "Your shop connection",
    "WORKSPACE SETTINGS",
    `<p class="form-description">Connect to an authenticated shop if your server requires an access token. The token stays in this page’s memory.</p><form id="settings-form"><label for="shop-token">Shop access token</label><input id="shop-token" type="password" value="${esc(state.token)}" autocomplete="off" placeholder="Not required for the local demo"><p class="field-help">No token is saved in browser storage. Reopening the page requires entering it again.</p><button class="button dark full" type="submit">Connect to shop ${icon("arrow")}</button></form><div class="plan-section"><h4>Demo environment</h4><p>Each scenario is an independent, persistent synthetic shop. Your earlier demo data is retained when you switch.</p><button class="text-button underlined" data-action="scenarios">Choose a demo scenario</button></div>`,
  );
}
function openScenarios() {
  openDrawer(
    "Choose your disruption",
    "SYNTHETIC DEMO SCENARIOS",
    `<p class="form-description">Explore six different operating conditions. Each shop keeps its own decisions, schedule and material records.</p><div class="scenario-options">${Object.entries(
      scenarios,
    )
      .map(
        ([id, [name, description]], i) =>
          `<button class="scenario-option ${state.scenario === id ? "active" : ""}" data-scenario="${id}" aria-pressed="${state.scenario === id}"><span class="scenario-number">${String(i + 1).padStart(2, "0")}</span><span><strong>${esc(name)}</strong><p>${esc(description)}</p></span></button>`,
      )
      .join("")}</div>`,
  );
}
async function changeShop(scenario = state.scenario, token = state.token) {
  if (scenario !== state.scenario || token !== state.token) state.factory = "";
  state.hourlyReview = null;
  state.hourlyImpact = null;
  state.epoch++;
  document.dispatchEvent(new CustomEvent("factory:scope"));
  state.scenario = scenario;
  state.token = token;
  state.board = null;
  state.incident = null;
  state.selected = 0;
  state.selectedNode = null;
  state.stale = false;
  state.refreshing = false;
  state.refreshVersion++;
  state.busy = 0;
  state.boardFilter = "all";
  state.version = "current";
  $("connection").className = "connection";
  $("connection").innerHTML = "<i></i><span>Connecting</span>";
  $("revision").textContent = "CONNECTING TO FLOOR";
  $("brief").innerHTML =
    '<div class="eyebrow">OPENING SHOP</div><h2>CONTEXT IS<br>ON ITS WAY.</h2><p>Loading this shop’s current assignments and decisions.</p>';
  $("recent-activity").innerHTML =
    '<p class="empty-copy">Loading shop activity…</p>';
  [
    "tasks",
    "reservations",
    "memory",
    "investigation-content",
    "review-content",
  ].forEach((id) => ($(id).innerHTML = ""));
  localStorage.setItem("factory-scenario", scenario);
  $("scenario-label").textContent = scenarios[scenario][0];
  $("notice").hidden = true;
  $("drawer").close();
  $("confirm-dialog").close();
  setView("overview");
  $("board").innerHTML = '<div class="skeleton board-placeholder"></div>';
  $("kpis").innerHTML = Array.from(
    { length: 4 },
    () => '<div class="skeleton kpi-placeholder"></div>',
  ).join("");
  await loadShop();
}
async function loadShop() {
  const epoch = state.epoch;
  await refresh({ force: true });
  if (epoch !== state.epoch || !state.board || state.board.hourly_mode) return;
  try {
    const key = await threadKey();
    if (epoch !== state.epoch) return;
    const thread = localStorage.getItem(key);
    if (thread) {
      try {
        const incident = await api("incidents/" + encodeURIComponent(thread));
        state.incident = incident;
        if (incident.awaiting_approval && incident.plans[0]) {
          $("machine").value = incident.plans[0].machine;
          $("day").value = incident.plans[0].day;
          $("until").value = incident.plans[0].until;
        }
        state.selected = Math.max(
          0,
          incident.plans.findIndex(
            (p) =>
              p.hash ===
              (localStorage.getItem(key + ":plan") ||
                incident.recommendation?.hash),
          ),
        );
        state.stale =
          pending() && currentPlan().revision !== state.board.revision;
        renderAll();
        if (pending()) {
          setView("review");
          notice(
            state.stale
              ? "Your saved review needs a new analysis."
              : "Your saved recovery review is ready.",
            "info",
            state.stale ? "reanalyse" : "",
          );
        }
      } catch (error) {
        if (error instanceof ScopeChanged) return;
        localStorage.removeItem(key);
        localStorage.removeItem(key + ":plan");
        notice(
          "Your previous review is unavailable. Start a new outage analysis.",
          "info",
        );
      }
    }
  } catch (error) {
    fail(error);
  }
}
async function analyse(event) {
  event?.preventDefault();
  if (state.board?.hourly_mode) {
    setView("operations", true);
    return;
  }
  if (state.busy) return;
  const from = Number($("day").value),
    until = Number($("until").value);
  const error = $("incident-error");
  error.hidden = true;
  ["day", "until"].forEach((id) => $(id).removeAttribute("aria-invalid"));
  if (
    $("day").value === "" ||
    $("until").value === "" ||
    !Number.isInteger(from) ||
    !Number.isInteger(until) ||
    from < 0 ||
    until > 29 ||
    until < from
  ) {
    error.textContent =
      "Enter whole days from 0 to 29. The end day must be on or after the start day.";
    error.hidden = false;
    $("until").setAttribute("aria-invalid", "true");
    $("until").focus();
    return;
  }
  const epoch = state.epoch;
  state.busy++;
  loading($("analyze"), true, "Following the connections…");
  notice(
    "Tracing impact, checking capacity, and recalling shop context…",
    "info",
  );
  try {
    const incident = await api("incidents", {
      machine: $("machine").value,
      day: from,
      until,
    });
    state.incident = incident;
    state.selected = Math.max(
      0,
      incident.plans.findIndex((p) => p.hash === incident.recommendation?.hash),
    );
    state.selectedNode = null;
    state.stale = false;
    state.version = "current";
    const key = await threadKey();
    if (epoch !== state.epoch) return;
    localStorage.setItem(key, incident.thread);
    localStorage.setItem(key + ":plan", incident.plans[state.selected].hash);
    renderAll();
    setView("review", true);
    notice(
      "Recovery options are ready. Compare the trade-offs before approving.",
    );
  } catch (error) {
    fail(error);
  } finally {
    if (epoch === state.epoch) {
      state.busy--;
      loading($("analyze"), false, "Find recovery plans");
    }
  }
}
function openApproval(approve) {
  if (state.busy) return;
  const plan = currentPlan();
  if (!plan || !pending() || state.stale) return;
  const title = approve
    ? "Put this recovery into motion?"
    : "Reject this proposal?";
  $("confirm-title").textContent = title;
  $("confirm-body").innerHTML = approve
    ? `<p>Apply <strong>${esc(planName(plan))}</strong> to the production floor. This changes the reviewed job assignments and creates any required material requests.</p><div class="approval-summary"><div><span>Jobs rescheduled</span><strong>${plan.assignments.length}</strong></div><div><span>Jobs still late</span><strong>${plan.late_jobs.length}</strong></div><div><span>Operator overtime</span><strong>${plan.metrics?.operator_overtime_hours || 0} hours</strong></div><div><span>New material requests</span><strong>${plan.procurement.length}</strong></div></div><label class="approval-check"><input type="checkbox" id="approval-check"> I reviewed the schedule, remaining delays and material assumptions.</label><details class="technical"><summary>Exact proposal identifier</summary><div class="exact-hash">${esc(plan.hash)}</div></details><p class="field-error" id="decision-error" role="alert" hidden></p><div class="confirmation-actions"><button class="button secondary" data-close="confirm-dialog">Keep reviewing</button><button class="button primary" id="confirm-decision" data-approve="true" disabled>Approve & apply</button></div>`
    : `<p>This ends the current review without changing production assignments. You can analyse the outage again to prepare a new recovery.</p><p class="field-error" id="decision-error" role="alert" hidden></p><div class="confirmation-actions"><button class="button secondary" data-close="confirm-dialog">Keep reviewing</button><button class="button danger" id="confirm-decision" data-approve="false">Reject proposal</button></div>`;
  $("confirm-dialog").showModal();
  $("approval-check")?.addEventListener("change", (event) => {
    $("confirm-decision").disabled = !event.target.checked;
  });
}
async function applyDecision(approve) {
  if (state.busy) return;
  const plan = currentPlan(),
    incident = state.incident,
    epoch = state.epoch;
  if (!plan || !pending() || state.stale) return;
  state.busy++;
  loading(
    $("confirm-decision"),
    true,
    approve ? "Applying reviewed recovery…" : "Closing review…",
  );
  try {
    const result = await api(
      "incidents/" + encodeURIComponent(incident.thread) + "/decision",
      { hash: plan.hash, approve },
    );
    state.incident = { ...incident, ...result, awaiting_approval: false };
    const key = await threadKey();
    if (epoch !== state.epoch) return;
    localStorage.removeItem(key);
    localStorage.removeItem(key + ":plan");
    $("confirm-dialog").close();
    await refresh({ force: true });
    setView(approve ? "overview" : "review", true);
    notice(
      approve
        ? "Recovery applied. The floor, material reservations and handoffs are updated."
        : "Proposal rejected. Production assignments were retained.",
    );
  } catch (error) {
    if (error instanceof ScopeChanged) return;
    state.stale = true;
    const el = $("decision-error");
    if (el) {
      el.textContent = error.message;
      el.hidden = false;
    }
    notice(error.message, "error", "reanalyse");
    renderActionbar();
  } finally {
    if (epoch === state.epoch) {
      state.busy--;
      if ($("confirm-decision"))
        loading(
          $("confirm-decision"),
          false,
          approve ? "Approve & apply" : "Reject proposal",
        );
    }
  }
}
function openFeedback(id) {
  const task = state.board?.tasks.find((t) => t.id === id);
  if (!task) return;
  openDrawer(
    "Record a supplier update",
    "PROCUREMENT / MATERIAL READINESS",
    `<p class="form-description">${esc(task.draft || "Material request")}<br><strong>${task.quantity} units · currently expected day ${task.arrival_day}</strong></p><form id="feedback-form" data-task="${esc(id)}" novalidate><label for="arrival-day">Arrival day</label><input id="arrival-day" name="arrival_day" type="number" min="0" max="60" value="${task.arrival_day}" required aria-describedby="arrival-help"><p class="field-help" id="arrival-help">A relative demo day from 0 to 60. Day 0 is the reference day.</p><span class="form-label" id="receipt-label">What has the supplier confirmed?</span><div class="radio-options" role="group" aria-labelledby="receipt-label"><label class="radio-option"><input type="radio" name="receipt" value="expected" checked><span><strong>Expected arrival</strong><small>Update this request’s arrival estimate. Material has not been received.</small></span></label><label class="radio-option"><input type="radio" name="receipt" value="received"><span><strong>Material received</strong><small>Record the actual delivery, reserve the units for the linked job, and remember the observed supplier lead.</small></span></label></div><p class="field-help">Changed facts require a fresh recovery review before further schedule changes.</p><p class="field-error" id="feedback-error" role="alert" hidden></p><button class="button dark full" id="save-feedback" type="submit">Save supplier update ${icon("arrow")}</button></form>`,
  );
}
async function saveFeedback(event) {
  event.preventDefault();
  if (state.busy) return;
  const form = event.target,
    input = $("arrival-day"),
    day = Number(input.value),
    error = $("feedback-error");
  error.hidden = true;
  input.removeAttribute("aria-invalid");
  if (input.value === "" || !Number.isInteger(day) || day < 0 || day > 60) {
    error.textContent = "Enter a whole arrival day between 0 and 60.";
    error.hidden = false;
    input.setAttribute("aria-invalid", "true");
    input.focus();
    return;
  }
  const epoch = state.epoch;
  state.busy++;
  loading($("save-feedback"), true, "Saving supplier update…");
  try {
    await api("tasks/" + encodeURIComponent(form.dataset.task) + "/feedback", {
      arrival_day: day,
      received: new FormData(form).get("receipt") === "received",
    });
    $("drawer").close();
    await refresh({ force: true });
    notice(
      "Supplier update saved with its source. Reanalyse to review the new material assumptions.",
      "success",
      currentPlan() ? "reanalyse" : "",
    );
  } catch (e) {
    if (!(e instanceof ScopeChanged)) {
      error.textContent = e.message;
      error.hidden = false;
    }
  } finally {
    if (epoch === state.epoch) {
      state.busy--;
      loading($("save-feedback"), false, "Save supplier update");
    }
  }
}
let confirmationAction = null;
function confirmOperation(title, copy, label, operation) {
  $("confirm-title").textContent = title;
  $("confirm-body").innerHTML =
    `<p>${esc(copy)}</p><p class="field-error" id="operation-error" role="alert" hidden></p><div class="confirmation-actions"><button class="button secondary" data-close="confirm-dialog">Go back</button><button class="button dark" id="confirm-operation">${esc(label)}</button></div>`;
  confirmationAction = operation;
  $("confirm-dialog").showModal();
}
async function performOperation(path, body, message) {
  if (state.busy) return;
  const epoch = state.epoch;
  state.busy++;
  try {
    await api(path, body);
    $("confirm-dialog").close();
    await refresh({ force: true });
    notice(message, "success", currentPlan() ? "reanalyse" : "");
  } catch (error) {
    if (!(error instanceof ScopeChanged)) {
      const el = $("operation-error");
      if (el) {
        el.textContent = error.message;
        el.hidden = false;
      } else fail(error);
    }
  } finally {
    if (epoch === state.epoch) state.busy--;
  }
}
function showJob(id) {
  const job =
    boardJobs().find((j) => j.id === id) ||
    state.board?.jobs.find((j) => j.id === id);
  if (!job) return;
  openDrawer(
    job.name,
    "PRODUCTION / JOB " + job.id,
    `<p class="form-description">${esc(job.id)} is ${job.day > job.due ? "scheduled after its due day." : "scheduled within its due day."}</p>${[
      ["Machine", machineName(job.machine)],
      ["Scheduled day", job.day],
      ["Due day", job.due],
      ["Work duration", job.hours + " hours"],
      ["Priority", job.priority],
    ]
      .map(
        ([key, value]) =>
          `<div class="inspector-row"><span>${key}</span><strong>${esc(value)}</strong></div>`,
      )
      .join(
        "",
      )}<div class="plan-section"><p>Work hours are scheduled in day buckets. The board does not imply an exact clock-time booking.</p>${currentPlan() ? '<button class="button secondary" data-action="job-impact">See connected impact ' + icon("arrow") + "</button>" : ""}</div>`,
  );
  state.selectedNode = id;
}
function showPlanProof(index) {
  const plan = state.incident?.plans[index];
  if (!plan) return;
  openDrawer(
    "Why this recovery is feasible",
    "VALIDATED CONSTRAINTS",
    `<p class="form-description">The solver checked graph-derived machine capacity, operator availability, dependencies, material readiness and the outage window.</p>${Object.entries(
      plan.excluded_candidates || {},
    )
      .map(
        ([reason, count]) =>
          `<div class="inspector-row"><span>${esc(reason.replaceAll("_", " "))}</span><strong>${count} excluded</strong></div>`,
      )
      .join(
        "",
      )}<p class="field-help">Counts reflect search candidate exclusions, not distinct rejected recovery plans.</p><div class="plan-section"><h4>Policy sources</h4>${(plan.policy_sources || []).map((p) => `<p>${esc(p.source || p.name)}</p>`).join("")}</div><details class="technical"><summary>Exact proposal identifier</summary><div class="exact-hash">${esc(plan.hash)}</div></details>`,
  );
}
function updateSegments(container, selector, key, value) {
  $(container)
    .querySelectorAll(selector)
    .forEach((button) => {
      const active = button.dataset[key] === value;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
}
function focusIncident() {
  if (state.board?.hourly_mode) {
    setView("operations", true);
    return;
  }
  setView("overview");
  $("incident-form").scrollIntoView({
    block: "center",
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
  });
  $("machine").focus({ preventScroll: true });
}
document.addEventListener("click", async (event) => {
  const el = event.target.closest("button,[data-view]");
  if (!el) return;
  if (el.dataset.close) {
    if (state.busy) return;
    $(el.dataset.close).close();
    return;
  }
  if (el.dataset.view) {
    if (el.dataset.kpi === "late") {
      state.boardFilter = "late";
      state.version = currentPlan() ? "proposed" : "current";
      state.mode = "table";
      updateSegments("schedule-mode", "button", "mode", "table");
      updateSegments("schedule-version", "button", "version", state.version);
      renderBoard();
      setView("overview", true);
      notice(
        "Showing late jobs in " +
          (currentPlan() ? "the selected recovery." : "the current schedule.") +
          " Switch the schedule view to see all assignments.",
        "info",
      );
    } else {
      state.boardFilter = "all";
      renderBoard();
      setView(el.dataset.view, true);
    }
    return;
  }
  if (el.dataset.mode) {
    state.mode = el.dataset.mode;
    state.boardFilter = "all";
    updateSegments("schedule-mode", "button", "mode", state.mode);
    renderBoard();
    return;
  }
  if (el.dataset.version) {
    state.version = el.dataset.version;
    state.boardFilter = "all";
    updateSegments("schedule-version", "button", "version", state.version);
    renderBoard();
    return;
  }
  if (el.dataset.graphMode) {
    state.graphMode = el.dataset.graphMode;
    renderGraph();
    document
      .querySelector(`[data-graph-mode="${state.graphMode}"]`)
      ?.focus({ preventScroll: true });
    return;
  }
  if (el.dataset.node) {
    state.selectedNode = el.dataset.node;
    renderGraph();
    Array.from(document.querySelectorAll("[data-node]"))
      .find((n) => n.dataset.node === state.selectedNode)
      ?.focus({ preventScroll: true });
    return;
  }
  if (el.dataset.job) {
    showJob(el.dataset.job);
    return;
  }
  if (el.dataset.planProof !== undefined) {
    showPlanProof(Number(el.dataset.planProof));
    return;
  }
  if (el.dataset.taskFilter) {
    state.taskFilter = el.dataset.taskFilter;
    updateSegments("task-filters", "button", "taskFilter", state.taskFilter);
    renderMaterials();
    return;
  }
  if (el.dataset.memoryFilter) {
    state.memoryFilter = el.dataset.memoryFilter;
    updateSegments(
      "memory-filters",
      "button",
      "memoryFilter",
      state.memoryFilter,
    );
    renderHistory();
    return;
  }
  if (el.dataset.scenario) {
    if (state.busy) {
      notice(
        "Wait for the current operation to finish before switching shops.",
        "info",
      );
      return;
    }
    if (el.dataset.scenario === state.scenario) {
      $("drawer").close();
      return;
    }
    await changeShop(el.dataset.scenario);
    return;
  }
  if (el.dataset.feedback) {
    openFeedback(el.dataset.feedback);
    return;
  }
  if (el.dataset.cancelTask) {
    const id = el.dataset.cancelTask;
    confirmOperation(
      "Cancel this material request?",
      "This closes the request. Stock reservations are separate and remain reserved until you explicitly release them. Review material availability again before rescheduling.",
      "Cancel request",
      () =>
        performOperation(
          "tasks/" + encodeURIComponent(id) + "/cancel",
          {},
          "Material request cancelled. Review the remaining material needs.",
        ),
    );
    return;
  }
  if (el.dataset.reservation) {
    const id = el.dataset.reservation,
      consume = el.dataset.consume === "true";
    confirmOperation(
      consume ? "Record material consumption?" : "Release reserved stock?",
      consume
        ? "Record these units as used by the linked job. They will not return to available stock."
        : "Return these reserved units to available stock. The linked job may need another material allocation on its next recovery.",
      consume ? "Record consumption" : "Release stock",
      () =>
        performOperation(
          "reservations/" + encodeURIComponent(id) + "/close",
          { consume },
          consume
            ? "Material consumption recorded."
            : "Reserved material returned to available stock.",
        ),
    );
    return;
  }
  if (el.dataset.retry) {
    await performOperation(
      "tasks/" + encodeURIComponent(el.dataset.retry) + "/retry",
      {},
      "Request queued for another worker attempt.",
    );
    return;
  }
  const action = el.dataset.action;
  if (action === "dismiss") $("notice").hidden = true;
  if (action === "settings") openSettings();
  if (action === "scenarios") openScenarios();
  if (action === "incident") focusIncident();
  if (action === "clear-node") {
    state.selectedNode = null;
    renderGraph();
  }
  if (action === "approve") openApproval(true);
  if (action === "reject") openApproval(false);
  if (action === "reanalyse") {
    if (!pending()) {
      focusIncident();
      notice(
        "Set the affected machine and outage window for a new analysis. Updated supplier context will be included.",
        "info",
      );
      return;
    }
    const p = currentPlan();
    if (p) {
      $("machine").value = p.machine;
      $("day").value = p.day;
      $("until").value = p.until;
    }
    $("confirm-dialog").close();
    await analyse();
  }
  if (action === "memory-used") {
    state.memoryFilter = "semantic";
    updateSegments("memory-filters", "button", "memoryFilter", "semantic");
    renderHistory();
    setView("history", true);
  }
  if (action === "job-impact") {
    $("drawer").close();
    renderGraph();
    setView("investigate", true);
  }
  if (el.id === "confirm-decision")
    await applyDecision(el.dataset.approve === "true");
  if (el.id === "confirm-operation") {
    if (state.busy) return;
    const button = el;
    button.disabled = true;
    try {
      await confirmationAction?.();
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  }
});
document.addEventListener("change", (event) => {
  if (event.target.name === "selected-plan") {
    if (state.busy) {
      renderReview();
      return;
    }
    state.selected = Number(event.target.value);
    const epoch = state.epoch,
      hash = currentPlan().hash;
    threadKey()
      .then((key) => {
        if (epoch === state.epoch) localStorage.setItem(key + ":plan", hash);
      })
      .catch(fail);
    renderAll();
    document
      .querySelector(`input[name="selected-plan"][value="${state.selected}"]`)
      ?.focus({ preventScroll: true });
  }
});
document.addEventListener("submit", async (event) => {
  if (event.target.id === "feedback-form") await saveFeedback(event);
  if (event.target.id === "settings-form") {
    event.preventDefault();
    if (state.busy) return;
    await changeShop(state.scenario, $("shop-token").value.trim());
  }
});
$("incident-form").addEventListener("submit", analyse);
$("settings-open").onclick = openSettings;
$("shop-open").onclick = openSettings;
$("scenario-open").onclick = openScenarios;
$("focus-incident").onclick = focusIncident;
$("worker").onclick = async () => {
  if (state.busy) return;
  const epoch = state.epoch;
  state.busy++;
  loading($("worker"), true, "Processing requests…");
  try {
    const result = await api("procurement/run", {});
    await refresh({ force: true });
    notice(
      result.drafts.length
        ? `${result.drafts.length} request${result.drafts.length === 1 ? "" : "s"} prepared by procurement.`
        : "No pending request was available to this worker.",
    );
  } catch (e) {
    fail(e);
  } finally {
    if (epoch === state.epoch) {
      state.busy--;
      loading($("worker"), false, "Process pending requests");
    }
  }
};
$("confirm-dialog").addEventListener("cancel", (event) => {
  if (state.busy) event.preventDefault();
});
$("drawer").addEventListener("cancel", (event) => {
  if (state.busy) event.preventDefault();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !state.busy) refresh({ quiet: true });
});
setInterval(() => {
  if (
    !document.hidden &&
    !state.busy &&
    !$("drawer").open &&
    !$("confirm-dialog").open &&
    (pending() ||
      state.board?.tasks.some((t) => ["pending", "claimed"].includes(t.status)))
  )
    refresh({ quiet: true });
}, 15000);
hydrateIcons();
$("scenario-label").textContent = scenarios[state.scenario][0];
renderGraph();
renderReview();
loadShop();
