const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(
  path.join(__dirname, "../src/factory_graph/static/guided-demo.js"),
  "utf8",
);
function harness() {
  const calls = [],
    handlers = {},
    elements = {},
    storage = new Map();
  const plan = {
    hash: "a".repeat(64),
    assignments: [
      { job: "J1", machine: "M2", operator: "O1", start_hour: 9, hours: 3 },
    ],
    before: [
      { job: "J1", machine: "M1", operator: "O1", start_hour: 8, hours: 3 },
    ],
    financial: { total_paise: 375000, orders: [] },
  };
  const review = {
    id: "review",
    event: { breakdown: "breakdown" },
    status: "pending",
    revision: 1,
    plans: [plan],
  };
  const board = {
    name: "Practice",
    revision: 1,
    hour_now: 8,
    policy: { horizon_days: 7 },
    jobs: [],
    machines: [],
    orders: [],
    operators: [],
    maintenance: [],
    repair_memory: [],
    reviews: [
      { id: "review", status: "pending", payload: JSON.stringify(review) },
    ],
  };
  const context = {
    state: { factory: "practice", epoch: 1, busy: 0 },
    document: {
      addEventListener: (name, fn) => {
        (handlers[name] ??= []).push(fn);
      },
    },
    $: (id) =>
      (elements[id] ??= {
        innerHTML: "",
        open: false,
        scrollIntoView() {},
        close() {
          this.open = false;
        },
      }),
    api: async (endpoint, body) => {
      calls.push({ endpoint, body });
      return endpoint === "operations" ? board : { status: "applied" };
    },
    localStorage: {
      setItem: (k, v) => storage.set(k, v),
      getItem: (k) => storage.get(k) ?? null,
    },
    factorySelectionKey: async () => "shop",
    icon: () => "",
    inr: (n) => String(n || 0),
    planName: () => "Standard capacity",
    badge: (s) => s,
    componentNames: {},
    refresh: async () => {},
    setView: () => {},
    esc: (value) =>
      String(value ?? "").replace(
        /[<>&"']/g,
        (c) =>
          ({
            "<": "&lt;",
            ">": "&gt;",
            "&": "&amp;",
            '"': "&quot;",
            "'": "&#39;",
          })[c],
      ),
    ScopeChanged: class ScopeChanged extends Error {},
    confirmOperation: (...args) => {
      context.confirmedAction = args[3];
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  const guide = vm.runInContext("guide", context);
  guide.meta = {
    factory: "practice",
    phase: 0,
    selected: 0,
    reviewId: "review",
  };
  guide.board = board;
  guide.review = review;
  return {
    context,
    guide,
    board,
    review,
    plan,
    calls,
    handlers,
    run: (code) => vm.runInContext(code, context),
  };
}
test("story navigation alone does not mutate the factory", async () => {
  const h = harness();
  await h.handlers.click[0]({
    target: { closest: () => ({ dataset: { guide: "next" } }) },
  });
  assert.equal(h.guide.meta.phase, 1);
  assert.equal(h.calls.length, 0);
});
test("production approval waits for explicit confirmation and binds the reviewed hash", async () => {
  const h = harness();
  h.guide.meta.phase = 4;
  await h.run("guideReviewApplied()");
  assert.equal(h.calls.length, 0);
  assert.equal(typeof h.context.confirmedAction, "function");
  await h.context.confirmedAction();
  const mutations = h.calls.filter((c) => c.endpoint.endsWith("/decision"));
  assert.equal(mutations.length, 1);
  assert.equal(mutations[0].body.hash, h.plan.hash);
  assert.equal(mutations[0].body.approve, true);
  assert.equal(h.guide.meta.phase, 5);
});
test("a guide from another workspace cannot load data", async () => {
  const h = harness();
  h.context.state.factory = "another-factory";
  await h.run("loadGuide()");
  assert.equal(h.calls.length, 0);
});
test("resuming an applied review moves past the approval step", async () => {
  const h = harness();
  h.board.reviews[0].status = "applied";
  h.guide.meta.phase = 4;
  await h.run("loadGuide()");
  assert.equal(h.guide.meta.phase, 5);
});
test("practice factory text is escaped in the guide", () => {
  const h = harness();
  h.guide.board.name = "<img src=x onerror=alert(1)>";
  h.run("renderGuide()");
  const html = h.context.$("guided-demo-root").innerHTML;
  assert(!html.includes("<img src=x"));
  assert(html.includes("&lt;img"));
});
