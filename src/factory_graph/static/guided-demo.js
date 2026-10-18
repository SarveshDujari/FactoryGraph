"use strict";
const guide = {
  meta: null,
  board: null,
  review: null,
  restoration: null,
  busy: false,
  error: "",
  briefing: null,
  version: 0,
  expanded: {},
};
const guideSteps = [
  "Meet the factory",
  "The breakdown",
  "The impact",
  "The recovery",
  "Your decision",
  "The repair",
  "Return to normal",
  "What we remember",
];
const guideButton = (action, label, tone = "dark") =>
  `<button class="button ${tone}" data-guide="${action}" ${guide.busy ? "disabled" : ""}>${esc(label)} ${icon("arrow")}</button>`;
const guideTime = (h) =>
  `Day ${Math.floor(h / 24)}, ${String(h % 24).padStart(2, "0")}:00`;
const guideJob = (id) => guide.board?.jobs.find((j) => j.id === id);
const guideLabel = (id) =>
  id === "J1"
    ? "Cut precision brackets"
    : id === "J2"
      ? "Inspect the brackets"
      : guideJob(id)?.name || id;
const guideMachine = (id) =>
  guide.board?.machines.find((m) => m.id === id)?.name || id;
const guidePlan = () => guide.review?.plans?.[guide.meta?.selected || 0];
const guideTask = () =>
  guide.board?.maintenance.find((t) => t.id === guide.review?.event.breakdown);
async function guideKey() {
  return (await factorySelectionKey()) + ":guided-demo";
}
async function saveGuide() {
  localStorage.setItem(await guideKey(), JSON.stringify(guide.meta));
}
async function loadGuide() {
  if (!guide.meta || guide.meta.factory !== state.factory) return;
  const version = ++guide.version,
    epoch = state.epoch;
  const board = await api("operations");
  if (version !== guide.version || epoch !== state.epoch)
    throw new ScopeChanged();
  guide.board = board;
  if (!guide.meta.reviewId) {
    const found = board.reviews.find((row) => row.kind === "breakdown");
    if (found) {
      guide.meta.reviewId = found.id;
      guide.meta.phase = 2;
    }
  }
  const read = (id) => {
    const row = board.reviews.find((r) => r.id === id);
    return row
      ? {
          ...JSON.parse(row.payload),
          status: row.status,
          selected_hash: row.selected_hash,
        }
      : null;
  };
  guide.review = read(guide.meta.reviewId);
  guide.restoration = read(guide.meta.restorationId);
  if (guide.review?.status === "applied" && guide.meta.phase < 5)
    guide.meta.phase = 5;
  if (guide.restoration?.status === "applied") guide.meta.phase = 7;
  await saveGuide();
}
async function openGuide() {
  if (guide.busy) {
    renderGuide();
    return;
  }
  try {
    const saved = JSON.parse(localStorage.getItem(await guideKey()) || "null");
    if (saved?.factory === state.factory) guide.meta = saved;
    if (guide.meta?.factory === state.factory) await loadGuide();
    else {
      guide.meta = null;
      guide.board = null;
    }
    guide.error = "";
  } catch (error) {
    if (!(error instanceof ScopeChanged)) guide.error = error.message;
  }
  renderGuide();
}
function guideChain(affected = false) {
  return `<div class="guide-chain" aria-label="Production dependency chain"><article class="${affected ? "guide-stopped" : ""}"><span>01 / MAKE THE PARTS</span><h3>${esc(guideLabel("J1"))}</h3><p>${esc(guideMachine("M1"))}${affected ? " has stopped" : " does the cutting"}.</p></article><b aria-hidden="true">→</b><article class="${affected ? "guide-waiting" : ""}"><span>02 / CHECK THE PARTS</span><h3>${esc(guideLabel("J2"))}</h3><p>${affected ? "Inspection must wait for finished parts." : "Inspection follows cutting; it cannot start first."}</p></article><b aria-hidden="true">→</b><article><span>03 / KEEP THE PROMISE</span><h3>${esc(guide.board?.orders.find((o) => o.id === "ORD1")?.customer || "Metro Components")}</h3><p>Order value ${inr(guide.board?.orders.find((o) => o.id === "ORD1")?.value_paise)}. Due at ${guideTime(guide.board?.orders.find((o) => o.id === "ORD1")?.due_hour || 14)}.</p></article></div>`;
}
function guideMetrics() {
  const p = guidePlan(),
    base = guide.review?.baseline;
  if (!p || !base) return "";
  return `<div class="guide-metrics"><div><span>If we wait for repair</span><strong>${inr(base.total_paise)}</strong><small>Feasible baseline estimate</small></div><div><span>With this recovery</span><strong>${inr(p.financial.total_paise)}</strong><small>${esc(planName(p))}</small></div><div class="guide-saving"><span>Estimated cost ${base.total_paise >= p.financial.total_paise ? "avoided" : "increase"}</span><strong>${inr(Math.abs(base.total_paise - p.financial.total_paise))}</strong><small>Under the same configured assumptions</small></div></div>`;
}
function guideAssignments(plan) {
  return `<div class="guide-assignment-list">${plan.assignments
    .map((a) => {
      const old = plan.before.find((j) => j.job === a.job);
      const worker =
        guide.board.operators.find((o) => o.id === a.operator)?.name ||
        a.operator;
      return `<article><h3>${esc(guideLabel(a.job))}</h3><div><span>Before</span><p>${esc(guideMachine(old?.machine))}<br>${old ? guideTime(old.start_hour) : "—"}</p><b aria-hidden="true">→</b><span>Proposed</span><p><strong>${esc(guideMachine(a.machine))}</strong><br>${guideTime(a.start_hour)}–${String((a.start_hour + a.hours) % 24).padStart(2, "0")}:00 · ${esc(worker)}</p></div></article>`;
    })
    .join("")}</div>`;
}
function guidePromise() {
  const before = guide.review?.baseline?.orders.find(
    (order) => order.id === "ORD1",
  );
  const after = guidePlan()?.financial.orders.find(
    (order) => order.id === "ORD1",
  );
  if (!before || !after) return "";
  return `<div class="guide-note"><strong>The customer promise, in plain language</strong><p>${esc(after.customer)} needs the order by ${guideTime(after.due_hour)}. Waiting projects a finish at ${guideTime(before.finish_hour)}. This recovery projects ${guideTime(after.finish_hour)}.</p></div>`;
}
function guideStage() {
  const phase = guide.meta.phase,
    r = guide.review,
    p = guidePlan(),
    task = guideTask();
  if (phase === 0)
    return `<div class="guide-kicker">THE NORMAL DAY</div><h2>One order. Three connected steps.</h2><p class="guide-lead">You run a small precision workshop. A customer needs brackets by 2 PM. First we make them, then we inspect them. The graph remembers that order of work.</p>${guideChain()}<div class="guide-note"><strong>Why this matters</strong><p>A free inspection bench is not enough. It still needs the parts from the previous stage.</p></div>${guideButton("next", "Show me what breaks")}`;
  if (phase === 1)
    return `<div class="guide-kicker">A REAL EVENT IN A PRACTICE FACTORY</div><h2>At 8 AM, CNC One stops.</h2><p class="guide-lead">We expect a two-hour repair. You are about to record that interruption, not approve a new production schedule.</p><div class="guide-event"><span>${icon("machine")}</span><div><h3>${esc(guideMachine("M1"))}</h3><p>Breakdown starts: <strong>Day 0, 08:00</strong><br>Estimated repair duration: <strong>2 hours</strong></p></div>${badge("SIMULATED FACTORY", "orange")}</div><div class="guide-note"><strong>What this click does</strong><p>Marks CNC One unavailable, finds dependent work and creates a proposed repair handoff. Jobs stay where they are until you approve a recovery.</p></div>${guideButton("report", "Report this breakdown")}`;
  if (!p && phase >= 2 && phase <= 4)
    return `<h2>The interruption is recorded, but no recovery is feasible.</h2><p>Use the full workspace to inspect constraints or approve maintenance alone.</p>${guideButton("workspace", "Open the full workspace")}`;
  if (phase === 2) {
    const late = r.baseline?.orders.filter((o) => o.at_risk) || [];
    return `<div class="guide-kicker">FOLLOW THE CONSEQUENCES</div><h2>The inspection bench works. The order can still be late.</h2><p class="guide-lead">${r.affected_jobs} production stages and ${r.affected_workers} linked workers are affected. This is the chain reaction FalkorDB finds.</p>${guideChain(true)}<div class="guide-note"><strong>If we wait for repair</strong><p>${late.length ? late.map((o) => `${esc(o.customer)} finishes at ${guideTime(o.finish_hour)}, ${o.late_hours} hour${o.late_hours === 1 ? "" : "s"} after its promise.`).join(" ") : "The baseline keeps the modeled customer orders within their deadlines."} The order value is not the same as the loss.</p></div><details data-guide-disclosure="paths" ${guide.expanded.paths ? "open" : ""}><summary>Show the actual graph paths</summary>${p.impact_paths
      .map(
        (path) =>
          `<p>${esc(guideMachine(r.event.machine))} → ${path
            .slice()
            .reverse()
            .map((id) => esc(guideLabel(id)))
            .join(" → ")}</p>`,
      )
      .join(
        "",
      )}</details>${guideButton("next", "Show a better way to recover")}`;
  }
  if (phase === 3)
    return `<div class="guide-kicker">THE ALTERNATIVE</div><h2>Move the work. Keep the customer promise.</h2><p class="guide-lead">The planner checks another suitable machine, a qualified worker and available time. These are proposed bookings, not changes already applied.</p>${guideMetrics()}${guidePromise()}${guideAssignments(p)}<div class="guide-note"><strong>What changed?</strong><p>${p.financial.orders.filter((o) => o.at_risk).length} orders are projected late in this option. Moving work may add a changeover cost, but can reduce waiting and delay penalties. The broken machine still has downtime cost.</p></div><details data-guide-disclosure="cost" ${guide.expanded.cost ? "open" : ""}><summary>Compare the cost components</summary><div class="table-wrap"><table class="data-table"><thead><tr><th>Component</th><th>Wait</th><th>Recovery</th></tr></thead><tbody>${Object.entries(
      componentNames,
    )
      .map(
        ([key, label]) =>
          `<tr><td>${label}</td><td>${r.baseline ? inr(r.baseline.components[key]) : "—"}</td><td>${inr(p.financial.components[key])}</td></tr>`,
      )
      .join(
        "",
      )}</tbody></table></div></details><details data-guide-disclosure="ai" ${guide.expanded.ai ? "open" : ""}><summary>Optional: what does Gemma add?</summary><p>Gemma highlights calculated facts. It does not make these bookings or invent the numbers.</p>${guideButton("briefing", "Explain this option with Gemma", "secondary")}${guide.briefing ? `<div class="guide-ai">${badge(guide.briefing.provider === "ollama" ? "GEMMA-SELECTED FACTS" : "RULE-BASED FALLBACK")}<p>${esc(guide.briefing.summary)}</p>${guide.briefing.notice ? `<p>${esc(guide.briefing.notice)}</p>` : ""}<ul>${[...guide.briefing.tradeoffs, ...guide.briefing.checks].map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}</details>${guideButton("next", "Review my decision")}`;
  if (phase === 4)
    return `<div class="guide-kicker">YOU ARE IN CONTROL</div><h2>A recommendation is not permission.</h2><p class="guide-lead">Review the exact assignments below. Approving applies these production changes and approves the internal maintenance handoff. No purchase or physical machine command is sent.</p>${guideAssignments(p)}<div class="guide-note"><strong>Your decision</strong><p>Selected estimate: ${inr(p.financial.total_paise)}. ${p.financial.orders.filter((o) => o.at_risk).length} orders projected late. ${r.revision !== guide.board.revision ? "Context changed; reanalyse before approving." : "The server checks this proposal again when you approve."}</p></div>${r.revision !== guide.board.revision ? guideButton("reanalyse", "Reanalyse the changed factory") : guideButton("approve", "Approve this recovery")}`;
  if (phase === 5)
    return task?.status === "completed"
      ? `<div class="guide-kicker">REPAIR OUTCOME RECORDED</div><h2>The machine is available. Jobs have not moved back.</h2><p class="guide-lead">Actual completion: ${guideTime(task.actual_end_hour)}. Reported repair cost: ${inr(task.actual_cost_paise)}. The real outcome is stored separately from the earlier estimate.</p><div class="guide-note"><strong>Why another review?</strong><p>Automatically moving jobs back could create new conflicts. We calculate a new plan first.</p></div>${guideButton("restore", "Review return to normal")}`
      : `<div class="guide-kicker">PRODUCTION RECOVERED / REPAIR STILL OPEN</div><h2>The approved plan is saved. Now record what actually happened.</h2><p class="guide-lead">The technician took three hours rather than the estimated two. Enter the observed outcome so the next estimate has evidence to use.</p><form id="guide-repair-form" class="guide-repair"><label>Actual completion hour<input id="guide-repair-hour" type="number" min="${Math.max(guide.board.hour_now, (task?.repair_start_hour || 8) + 1)}" max="${guide.board.policy.horizon_days * 24}" value="11" step="1" required></label><label>Reported repair cost (₹)<input id="guide-repair-cost" type="number" value="3000" min="0" max="10000000" step="0.01" required></label><label>What was repaired?<textarea id="guide-repair-notes" maxlength="1000" required>Bearing replaced; repair completed in three hours.</textarea></label><p>Hour 11 means day 0 at 11 AM. Recording this makes the machine available; it does not automatically change job assignments.</p><button class="button dark" ${guide.busy ? "disabled" : ""}>Record this repair outcome ${icon("arrow")}</button></form>`;
  if (phase === 6) {
    const restoration = guide.restoration?.plans[0];
    if (!restoration)
      return `<h2>No return-to-normal plan is available.</h2>${guideButton("workspace", "Inspect the full workspace")}`;
    return `<div class="guide-kicker">POST-REPAIR REVIEW</div><h2>Return to normal, with another decision.</h2><p class="guide-lead">CNC One is available again. This proposal concerns the remaining work. Historical breakdown and repair costs are excluded; a zero here does not erase the original cost.</p>${guideAssignments(restoration)}<div class="guide-note"><strong>A separate approval</strong><p>The original recovery and repair remain in history. This click applies only the reviewed post-repair assignments.</p></div>${guideButton("approve-restore", "Approve return to normal")}`;
  }
  return `<div class="guide-kicker">THE NEXT INCIDENT STARTS WITH MORE CONTEXT</div><h2>This factory remembers the repair.</h2><p class="guide-lead">You followed a breakdown from production dependencies to a recovery decision, recorded the actual repair, and reviewed the return to normal.</p><div class="guide-memory">${guide.board.repair_memory
    .filter((m) => m.machine === "M1")
    .map(
      (m) =>
        `<article><span>OBSERVED OUTCOME</span><h3>${m.observed_repair_hours}-hour repair</h3><p>Reported cost: ${inr(m.actual_repair_cost_paise)}.</p><details><summary>Where did this come from?</summary><p>${esc(m.source)}</p></details></article>`,
    )
    .join(
      "",
    )}</div><div class="guide-note"><strong>What memory means here</strong><p>A later estimate can consider this sourced duration and cost. This is saved evidence influencing planning, not Gemma retraining itself.</p></div><details><summary>Where do Materials fit into this story?</summary><p>This example has enough material, so no request is needed. In a shortage scenario, Materials tracks internal supply requests, expected deliveries and actual receipts. Memory can then retain the observed supplier lead time.</p></details><div class="guide-closing"><span>FalkorDB connects the factory.</span><span>Deterministic rules calculate the options.</span><span>Gemma highlights facts.</span><span>You approve the changes.</span></div>${guideButton("workspace", "Explore this factory in the full app")} ${guideButton("fresh", "Start a fresh practice run", "secondary")}`;
}
function renderGuide() {
  const root = $("guided-demo-root");
  if (!root) return;
  if (!guide.meta || guide.meta.factory !== state.factory) {
    root.innerHTML = `<div class="guide-shell"><div class="guide-top"><span class="eyebrow">FACTORYGRAPH / GUIDED DEMO</span><button class="text-button" data-view="overview">Back to workspace ${icon("arrow")}</button></div><div class="guide-welcome"><span class="guide-kicker">ONE FACTORY. ONE BREAKDOWN. A CLEAR NEXT MOVE.</span><h2>Understand the story<br>before the numbers.</h2><p>Follow a customer order from cutting to inspection. Break a machine, compare the consequences, approve a recovery and see what the factory remembers.</p><div class="guide-preview"><span>01 / See the factory</span><span>02 / Follow the impact</span><span>03 / Make the decision</span></div>${guideButton("fresh", guide.busy ? "Preparing your practice factory…" : "Begin the guided demo")}<p class="field-help">Creates a separate synthetic factory. Your existing workspaces stay available. Nothing controls physical equipment.</p></div>${guide.error ? `<p class="field-error" role="alert">${esc(guide.error)}</p>` : ""}</div>`;
    return;
  }
  if (!guide.board) {
    root.innerHTML =
      '<div class="guide-shell"><p role="status">Loading your practice factory…</p></div>';
    return;
  }
  root.innerHTML = `<div class="guide-shell"><div class="guide-top"><span class="eyebrow">GUIDED DEMO / SYNTHETIC FACTORY</span><button class="text-button" data-guide="workspace">Exit to workspace ${icon("arrow")}</button></div><ol class="guide-progress" aria-label="Demo progress">${guideSteps.map((label, i) => `<li class="${i === guide.meta.phase ? "current" : i < guide.meta.phase ? "done" : ""}" ${i === guide.meta.phase ? 'aria-current="step"' : ""}><span>${i < guide.meta.phase ? "✓" : i + 1}</span>${label}</li>`).join("")}</ol><article class="guide-stage" aria-live="polite"><span class="guide-step-count">STEP ${guide.meta.phase + 1} OF ${guideSteps.length}</span>${guideStage()}${guide.busy ? '<p class="guide-busy" role="status">Working on this step…</p>' : ""}${guide.error ? `<p class="field-error" role="alert">${esc(guide.error)}</p>` : ""}</article><p class="guide-footer">${esc(guide.board.name)} · Every change belongs to this practice workspace. Estimates use its configured demo rates.</p></div>`;
}

async function runGuide(work, { switchesScope = false } = {}) {
  if (guide.busy || state.busy) return;
  const epoch = state.epoch;
  guide.busy = true;
  guide.error = "";
  if (!switchesScope) state.busy++;
  renderGuide();
  try {
    await work();
  } catch (error) {
    if (!(error instanceof ScopeChanged)) {
      guide.error = error.message;
      if ($("confirm-dialog").open && $("operation-error")) {
        $("operation-error").textContent = error.message;
        $("operation-error").hidden = false;
      }
    }
  } finally {
    if (!switchesScope && epoch === state.epoch)
      state.busy = Math.max(0, state.busy - 1);
    guide.busy = false;
    renderGuide();
  }
}
async function beginGuide() {
  await runGuide(
    async () => {
      const specification = await api("factories/example");
      specification.name =
        "Guided demo — " +
        new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
      specification.policy.source =
        "Synthetic guided-demo rates; illustrative factory assumptions";
      const created = await api("factories", specification);
      guide.meta = {
        factory: created.id,
        phase: 0,
        selected: 0,
        reviewId: null,
        restorationId: null,
      };
      guide.board = null;
      guide.review = null;
      guide.restoration = null;
      guide.briefing = null;
      guide.expanded = {};
      await switchFactory(created.id);
      await loadGuide();
      setView("demo", true);
    },
    { switchesScope: true },
  );
}
async function guideReviewApplied(restoration = false) {
  const review = restoration ? guide.restoration : guide.review;
  const plan = restoration ? review?.plans[0] : guidePlan();
  if (!plan || review.status !== "pending") return;
  confirmOperation(
    restoration
      ? "Apply the post-repair plan?"
      : "Apply this practice recovery?",
    `${plan.assignments.length} production stages will receive the reviewed assignments. Estimated cost: ${inr(plan.financial.total_paise)}. ${restoration ? "Historical costs are excluded from this comparison." : "The maintenance handoff will also be approved."} This changes only this practice factory's saved model.`,
    "Approve & apply",
    () =>
      runGuide(async () => {
        await api("operations/reviews/" + review.id + "/decision", {
          hash: plan.hash,
          approve: true,
        });
        $("confirm-dialog").close();
        guide.meta.phase = restoration ? 7 : 5;
        await saveGuide();
        await loadGuide();
        await refresh({ force: true });
      }),
  );
}
document.addEventListener("factory:demo", () => openGuide());
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-guide]");
  if (!button || guide.busy || state.busy) return;
  const action = button.dataset.guide;
  if (action === "fresh") {
    await beginGuide();
    return;
  }
  if (action === "workspace") {
    setView("operations", true);
    return;
  }
  if (!guide.meta || guide.meta.factory !== state.factory) return;
  if (action === "next" && [0, 2, 3].includes(guide.meta.phase)) {
    guide.meta.phase++;
    await saveGuide();
    renderGuide();
    $("view-demo").scrollIntoView({ block: "start" });
    return;
  }
  if (action === "approve") {
    await guideReviewApplied();
    return;
  }
  if (action === "approve-restore") {
    await guideReviewApplied(true);
    return;
  }
  await runGuide(async () => {
    if (action === "report") {
      const review = await api("operations/analyse", {
        machine: "M1",
        start_hour: 8,
        duration_hours: 2,
        use_memory: true,
      });
      guide.meta.reviewId = review.id;
      guide.meta.phase = 2;
    }
    if (action === "reanalyse") {
      const review = await api(
        "operations/" + guide.review.event.breakdown + "/reanalyse",
        { restoration: false },
      );
      guide.meta.reviewId = review.id;
      guide.meta.phase = 3;
    }
    if (action === "restore") {
      const review = await api(
        "operations/" + guide.review.event.breakdown + "/reanalyse",
        { restoration: true },
      );
      guide.meta.restorationId = review.id;
      guide.meta.phase = 6;
    }
    if (action === "briefing") {
      const plan = guidePlan();
      guide.briefing = await api(
        "operations/reviews/" + guide.review.id + "/briefing",
        { hash: plan.hash },
      );
    }
    await saveGuide();
    await loadGuide();
    await refresh({ force: true });
  });
});
document.addEventListener("submit", async (event) => {
  if (event.target.id !== "guide-repair-form") return;
  event.preventDefault();
  const form = event.target;
  if (guide.busy || state.busy || !form.reportValidity()) return;
  const actual = Number($("guide-repair-hour").value),
    cost = Math.round(Number($("guide-repair-cost").value) * 100),
    notes = $("guide-repair-notes").value;
  if (!Number.isInteger(actual) || !Number.isSafeInteger(cost)) return;
  await runGuide(async () => {
    await api("maintenance/" + guide.review.event.breakdown + "/complete", {
      actual_end_hour: actual,
      actual_cost_paise: cost,
      notes,
    });
    await loadGuide();
    await refresh({ force: true });
  });
});

document.addEventListener(
  "toggle",
  (event) => {
    if (event.target.isConnected && event.target.dataset.guideDisclosure)
      guide.expanded[event.target.dataset.guideDisclosure] = event.target.open;
  },
  true,
);
