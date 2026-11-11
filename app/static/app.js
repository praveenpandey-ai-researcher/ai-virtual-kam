// Browser side of the voice KAM.
// The ElevenLabs SDK handles mic capture, streaming audio, turn-taking and playback (WebRTC).
// Our code: get a token from our backend, implement the client tools, render the UI.
//
// The right-hand side is a FEED that starts empty. A section appears only when the conversation
// reaches that topic: the agent pushes it with the `show_on_screen` tool, and as a safety net we
// also watch the agent's words. Every number comes from /api/diagnosis (the Python engine).
import { Conversation } from "https://cdn.jsdelivr.net/npm/@elevenlabs/client@1.35.0/+esm";

const $ = (id) => document.getElementById(id);
const els = {
  btn: $("call-btn"), status: $("status"), orb: $("orb"), wave: $("wave"), hint: $("hint"),
  transcript: $("transcript"), feed: $("feed"), tooltip: $("tooltip"),
};

const query = new URLSearchParams(location.search);
// ?instant=1 (or the OS "reduce motion" setting) skips animations; ?demo=1 plays a scripted preview without a call.
const INSTANT = query.has("instant") || matchMedia("(prefers-reduced-motion: reduce)").matches;

let conversation = null;
let diagnosis = null;            // set when the agent fetches the data
let shown = new Set();           // sections already on screen
let queue = Promise.resolve();   // sections animate one after another, never on top of each other
let session = 0;                 // bumping this cancels animations from a previous call

// ---------------------------------------------------------------- small helpers
const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const pct = (x) => `${Math.round(x * 100)}%`;
const int = (n) => Math.round(n).toString();
const sleep = (ms) => (INSTANT ? Promise.resolve() : new Promise((r) => setTimeout(r, ms)));
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Typewriter: reveals `text` inside `node` character by character, like a streaming LLM answer. */
async function type(node, text, cps = 170) {
  const mine = session;
  if (INSTANT) { node.textContent = text; return; }
  node.textContent = "";
  node.classList.add("typing");
  const start = performance.now();
  let n = 0;
  while (n < text.length && mine === session) {
    await new Promise((r) => requestAnimationFrame(r));
    const target = Math.min(text.length, Math.ceil(((performance.now() - start) / 1000) * cps));
    if (target > n) { node.textContent = text.slice(0, target); n = target; }
  }
  node.textContent = text;
  node.classList.remove("typing");
}

function countUp(node, to, fmt, ms = 900) {
  if (INSTANT) { node.textContent = fmt(to); return; }
  const start = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - start) / ms);
    node.textContent = fmt(to * (1 - Math.pow(1 - t, 3)));
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function attachTooltip(node, text) {
  node.addEventListener("mousemove", (e) => {
    els.tooltip.textContent = text;
    els.tooltip.classList.remove("hidden");
    els.tooltip.style.left = `${Math.min(e.clientX + 12, innerWidth - 280)}px`;
    els.tooltip.style.top = `${e.clientY + 14}px`;
  });
  node.addEventListener("mouseleave", () => els.tooltip.classList.add("hidden"));
}

// ---------------------------------------------------------------- call UI
function setState(state, label) {
  els.orb.dataset.state = state;
  els.wave.dataset.state = state;
  els.status.textContent = label;
}

const scrollTranscript = () => { els.transcript.scrollTop = els.transcript.scrollHeight; };
function addMessage(kind, text) {
  els.transcript.querySelector(".empty")?.remove();
  const div = el("div", `msg ${kind}`);
  if (kind === "event") {
    div.textContent = text;
  } else {
    div.appendChild(el("div", "who", kind === "agent" ? "Riya · Virtual KAM" : "Seller"));
    const body = div.appendChild(el("span"));
    if (kind === "agent") type(body, text, 55).then(scrollTranscript);
    else body.textContent = text;
  }
  els.transcript.appendChild(div);
  scrollTranscript();
}

// Live voice waveform: Riya's voice while she speaks, the seller's microphone while she listens.
const BARS = 30;
for (let i = 0; i < BARS; i++) els.wave.appendChild(el("span"));
function animateVoice() {
  if (!conversation) {
    [...els.wave.children].forEach((b) => { b.style.height = "4px"; });
    els.orb.style.removeProperty("--level");
    return;
  }
  const speaking = els.orb.dataset.state === "speaking";
  let data = null, volume = 0;
  try {
    data = speaking ? conversation.getOutputByteFrequencyData() : conversation.getInputByteFrequencyData();
    volume = speaking ? conversation.getOutputVolume() : conversation.getInputVolume();
  } catch { /* not available on every connection type; fall back to a flat line */ }
  const bars = els.wave.children;
  for (let i = 0; i < BARS; i++) {
    const k = Math.abs(i - (BARS - 1) / 2) / ((BARS - 1) / 2); // mirror around the centre
    let level = 0;
    if (data && data.length) level = data[Math.floor((1 - k) * Math.min(data.length - 1, 40))] / 255;
    else level = volume * (1 - k * 0.7);
    bars[i].style.height = `${4 + Math.round(level * 30)}px`;
  }
  els.orb.style.setProperty("--level", (1 + Math.min(volume, 1) * 0.18).toFixed(3));
  requestAnimationFrame(animateVoice);
}

// ---------------------------------------------------------------- feed building blocks
function resetFeed(message) {
  session++;
  diagnosis = null;
  shown = new Set();
  queue = Promise.resolve();
  focusNode = null;
  scrollTo({ top: 0 });
  const empty = el("div", "card empty-state");
  empty.appendChild(el("div", "empty-icon", "◎"));
  empty.appendChild(el("p", "", message));
  els.feed.replaceChildren(empty);
}

// Auto-follow: while content is being revealed, glide the page so the newest line stays in view.
// `focusNode` is whatever is growing right now (the latest card, or the latest action in the plan).
let focusNode = null;
let revealing = 0;          // number of sections queued or animating
let followLoop = false;
let userScrolledAt = 0;     // manual scrolling pauses auto-follow briefly
for (const evt of ["wheel", "touchmove", "keydown"]) {
  addEventListener(evt, () => { userScrolledAt = performance.now(); }, { passive: true });
}
function follow() {
  const idleSince = performance.now() - userScrolledAt;
  if (focusNode?.isConnected && idleSince > 3000) {
    const rect = focusNode.getBoundingClientRect();
    const margin = 28;
    let delta = 0;
    if (rect.bottom > innerHeight - margin) delta = rect.bottom - (innerHeight - margin);  // growing past the bottom
    else if (rect.top < margin && rect.height < innerHeight - 2 * margin) delta = rect.top - margin; // above the fold
    // taller than the screen: keep its bottom (where new text appears) in view, never scroll past its top needlessly
    if (Math.abs(delta) > 1) scrollBy(0, INSTANT ? delta : delta * 0.14);
  }
  if (revealing > 0) requestAnimationFrame(follow);
  else followLoop = false;
}
function startFollowing() {
  if (!followLoop) { followLoop = true; requestAnimationFrame(follow); }
}

/** Adds a card to the feed; auto-follow keeps it in view while it fills in. */
function card(title, badge, extraCls = "") {
  els.feed.querySelector(".empty-state")?.remove();
  const c = el("div", `card reveal ${extraCls}`);
  const h = c.appendChild(el("h2", "section-title", title));
  if (badge) h.appendChild(el("span", "badge live", badge));
  els.feed.appendChild(c);
  focusNode = c;
  return c;
}

/** KPI tile: big number that counts up, change badge, last-vs-now bars. */
function tile({ label, last, now, fmt, delta, change, upIsGood }) {
  const t = el("div", "tile");
  t.appendChild(el("div", "tile-label", label));
  countUp(t.appendChild(el("div", "tile-value", fmt(0))), now, fmt);
  const foot = t.appendChild(el("div", "tile-foot"));
  foot.appendChild(el("span", "", `was ${fmt(last)}`));
  const cls = change === 0 ? "flat" : (change > 0) === upIsGood ? "good" : "bad";
  const d = foot.appendChild(el("span", `delta ${cls}`));
  d.appendChild(el("span", "ico", change === 0 ? "●" : change > 0 ? "▲" : "▼"));
  d.appendChild(el("span", "", delta));
  const pair = t.appendChild(el("div", "pair"));
  const max = Math.max(last, now) || 1;
  for (const [name, v, kind] of [["Last", last, "last"], ["Now", now, "now"]]) {
    const row = pair.appendChild(el("div", "pair-row"));
    row.appendChild(el("span", "", name));
    const bar = row.appendChild(el("div", "pair-track")).appendChild(el("div", `pair-bar ${kind}`));
    attachTooltip(row, `${label} · ${name === "Last" ? "last month" : "this month"}: ${fmt(v)}`);
    nextFrame().then(() => { bar.style.width = `${Math.max(3, (v / max) * 100)}%`; });
  }
  return t;
}

const signed = (n, unit) => (n === 0 ? "no change" : `${n > 0 ? "+" : "−"}${Math.abs(n)}${unit}`);
const finding = (id) => diagnosis.findings.find((f) => f.id === id);

async function typeList(parent, lines, cps = 240) {
  const ul = parent.appendChild(el("ul", "points"));
  for (const line of lines) await type(ul.appendChild(el("li")), line, cps);
}

function severityPills(parent, f) {
  const row = parent.appendChild(el("div", "pill-row reveal"));
  const sev = row.appendChild(el("span", `pill ${f.severity}`));
  sev.appendChild(el("span", "ico", f.severity === "high" ? "▲" : "◆"));
  sev.appendChild(el("span", "", `${f.severity} severity · ${f.confidence} confidence`));
  if (f.estimated_monthly_impact_inr) row.appendChild(el("span", "pill impact", `≈ ${inr(f.estimated_monthly_impact_inr)} / month`));
}

// ---------------------------------------------------------------- sections (each appears only when discussed)
const SECTIONS = {
  // Shown automatically once the agent has pulled the data.
  async analysing() {
    const c = card("Riya is reviewing your account", null, "scan-card");
    const list = c.appendChild(el("ul", "scan-steps"));
    const steps = ["Reading sales and orders", "Comparing prices with last month", "Checking inventory and stock-outs",
      "Reviewing cancellations", "Estimating impact and next actions"].map((s) => {
      const li = list.appendChild(el("li"));
      li.appendChild(el("span", "scan-ico"));
      li.appendChild(el("span", "", s));
      return li;
    });
    c.appendChild(el("div", "scan-bar"));
    for (const li of steps) {
      li.classList.add("active");
      await sleep(380);
      li.classList.replace("active", "done");
    }
    await sleep(200);
    c.remove();
  },

  async sales_drop() {
    const { last_month: a, this_month: b } = diagnosis.metrics, g = diagnosis.derived;
    const c = card("Sales overview", "this month vs last month");
    await type(c.appendChild(el("p", "headline")), `Sales fell ${Math.abs(g.sales_change_pct)}% this month, a drop of ${inr(g.sales_lost)}.`, 60);
    const tiles = c.appendChild(el("div", "tiles two"));
    tiles.appendChild(tile({ label: "Sales", last: a.sales, now: b.sales, fmt: inr, delta: signed(g.sales_change_pct, "%"), change: g.sales_change_pct, upIsGood: true }));
    tiles.appendChild(tile({ label: "Orders", last: a.orders, now: b.orders, fmt: int, delta: signed(g.orders_change_pct, "%"), change: g.orders_change_pct, upIsGood: true }));
    await sleep(700);
  },

  async pricing() {
    const { last_month: a, this_month: b } = diagnosis.metrics, g = diagnosis.derived;
    const c = card("Pricing", "checked");
    const split = c.appendChild(el("div", "split"));
    split.appendChild(tile({ label: "Avg selling price", last: a.average_selling_price, now: b.average_selling_price, fmt: inr, delta: signed(g.asp_change_pct, "%"), change: g.asp_change_pct, upIsGood: true }));
    const body = split.appendChild(el("div"));
    const r = diagnosis.ruled_out.find((x) => x.id === "pricing");
    if (r) {
      const box = body.appendChild(el("div", "ruled-out"));
      box.appendChild(el("span", "ico", "✓"));
      await type(box.appendChild(el("span")), `${r.title}. ${r.evidence}`, 200);
      await type(body.appendChild(el("p", "note")), r.implication, 200);
    } else if (finding("pricing")) {
      await typeList(body, finding("pricing").evidence);
    }
  },

  async inventory() {
    const { last_month: a, this_month: b } = diagnosis.metrics;
    const f = finding("inventory");
    const c = card("Inventory", f ? "main cause" : "checked");
    const split = c.appendChild(el("div", "split"));
    const chg = b.out_of_stock_products - a.out_of_stock_products;
    split.appendChild(tile({ label: "Out-of-stock products", last: a.out_of_stock_products, now: b.out_of_stock_products, fmt: int, delta: signed(chg, ""), change: chg, upIsGood: false }));
    const body = split.appendChild(el("div"));
    if (!f) { await type(body.appendChild(el("p", "note")), "No unusual change in stock-outs this month.", 200); return; }
    await type(body.appendChild(el("div", "finding-title")), f.title, 120);
    severityPills(body, f);
    await typeList(body, f.evidence.slice(0, 3));

    // Best-sellers that ran out: one bar per product, on a shared scale
    if (f.top_products?.length) {
      c.appendChild(el("div", "sub-title reveal", "Best-sellers that ran out (orders last month)"));
      const chart = c.appendChild(el("div", "hbars reveal"));
      const max = Math.max(...f.top_products.map((p) => p.orders_last_month));
      for (const p of f.top_products) {
        const row = chart.appendChild(el("div", "hbar-row"));
        row.appendChild(el("span", "hbar-label", p.product));
        const bar = row.appendChild(el("div", "hbar-track")).appendChild(el("div", "hbar"));
        row.appendChild(el("span", "hbar-value", String(p.orders_last_month)));
        attachTooltip(row, `${p.product}: ${p.orders_last_month} orders last month, now out of stock`);
        await nextFrame();
        bar.style.width = `${(p.orders_last_month / max) * 100}%`;
        await sleep(120);
      }
    }
  },

  async cancellations() {
    const { last_month: a, this_month: b } = diagnosis.metrics;
    const f = finding("cancellations");
    const c = card("Cancellations", f ? "needs attention" : "checked");
    const split = c.appendChild(el("div", "split"));
    const pts = Math.round((b.cancellation_rate - a.cancellation_rate) * 100);
    split.appendChild(tile({ label: "Cancellation rate", last: a.cancellation_rate, now: b.cancellation_rate, fmt: pct, delta: signed(pts, " pts"), change: pts, upIsGood: false }));
    const body = split.appendChild(el("div"));
    if (!f) { await type(body.appendChild(el("p", "note")), "Cancellations are in line with last month.", 200); return; }
    await type(body.appendChild(el("div", "finding-title")), f.title, 120);
    severityPills(body, f);
    await typeList(body, [...f.evidence, f.explanation]);
  },

  async root_cause() {
    const { last_month: a, this_month: b } = diagnosis.metrics, g = diagnosis.derived, br = diagnosis.revenue_bridge;
    const c = card("How it all connects", "root cause");
    await type(c.appendChild(el("p", "headline")), diagnosis.headline, 60);

    // Cause chain
    const addRow = async (nodes) => {
      const row = c.appendChild(el("div", "chain"));
      for (const [i, n] of nodes.entries()) {
        if (i) row.appendChild(el("span", "arrow reveal", "→"));
        const node = row.appendChild(el("div", `node reveal ${n.cls || ""}`));
        node.appendChild(el("span", "", n.label));
        node.appendChild(el("b", "", n.value));
        await sleep(260);
      }
    };
    if (finding("inventory")) {
      const main = [{ label: "Out-of-stock products", value: `${a.out_of_stock_products} → ${b.out_of_stock_products}`, cls: "root" }];
      if (g.live_products_last) main.push({ label: "Live products", value: `${g.live_products_last} → ${g.live_products_now}` });
      main.push({ label: "Orders", value: `${a.orders} → ${b.orders}` }, { label: "Sales", value: `${inr(a.sales)} → ${inr(b.sales)}` });
      await addRow(main);
    }
    if (finding("cancellations")) {
      const side = [{ label: "Orders taken without stock", value: "seller cancels", cls: "root" },
        { label: "Cancellation rate", value: `${pct(a.cancellation_rate)} → ${pct(b.cancellation_rate)}` }];
      if (finding("visibility")) side.push({ label: "Search visibility", value: "likely lower (inferred)", cls: "inferred" });
      await addRow(side);
    }

    // Where last month's sales went: one bar, three parts
    if (br.lost_to_stock_outs + br.lost_other > 0) {
      c.appendChild(el("div", "sub-title reveal", `Where last month's ${inr(br.last_month_sales)} went`));
      const bar = c.appendChild(el("div", "bridge"));
      const legend = c.appendChild(el("div", "legend reveal"));
      const segs = [["kept", "Still earned this month", br.this_month_sales], ["stock", "Lost to stock-outs", br.lost_to_stock_outs],
        ["other", "Other, likely lower visibility", br.lost_other]].filter(([, , v]) => v > 0).map(([kind, label, v]) => {
        const seg = bar.appendChild(el("div", `seg ${kind}`));
        attachTooltip(seg, `${label}: ${inr(v)} (${Math.round((v / br.last_month_sales) * 100)}% of last month's sales)`);
        const item = legend.appendChild(el("span", "legend-item"));
        item.appendChild(el("span", `swatch seg ${kind}`));
        item.appendChild(el("span", "", label));
        item.appendChild(el("b", "", inr(v)));
        return [seg, v];
      });
      await nextFrame();
      segs.forEach(([seg, v]) => { seg.style.flexBasis = `${(v / br.last_month_sales) * 100}%`; });
      await sleep(900);
    }
  },

  action_1: () => action(0),
  action_2: () => action(1),
  action_3: () => action(2),
};

/** Action cards collect in one "Action plan" card, each added when Riya recommends it. */
async function action(i) {
  const r = diagnosis.recommendations[i];
  if (!r) return;
  let plan = $("plan");
  if (!plan) {
    plan = card("Action plan", "recommended");
    plan.id = "plan";
    plan.appendChild(el("div", "actions"));
  }
  const item = plan.querySelector(".actions").appendChild(el("div", "action reveal"));
  focusNode = plan; // the plan may no longer be the last card; follow it while this action is added
  item.appendChild(el("div", "action-num", String(i + 1)));
  const body = item.appendChild(el("div"));
  await type(body.appendChild(el("div", "action-text")), r.action, 200);
  if (r.priority_products?.length) body.appendChild(el("div", "products reveal", `Start with: ${r.priority_products.join(", ")}`));
  const meta = body.appendChild(el("div", "action-meta reveal"));
  const when = meta.appendChild(el("span", "chip"));
  when.appendChild(el("span", "ico", "⏱"));
  when.appendChild(el("span", "", r.timeframe));
  const gain = meta.appendChild(el("span", "chip gain"));
  gain.appendChild(el("span", "ico", "▲"));
  gain.appendChild(el("span", "", r.expected_monthly_impact_inr ? `≈ ${inr(r.expected_monthly_impact_inr)} / month` : r.expected_impact_spoken));
}

async function summary({ key_issue, recommendations = [], next_steps = [] }) {
  const c = card("Call summary", "agreed on this call", "summary-card");
  c.appendChild(el("h3", "", "Key issue"));
  await type(c.appendChild(el("p")), key_issue, 140);
  for (const [title, items, cls, icon] of [["Agreed actions", recommendations, "", "✓"], ["Next steps", next_steps, "next", "→"]]) {
    c.appendChild(el("h3", "reveal", title));
    const ul = c.appendChild(el("ul", `checklist ${cls}`));
    for (const item of items) {
      const li = ul.appendChild(el("li", "reveal"));
      li.appendChild(el("span", "ico", icon));
      await type(li.appendChild(el("span")), item, 200);
    }
  }
}

/** Queue a section. Idempotent: each section appears once, in the order it was first discussed. */
function show(id, run = SECTIONS[id]) {
  if (!run || shown.has(id)) return;
  shown.add(id);
  const mine = session;
  revealing++;
  startFollowing();
  queue = queue
    .then(() => (mine === session && diagnosis ? run() : null))
    .catch(console.error)
    // keep following a little longer so the last glide and bar animations settle (does not delay the next section)
    .finally(() => { setTimeout(() => { revealing--; }, 900); });
}

// Safety net: if the agent talks about a topic without calling show_on_screen, show it anyway.
const KEYWORDS = [
  ["pricing", /\b(price|prices|pricing|discount)/i],
  ["inventory", /out of stock|stock-?outs?|ran out|inventory|supply|restock/i],
  ["cancellations", /cancel/i],
  ["root_cause", /visibility|ranking|root cause|one (inventory )?problem/i],
  ["action_1", /restock.*(best-?sellers|first)|reorder alert|prioriti[sz]e restocking/i],
  ["action_2", /stock counts|pause (your )?listings|small buffer/i],
  ["action_3", /promotion|sponsored|sale event/i],
];
function showFromSpeech(text) {
  if (!diagnosis) return;
  for (const [id, re] of KEYWORDS) if (re.test(text)) show(id);
}

// ---------------------------------------------------------------- client tools (called by the agent)
const clientTools = {
  get_seller_diagnosis: async () => {
    addMessage("event", "Riya is reviewing the account data…");
    const d = await (await fetch("/api/diagnosis")).json();
    diagnosis = d;
    show("analysing");   // animations run on their own;
    show("sales_drop");  // the agent gets the data immediately (no added latency)
    return d;
  },
  show_on_screen: async ({ topics = [] }) => {
    (Array.isArray(topics) ? topics : [topics]).forEach((t) => show(t));
    return "Shown.";
  },
  show_call_summary: async (params) => {
    show("summary", () => summary(params));
    return "The summary is now displayed on the seller's screen.";
  },
};

// ---------------------------------------------------------------- call control
async function startCall() {
  els.btn.disabled = true;
  els.hint.classList.remove("error");
  setState("connecting", "Connecting…");
  try {
    await navigator.mediaDevices.getUserMedia({ audio: true }); // ask for mic permission up front
    const res = await fetch("/api/session");
    if (!res.ok) throw new Error((await res.json()).detail || "Could not start a session");
    const { conversation_token } = await res.json();

    els.transcript.replaceChildren();
    resetFeed("Call in progress. Riya will bring up your data here as you talk.");

    conversation = await Conversation.startSession({
      conversationToken: conversation_token,
      connectionType: "webrtc",
      clientTools,
      onConnect: () => {
        setState("listening", "Listening");
        els.btn.textContent = "End call";
        els.btn.className = "btn btn-end";
        els.btn.disabled = false;
        addMessage("event", "Call connected");
      },
      onDisconnect: () => endedUI(),
      onMessage: ({ source, message }) => {
        addMessage(source === "ai" ? "agent" : "user", message);
        if (source === "ai") showFromSpeech(message);
      },
      onModeChange: ({ mode }) => {
        if (conversation) setState(mode === "speaking" ? "speaking" : "listening", mode === "speaking" ? "Riya is speaking" : "Listening");
      },
      onError: (msg) => console.error("Conversation error:", msg),
    });
    requestAnimationFrame(animateVoice);
  } catch (err) {
    console.error(err);
    els.hint.textContent = err.name === "NotAllowedError"
      ? "Microphone access was blocked. Allow it in the browser and try again."
      : `Could not start the call: ${err.message}`;
    els.hint.classList.add("error");
    endedUI(false);
  }
}

function endedUI(showEvent = true) {
  if (conversation && showEvent) addMessage("event", "Call ended");
  conversation = null;
  setState("idle", "Ready to call");
  els.btn.textContent = "Start call";
  els.btn.className = "btn btn-start";
  els.btn.disabled = false;
}

els.btn.addEventListener("click", async () => {
  if (conversation) {
    els.btn.disabled = true;
    await conversation.endSession();
    endedUI();
  } else {
    startCall();
  }
});

// ---------------------------------------------------------------- page load: nothing is shown until a call starts
resetFeed("Nothing to show yet. Start the call and Riya will bring up your data here as you talk.");

// Scripted preview without a call or credits: http://localhost:8000/?demo=1
if (query.has("demo")) {
  const say = async (who, text, topics = []) => {
    addMessage(who, text);
    topics.forEach((t) => show(t));
    await queue;
    await sleep(1400);
  };
  const d = await (await fetch("/api/diagnosis")).json();
  resetFeed("Call in progress. Riya will bring up your data here as you talk.");
  await say("agent", `Hi ${d.seller.contact_name}, this is Riya, your Snapdeal account manager. How can I help you today?`);
  await say("user", "My sales have dropped significantly this month. I don't understand why. Can you help me?");
  diagnosis = d;
  await say("agent", "Let me pull up your account.", ["analysing", "sales_drop"]);
  await say("agent", "Your orders fell from four hundred to two seventy, but your price is unchanged, so pricing isn't the problem.", ["pricing"]);
  await say("agent", "What stands out is that eighteen products are out of stock, up from five. Did you face a supply problem?", ["inventory"]);
  await say("user", "Yes, my supplier delayed two shipments.");
  await say("agent", "Your cancellations also rose from eight to fourteen percent. Were those mostly orders you couldn't fulfil?", ["cancellations"]);
  await say("user", "Yes, I had to cancel them because the stock wasn't there.");
  await say("agent", "So one inventory problem is causing fewer orders, more cancellations and likely lower visibility.", ["root_cause"]);
  await say("agent", "First, restock your best-sellers within two to three days.", ["action_1"]);
  await say("agent", "Second, update your stock counts daily and pause listings at zero stock.", ["action_2"]);
  await say("agent", "Third, once restocked, run a short promotion to rebuild visibility.", ["action_3"]);
  show("summary", () => summary({
    key_issue: "Sales fell 32.5% (₹65,000) mainly because 13 more products went out of stock, which also pushed cancellations from 8% to 14%.",
    recommendations: d.recommendations.map((r) => `${r.action} (${r.timeframe.toLowerCase()})`),
    next_steps: ["Restock the three best-sellers within 48-72 hours.", "Update stock counts daily from this week.", "Follow-up call in 7 days to review orders, cancellations and stock."],
  }));
}
