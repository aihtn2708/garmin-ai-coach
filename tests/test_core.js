// Tests for docs/core.js (the browser engine), run under Node 18+.
//   node --test tests/test_core.js
// Needs python3 on PATH: the Python filter is the reference, and the mock servers are Python.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { execFileSync, spawn } = require("node:child_process");
const path = require("node:path");
const core = require("../docs/core.js");

const ROOT = path.join(__dirname, "..");

function close(a, b, where = "") {
  if (typeof a === "number" && typeof b === "number") {
    assert.ok(Math.abs(a - b) <= 0.011, `${where}: ${a} vs ${b}`);
  } else if (Array.isArray(a)) {
    assert.ok(Array.isArray(b), `${where}: expected array`);
    assert.equal(a.length, b.length, `${where}: length`);
    a.forEach((v, i) => close(v, b[i], `${where}[${i}]`));
  } else if (a && typeof a === "object") {
    assert.ok(b && typeof b === "object" && !Array.isArray(b), `${where}: expected object`);
    assert.deepEqual(Object.keys(a), Object.keys(b), `${where}: keys`);
    for (const k of Object.keys(a)) close(a[k], b[k], `${where}.${k}`);
  } else assert.equal(a, b, where);
}

test("privacy filter matches the Python reference on every case", () => {
  const cases = JSON.parse(execFileSync("python3", ["-m", "tests.parity_dump"], { cwd: ROOT, maxBuffer: 64 << 20 }));
  assert.ok(cases.length >= 16);
  for (const c of cases) {
    const tag = `${c.name}/${c.level}`;
    const scrubbed = core.scrub(c.input, c.level, new core.AliasMap());
    close(scrubbed, c.scrub, `${tag} scrub`);
    close(core.compact(scrubbed), c.compact, `${tag} compact`);
    if (c.name !== "big_blobs") close(core.compact(scrubbed, 8000), c.compact_small, `${tag} compact_small`);
    else assert.ok(JSON.stringify(core.compact(scrubbed, 8000)).length <= 8200);
    if (Array.isArray(scrubbed)) {
      const rows = scrubbed.filter((r) => r && typeof r === "object" && !Array.isArray(r));
      close(core.aggregate(rows, "week"), c.week, `${tag} week`);
      close(core.aggregate(rows, "month"), c.month, `${tag} month`);
      close(core.slimActivities(scrubbed), c.slim, `${tag} slim`);
    }
  }
});

test("nothing identifying survives the filter", () => {
  const raw = { activityId: 987654321, ownerId: 55, displayName: "jane.doe", ownerFullName: "Jane Doe", activityName: "Riverside Run",
    locationName: "Springfield", startLatitude: 1.23, deviceId: 3, profileImageUrlLarge: "https://x/y.png", distance: 10000,
    userData: { birthDate: "1990-01-01", email: "j@example.com" }, geoPolylineDTO: { polyline: [[1, 2]] } };
  const balanced = JSON.stringify(core.scrub(raw, "balanced", new core.AliasMap()));
  for (const leaked of ["987654321", "jane", "Jane", "1.23", "y.png", "1990", "example.com", "polyline"]) assert.ok(!balanced.includes(leaked), leaked);
  assert.ok(balanced.includes('"activity":"A1"') && balanced.includes("Riverside Run"));
  const strict = JSON.stringify(core.scrub(raw, "strict", new core.AliasMap()));
  assert.ok(!strict.includes("Riverside") && !strict.includes("Springfield"));
});

const box = (cats) => new core.Toolbox(new core.DemoGarmin(), new Set(cats || ["activities", "sleep", "heart", "stress_energy", "daily", "profile"]), "balanced", new core.AliasMap());
const iso = (offset) => { const d = new Date(); d.setDate(d.getDate() + offset); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

test("tools: category gating, validation, chunking, aliases, chart cleaning", async () => {
  const only = box(["sleep"]);
  assert.deepEqual(only.specs().map((t) => t.name), ["get_range", "get_day", "show_chart"]);
  await assert.rejects(only.run("get_range", { metric: "hrv", start_date: iso(-2), end_date: iso(0) }), core.ToolError);
  await assert.rejects(only.run("list_activities", {}), core.ToolError);
  const tb = box();
  await assert.rejects(tb.run("get_range", { metric: "sleep", start_date: "../../etc", end_date: iso(0) }), core.ToolError);
  await assert.rejects(tb.run("get_range", { metric: "sleep", start_date: iso(-200), end_date: iso(0) }), core.ToolError);
  await assert.rejects(tb.run("get_range", { metric: "__proto__", start_date: iso(-2), end_date: iso(0) }), core.ToolError);
  await assert.rejects(tb.run("get_activity", { activity: "123456" }), core.ToolError);
  const [out] = await tb.run("get_range", { metric: "stress", start_date: iso(-69), end_date: iso(0) });
  assert.equal(out.data.rows.length, 70);
  assert.equal(tb.callsThisTurn, 3);
  const [byMonth] = await tb.run("get_range", { metric: "sleep", start_date: iso(-69), end_date: iso(0), group_by: "month" });
  assert.ok(byMonth.data.every((r) => "period" in r && "avg.sleepTimeSeconds" in r));
  const [list] = await tb.run("list_activities", { limit: 3 });
  assert.ok(!JSON.stringify(list).includes("activityId"));
  const [detail] = await tb.run("get_activity", { activity: "A1" });
  assert.equal(detail.activity, "A1");
  assert.ok(!JSON.stringify(detail).includes("Latitude"));
  const [, ev] = await tb.run("show_chart", { title: "t", kind: "pie", x_labels: ["a", "b"], series: Array(9).fill({ name: "s", values: [1, "x", 3] }) });
  assert.equal(ev.chart.kind, "line");
  assert.equal(ev.chart.series.length, 4);
  assert.deepEqual(ev.chart.series[0].values, [1, null]);
});

test("egress refuses hosts off the allowlist and plain HTTP to remote hosts", async () => {
  const eg = new core.Egress(new core.Ledger(), async () => { throw new Error("fetch must not be called"); });
  await assert.rejects(eg.request("GET", "https://example.com/"), core.EgressBlocked);
  eg.allow("http://example.com", "model");
  await assert.rejects(eg.request("GET", "http://example.com/"), core.EgressBlocked);
});

test("egress sends no cookies, follows no redirects, and never logs headers", async () => {
  const ledger = new core.Ledger();
  let seen;
  const eg = new core.Egress(ledger, async (url, init) => { seen = init; return new Response("{}", { status: 200 }); });
  eg.allow("https://api.example", "model");
  await eg.request("POST", "https://api.example/x", { headers: { Authorization: "Bearer SECRET" }, body: { a: 1 }, keepDetail: true });
  assert.equal(seen.credentials, "omit");
  assert.equal(seen.redirect, "error");
  assert.equal(seen.referrerPolicy, "no-referrer");
  assert.ok(!JSON.stringify([ledger.entries(), ledger.detail(1)]).includes("SECRET"));
});

// ── against the Python mock servers ─────────────────────────────────────────
let mocks, urls;
test.before(async () => {
  mocks = spawn("python3", ["-m", "tests.serve_mocks"], { cwd: ROOT, stdio: ["pipe", "pipe", "inherit"] });
  urls = await new Promise((resolve, reject) => {
    mocks.stdout.once("data", (d) => resolve(JSON.parse(String(d).split("\n")[0])));
    mocks.once("error", reject);
  });
});
test.after(() => { mocks.stdin.end(); mocks.kill(); });

test("Garmin client: bad key, token refresh, cache, path allowlist, test override is loopback-only", async () => {
  await assert.rejects(new core.GarminClient("wrong", new core.Egress(new core.Ledger()), urls.garmin).connect(), /rejected this API key/);
  const ledger = new core.Ledger();
  const client = new core.GarminClient("good-key", new core.Egress(ledger), urls.garmin);
  assert.ok((await client.connect()) > Date.now() / 1000);
  const path = "/sleep-service/sleep/dailySleepSummariesByDate";
  const data = await client.get(path, { startDate: "2026-10-01", endDate: "2026-10-03" });
  assert.equal(data.length, 3); // the mock rejects the first token once; the client refreshed and retried
  const before = ledger.entries().length;
  await client.get(path, { startDate: "2026-10-01", endDate: "2026-10-03" });
  assert.equal(ledger.entries().length, before); // served from memory
  for (const bad of ["/activity-service/activity/1/../../x", "/upload-service/upload", "/userprofile-service/userprofile/settings/x"]) {
    await assert.rejects(client.get(bad), core.GarminError);
  }
  assert.ok(!JSON.stringify(ledger.entries()).includes("good-key"));
  assert.equal(new core.GarminClient("k", new core.Egress(new core.Ledger()), "https://evil.example").baseUrl, "https://personal.connectapi.garmin.com");
});

test("a provider the browser cannot reach gives a clear message, not a crash", async () => {
  const eg = new core.Egress(new core.Ledger(), async () => { throw new TypeError("Failed to fetch"); });
  await assert.rejects(core.listModels("deepseek", "k", eg), /could not reach DeepSeek/);
  await assert.rejects(new core.GarminClient("k", eg).connect(), /could not reach Garmin/);
});

test("full session in the page engine: OpenAI format with Gemini thought signature", async () => {
  const saved = core.PROVIDERS.gemini.base_url;
  core.PROVIDERS.gemini.base_url = urls.openai;
  try {
    const backend = core.createBackend();
    await backend.api("/api/garmin/connect", { demo: true });
    const { models } = await backend.api("/api/provider/models", { provider: "gemini", api_key: "secret-key-1" });
    assert.deepEqual(models.map((m) => m.id), ["gemini-9.9-flash"]);
    await backend.api("/api/provider/select", { provider: "gemini", model: "gemini-9.9-flash" });
    await backend.api("/api/settings", { level: "strict" });
    const events = [];
    await backend.chat("How did I sleep?", (e) => events.push(e));
    assert.equal(events.at(-1).type, "answer");
    assert.match(events.at(-1).text, /7 h 12 min/);
    assert.equal(events.filter((e) => e.type === "tool").length, 2);
    assert.equal(events.find((e) => e.type === "chart").chart.x_labels.length, 14);
    assert.deepEqual(events.find((e) => e.type === "usage"), { type: "usage", tokens_in: 5400, tokens_out: 310 });
    const state = await backend.api("/api/state");
    assert.equal(state.turns, 1);
    assert.equal(state.mode, "browser");
    const { entries } = await backend.api("/api/ledger?since=0");
    const details = [];
    for (const e of entries) details.push((await backend.api(`/api/ledger/detail?id=${e.id}`)).detail);
    const dump = JSON.stringify([entries, details, state]);
    assert.ok(!dump.includes("secret-key-1") && !dump.includes("userProfilePK"));
    assert.equal(details.at(-1).messages.at(-1).role, "tool");
    const wiped = await backend.api("/api/wipe", {});
    assert.equal(wiped.garmin.connected, false);
    assert.deepEqual((await backend.api("/api/ledger?since=0")).entries, []);
    assert.deepEqual(wiped.allowed_hosts, {});
  } finally { core.PROVIDERS.gemini.base_url = saved; }
});

test("OpenRouter requests ask to refuse hosts that train; Anthropic format round trip", async () => {
  const savedOr = core.PROVIDERS.openrouter.base_url, savedAn = core.PROVIDERS.anthropic.base_url;
  core.PROVIDERS.openrouter.base_url = urls.openai;
  core.PROVIDERS.anthropic.base_url = urls.anthropic;
  try {
    const ledger = new core.Ledger();
    const chat = new core.Chat("openrouter", "k", "m", new core.Egress(ledger), "sys", [], "hi", { block_training: true });
    await chat.send([]);
    assert.deepEqual(ledger.detail(1).provider, { data_collection: "deny" });

    await assert.rejects(core.listModels("anthropic", "nope", new core.Egress(new core.Ledger())), core.ProviderError);
    const models = await core.listModels("anthropic", "sk-ant-test", new core.Egress(new core.Ledger()));
    assert.equal(models[0].id, "claude-haiku-9");
    const events = [];
    const led2 = new core.Ledger();
    const answer = await core.runTurn({ provider: "anthropic", key: "sk-ant-test", model: "claude-haiku-9", options: {}, egress: new core.Egress(led2),
      toolbox: box(), history: [["earlier q", "earlier a"]], userText: "How much did I run?", emit: (e) => events.push(e) });
    assert.equal(answer, "You ran three times.");
    const second = led2.detail(led2.entries().at(-1).id);
    assert.equal(second.messages.at(-2).content[1].type, "tool_use");
    assert.ok("system" in second);
  } finally { core.PROVIDERS.openrouter.base_url = savedOr; core.PROVIDERS.anthropic.base_url = savedAn; }
});
