"use strict";
const opsState = {
  board: null,
  review: null,
  selected: 0,
  loading: false,
  version: 0,
  busy: false,
  factories: [],
  draft: null,
  step: 0,
  briefings: {},
  briefingBusy: false,
};
const inr = (paise) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format((paise || 0) / 100);
const hourLabel = (h) =>
  `D${Math.floor(h / 24)} ${String(h % 24).padStart(2, "0")}:00`;
const opsPlan = () => opsState.review?.plans?.[opsState.selected];
const opsStale = () => opsState.review?.revision !== opsState.board?.revision;
const componentNames = {
  equipment_downtime_paise: "Equipment downtime",
  idle_labour_paise: "Delay-related idle labour",
  overtime_premium_paise: "Overtime premium",
  changeover_paise: "Machine changeovers",
  estimated_repair_paise: "Estimated repair labour",
  delay_penalties_paise: "Order delay penalties",
};
async function opsStorageKey() {
  return (await threadKey()) + ":hourly";
}
async function loadOperations(force = false) {
  if (opsState.loading && !force) return;
  const version = ++opsState.version,
    epoch = state.epoch;
  opsState.loading = true;
  if (!opsState.board)
    $("operations-root").innerHTML =
      '<div class="skeleton ops-loading" aria-label="Loading hourly factory context"></div>';
  try {
    const [board, listing] = await Promise.all([
      api("operations"),
      api("factories"),
    ]);
    if (version !== opsState.version || epoch !== state.epoch) return;
    const storageKey = await opsStorageKey();
    if (version !== opsState.version || epoch !== state.epoch) return;
    const previous = opsState.review?.id || localStorage.getItem(storageKey);
    const row =
      board.reviews.find((r) => r.id === previous) ||
      board.reviews.find(
        (r) => r.status === "pending" && r.revision === board.revision,
      ) ||
      board.reviews[0];
    opsState.board = board;
    opsState.factories = listing.factories;
    opsState.review = row
      ? {
          ...JSON.parse(row.payload),
          status: row.status,
          selected_hash: row.selected_hash,
        }
      : null;
    if (opsState.review) {
      const chosen =
        row.status === "applied"
          ? row.selected_hash
          : localStorage.getItem(storageKey + ":plan");
      const idx = opsState.review.plans.findIndex(
        (p) => p.hash === (chosen || row.selected_hash),
      );
      opsState.selected = idx >= 0 ? idx : 0;
    }
    syncHourlySummary();
    renderOperations();
    await refresh({ force: true });
  } catch (error) {
    if (!(error instanceof ScopeChanged)) {
      $("operations-root").innerHTML = empty(
        "Hourly context is unavailable.",
        error.message,
        "settings",
        "Open connection settings",
        "alert",
      );
      fail(error);
    }
  } finally {
    if (version === opsState.version) opsState.loading = false;
  }
}
function syncHourlySummary() {
  state.hourlyReview = opsState.review
    ? { ...opsState.review, selected: opsState.selected }
    : null;
  const current = opsPlan();
  let evidence = current?.kind === "breakdown" ? current : null;
  if (!evidence && opsState.board) {
    const row = opsState.board.reviews.find(
      (row) =>
        row.kind === "breakdown" &&
        (!opsState.review || row.breakdown === opsState.review.event.breakdown),
    );
    if (row) {
      const snapshot = JSON.parse(row.payload);
      evidence =
        snapshot.plans.find((plan) => plan.hash === row.selected_hash) ||
        snapshot.plans[0];
    }
  }
  state.hourlyImpact = evidence
    ? {
        machine: evidence.event.machine,
        impact_paths: evidence.impact_paths.map((path) => {
          const job = state.board?.jobs.find((job) => job.id === path[0]);
          const order = evidence.financial.orders.find(
            (order) => order.id === job?.order_id,
          );
          return [path, order?.id, order?.customer];
        }),
        impact: evidence.affected.map((id) => [evidence.event.machine, id]),
        tool_trace: [],
      }
    : null;
  if (state.board) {
    renderKpis();
    renderOverview();
    renderGraph();
  }
}
async function adoptReview(review) {
  opsState.review = review;
  opsState.selected = 0;
  const key = await opsStorageKey();
  localStorage.setItem(key, review.id);
  if (review.plans[0])
    localStorage.setItem(key + ":plan", review.plans[0].hash);
  await loadOperations(true);
}
function renderOperations() {
  const b = opsState.board;
  if (!b) return;
  const review = opsState.review,
    plan = opsPlan(),
    finance = plan?.financial,
    baseline = review?.baseline;
  const saving =
    finance && baseline ? baseline.total_paise - finance.total_paise : null;
  const machineOptions = b.machines
    .map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`)
    .join("");
  const firstJob = b.jobs
    .filter((j) => j.start_hour >= b.hour_now)
    .sort((a, c) => a.start_hour - c.start_hour)[0];
  const defaultHour = firstJob?.start_hour ?? Math.max(8, b.hour_now);
  $("operations-root").innerHTML =
    `<div class="section-title"><div><div class="eyebrow">HOURLY CONTEXT / ESTIMATED FINANCIAL EXPOSURE</div><h2>Every hour has a cost.</h2><p>Model the interruption. Compare the trade-offs. Record the repair.</p></div><button class="button dark" data-ops="setup">Configure a factory ${icon("arrow")}</button></div><div class="ops-factory-bar"><div><strong>${esc(b.name)}</strong><span>Reference clock ${hourLabel(b.hour_now)} · INR estimates · ${b.policy.shift_hours}h regular shift from ${String(b.policy.shift_start).padStart(2, "0")}:00</span></div><label>Factory workspace<select id="ops-factory">${opsState.factories.map((f) => `<option value="${esc(f.id)}" ${f.id === state.factory ? "selected" : ""}>${esc(f.name)}</option>`).join("")}</select></label>${
      b.reviews.length > 1
        ? `<label>Review record<select id="ops-review-record">${b.reviews
            .map((row) => {
              const saved = JSON.parse(row.payload);
              return `<option value="${esc(row.id)}" ${row.id === review?.id ? "selected" : ""}>${saved.event.kind === "restoration" ? "Post-repair" : "Breakdown"} / ${esc(saved.event.machine)} / ${hourLabel(saved.event.start_hour)} / ${esc(row.status)}</option>`;
            })
            .join("")}</select></label>`
        : ""
    }</div><div class="ops-top-grid"><section class="ops-report"><div class="eyebrow">REPORT OR SIMULATE A BREAKDOWN</div><h3>A short stop. A clear response.</h3><form id="ops-report-form" novalidate><label for="ops-machine">Machine</label><select id="ops-machine">${machineOptions}</select><div class="ops-form-row"><label>Start hour<input name="start_hour" id="ops-start" type="number" value="${defaultHour}" min="${b.hour_now}" max="719" step="1" required></label><label>Estimated repair hours<input name="duration_hours" id="ops-duration" type="number" value="2" min="1" max="24" step="1" required></label></div><p class="field-help" id="ops-time-help">Hour ${defaultHour} = ${hourLabel(defaultHour)}. Day 0 is the demo reference day.</p><label class="ops-check"><input name="use_memory" type="checkbox" checked> Consider recorded repair-duration observations</label><p class="field-error" id="ops-report-error" role="alert" hidden></p><button class="button dark full" id="ops-report-submit">Calculate impact & options ${icon("arrow")}</button></form><p class="field-help">Recording a breakdown marks this machine unavailable in the model. A proposed repair task is created; production changes still require approval.</p></section><div class="ops-summary-grid"><article class="ops-stat"><span>${review?.event?.kind === "restoration" ? "Keep-current estimate" : "Wait-for-repair estimate"}</span><strong>${baseline ? inr(baseline.total_paise) : "—"}</strong><small>${baseline ? "Feasible constrained baseline" : "Run a report to calculate a baseline"}</small></article><article class="ops-stat orange-tint"><span>Selected recovery estimate</span><strong>${finance ? inr(finance.total_paise) : "—"}</strong><small>${plan ? esc(planName(plan)) : "No recovery selected"}</small></article><article class="ops-stat"><span>${saving !== null && saving < 0 ? "Additional estimated cost" : "Estimated cost avoided"}</span><strong>${saving !== null ? inr(Math.abs(saving)) : "—"}</strong><small>Same assumptions; not guaranteed savings</small></article><article class="ops-stat"><span>Reported outage impact</span><strong>${review ? review.affected_jobs : "—"} <em>jobs</em></strong><small>${review ? review.affected_workers + " production workers linked to affected jobs" : "Multi-hop graph investigation"}</small></article></div></div>${renderHourlyReview()}${renderOrderRisk()}<div class="section-title smaller"><div><div class="eyebrow">MAINTENANCE HANDOFF / HUMAN APPROVAL</div><h2>Repair is part of the recovery.</h2></div><span class="badge">NO PHYSICAL MACHINE CONTROL</span></div><div class="ops-maintenance">${b.maintenance.length ? b.maintenance.map((t) => `<article class="task-card"><div class="task-card-top">${badge(t.status, t.status === "completed" ? "green" : t.status === "proposed" ? "orange" : "dark")}<span>${esc(t.technician)}</span></div><h3>${esc(b.machines.find((m) => m.id === t.machine)?.name || t.machine)}</h3><p>Repair reservation ${hourLabel(t.repair_start_hour)} → ${hourLabel(t.expected_end_hour)}.${t.status === "proposed" ? " Awaiting dispatch approval." : ""}</p>${t.status === "completed" ? `<dl class="task-details"><div><dt>Recorded completion</dt><dd>${hourLabel(t.actual_end_hour)}</dd></div><div><dt>Reported repair cost</dt><dd>${inr(t.actual_cost_paise)}</dd></div></dl><button class="button secondary" data-ops="restore" data-id="${t.id}">Review return to normal</button>` : `<div class="task-card-actions">${t.status === "proposed" ? `<button class="button dark" data-ops="assign" data-id="${t.id}">Approve maintenance only</button>` : `<button class="button dark" data-ops="repair" data-id="${t.id}">Record repair completion</button>`}<button class="text-button" data-ops="reanalyse" data-id="${t.id}">Reopen recovery analysis</button></div>`}</article>`).join("") : empty("No repair handoffs yet.", "A reported breakdown creates a proposed maintenance task. Approving a recovery dispatches it, or you can approve maintenance alone.", "", "", "machine")}</div><div class="ops-bottom-grid"><section class="panel"><div class="eyebrow">SIMULATED SIGNALS / THRESHOLD CHECKS ONLY</div><h2>Inspect before you infer.</h2><p class="form-description">Generate a clearly labelled sample reading. A threshold breach suggests inspection; it does not predict failure or automatically stop a machine.</p><label for="ops-health-machine" class="form-label">Machine</label><select id="ops-health-machine" class="ops-select">${machineOptions}</select><div class="ops-inline-actions"><button class="button secondary" data-ops="health-normal">Normal sample</button><button class="button secondary" data-ops="health-hot">Elevated sample</button></div><div class="ops-health-feed">${
      b.health
        .slice(0, 4)
        .map(
          (r) =>
            `<div class="ops-health-row">${badge("SIMULATED", r.status === "inspection_advised" ? "orange" : "green")}<strong>${esc(r.machine)} / ${r.temperature}°C · vibration ${r.vibration} · load ${r.load}%</strong><span>${r.breaches ? "Inspect: " + esc(r.breaches) : "Within configured thresholds"} · ${hourLabel(r.hour)}</span></div>`,
        )
        .join("") || '<p class="empty-copy">No simulated readings recorded.</p>'
    }</div></section><section class="panel"><div class="eyebrow">REPAIR EXPERIENCE / SHARED GRAPH MEMORY</div><h2>The next estimate remembers.</h2><p class="form-description">Valid observations can conservatively extend a repair estimate and raise its labour-rate assumption. Every observation keeps its source.</p>${b.repair_memory.map((m) => `<div class="ops-repair-memory"><strong>${esc(m.machine)} · ${m.observed_repair_hours}h recorded repair</strong><p>Reported repair cost ${inr(m.actual_repair_cost_paise)}.</p><details><summary>Source</summary><span class="exact-hash">${esc(m.source)}</span></details></div>`).join("") || '<p class="empty-copy">Complete a repair to create the first observation.</p>'}</section></div>`;
  if (firstJob) $("ops-machine").value = firstJob.machine;
}
function renderBriefing(plan) {
  const briefing = opsState.briefings[plan.hash];
  return `<section class="panel ops-ai-briefing"><div class="eyebrow">GEMMA 3 4B / READ-ONLY RECOVERY EXPLAINER</div><h3>A plain-language second look.</h3><p class="field-help">Gemma selects the key points from calculated factory facts. It cannot write new costs or change assignments.</p><button class="button secondary" data-ops="briefing" ${opsState.briefingBusy ? "disabled" : ""}>${opsState.briefingBusy ? "Gemma is preparing the briefing…" : briefing ? "Regenerate explanation" : "Generate explanation"}</button>${briefing ? `<div role="status"><p>${badge(briefing.provider === "ollama" ? "GEMMA-SELECTED / GROUNDED IN FACTORY FACTS" : "RULE-BASED FALLBACK", briefing.provider === "ollama" ? "dark" : "orange")}</p>${briefing.notice ? `<p class="field-help">${esc(briefing.notice)}</p>` : ""}<p>${esc(briefing.summary)}</p>${briefing.grounding ? `<p class="field-help">${esc(briefing.grounding)}</p>` : ""}<h4>Trade-offs</h4><ul>${briefing.tradeoffs.map((item) => `<li>${esc(item)}</li>`).join("")}</ul><h4>Before you approve</h4><ul>${briefing.checks.map((item) => `<li>${esc(item)}</li>`).join("")}</ul><p class="field-help">Saved proposal context, revision ${briefing.revision}. This explanation cannot change or approve production.</p></div>` : ""}</section>`;
}
function renderHourlyReview() {
  const r = opsState.review,
    p = opsPlan();
  if (!r)
    return '<div class="ops-empty-banner">Your cost comparison, order exposure and maintenance handoff appear after a breakdown report.</div>';
  const stale = r.status === "pending" && opsStale();
  return `<section class="ops-review"><div class="section-title smaller"><div><div class="eyebrow">${r.event.kind === "restoration" ? "POST-REPAIR REBALANCING" : "GRAPH-DERIVED RECOVERY OPTIONS"}</div><h2>${r.event.kind === "restoration" ? "Return to normal, deliberately." : "The numbers behind the next move."}</h2><p>${esc(r.event.machine)} · ${hourLabel(r.event.start_hour)}${r.event.end_hour !== r.event.start_hour ? " → " + hourLabel(r.event.end_hour) : ""}. ${r.event.repair_memory_used ? "A recorded repair duration extended this estimate." : ""}</p></div>${badge(stale ? "CONTEXT CHANGED" : r.status, stale ? "orange" : r.status === "applied" ? "green" : "dark")}</div>${r.errors.length ? `<details class="technical"><summary>Feasibility notes (${r.errors.length})</summary><p>${r.errors.map(esc).join("<br>")}</p></details>` : ""}${
    r.plans.length
      ? `<div class="ops-plan-select">${r.plans.map((plan, i) => `<label class="ops-choice ${i === opsState.selected ? "selected" : ""}"><input type="radio" name="hourly-plan" value="${i}" ${r.status === "applied" ? "disabled" : ""} ${i === opsState.selected ? "checked" : ""}> <span><strong>${esc(planName(plan))}</strong><small>${inr(plan.financial.total_paise)} · ${plan.financial.overtime_hours}h overtime</small></span></label>`).join("")}</div><div class="table-wrap panel ops-cost-table"><table class="data-table"><thead><tr><th>Estimate component</th><th>${r.event.kind === "restoration" ? "Keep current schedule" : "Wait for repair"}</th><th>Selected recovery</th></tr></thead><tbody>${Object.entries(
          componentNames,
        )
          .map(
            ([key, label]) =>
              `<tr><td>${label}</td><td>${r.baseline ? inr(r.baseline.components[key]) : "No feasible baseline"}</td><td>${inr(p.financial.components[key])}</td></tr>`,
          )
          .join(
            "",
          )}<tr class="ops-total"><td><strong>Estimated total</strong></td><td>${r.baseline ? inr(r.baseline.total_paise) : "—"}</td><td>${inr(p.financial.total_paise)}</td></tr></tbody></table></div>${r.event.kind === "breakdown" ? `<details class="technical"><summary>Inspect graph dependency paths</summary><p class="field-help">Failure at ${esc(r.event.machine)} reaches these stages through recorded production dependencies.</p>${p.impact_paths.map((path) => `<p>${esc(r.event.machine)} &rarr; ${path.slice().reverse().map(esc).join(" &rarr; ")}</p>`).join("")}</details>` : ""}<div class="ops-schedule-changes">${p.assignments.map((a) => `<div><strong>${esc(a.job)} / ${esc(a.machine)} / ${esc(a.operator)}</strong><span>${hourLabel(a.start_hour)} → ${hourLabel(a.start_hour + a.hours)}</span></div>`).join("")}</div>${renderBriefing(p)}<div class="ops-review-actions">${r.status === "pending" ? (stale ? `<button class="button dark" data-ops="${r.event.kind === "restoration" ? "restore" : "reanalyse"}" data-id="${r.event.breakdown}">Reanalyse changed context</button>` : `<button class="button secondary" data-ops="reject">Reject selected proposal</button><button class="button primary" data-ops="approve">Review costs & approve ${icon("arrow")}</button>`) : '<span class="badge">REVIEW ' + esc(r.status.toUpperCase()) + "</span>"}<button class="text-button underlined" data-ops="assumptions">Inspect assumptions & source rates</button></div><p class="field-help">${p.financial.equivalent_cost_per_outage_hour_paise !== null ? "Equivalent cost per outage hour: " + inr(p.financial.equivalent_cost_per_outage_hour_paise) + ". This spreads the total estimate over the outage duration; it is not a live loss rate." : "Historical downtime and repair costs are excluded from restoration comparisons."}</p>`
      : empty(
          "No feasible production recovery yet.",
          "Inspect feasibility notes. You can still approve the maintenance task below and reanalyse after repair.",
          "",
          "",
          "alert",
        )
  }</section>`;
}
function renderOrderRisk() {
  const p = opsPlan();
  if (!p) return "";
  return `<section class="panel ops-order-panel"><div class="panel-head"><div><div class="eyebrow">CUSTOMER COMMITMENTS / SELECTED RECOVERY</div><h2>Orders behind the exposure</h2></div>${badge("ORDER VALUE ≠ ESTIMATED LOSS")}</div><div class="table-wrap"><table class="data-table mobile-cards"><thead><tr><th>Order / customer</th><th>Order value</th><th>Promise / projected finish</th><th>Late hours</th><th>Estimated penalty</th></tr></thead><tbody>${p.financial.orders.map((o) => `<tr><td data-label="Order"><strong>${esc(o.id)} / ${esc(o.customer)}</strong>${badge(o.at_risk ? "At risk" : "Within promise", o.at_risk ? "orange" : "green")}</td><td data-label="Order value">${inr(o.value_paise)}</td><td data-label="Promise / finish">${hourLabel(o.due_hour)} / ${hourLabel(o.finish_hour)}</td><td data-label="Late hours">${o.late_hours}</td><td data-label="Estimated penalty">${inr(o.penalty_paise)}</td></tr>`).join("")}</tbody></table></div><p class="field-help">Value of orders projected late: ${inr(p.financial.order_value_at_risk_paise)}. This value is not added to the estimated cost.</p></section>`;
}
async function opsMutation(path, body, message, reviewResult = false) {
  if (state.busy) return;
  const epoch = state.epoch;
  state.busy++;
  opsState.busy = true;
  try {
    const result = await api(path, body);
    if ($("confirm-dialog").open) $("confirm-dialog").close();
    if ($("drawer").open) $("drawer").close();
    if (reviewResult) await adoptReview(result);
    else await loadOperations(true);
    notice(message);
    return result;
  } catch (error) {
    if (!(error instanceof ScopeChanged)) {
      const el = $("confirm-dialog").open
        ? $("operation-error")
        : $("drawer").open
          ? $("ops-form-error")
          : null;
      if (el) {
        el.textContent = error.message;
        el.hidden = false;
      } else fail(error);
    }
  } finally {
    if (epoch === state.epoch) {
      state.busy--;
      opsState.busy = false;
    }
  }
}
function showCostAssumptions() {
  const p = opsPlan(),
    b = opsState.board;
  if (!p) return;
  openDrawer(
    "Where the estimate comes from",
    "DETERMINISTIC RULES / SOURCE ASSUMPTIONS",
    `<p class="form-description">These are configured planning assumptions, not numbers invented by a model.</p>${p.financial.assumptions.map((a) => `<p class="ops-assumption">${esc(a)}</p>`).join("")}<div class="plan-section"><h4>Rates used by this review</h4><div class="inspector-row"><span>Overtime premium / worker-hour</span><strong>${inr(p.policy.overtime_paise_per_hour)}</strong></div><div class="inspector-row"><span>Changeover / moved job</span><strong>${inr(p.policy.changeover_paise)}</strong></div><div class="inspector-row"><span>Repair labour / hour</span><strong>${inr(p.policy.repair_paise_per_hour)}</strong></div>${b.machines.map((m) => `<div class="inspector-row"><span>${esc(m.name)} equipment / productive hour</span><strong>${inr(m.downtime_paise_per_hour)}</strong></div>`).join("")}${b.operators.map((w) => `<div class="inspector-row"><span>${esc(w.name)} idle / hour</span><strong>${inr(w.idle_paise_per_hour)}</strong></div>`).join("")}</div><details class="technical"><summary>Exact reviewed proposal</summary><div class="exact-hash">${esc(p.hash)}</div></details>`,
  );
}
function repairForm(id) {
  const task = opsState.board.maintenance.find((t) => t.id === id);
  if (!task) return;
  openDrawer(
    "Record repair completion",
    "OBSERVED OUTCOME / MACHINE AVAILABILITY",
    `<p class="form-description">Record what actually happened. This makes the machine available in the model and stores a repair observation. Jobs are not automatically moved back.</p><form id="ops-repair-form" data-id="${id}" novalidate><label for="ops-repair-hour">Actual completion hour</label><input id="ops-repair-hour" type="number" min="${Math.max(task.repair_start_hour + 1, opsState.board.hour_now)}" max="${opsState.board.policy.horizon_days * 24}" value="${Math.max(task.expected_end_hour, opsState.board.hour_now)}" required><p class="field-help">Expected finish: ${hourLabel(task.expected_end_hour)}. Hour 10 means D0 10:00.</p><label for="ops-repair-cost">Reported repair cost (₹)</label><input id="ops-repair-cost" type="number" min="0" step="0.01" max="10000000" value="${((task.expected_end_hour - task.repair_start_hour) * opsState.board.policy.repair_paise_per_hour) / 100}" required><label for="ops-repair-notes">Repair notes</label><textarea id="ops-repair-notes" maxlength="1000" rows="4" placeholder="What was repaired? Any delay or follow-up?"></textarea><p class="field-error" id="ops-form-error" role="alert" hidden></p><button class="button dark full" id="ops-repair-submit">Record outcome ${icon("arrow")}</button></form>`,
  );
}
async function openFactoryWizard() {
  if (state.busy) return;
  try {
    opsState.draft = await api("factories/example");
    opsState.step = 0;
    renderWizard();
    $("factory-wizard").showModal();
  } catch (e) {
    fail(e);
  }
}
function wizardInput(label, path, type = "text", opts = {}) {
  const value = path.split(".").reduce((o, k) => o?.[k], opsState.draft);
  const shown = opts.money
    ? (value / 100).toFixed(2)
    : Array.isArray(value)
      ? value.join(", ")
      : value;
  return `<label class="wizard-field">${esc(label)}<input data-path="${path}" ${opts.money ? 'data-money="true"' : ""} ${opts.array ? 'data-array="true"' : ""} type="${type}" value="${esc(shown)}" ${opts.optional ? "" : "required"} ${type === "number" ? `step="${opts.money ? "0.01" : "1"}" min="${opts.min ?? 0}" ${opts.max !== undefined ? 'max="' + opts.max + '"' : ""}` : ""}></label>`;
}
function renderWizard() {
  const d = opsState.draft,
    step = opsState.step;
  const names = [
    "Factory & rates",
    "Machines & people",
    "Orders & flow",
    "Review & create",
  ];
  let content = "";
  if (step === 0)
    content = `<p class="form-description">Create an independent factory workspace. Your current shop and its history stay available in the factory selector. All amounts use INR.</p><div class="wizard-fields">${wizardInput("Factory name", "name")}${wizardInput("Shift starts at hour (0–16)", "policy.shift_start", "number", { max: 16 })}${wizardInput("Regular shift hours", "policy.shift_hours", "number", { min: 1, max: 12 })}${wizardInput("Allowed overtime hours", "policy.overtime_hours", "number", { max: 4 })}${wizardInput("Overtime premium / worker-hour (₹)", "policy.overtime_paise_per_hour", "number", { money: true })}${wizardInput("Changeover / moved job (₹)", "policy.changeover_paise", "number", { money: true })}${wizardInput("Repair labour / hour (₹)", "policy.repair_paise_per_hour", "number", { money: true })}${wizardInput("Planning horizon (days)", "policy.horizon_days", "number", { min: 2, max: 30 })}${wizardInput("Source of these assumptions", "policy.source")}</div>`;
  if (step === 1)
    content = `<p class="form-description">Machines of the same kind are potential substitutes. Operators must be explicitly qualified for the destination machine. These rates exclude one another: equipment downtime does not include wages.</p><div class="wizard-subhead"><h3>Machines</h3><button type="button" class="button secondary" data-wizard-add="machines">Add machine</button></div>${d.machines.map((m, i) => `<article class="wizard-record"><div class="wizard-record-head"><strong>${esc(m.id)}</strong><button type="button" class="text-button" data-wizard-remove="machines" data-index="${i}">Remove</button></div><div class="wizard-fields">${wizardInput("Name", `machines.${i}.name`)}${wizardInput("Machine kind", `machines.${i}.kind`)}${wizardInput("Equipment downtime / productive hour (₹)", `machines.${i}.downtime_paise_per_hour`, "number", { money: true })}${wizardInput("Temperature inspection threshold (°C)", `machines.${i}.temperature_limit`, "number", { min: 1, max: 300 })}${wizardInput("Vibration threshold (demo units)", `machines.${i}.vibration_limit`, "number", { min: 1, max: 100 })}${wizardInput("Load inspection threshold (%)", `machines.${i}.load_limit`, "number", { min: 1, max: 100 })}</div></article>`).join("")}<div class="wizard-subhead"><h3>Production workers</h3><button type="button" class="button secondary" data-wizard-add="workers">Add worker</button></div>${d.workers.map((w, i) => `<article class="wizard-record"><div class="wizard-record-head"><strong>${esc(w.id)}</strong><button type="button" class="text-button" data-wizard-remove="workers" data-index="${i}">Remove</button></div><div class="wizard-fields">${wizardInput("Name", `workers.${i}.name`)}${wizardInput("Qualified machine IDs, separated by commas", `workers.${i}.machines`, "text", { array: true })}${wizardInput("Delay-related idle cost / hour (₹)", `workers.${i}.idle_paise_per_hour`, "number", { money: true })}</div></article>`).join("")}<p class="field-help">A separate maintenance technician desk is included. Its repair reservations are queued to avoid double booking.</p>`;
  if (step === 2)
    content = `<p class="form-description">Use whole clock hours: 8 means D0 08:00; 32 means D1 08:00. Initial jobs must fit regular shifts, qualifications and dependency order.</p><div class="wizard-subhead"><h3>Customer orders</h3><button type="button" class="button secondary" data-wizard-add="orders">Add order</button></div>${d.orders.map((o, i) => `<article class="wizard-record"><div class="wizard-record-head"><strong>${esc(o.id)}</strong><button type="button" class="text-button" data-wizard-remove="orders" data-index="${i}">Remove</button></div><div class="wizard-fields">${wizardInput("Customer", `orders.${i}.customer`)}${wizardInput("Promised completion hour", `orders.${i}.due_hour`, "number", { min: 1, max: 720 })}${wizardInput("Order value (₹)", `orders.${i}.value_paise`, "number", { money: true })}${wizardInput("Contractual penalty / late hour (₹)", `orders.${i}.penalty_paise_per_hour`, "number", { money: true })}</div></article>`).join("")}<div class="wizard-subhead"><h3>Production stages</h3><button type="button" class="button secondary" data-wizard-add="jobs">Add stage</button></div>${d.jobs.map((j, i) => `<article class="wizard-record"><div class="wizard-record-head"><strong>${esc(j.id)}</strong><button type="button" class="text-button" data-wizard-remove="jobs" data-index="${i}">Remove</button></div><div class="wizard-fields">${wizardInput("Stage name", `jobs.${i}.name`)}${wizardInput("Machine ID", `jobs.${i}.machine`)}${wizardInput("Operator ID", `jobs.${i}.operator`)}${wizardInput("Order ID", `jobs.${i}.order`)}${wizardInput("Start hour", `jobs.${i}.start_hour`, "number", { max: 719 })}${wizardInput("Duration (whole hours)", `jobs.${i}.hours`, "number", { min: 1, max: 8 })}${wizardInput("Priority (1–100)", `jobs.${i}.priority`, "number", { min: 1, max: 100 })}${wizardInput("Prerequisite job IDs, separated by commas", `jobs.${i}.depends_on`, "text", { array: true, optional: true })}</div></article>`).join("")}`;
  if (step === 3)
    content = `<div class="ops-review-summary"><h3>${esc(d.name)}</h3><p>${d.machines.length} machines · ${d.workers.length} production workers · ${d.orders.length} orders · ${d.jobs.length} connected stages</p><p>Regular shift ${String(d.policy.shift_start).padStart(2, "0")}:00 for ${d.policy.shift_hours} hours. Horizon ${d.policy.horizon_days} days. All financial results will remain estimates based on these source rates.</p><p>The server validates identifiers, dependency order, qualifications and resource overlaps before creating the factory.</p></div><details class="technical"><summary>Advanced: inspect or import the configuration</summary><p class="field-help">Money is integer paise in this JSON format. Importing changes this draft only.</p><textarea id="wizard-json" rows="15" spellcheck="false">${esc(JSON.stringify(d, null, 2))}</textarea><button type="button" class="button secondary" data-wizard="import">Use this JSON draft</button></details>`;
  $("factory-wizard").innerHTML =
    `<div class="drawer-head"><div><div class="eyebrow">FACTORY SETUP / NEW WORKSPACE</div><h2 id="wizard-title">Build your factory context.</h2></div><button type="button" class="icon-button" data-wizard="close" aria-label="Close factory setup">${icon("close")}</button></div><ol class="wizard-steps">${names.map((n, i) => `<li class="${i === step ? "active" : ""}">${i + 1} / ${n}</li>`).join("")}</ol><form id="factory-setup-form">${content}<p id="wizard-error" class="field-error" role="alert" hidden></p><div class="wizard-actions">${step ? '<button type="button" class="button secondary" data-wizard="back">Back</button>' : "<span></span>"}<button class="button dark" type="submit" id="wizard-next">${step === 3 ? "Create factory workspace" : "Continue"} ${icon("arrow")}</button></div></form>`;
}
function readWizard() {
  const form = $("factory-setup-form");
  if (!form.reportValidity()) return false;
  form.querySelectorAll("[data-path]").forEach((input) => {
    const keys = input.dataset.path.split(".");
    const last = keys.pop();
    const parent = keys.reduce((o, k) => o[k], opsState.draft);
    parent[last] =
      input.dataset.array !== undefined
        ? input.value
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean)
        : input.type === "number"
          ? input.dataset.money
            ? Math.round(Number(input.value) * 100)
            : Number(input.value)
          : input.value.trim();
  });
  return true;
}
function nextRecordId(records, prefix) {
  let i = 1;
  while (records.some((r) => r.id === prefix + i)) i++;
  return prefix + i;
}
function addWizardRecord(kind) {
  if (!readWizard()) return;
  const d = opsState.draft;
  if (kind === "machines")
    d.machines.push({
      id: nextRecordId(d.machines, "M"),
      name: "New machine",
      kind: "machining",
      downtime_paise_per_hour: 100000,
      temperature_limit: 80,
      vibration_limit: 10,
      load_limit: 95,
    });
  if (kind === "workers")
    d.workers.push({
      id: nextRecordId(d.workers, "O"),
      name: "New operator",
      machines: [d.machines[0]?.id || "M1"],
      idle_paise_per_hour: 15000,
    });
  if (kind === "orders")
    d.orders.push({
      id: nextRecordId(d.orders, "ORD"),
      customer: "New customer",
      due_hour: 40,
      value_paise: 1000000,
      penalty_paise_per_hour: 10000,
    });
  if (kind === "jobs")
    d.jobs.push({
      id: nextRecordId(d.jobs, "J"),
      name: "New production stage",
      machine: d.machines[0]?.id || "M1",
      operator: d.workers[0]?.id || "O1",
      start_hour: 32,
      hours: 1,
      order: d.orders[0]?.id || "ORD1",
      priority: 5,
      depends_on: [],
    });
  renderWizard();
}
async function factorySelectionKey() {
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(state.token || "local-demo"),
  );
  return (
    "factory-selection:" +
    state.scenario +
    ":" +
    Array.from(new Uint8Array(hash))
      .slice(0, 12)
      .map((n) => n.toString(16).padStart(2, "0"))
      .join("")
  );
}
async function switchFactory(id) {
  if (state.busy) return;
  state.factory = id;
  localStorage.setItem(await factorySelectionKey(), id);
  await changeShop(state.scenario, state.token);
  setView("operations", true);
  await loadOperations(true);
}
document.addEventListener("factory:operations", () => loadOperations());
document.addEventListener("factory:scope", () => {
  opsState.version++;
  opsState.board = null;
  opsState.review = null;
  opsState.selected = 0;
  opsState.loading = false;
  opsState.busy = false;
  opsState.briefings = {};
  opsState.briefingBusy = false;
});
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const action = button.dataset.ops;
  if (action === "briefing" && !opsState.briefingBusy) {
    const plan = opsPlan(),
      review = opsState.review,
      epoch = state.epoch;
    if (!plan) return;
    opsState.briefingBusy = true;
    renderOperations();
    try {
      const result = await api(
        "operations/reviews/" + review.id + "/briefing",
        { hash: plan.hash },
      );
      if (epoch === state.epoch) opsState.briefings[plan.hash] = result;
    } catch (error) {
      if (!(error instanceof ScopeChanged)) fail(error);
    } finally {
      if (epoch === state.epoch) {
        opsState.briefingBusy = false;
        renderOperations();
      }
    }
    return;
  }
  if (action === "setup") await openFactoryWizard();
  if (action === "assumptions") showCostAssumptions();
  if (action === "repair") repairForm(button.dataset.id);
  if (action === "assign") {
    const id = button.dataset.id;
    confirmOperation(
      "Approve maintenance dispatch?",
      "Reserve the proposed technician slot and approve the internal maintenance handoff. This does not change production assignments. Existing recovery proposals will require a fresh analysis.",
      "Approve maintenance",
      () =>
        opsMutation(
          "maintenance/" + id + "/assign",
          {},
          "Maintenance handoff approved. Reanalyse production when ready.",
        ),
    );
  }
  if (action === "reanalyse" || action === "restore") {
    const id = button.dataset.id;
    loading(button, true, "Preparing review…");
    try {
      await opsMutation(
        "operations/" + id + "/reanalyse",
        { restoration: action === "restore" },
        action === "restore"
          ? "Return-to-normal options are ready. Jobs remain unchanged until approval."
          : "Hourly recovery options refreshed.",
        true,
      );
    } finally {
      if (button.isConnected)
        loading(
          button,
          false,
          action === "restore"
            ? "Review return to normal"
            : "Reopen recovery analysis",
        );
    }
  }
  if (action === "approve" || action === "reject") {
    const review = opsState.review,
      plan = opsPlan();
    if (!plan || review.status !== "pending" || opsStale()) return;
    const approve = action === "approve";
    confirmOperation(
      approve ? "Apply this costed recovery?" : "Reject this hourly proposal?",
      approve
        ? `${plan.assignments.length} jobs will be scheduled as reviewed. Estimated total ${inr(plan.financial.total_paise)}; ${plan.financial.orders.filter((o) => o.at_risk).length} orders projected late. ${plan.kind === "breakdown" ? "The proposed maintenance handoff will also be approved." : "This is a post-repair rebalance; historical downtime costs are excluded."} Estimates use configured source rates. No purchase or physical machine command is sent.`
        : "Close this review without applying its production assignments. The reported breakdown and proposed maintenance task remain recorded.",
      approve ? "Approve & apply" : "Reject proposal",
      () =>
        opsMutation(
          "operations/reviews/" + review.id + "/decision",
          { hash: plan.hash, approve },
          approve
            ? "Hourly recovery applied and its shared handoffs saved."
            : "Hourly review rejected. The breakdown remains available for a new analysis.",
        ),
    );
  }
  if (action === "health-normal" || action === "health-hot") {
    const id = $("ops-health-machine").value,
      m = opsState.board.machines.find((v) => v.id === id),
      hot = action === "health-hot";
    await opsMutation(
      "operations/health/simulate",
      {
        machine: id,
        hour: opsState.board.hour_now,
        temperature: hot
          ? Math.min(300, m.temperature_limit + 15)
          : Math.max(0, m.temperature_limit - 20),
        vibration: hot
          ? Math.min(100, m.vibration_limit + 3)
          : Math.max(0, m.vibration_limit - 4),
        load: hot
          ? Math.min(100, m.load_limit + 3)
          : Math.max(0, m.load_limit - 30),
      },
      "Simulated sample recorded. Threshold checks are inspection prompts, not failure predictions.",
    );
  }
  const wizard = button.dataset.wizard;
  if (wizard === "close" && !state.busy) $("factory-wizard").close();
  if (wizard === "back") {
    readWizard();
    opsState.step = Math.max(0, opsState.step - 1);
    renderWizard();
  }
  if (wizard === "import") {
    try {
      const data = JSON.parse($("wizard-json").value);
      if (
        !data ||
        typeof data.name !== "string" ||
        !data.name.trim() ||
        !Array.isArray(data.machines) ||
        !Array.isArray(data.workers) ||
        !Array.isArray(data.jobs) ||
        !Array.isArray(data.orders) ||
        !data.policy
      )
        throw new Error(
          "Include name, machines, workers, orders, jobs and policy.",
        );
      for (const key of ["machines", "workers", "orders", "jobs"]) {
        if (
          !data[key].length ||
          data[key].some(
            (row) =>
              !row || typeof row !== "object" || typeof row.id !== "string",
          )
        )
          throw new Error(
            "Each configuration list needs records with string IDs.",
          );
      }
      if (
        data.workers.some((worker) => !Array.isArray(worker.machines)) ||
        data.jobs.some((job) => !Array.isArray(job.depends_on)) ||
        typeof data.policy !== "object" ||
        !Number.isInteger(data.policy.shift_start) ||
        !Number.isInteger(data.policy.shift_hours)
      )
        throw new Error(
          "Include worker qualifications, job prerequisites and a complete shift policy.",
        );
      opsState.draft = data;
      opsState.step = 0;
      renderWizard();
    } catch (e) {
      $("wizard-error").textContent = e.message;
      $("wizard-error").hidden = false;
    }
  }
  if (button.dataset.wizardAdd) addWizardRecord(button.dataset.wizardAdd);
  if (button.dataset.wizardRemove) {
    if (readWizard()) {
      opsState.draft[button.dataset.wizardRemove].splice(
        Number(button.dataset.index),
        1,
      );
      renderWizard();
    }
  }
});
document.addEventListener("change", async (event) => {
  if (event.target.id === "ops-review-record") {
    const row = opsState.board.reviews.find(
      (row) => row.id === event.target.value,
    );
    if (row && !state.busy)
      await adoptReview({ ...JSON.parse(row.payload), status: row.status });
  }
  if (event.target.id === "ops-factory")
    await switchFactory(event.target.value);
  if (event.target.name === "hourly-plan") {
    opsState.selected = Number(event.target.value);
    const key = await opsStorageKey();
    localStorage.setItem(key + ":plan", opsPlan().hash);
    syncHourlySummary();
    renderOperations();
    renderKpis();
  }
  if (event.target.id === "ops-start")
    $("ops-time-help").textContent =
      "Hour " +
      event.target.value +
      " = " +
      hourLabel(Number(event.target.value)) +
      ". Day 0 is the demo reference day.";
  if (event.target.id === "ops-machine") {
    const next = opsState.board.jobs
      .filter(
        (j) =>
          j.machine === event.target.value &&
          j.start_hour >= opsState.board.hour_now,
      )
      .sort((a, b) => a.start_hour - b.start_hour)[0];
    if (next) {
      $("ops-start").value = next.start_hour;
      $("ops-time-help").textContent =
        "Hour " +
        next.start_hour +
        " = " +
        hourLabel(next.start_hour) +
        ". Day 0 is the demo reference day.";
    }
  }
});
document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (form.id === "ops-report-form") {
    event.preventDefault();
    if (state.busy) return;
    const from = Number($("ops-start").value),
      duration = Number($("ops-duration").value),
      error = $("ops-report-error");
    error.hidden = true;
    if (
      !form.reportValidity() ||
      !Number.isInteger(from) ||
      !Number.isInteger(duration) ||
      duration < 1 ||
      duration > 24
    ) {
      error.textContent =
        "Use whole hours, a start at or after the reference clock, and a repair duration from 1 to 24 hours.";
      error.hidden = false;
      return;
    }
    const epoch = state.epoch;
    state.busy++;
    loading($("ops-report-submit"), true, "Calculating graph impact & costs…");
    try {
      const review = await api("operations/analyse", {
        machine: $("ops-machine").value,
        start_hour: from,
        duration_hours: duration,
        use_memory: new FormData(form).get("use_memory") === "on",
      });
      await adoptReview(review);
      notice(
        review.plans.length
          ? "Hourly costed options are ready. Review assumptions before approval."
          : "Breakdown recorded. No feasible production plan was found; maintenance can still be approved.",
        "info",
      );
    } catch (e) {
      if (!(e instanceof ScopeChanged)) {
        error.textContent = e.message;
        error.hidden = false;
      }
    } finally {
      if (epoch === state.epoch) {
        state.busy--;
        loading($("ops-report-submit"), false, "Calculate impact & options");
      }
    }
  }
  if (form.id === "ops-repair-form") {
    event.preventDefault();
    if (state.busy || !form.reportValidity()) return;
    const hour = Number($("ops-repair-hour").value),
      cost = Math.round(Number($("ops-repair-cost").value) * 100);
    if (!Number.isInteger(hour) || !Number.isSafeInteger(cost)) {
      $("ops-form-error").textContent =
        "Use a whole completion hour and a valid rupee amount.";
      $("ops-form-error").hidden = false;
      return;
    }
    const notes = $("ops-repair-notes").value;
    await opsMutation(
      "maintenance/" + form.dataset.id + "/complete",
      { actual_end_hour: hour, actual_cost_paise: cost, notes },
      "Repair completion recorded. Review a return-to-normal plan before changing production again.",
    );
  }
  if (form.id === "factory-setup-form") {
    event.preventDefault();
    if (state.busy || !readWizard()) return;
    if (opsState.step < 3) {
      opsState.step++;
      renderWizard();
      return;
    }
    const epoch = state.epoch;
    state.busy++;
    loading($("wizard-next"), true, "Validating & creating graph…");
    try {
      const created = await api("factories", opsState.draft);
      $("factory-wizard").close();
      state.busy--;
      await switchFactory(created.id);
      notice(
        "Factory workspace created with validated resources, order values and source cost assumptions.",
      );
    } catch (e) {
      if (!(e instanceof ScopeChanged)) {
        $("wizard-error").textContent = e.message;
        $("wizard-error").hidden = false;
      }
    } finally {
      if (epoch === state.epoch) {
        state.busy = Math.max(0, state.busy - 1);
        loading($("wizard-next"), false, "Create factory workspace");
      }
    }
  }
});
$("factory-wizard").addEventListener("cancel", (event) => {
  if (state.busy) event.preventDefault();
});
setInterval(() => {
  if (
    state.view === "operations" &&
    !state.busy &&
    !opsState.loading &&
    !document.hidden &&
    !$("drawer").open &&
    !$("confirm-dialog").open &&
    !$("factory-wizard").open &&
    opsState.board
  ) {
    api("operations")
      .then((board) => {
        if (board.revision !== opsState.board.revision) loadOperations(true);
      })
      .catch(() => {});
  }
}, 15000);
(async () => {
  try {
    const saved = localStorage.getItem(await factorySelectionKey());
    if (saved) {
      state.factory = saved;
      await changeShop(state.scenario, state.token);
      await loadOperations();
    }
  } catch (e) {
    if (!(e instanceof ScopeChanged)) fail(e);
  }
})();
