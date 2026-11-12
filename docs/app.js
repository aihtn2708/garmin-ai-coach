"use strict";
/* Garmin AI Coach UI. Plain JavaScript, no libraries, no network calls except
   to this page's own server. Model output is always inserted as text nodes:
   links are not made clickable and images are never loaded, so a reply cannot
   smuggle data out through a URL. */
(() => {
  const $ = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  function s(tag, attrs) {
    const el = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }

  // ── session token: arrives in the URL fragment, then lives in this tab only ──
  let token = "";
  const fromHash = location.hash.match(/t=([\w-]+)/);
  if (fromHash) {
    token = fromHash[1];
    try { sessionStorage.setItem("t", token); } catch (e) { /* private mode */ }
    history.replaceState(null, "", location.pathname);
  } else {
    try { token = sessionStorage.getItem("t") || ""; } catch (e) { /* ignore */ }
  }

  // Two ways to run. "local": the Python app on this computer answers /api/...
  // "browser": there is no server; core.js answers the same routes inside this tab.
  let backend = null;

  async function api(path, body) {
    if (backend) return backend.api(path, body);
    const opts = { headers: { "X-Session-Token": token } };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  // The local server stamps data-mode="local" on the page it serves; a static host cannot.
  // The Cloudflare Worker stamps data-mode="relay". With neither stamp, the page is a demo.
  function detectMode() {
    const stamp = document.documentElement.dataset.mode;
    if (stamp === "local") return token ? "local" : "local-no-token";
    if (!window.CoachCore) return "local-no-token";
    return stamp === "relay" ? "relay" : "browser";
  }

  let state = null;
  let models = [];
  let busy = false;
  const transcript = [];

  // ── helpers ───────────────────────────────────────────────────────────────
  const kb = (n) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} kB`);
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const num = (v) => (v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  function showError(id, err) {
    const el = $(id);
    el.textContent = err ? (err.message || String(err)) : "";
    el.hidden = !err;
  }
  async function withButton(btn, label, fn) {
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
    try { return await fn(); } finally { btn.disabled = false; btn.textContent = old; }
  }

  // ── render state ──────────────────────────────────────────────────────────
  function render(next) {
    state = next;
    const g = state.garmin;
    $("garmin-off").hidden = g.connected;
    $("garmin-on").hidden = !g.connected;
    $("garmin-saved").hidden = !g.saved_key;
    $("step-garmin").classList.toggle("done", g.connected);
    if (g.connected) {
      $("garmin-state").textContent = g.demo
        ? (state.mode === "browser" ? "Built-in sample data is loaded. This page never contacts Garmin." : "Using built-in sample data. Nothing is requested from Garmin.")
        : `Connected. Access renews automatically until your key expires${g.token_expires_at ? "; current token valid until " + new Date(g.token_expires_at * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : ""}.`;
    }

    const sel = $("provider");
    if (!sel.options.length) {
      for (const [id, p] of Object.entries(state.providers)) sel.append(h("option", { value: id, text: p.label }));
      sel.value = state.provider.name || "gemini";
      renderProvider();
    }
    const ready = Boolean(state.provider.name && state.provider.model);
    $("step-model").classList.toggle("done", ready);
    $("or-block").checked = state.options.block_training;

    const cats = $("categories");
    cats.replaceChildren(
      ...state.categories.map((c) =>
        h("label", { class: "check" },
          h("input", { type: "checkbox", value: c.id, checked: c.on, onchange: saveScope }),
          h("span", {}, c.label, h("small", { text: c.description })))));
    for (const r of document.querySelectorAll('input[name="level"]')) r.checked = r.value === state.level;
    $("step-scope").classList.add("done");

    $("remember-wrap").hidden = !state.keystore.available;
    $("remember").checked = state.keystore.remember;
    $("remember-off").hidden = state.keystore.remember;

    const p = state.provider.name ? state.providers[state.provider.name] : null;
    $("top-status").textContent = [
      g.connected ? (g.demo ? "Sample data" : "Garmin connected") : "Garmin not connected",
      ready ? `${p.label}: ${state.provider.model}` : "No model chosen",
    ].join("  |  ");
    updateComposer();
  }

  function renderProvider() {
    const id = $("provider").value;
    const p = state.providers[id];
    $("provider-cost").textContent = p.cost;
    $("provider-data").textContent = p.data_use;
    $("provider-notice").classList.toggle("caution", Boolean(p.caution));
    $("provider-terms").hidden = !p.terms;
    if (p.terms) $("provider-terms").href = p.terms;
    $("provider-key-wrap").hidden = !p.needs_key;
    $("provider-key-link").href = p.key_page;
    $("provider-key-link").textContent = `Get a key from ${p.label}`;
    const have = state.provider.has_key.includes(id) || state.provider.saved.includes(id);
    $("provider-key").value = "";
    $("provider-key").placeholder = have ? "Key already added. Paste to replace it" : "Paste your key";
    $("or-block-wrap").hidden = id !== "openrouter";
    showError("model-error", null);
    models = [];
    if (state.provider.name === id && state.provider.model) {
      // Reloaded tab: show the model already in use until the list is loaded again.
      $("model").replaceChildren(h("option", { value: state.provider.model, text: state.provider.model }));
      $("paid-wrap").hidden = true;
      $("model-wrap").hidden = false;
    } else {
      $("model-wrap").hidden = true;
    }
  }

  function fillModels() {
    const id = $("provider").value;
    const showPaid = $("show-paid").checked;
    const hasFree = models.some((m) => m.free === true);
    $("paid-wrap").hidden = !(id === "openrouter" && hasFree);
    const list = id === "openrouter" && hasFree && !showPaid ? models.filter((m) => m.free) : models;
    $("model").replaceChildren(
      ...list.map((m) => h("option", { value: m.id, text: m.label + (m.free === true && id === "openrouter" ? " (free)" : "") })));
    $("model-wrap").hidden = false;
  }

  async function chooseModel() {
    const model = $("model").value;
    if (!model) return;
    try {
      render(await api("/api/provider/select", { provider: $("provider").value, model }));
      showError("model-error", null);
    } catch (err) { showError("model-error", err); }
  }

  function updateComposer() {
    let need = "";
    if (!token && !backend) need = "This tab has no session token. Open the link printed in the terminal.";
    else if (!state.garmin.connected) need = state.mode === "browser" ? "Load the sample data to start." : "Connect Garmin or load the sample data to start.";
    else if (!(state.provider.name && state.provider.model)) need = "Choose an AI model to start.";
    $("need").textContent = need;
    const off = Boolean(need) || busy;
    $("ask").disabled = off;
    for (const b of $("suggest").querySelectorAll("button")) b.disabled = off;
  }

  async function saveScope() {
    const categories = [...$("categories").querySelectorAll("input:checked")].map((i) => i.value);
    const level = document.querySelector('input[name="level"]:checked')?.value || "balanced";
    try { render(await api("/api/settings", { categories, level, block_training: $("or-block").checked })); } catch (e) { /* shown on next action */ }
  }

  // ── setup actions ─────────────────────────────────────────────────────────
  async function connectGarmin(body, btn) {
    showError("garmin-error", null);
    try {
      render(await withButton(btn, "Connecting", () => api("/api/garmin/connect", body)));
      $("garmin-key").value = "";
    } catch (err) { showError("garmin-error", err); }
  }
  $("garmin-connect").onclick = (e) => connectGarmin({ api_key: $("garmin-key").value }, e.target);
  $("garmin-saved").onclick = (e) => connectGarmin({ use_saved: true }, e.target);
  $("garmin-demo").onclick = (e) => connectGarmin({ demo: true }, e.target);
  $("garmin-key").onkeydown = (e) => { if (e.key === "Enter") $("garmin-connect").click(); };
  $("garmin-disconnect").onclick = async () => render(await api("/api/garmin/disconnect", {}));

  $("provider").onchange = renderProvider;
  $("load-models").onclick = async (e) => {
    showError("model-error", null);
    try {
      const res = await withButton(e.target, "Loading", () =>
        api("/api/provider/models", { provider: $("provider").value, api_key: $("provider-key").value }));
      models = res.models;
      $("provider-key").value = "";
      render(res.state);
      $("provider-key").placeholder = "Key already added. Paste to replace it";
      fillModels();
      await chooseModel();
    } catch (err) { showError("model-error", err); }
  };
  $("provider-key").onkeydown = (e) => { if (e.key === "Enter") $("load-models").click(); };
  $("model").onchange = chooseModel;
  $("show-paid").onchange = () => { fillModels(); chooseModel(); };
  $("or-block").onchange = saveScope;
  for (const r of document.querySelectorAll('input[name="level"]')) r.onchange = saveScope;
  $("remember").onchange = async (e) => {
    try { render(await api("/api/keys/remember", { on: e.target.checked })); } catch (err) { e.target.checked = false; }
  };

  let wipeArmed = 0;
  $("wipe").onclick = async (e) => {
    if (Date.now() - wipeArmed > 4000) {
      wipeArmed = Date.now();
      e.target.textContent = "Click again to confirm";
      setTimeout(() => { e.target.textContent = "Forget everything"; }, 4000);
      return;
    }
    wipeArmed = 0;
    e.target.textContent = "Forget everything";
    render(await api("/api/wipe", {}));
    if (state.mode === "browser") render(await api("/api/garmin/connect", { demo: true }));  // the demo always has its sample data
    resetThread();
    lastId = 0; entries = []; renderLog();
    $("provider").replaceChildren(); render(state);
  };
  $("quit").onclick = async () => {
    try { await api("/api/quit", {}); } catch (e) { /* server is going away */ }
    document.body.replaceChildren(
      h("div", { class: "thread" }, h("div", { class: "welcome" },
        h("h1", { text: "Stopped." }),
        h("p", { text: "Keys and data held in memory are gone. You can close this tab." }))));
  };

  // ── chat ──────────────────────────────────────────────────────────────────
  const SUGGESTIONS = [
    "How has my sleep changed over the last 30 days?",
    "Compare my resting heart rate and HRV this month with last month.",
    "Summarise my running over the last 4 weeks. Am I ramping up too fast?",
    "Which days this week was I most stressed, and what did I do on those days?",
  ];
  $("suggest").replaceChildren(...SUGGESTIONS.map((q) => h("button", { type: "button", text: q, onclick: () => ask(q) })));

  const METRIC = {
    daily_summary: "Daily summaries", steps: "Steps", sleep: "Sleep", hrv: "HRV", stress: "Stress",
    body_battery: "Body Battery", weight: "Weight", hydration: "Hydration", sleep_detail: "Sleep detail",
    heart_rate: "Heart rate", hrv_detail: "HRV detail", spo2: "SpO2", fitness_age: "Fitness age",
    food_log: "Food log", menstrual_cycle: "Cycle summary",
  };
  function describe(name, a) {
    if (name === "get_range") return `${METRIC[a.metric] || a.metric}, ${a.start_date} to ${a.end_date}${a.group_by && a.group_by !== "day" ? `, by ${a.group_by}` : ""}`;
    if (name === "get_day") return `${METRIC[a.metric] || a.metric}, ${a.date}`;
    if (name === "list_activities") return `Activities${a.start_date ? `, ${a.start_date} to ${a.end_date || "today"}` : ", most recent"}${a.activity_type ? ` (${a.activity_type})` : ""}`;
    if (name === "get_activity") return `Details of activity ${a.activity || ""}`;
    if (name === "get_profile") return "Profile and heart rate zones";
    if (name === "show_chart") return `Chart: ${a.title || ""}`;
    return name;
  }

  function resetThread() {
    transcript.length = 0;
    $("thread").replaceChildren(welcomeEl);
    welcomeEl.hidden = false;
  }
  const welcomeEl = $("welcome");

  async function ask(question) {
    question = question.trim();
    if (!question || busy) return;
    busy = true;
    updateComposer();
    welcomeEl.hidden = true;
    const thread = $("thread");
    thread.append(h("div", { class: "msg user" }, h("div", { class: "msg-who", text: "You" }), h("div", { class: "msg-text", text: question })));
    const steps = h("ul", { class: "steps" });
    const working = h("p", { class: "working", text: "Asking the model" });
    const body = h("div", { class: "body" });
    const msg = h("div", { class: "msg bot" }, h("div", { class: "msg-who", text: "Coach" }), steps, body, working);
    thread.append(msg);
    msg.scrollIntoView({ block: "end" });
    $("question").value = "";
    let pending = null;
    let answer = "";

    const handle = (ev) => {
      if (ev.type === "status") working.textContent = ev.text;
      else if (ev.type === "tool") {
        const local = ev.name === "show_chart";
        pending = h("li", {}, h("span", { class: `mark ${local ? "local" : "garmin"}` }), h("span", { text: describe(ev.name, ev.args) }));
        steps.append(pending);
        working.textContent = local ? "Drawing the chart" : "Fetching from Garmin";
      } else if (ev.type === "tool_done" && pending) {
        if (!ev.ok) pending.append(h("span", { class: "bad", text: ` (${ev.error})` }));
        else if (ev.name !== "show_chart") pending.append(h("span", { text: `: ${kb(ev.chars)} of filtered data goes to the model` }));
      } else if (ev.type === "chart") body.append(renderChart(ev.chart));
      else if (ev.type === "answer") { answer = ev.text; body.append(...markdown(ev.text)); }
      else if (ev.type === "error") body.append(h("p", { class: "error", text: ev.text }));
      else if (ev.type === "usage" && (ev.tokens_in || ev.tokens_out)) msg.append(h("p", { class: "usage", text: `${num(ev.tokens_in)} tokens in, ${num(ev.tokens_out)} tokens out` }));
      thread.scrollTop = thread.scrollHeight;
    };

    try {
      if (backend) await backend.chat(question, handle);
      else await streamChat(question, handle);
    } catch (err) {
      body.append(h("p", { class: "error", text: err.message || String(err) }));
    }
    working.remove();
    transcript.push({ question, answer });
    busy = false;
    updateComposer();
    pollLog();
    $("question").focus();
  }

  async function streamChat(question, handle) {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-Token": token },
      body: JSON.stringify({ message: question }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `Request failed (${res.status})`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, cut).trim();
        buffer = buffer.slice(cut + 1);
        if (line) handle(JSON.parse(line));
      }
    }
  }

  $("composer").onsubmit = (e) => { e.preventDefault(); ask($("question").value); };
  $("question").onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask($("question").value); }
  };
  $("new-chat").onclick = async () => { if (busy) return; await api("/api/chat/reset", {}); resetThread(); };
  $("save-chat").onclick = () => {
    if (!transcript.length) return;
    const text = transcript.map((t) => `## ${t.question}\n\n${t.answer}\n`).join("\n");
    download(`garmin-coach-${new Date().toISOString().slice(0, 10)}.md`, text, "text/markdown");
  };
  function download(name, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = h("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ── markdown (text nodes only) ────────────────────────────────────────────
  function inline(text) {
    const out = [];
    const re = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\s][^*]*\*|\[[^\]]+\]\([^)\s]+\))/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      const t = m[0];
      if (t.startsWith("**") || t.startsWith("__")) out.push(h("strong", { text: t.slice(2, -2) }));
      else if (t.startsWith("`")) out.push(h("code", { text: t.slice(1, -1) }));
      else if (t.startsWith("[")) out.push(t.slice(1, t.indexOf("]")));
      else out.push(h("em", { text: t.slice(1, -1) }));
      last = m.index + t.length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }
  const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

  function markdown(src) {
    const lines = String(src).replace(/\r/g, "").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (line.trim().startsWith("```")) {
        const code = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
        i++;
        out.push(h("pre", { text: code.join("\n") }));
        continue;
      }
      const head = line.match(/^(#{1,4})\s+(.*)$/);
      if (head) { out.push(h(head[1].length <= 2 ? "h3" : "h4", {}, inline(head[2]))); i++; continue; }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { i++; continue; }
      if (line.trim().startsWith("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const header = cells(line);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
        out.push(h("div", { class: "table-scroll" }, h("table", {},
          h("thead", {}, h("tr", {}, header.map((c) => h("th", {}, inline(c))))),
          h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, inline(c)))))))));
        continue;
      }
      const bullet = /^\s*[-*+]\s+/, ordered = /^\s*\d+[.)]\s+/;
      if (bullet.test(line) || ordered.test(line)) {
        const re = bullet.test(line) ? bullet : ordered;
        const list = h(re === bullet ? "ul" : "ol", {});
        while (i < lines.length && (re.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && list.lastChild))) {
          if (re.test(lines[i])) list.append(h("li", {}, inline(lines[i].replace(re, ""))));
          else list.lastChild.append(" ", ...inline(lines[i].trim().replace(/^[-*+]\s+/, "")));
          i++;
        }
        out.push(list);
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*[-*+]\s+|\s*\d+[.)]\s+|\s*\||```)/.test(lines[i])) para.push(lines[i++].trim());
      if (!para.length) para.push(lines[i++].trim());
      out.push(h("p", {}, inline(para.join(" "))));
    }
    return out;
  }

  // ── charts (inline SVG, drawn locally) ────────────────────────────────────
  function niceTicks(lo, hi, count) {
    if (hi === lo) { hi = lo + 1; }
    const raw = (hi - lo) / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((k) => k >= raw);
    const start = Math.floor(lo / step) * step;
    const ticks = [];
    for (let v = start; v < hi + step * 0.999; v += step) ticks.push(Number(v.toFixed(10)));
    return ticks;
  }

  function renderChart(c) {
    // Size the drawing to the column so axis text stays readable on a phone.
    const W = Math.max(320, Math.min(640, ($("thread").clientWidth || 730) - 90));
    const H = W < 480 ? 220 : 260, n = c.x_labels.length, k = c.series.length;
    const endLabels = c.kind === "line" && k > 1;
    const m = { l: 46, r: endLabels && W >= 480 ? 96 : 14, t: 10, b: 26 };
    const all = c.series.flatMap((se) => se.values.filter((v) => v != null));
    let lo = Math.min(...all), hi = Math.max(...all);
    if (c.kind === "bar") { lo = Math.min(0, lo); hi = Math.max(0, hi); }
    else {
      const pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.05 || 1;
      lo = lo >= 0 && lo - pad < 0 ? 0 : lo - pad;
      hi += pad;
    }
    const ticks = niceTicks(lo, hi, 4);
    lo = ticks[0]; hi = ticks[ticks.length - 1];
    const iw = W - m.l - m.r, ih = H - m.t - m.b, band = iw / n;
    const x = (i) => m.l + band * (i + 0.5);
    const y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;

    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": c.title });
    for (const t of ticks) {
      svg.append(s("line", { class: t === 0 || t === lo ? "axis" : "grid", x1: m.l, x2: W - m.r, y1: y(t), y2: y(t) }));
      const label = s("text", { x: m.l - 8, y: y(t) + 4, "text-anchor": "end" });
      label.textContent = num(t);
      svg.append(label);
    }
    const every = Math.max(1, Math.ceil(n / Math.floor(W / 78)));
    c.x_labels.forEach((lab, i) => {
      if (i % every) return;
      const t = s("text", { x: x(i), y: H - 8, "text-anchor": "middle" });
      t.textContent = lab.length > 12 ? lab.slice(0, 11) + "…" : lab;
      svg.append(t);
    });
    const cross = s("line", { class: "cross", y1: m.t, y2: m.t + ih });
    svg.append(cross);

    if (c.kind === "bar") {
      const group = Math.min(band * 0.76, 34 * k);
      const bw = Math.max(1, (group - 2 * (k - 1)) / k);
      c.series.forEach((se, si) => se.values.forEach((v, i) => {
        if (v == null) return;
        const x0 = x(i) - group / 2 + si * (bw + 2), y0 = y(0), y1 = y(v);
        const r = Math.min(4, bw / 2, Math.abs(y1 - y0));
        const up = y1 < y0 ? -1 : 1;
        svg.append(s("path", {
          class: `bar c${si + 1}`,
          d: `M${x0},${y0}V${y1 - up * r}Q${x0},${y1} ${x0 + r},${y1}H${x0 + bw - r}Q${x0 + bw},${y1} ${x0 + bw},${y1 - up * r}V${y0}Z`,
        }));
      }));
    } else {
      const ends = [];
      c.series.forEach((se, si) => {
        let d = "", pen = false, lastI = -1;
        se.values.forEach((v, i) => {
          if (v == null) { pen = false; return; }
          d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
          pen = true; lastI = i;
        });
        svg.append(s("path", { class: `line c${si + 1}`, d }));
        if (n <= 16) se.values.forEach((v, i) => { if (v != null) svg.append(s("circle", { class: `pt c${si + 1}`, cx: x(i), cy: y(v), r: 4 })); });
        if (endLabels && W >= 480 && lastI >= 0) ends.push({ name: se.name, x: x(lastI), y: y(se.values[lastI]) });
      });
      ends.sort((a, b) => a.y - b.y);
      for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
      for (const e of ends) {
        const t = s("text", { class: "end-label", x: e.x + 9, y: e.y + 4 });
        t.textContent = e.name.length > 14 ? e.name.slice(0, 13) + "…" : e.name;
        svg.append(t);
      }
    }

    const tip = h("div", { class: "tip" });
    const hit = s("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
    svg.append(hit);
    const move = (ev) => {
      const box = svg.getBoundingClientRect();
      const px = ((ev.clientX - box.left) * W) / box.width;
      const i = Math.max(0, Math.min(n - 1, Math.floor((px - m.l) / band)));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
      cross.style.visibility = "visible";
      tip.replaceChildren(h("b", { text: c.x_labels[i] }),
        ...c.series.map((se, si) => h("div", {}, h("span", { class: `swatch c${si + 1}` }), `${k > 1 ? se.name + ": " : ""}${num(se.values[i])}${c.y_label ? " " + c.y_label : ""}`)));
      const outer = wrap.getBoundingClientRect();
      const left = box.left - outer.left + (x(i) / W) * box.width;
      tip.style.visibility = "visible";
      tip.style.top = `${box.top - outer.top + 8}px`;
      if (x(i) > W * 0.6) { tip.style.left = ""; tip.style.right = `${wrap.clientWidth - left + 12}px`; }
      else { tip.style.right = ""; tip.style.left = `${left + 12}px`; }
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerdown", move);
    hit.addEventListener("pointerleave", () => { tip.style.visibility = "hidden"; cross.style.visibility = "hidden"; });

    const table = h("div", { class: "table-scroll", hidden: true }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "" }), c.series.map((se) => h("th", { text: se.name })))),
      h("tbody", {}, c.x_labels.map((lab, i) => h("tr", {}, h("td", { text: lab }), c.series.map((se) => h("td", { text: num(se.values[i]) })))))));
    const toggle = h("button", { type: "button", class: "link", text: "Show as table", onclick: () => {
      table.hidden = !table.hidden;
      toggle.textContent = table.hidden ? "Show as table" : "Hide table";
    } });
    const wrap = h("figure", { class: "chart" },
      h("div", { class: "chart-title", text: c.title }),
      c.y_label ? h("div", { class: "chart-unit", text: c.y_label }) : null,
      k > 1 ? h("div", { class: "legend" }, c.series.map((se, si) => h("span", {}, h("span", { class: `swatch c${si + 1}` }), se.name))) : null,
      svg, tip, h("div", { class: "chart-tools" }, toggle), table);
    return wrap;
  }

  // ── outbound log ──────────────────────────────────────────────────────────
  let lastId = 0;
  let entries = [];
  const KIND = { garmin: "Garmin (your data source)", "garmin-relay": "Garmin, through your Cloudflare Worker", model: "AI provider", "local-model": "Local model (this computer)" };

  async function pollLog() {
    if (!token && !backend) return;
    try {
      const res = await api(`/api/ledger?since=${lastId}`);
      if (res.entries.length) {
        entries.push(...res.entries);
        lastId = entries[entries.length - 1].id;
        renderLog();
      }
    } catch (e) { /* server stopped */ }
  }

  function renderLog() {
    $("log-empty").hidden = entries.length > 0;
    const totals = {};
    for (const e of entries) {
      const t = (totals[e.kind] ||= { n: 0, out: 0 });
      t.n++; t.out += e.bytes_out;
    }
    $("totals").replaceChildren(...Object.entries(totals).flatMap(([kind, t]) => [
      h("dt", {}, h("span", { class: `sq ${kind}` }), KIND[kind] || kind),
      h("dd", { text: `${t.n} request${t.n === 1 ? "" : "s"}${t.out ? ", " + kb(t.out) + " sent" : ""}` }),
    ]));
    $("entries").replaceChildren(...entries.slice(-150).reverse().map((e) => {
      const ok = typeof e.status === "number" && e.status < 400;
      return h("li", {},
        h("div", { class: "entry-head" }, h("span", { class: `sq ${e.kind}` }), h("span", { class: "entry-host", text: e.host }), h("span", { class: "entry-time", text: clock(e.ts) })),
        h("div", { class: "entry-path", text: `${e.method} ${e.path}` }),
        h("div", { class: "entry-meta" },
          e.note ? `${e.note}. ` : "",
          `Sent ${kb(e.bytes_out)}, received ${kb(e.bytes_in)}. `,
          h("span", { class: ok ? "" : "bad", text: ok ? "" : `Failed (${e.status}). ` }),
          e.has_detail ? h("button", { type: "button", class: "link", text: "See exactly what was sent", onclick: () => showDetail(e) }) : null));
    }));
  }

  async function showDetail(e) {
    const res = await api(`/api/ledger/detail?id=${e.id}`);
    $("detail-sub").textContent = `Request body sent to ${e.host} at ${clock(e.ts)}. API keys travel in request headers and are never recorded.`;
    $("detail-body").textContent = res.detail ? JSON.stringify(res.detail, null, 2) : "This request is no longer kept in memory.";
    $("detail").showModal();
  }
  $("detail-close").onclick = () => $("detail").close();
  $("save-log").onclick = () => download(`garmin-coach-log-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(entries, null, 2), "application/json");

  // ── tabs on narrow screens ────────────────────────────────────────────────
  for (const b of document.querySelectorAll(".tabs button")) {
    b.onclick = () => {
      document.querySelector(".layout").dataset.view = b.dataset.tab;
      for (const o of document.querySelectorAll(".tabs button")) o.setAttribute("aria-current", o === b ? "true" : "false");
    };
  }

  // ── start ─────────────────────────────────────────────────────────────────
  (async () => {
    const mode = detectMode();
    if (mode === "relay") {
      // Served by the owner's Cloudflare Worker, which relays Garmin requests on this address.
      backend = window.CoachCore.createBackend({ garminRelay: `${location.origin}/garmin` });
      document.body.classList.add("mode-relay");
      $("brand-where").textContent = "running in this browser tab, reaching Garmin through your Cloudflare Worker";
      $("welcome-text").textContent =
        "Your keys stay in this tab's memory and are forgotten when you close or reload it. Requests for your " +
        "Garmin data pass through the Cloudflare Worker that serves this page, which forwards them to Garmin " +
        "and stores nothing. Questions and filtered data go straight from this tab to the AI model you choose. " +
        "The log on the right shows every request.";
      $("remember-off").textContent = "Keys are held in this tab's memory and forgotten when you close or reload it.";
      $("quit").hidden = true;
    }
    if (mode === "browser") {
      // Hosted page: a demo on sample data. Garmin refuses requests from web pages,
      // so real data needs the local app; this page never asks for a Garmin key.
      backend = window.CoachCore.createBackend();
      document.body.classList.add("mode-browser");
      $("brand-where").textContent = "demo on sample data, running inside this browser tab";
      $("garmin-title").textContent = "Sample Garmin data";
      $("garmin-demo").textContent = "Load sample data";
      $("welcome-text").textContent =
        "This page is a demo on built-in sample data. Garmin does not let web pages read your account, " +
        "so to ask about your own training, run the app on your computer (three commands, on the left). " +
        "The AI part here is real: add a model key and it analyses the sample data. The key goes straight " +
        "from this tab to the provider you choose and is forgotten when you close or reload the tab.";
      $("remember-off").textContent = "Keys are held in this tab's memory and forgotten when you close or reload it.";
      $("quit").hidden = true;
      await api("/api/garmin/connect", { demo: true });
    }
    try {
      if (mode === "local-no-token") throw new Error("no token");
      const first = await api("/api/state");
      // A fresh tab starts a fresh conversation, so the server's context matches the screen.
      render(first.turns ? await api("/api/chat/reset", {}) : first);
      pollLog();
      setInterval(() => { if (!document.hidden) pollLog(); }, backend ? 1000 : 2500);
    } catch (err) {
      $("need").textContent = mode === "local-no-token"
        ? "This tab has no session token. Open the link printed in the terminal."
        : `Cannot reach the local server: ${err.message}`;
      $("ask").disabled = true;
    }
  })();
})();
