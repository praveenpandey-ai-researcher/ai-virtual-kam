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
  transcript: $("transcript"), feed: $("feed"), tooltip: $("tooltip"), mode: $("mode"), statusSub: $("status-sub"),
};

const query = new URLSearchParams(location.search);
// ?instant=1 (or the OS "reduce motion" setting) skips animations; ?demo=1 plays a scripted preview without a call.
const INSTANT = query.has("instant") || matchMedia("(prefers-reduced-motion: reduce)").matches;

let call = null;                 // the active call: { end(), meter(speaking) }
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

/** A transcript bubble whose text is updated as it streams in. */
function liveBubble(kind) {
  els.transcript.querySelector(".empty")?.remove();
  const div = el("div", `msg ${kind}`);
  div.appendChild(el("div", "who", kind === "agent" ? "Riya · Virtual KAM" : "Seller"));
  const body = div.appendChild(el("span"));
  els.transcript.appendChild(div);
  return { set: (text) => { body.textContent = text; scrollTranscript(); } };
}

// Live voice waveform: Riya's voice while she speaks, the seller's microphone while she listens.
const BARS = 30;
for (let i = 0; i < BARS; i++) els.wave.appendChild(el("span"));
let voiceLoop = false;
function startVoiceLoop() {
  if (!voiceLoop) { voiceLoop = true; requestAnimationFrame(animateVoice); }
}
function animateVoice() {
  if (!call) {
    [...els.wave.children].forEach((b) => { b.style.height = "4px"; });
    els.orb.style.removeProperty("--level");
    voiceLoop = false;
    return;
  }
  const speaking = els.orb.dataset.state === "speaking";
  let data = null, volume = 0;
  try { ({ data, volume } = call.meter(speaking)); } catch { /* fall back to a flat line */ }
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
const SVG_NS = "http://www.w3.org/2000/svg";
function icon(id, cls = "ico-svg") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  svg.appendChild(document.createElementNS(SVG_NS, "use")).setAttribute("href", `#${id}`);
  return svg;
}

/** Clears the data panel back to the welcome view (before a call) or a waiting view (call in progress). */
function resetFeed(inCall = false) {
  session++;
  diagnosis = null;
  shown = new Set();
  queue = Promise.resolve();
  focusNode = null;
  scrollTo({ top: 0 });
  const w = el("div", "card empty-state welcome");
  const hero = w.appendChild(el("div", "hero"));
  hero.appendChild(icon("i-riya", "mascot"));
  const copy = hero.appendChild(el("div", "hero-copy"));
  copy.appendChild(el("h1", "", inCall ? "I'm listening 👋" : "Hi there! 👋"));
  copy.appendChild(el("p", "", inCall
    ? "Tell me what's on your mind. I'll bring up your account data here as we talk."
    : "I'm Riya, your Snapdeal Key Account Manager. How can I help you today?"));
  w.appendChild(icon("i-box", "watermark"));
  els.feed.replaceChildren(w);
}

function transcriptPlaceholder() {
  const row = el("div", "empty");
  row.appendChild(el("span", "empty-ico")).appendChild(icon("i-wave"));
  row.appendChild(el("span", "", "The conversation will appear here."));
  els.transcript.replaceChildren(row);
}

function setCallButton(inCall) {
  els.btn.replaceChildren(icon(inCall ? "i-phone-off" : "i-phone"), el("span", "", inCall ? "End call" : "Call"));
  els.btn.className = inCall ? "btn btn-end" : "btn btn-start";
  els.btn.disabled = false;
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
  ["pricing", /\b(price|prices|pricing|discount)|प्राइस|कीमत|दाम|डिस्काउंट/i],
  ["inventory", /out of stock|stock-?outs?|ran out|inventory|supply|restock|स्टॉक|सप्लाई|इन्वेंटरी/i],
  ["cancellations", /cancel|कैंसल|कैन्सल|कैंसिल|रद्द/i],
  ["root_cause", /visibility|ranking|root cause|one (inventory )?problem|विज़िबिलिटी|विजिबिलिटी|रैंकिंग/i],
  ["action_1", /restock.*(best-?sellers?|first|पहले|सबसे)|(best-?sellers?|बेस्ट.?सेलर).*(restock|रीस्टॉक)|reorder alert|रीऑर्डर|prioriti[sz]e restocking/i],
  ["action_2", /stock counts?|pause (your )?listings?|listings? (को )?pause|small buffer|buffer|बफ़र|बफर|स्टॉक काउंट/i],
  ["action_3", /promotion|sponsored|sale event|प्रमोशन|स्पॉन्सर/i],
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
// Two voice modes behind one interface, `call = { end(), meter(speaking) }`:
//   1. ElevenLabs agent (primary): premium real-time voice over WebRTC.
//   2. Free fallback: the browser's own speech recognition + speech synthesis, with our Groq
//      agent loop on the server. Used automatically when ElevenLabs is unavailable (for example
//      credits exhausted), or forced with ?mode=free.
// The data panel, tools and transcript are shared; only the voice layer differs.
const FORCE_MODE = query.get("mode");
// Browser echo cancellation is OFF on purpose: while the page is speaking it mutes the microphone almost
// completely, so the seller could never interrupt. Echo is handled by our own threshold instead
// (see `echoPeak`). Auto gain is off too, so it cannot amplify the agent's voice from the speakers.
const MIC = { audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: false } };
const DEFAULT_HINT = els.hint.textContent;
const FREE_HINT = "Speak naturally in English or Hindi; you can talk over Riya to interrupt her. The call ends by itself when you say goodbye.";

function callStarted(modeLabel) {
  setCallButton(true);
  document.body.classList.add("in-call");   // compact call panel so the transcript has room
  els.statusSub.classList.add("hidden");
  els.mode.textContent = modeLabel;
  els.mode.classList.remove("hidden");
  addMessage("event", "Call connected");
  startVoiceLoop();
}

async function getSession(mode) {
  const res = await fetch(`/api/session${mode ? `?mode=${encodeURIComponent(mode)}` : ""}`);
  if (!res.ok) throw new Error((await res.json()).detail || "Could not start a session");
  return res.json();
}

function showStartError(err) {
  console.error(err);
  const message = err.name === "NotAllowedError"
    ? "Microphone access was blocked. Allow it in the browser and try again."
    : `Could not start the call: ${err.message}`;
  endedUI(false);
  els.hint.textContent = message;
  els.hint.classList.add("error");
}

async function startCall() {
  els.btn.disabled = true;
  els.hint.classList.remove("error");
  els.hint.textContent = DEFAULT_HINT;
  setState("connecting", "Connecting…");
  try {
    const mic = await navigator.mediaDevices.getUserMedia(MIC); // ask for mic permission up front
    const info = await getSession(FORCE_MODE);
    transcriptPlaceholder();
    resetFeed(true);
    if (info.mode === "elevenlabs") {
      mic.getTracks().forEach((t) => t.stop()); // the SDK opens its own stream
      await startElevenLabs(info);
    } else {
      await startFree(info, mic);
    }
  } catch (err) {
    showStartError(err);
  }
}

// ---------- mode 1: ElevenLabs agent
async function startElevenLabs(info) {
  let heardAgent = false, userEnded = false, dead = false;
  const conversation = await Conversation.startSession({
    conversationToken: info.conversation_token,
    connectionType: "webrtc",
    clientTools,
    onConnect: () => {
      setState("listening", "Listening");
      callStarted("Voice: ElevenLabs");
    },
    onDisconnect: async () => {
      dead = true;
      // Connected but hung up before saying anything = the agent is unavailable (e.g. out of credits).
      // Switch to the free fallback instead of leaving the seller with a dead call.
      if (!heardAgent && !userEnded) {
        call = null;
        addMessage("event", "Premium voice unavailable, switching to backup voice…");
        try {
          const mic = await navigator.mediaDevices.getUserMedia(MIC);
          await startFree(await getSession("free"), mic);
        } catch (err) { showStartError(err); }
        return;
      }
      endedUI();
    },
    onMessage: ({ source, message }) => {
      if (source === "ai") heardAgent = true;
      addMessage(source === "ai" ? "agent" : "user", message);
      if (source === "ai") showFromSpeech(message);
    },
    onModeChange: ({ mode }) => {
      if (call) setState(mode === "speaking" ? "speaking" : "listening", mode === "speaking" ? "Riya is speaking" : "Listening");
    },
    onError: (msg) => console.error("Conversation error:", msg),
  });
  if (dead) return; // already switched to the fallback (or ended)
  call = {
    end: async () => { userEnded = true; await conversation.endSession(); },
    meter: (speaking) => ({
      data: speaking ? conversation.getOutputByteFrequencyData() : conversation.getInputByteFrequencyData(),
      volume: speaking ? conversation.getOutputVolume() : conversation.getInputVolume(),
    }),
  };
  startVoiceLoop();
}

// ---------- mode 2: free fallback (browser speech + Groq agent loop on our server)
/** Picks the most natural English voice (preferring Indian English) and the best Hindi voice, if any. */
async function pickVoices() {
  let voices = speechSynthesis.getVoices();
  if (!voices.length) {
    await new Promise((r) => { speechSynthesis.addEventListener("voiceschanged", r, { once: true }); setTimeout(r, 1500); });
    voices = speechSynthesis.getVoices();
  }
  const natural = (v) => /natural|online|neural/i.test(v.name);
  const english = (v) => {
    if (/neerja/i.test(v.name)) return 100;                       // Edge: natural Indian-English female voice
    if (/en[-_]IN/i.test(v.lang)) return natural(v) ? 90 : 70;
    if (/^en/i.test(v.lang) && natural(v)) return 60;
    if (/google (uk|us) english/i.test(v.name)) return 50;
    return /^en/i.test(v.lang) ? 20 : 0;
  };
  const hindi = (v) => {
    if (!/^hi/i.test(v.lang)) return 0;
    if (/swara/i.test(v.name)) return 100;                        // Edge: natural Hindi female voice
    return natural(v) ? 90 : /google/i.test(v.name) ? 70 : 50;
  };
  const best = (score) => voices.filter((v) => score(v) > 0).sort((p, q) => score(q) - score(p))[0] || null;
  return { en: best(english), hi: best(hindi) };
}

const DEVANAGARI = /[\u0900-\u097F]/;

async function startFree(info, mic) {
  if (!window.speechSynthesis || !window.MediaRecorder) {
    mic.getTracks().forEach((t) => t.stop());
    throw new Error("this browser cannot record or speak audio. Please use a current Chrome or Edge.");
  }
  const SILENCE_MS = 1100;      // pause that ends the seller's turn
  const TICK_MS = 50;
  const history = [];           // the browser owns the conversation; the server is stateless
  const voices = await pickVoices();
  const voice = voices.en;
  // Language auto-match: Riya answers in the language the seller last used. Tracked here so the
  // lines the page says by itself (filler, "are you there", goodbye) and the voice follow along.
  let convLang = "en";
  const hindi = () => convLang === "hi" && !!voices.hi;
  let active = true, phase = "idle", endAfterSpeech = false;
  let pending = 0, streamOpen = false, sentenceBuf = "", controller = null;

  // microphone analyser: drives the waveform and our voice-activity detection
  const audioCtx = new AudioContext();
  await audioCtx.resume().catch(() => {}); // a context created after an await can start suspended (= silence)
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  audioCtx.createMediaStreamSource(mic).connect(analyser);
  const micData = new Uint8Array(analyser.frequencyBinCount);
  const timeData = new Uint8Array(analyser.fftSize);
  const fakeData = new Uint8Array(48);

  // ----- speaking: sentences are spoken as they stream in
  const utterances = [];        // keep references: Chrome can garbage-collect an utterance and never fire onend
  let speechDeadline = 0, speechWatch = null;
  function enqueue(sentence) {
    const s = sentence.trim();
    if (!s || !active) return;
    const u = new SpeechSynthesisUtterance(s);
    const v = DEVANAGARI.test(s) && voices.hi ? voices.hi : voice;   // Hindi text gets the Hindi voice
    if (v) { u.voice = v; u.lang = v.lang; }
    u.rate = v === voices.hi ? 1.0 : 1.04;
    utterances.push(u);
    pending++;
    u.onend = u.onerror = () => { pending = Math.max(0, pending - 1); afterSpeech(); };
    if (phase !== "speaking") {
      phase = "speaking";
      echoPeak = 0; speakingMs = 0; bargeMs = 0;
      setState("speaking", "Riya is speaking");
      stopListening();
    }
    speechSynthesis.speak(u);
    // Watchdog: if the browser never reports the end of speech, move on anyway instead of hanging.
    speechDeadline = Math.max(performance.now(), speechDeadline) + s.length * 95 + 1500;
    clearTimeout(speechWatch);
    speechWatch = setTimeout(() => {
      if (!active || pending === 0) return;
      console.warn("Speech end was not reported by the browser; continuing.");
      pending = 0;
      speechSynthesis.cancel();
      afterSpeech();
    }, speechDeadline - performance.now());
  }
  function feed(delta) {
    sentenceBuf += delta;
    let m;
    while ((m = sentenceBuf.match(/^([\s\S]*?[.!?।])\s+/))) { enqueue(m[1]); sentenceBuf = sentenceBuf.slice(m[0].length); }
  }
  function flush() { enqueue(sentenceBuf); sentenceBuf = ""; }
  function afterSpeech() {
    // Only relevant while Riya is speaking. Cancelling her speech (an interruption) also fires
    // "end" events; by then we are already listening and must not restart the recording.
    if (!active || phase !== "speaking" || pending > 0 || streamOpen) return;
    clearTimeout(speechWatch);
    utterances.length = 0;
    speechDeadline = 0;
    if (endAfterSpeech) { finish(); return; }
    phase = "idle";
    setTimeout(() => { if (active && phase === "idle") listen(); }, 250); // let the speaker's echo tail die down
  }

  // ----- listening: our own voice-activity detection + Whisper transcription on the server.
  // (The browser's built-in SpeechRecognition fails silently on some setups, so we do not rely on it.)
  const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
  let recorder = null, recStarted = 0;
  let noise = 0.008, hearing = false, voicedMs = 0, runMs = 0, silenceMs = 0, bargeMs = 0;
  // Interrupting by voice works automatically. The risk is Riya hearing herself through the speakers,
  // so while she talks we track how loud her own voice arrives at the microphone (`echoPeak`) and
  // only treat something clearly louder as the seller. With headphones echoPeak stays near zero.
  let echoPeak = 0, speakingMs = 0, bargedIn = false, lastAgentText = "";
  let idleMs = 0, nudges = 0;                 // silence handling: check in once, then end the call politely
  const IDLE_NUDGE_MS = 20000;

  function micLevel() { // RMS loudness, 0..1
    analyser.getByteTimeDomainData(timeData);
    let sum = 0;
    for (const v of timeData) { const x = (v - 128) / 128; sum += x * x; }
    return Math.sqrt(sum / timeData.length);
  }
  function startRecorder() {
    stopRecorder(true);
    const chunks = [];
    const r = new MediaRecorder(mic, mime ? { mimeType: mime } : undefined);
    r.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    r.chunks = chunks;
    r.start(250);
    recorder = r;
    recStarted = performance.now();
  }
  /** Stops the recorder; resolves with the audio (or null when discarded). */
  function stopRecorder(discard) {
    const r = recorder;
    recorder = null;
    if (!r) return Promise.resolve(null);
    return new Promise((resolve) => {
      r.onstop = () => resolve(discard ? null : new Blob(r.chunks, { type: r.mimeType || mime || "audio/webm" }));
      try { r.stop(); } catch { resolve(null); }
    });
  }
  function stopListening() { stopRecorder(true); hearing = false; }
  function listen(alreadyHearing = false) {
    if (!active) return;
    phase = "listening";
    hearing = alreadyHearing;
    voicedMs = alreadyHearing ? 300 : 0;
    runMs = 0; silenceMs = 0; idleMs = 0;
    setState("listening", alreadyHearing ? "Hearing you…" : "Listening");
    if (!(alreadyHearing && recorder)) startRecorder();   // an interruption keeps the clip it already started
  }
  const vad = setInterval(() => {
    if (!active) return;
    const lv = micLevel();
    const voiced = lv > Math.max(0.018, noise * 3);
    if (phase === "listening") {
      if (!hearing && !voiced) noise = noise * 0.95 + lv * 0.05;   // track the room's background noise
      if (voiced) {
        runMs += TICK_MS; voicedMs += TICK_MS; silenceMs = 0;
        if (!hearing && runMs >= 150) { hearing = true; setState("listening", "Hearing you…"); }
      } else {
        runMs = 0; silenceMs += TICK_MS;
        if (!hearing) voicedMs = 0;
      }
      if (hearing && silenceMs >= SILENCE_MS) endOfSpeech();
      else if (!hearing && performance.now() - recStarted > 12000) startRecorder(); // keep the clip short while waiting
      // nobody is talking: ask once, then say goodbye and end the call
      idleMs = hearing ? 0 : idleMs + TICK_MS;
      if (idleMs >= IDLE_NUDGE_MS) {
        idleMs = 0;
        stopListening();
        const name = info.seller_name || "";
        const line = nudges++ === 0
          ? (hindi() ? `${name} जी, क्या आप line पर हैं?` : `Are you still there, ${name}?`)
          : (hindi() ? "लगता है आप अभी busy हैं, तो मैं call यहीं समाप्त करती हूँ। आप कभी भी दोबारा call कर सकते हैं। नमस्ते!"
            : "It seems you have stepped away, so I will end the call for now. Feel free to call back any time. Goodbye!");
        if (nudges > 1) endAfterSpeech = true;
        history.push({ role: "assistant", content: line });
        liveBubble("agent").set(line);
        enqueue(line);
      }
    } else if (phase === "speaking") {
      speakingMs += TICK_MS;
      if (speakingMs <= 700) {                 // learn how loud Riya's own voice is at the microphone
        echoPeak = Math.max(echoPeak, lv);
        bargeMs = 0;
      } else {
        const over = lv > Math.max(0.045, echoPeak * 1.7 + 0.02);
        // After calibration the echo estimate may only creep up (1% per tick). A jump would let it
        // "learn" the start of the seller's own speech as echo and never trigger.
        if (!over && lv > echoPeak) echoPeak += (lv - echoPeak) * 0.01;
        // Speech has gaps between words, so count loud ticks with a slow decay instead of resetting.
        bargeMs = over ? bargeMs + TICK_MS : Math.max(0, bargeMs - TICK_MS / 2);
        // Start recording at the first loud moment so the seller's opening words are not lost;
        // drop the clip again if it turns out to be nothing.
        if (over && !recorder) startRecorder();
        else if (bargeMs === 0 && recorder) stopRecorder(true);
        if (bargeMs >= 300) { bargeMs = 0; bargedIn = true; interrupt(true); }  // the seller is talking over Riya
      }
    }
  }, TICK_MS);

  async function endOfSpeech() {
    const spokenMs = voicedMs;
    phase = "thinking";
    setState("thinking", "Got it…");
    const audio = await stopRecorder(false);
    hearing = false;
    if (!active) return;
    if (!audio || audio.size < 1200) { listen(); return; }
    let text = "";
    try {
      const res = await fetch("/api/free/transcribe", { method: "POST", body: audio,
        headers: { "Content-Type": (audio.type || "audio/webm").split(";")[0],
          "X-Lang-Hint": convLang, "X-Voiced-Ms": String(spokenMs) } });
      if (!res.ok) throw new Error(`transcription failed (${res.status})`);
      text = ((await res.json()).text || "").trim();
    } catch (err) {
      console.error(err);
      els.hint.textContent = "I could not hear that clearly. Please say it again.";
    }
    if (!active || phase !== "thinking") return;
    if (bargedIn) {
      bargedIn = false;
      const mine = new Set(lastAgentText.toLowerCase().match(/[\p{L}\p{M}']+/gu) || []);
      const words = text.toLowerCase().match(/[\p{L}\p{M}']+/gu) || [];
      const overlap = words.filter((w) => mine.has(w)).length / (words.length || 1);
      if (words.length >= 4 && overlap >= 0.75) { console.info("Ignored echo of the agent's own voice."); listen(); return; }
    }
    nudges = 0;
    // Whisper invents short phrases for noise ("Thank you."); ignore those after a very short sound.
    const junk = spokenMs < 500 && /^(thank you|thanks|thanks for watching|you|bye|okay|\.*)[.!]*$/i.test(text);
    if (!text || junk) { listen(); return; }
    els.hint.textContent = FREE_HINT;
    liveBubble("user").set(text);
    submit(text);
  }

  function interrupt(byVoice = false) {
    if (phase !== "speaking" && phase !== "thinking") return;
    controller?.abort();
    streamOpen = false;
    sentenceBuf = "";
    pending = 0;
    clearTimeout(speechWatch);
    speechSynthesis.cancel();
    listen(byVoice === true);
  }

  // ----- one seller turn: send to our agent loop, speak the streamed reply, apply tool events
  async function submit(text, attempt = 0) {
    if (!active) return;
    if (attempt === 0) convLang = DEVANAGARI.test(text) ? "hi" : "en";   // speech-to-text writes Hindi in Devanagari
    if (attempt === 0) history.push({ role: "user", content: text });
    phase = "thinking";
    setState("thinking", "Riya is thinking…");
    controller = new AbortController();
    streamOpen = true;
    let bubble = null, said = "", failed = null;
    const mySignal = controller.signal;
    try {
      const res = await fetch("/api/free/turn", { method: "POST", signal: mySignal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ history, hindi_voice: !!voices.hi }) });
      if (!res.ok) throw new Error(`server error ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.type === "text") {
            if (!bubble) bubble = liveBubble("agent");
            said += ev.delta;
            lastAgentText = said;
            bubble.set(said);
            feed(ev.delta);
          } else if (ev.type === "tool") {
            if (ev.name === "get_seller_diagnosis") {
              if (!said.trim()) {
                // The data fetch costs one extra LLM round. Fill the gap straight away so there is no dead air.
                said = hindi() ? "एक मिनट, मैं आपका account देखती हूँ। " : "Let me pull up your account. ";
                lastAgentText = said;
                bubble = bubble || liveBubble("agent");
                bubble.set(said);
                feed(said);
              }
              await clientTools.get_seller_diagnosis();
            }
            else if (ev.name === "show_call_summary") clientTools.show_call_summary(ev.args);
            else if (ev.name === "end_call") endAfterSpeech = true;
          } else if (ev.type === "done") {
            history.push(...ev.messages);
          } else if (ev.type === "error") {
            failed = ev.message;
          }
        }
      }
    } catch (err) {
      if (mySignal.aborted) {                 // interrupted by the seller: keep what was said so far
        if (said.trim()) history.push({ role: "assistant", content: said.trim() });
        return;
      }
      failed = err.message;
    }
    if (!active) return;
    if (said) showFromSpeech(said);
    streamOpen = false;
    if (failed && !said) {
      // rate limit or network blip: tell the seller, wait, try once more
      if (attempt < 2) {
        if (attempt === 0) enqueue(hindi() ? "एक मिनट दीजिए, please।" : "Give me a moment, please.");
        await new Promise((r) => setTimeout(r, 6000));
        return submit(text, attempt + 1);
      }
      enqueue(hindi() ? "Sorry, अभी connection में दिक्कत आ रही है। कृपया एक मिनट बाद फिर कोशिश करें।"
        : "Sorry, I'm having trouble connecting right now. Please try again in a minute.");
      return;
    }
    flush();
    if (phase === "thinking") {             // the reply had no speech at all (tool only)
      if (endAfterSpeech) finish(); else listen();
    } else {
      afterSpeech();
    }
  }

  function finish() {
    if (!active) return;
    active = false;
    clearInterval(vad);
    clearTimeout(speechWatch);
    stopListening();
    controller?.abort();
    speechSynthesis.cancel();
    mic.getTracks().forEach((t) => t.stop());
    audioCtx.close().catch(() => {});
    removeEventListener("keydown", onKey);
    els.orb.removeEventListener("click", onOrb);
    els.orb.classList.remove("clickable");
    endedUI();
  }
  const onKey = (e) => { if (e.code === "Space" && e.target === document.body) { e.preventDefault(); interrupt(); } };
  const onOrb = () => interrupt();
  addEventListener("keydown", onKey);
  els.orb.addEventListener("click", onOrb);
  els.orb.classList.add("clickable");

  call = {
    end: async () => { finish(); },
    meter: (speaking) => {
      if (!speaking) {
        analyser.getByteFrequencyData(micData);
        const avg = micData.reduce((a, b) => a + b, 0) / micData.length / 255;
        return { data: micData, volume: Math.min(1, avg * 2.2) };
      }
      const t = performance.now() / 1000; // no audio tap on browser speech, so draw a soft synthetic wave
      for (let i = 0; i < fakeData.length; i++) {
        fakeData[i] = Math.max(0, 150 * Math.abs(Math.sin(t * 7 + i * 0.55)) * Math.abs(Math.sin(t * 2.3 + i * 0.17)) * (1 - i / 60));
      }
      return { data: fakeData, volume: 0.35 + 0.25 * Math.abs(Math.sin(t * 5)) };
    },
  };

  callStarted(`Voice: backup mode${voice ? ` · ${voice.name.replace(/\s*-\s*English.*$/i, "").replace(/^Microsoft\s+/i, "")}` : ""}`);
  els.hint.textContent = FREE_HINT;
  if (info.reason && info.reason !== "requested") console.info("Fallback voice mode:", info.reason);

  if (query.has("debug")) window.__kam = { submit, interrupt, history, voices, state: () => ({ phase, hearing, noise, convLang, level: micLevel(), pending, echoPeak, bargeMs, speakingMs }) };

  // Riya opens the call
  lastAgentText = info.first_message;
  history.push({ role: "assistant", content: info.first_message });
  liveBubble("agent").set(info.first_message);
  enqueue(info.first_message);
}

function endedUI(showEvent = true) {
  if (call && showEvent) addMessage("event", "Call ended");
  call = null;
  setState("idle", "Ready to call");
  els.mode.classList.add("hidden");
  els.mode.replaceChildren();
  document.body.classList.remove("in-call");
  els.statusSub.classList.remove("hidden");
  els.hint.textContent = DEFAULT_HINT;
  els.hint.classList.remove("error");
  setCallButton(false);
}

els.btn.addEventListener("click", async () => {
  if (call) {
    els.btn.disabled = true;
    const ending = call;
    await ending.end();
    if (call === ending) endedUI();
  } else {
    startCall();
  }
});

// ---------------------------------------------------------------- page load: nothing is shown until a call starts
resetFeed(false);
transcriptPlaceholder();
setCallButton(false);

// Scripted preview without a call or credits: http://localhost:8000/?demo=1
if (query.has("demo")) {
  const say = async (who, text, topics = []) => {
    addMessage(who, text);
    topics.forEach((t) => show(t));
    await queue;
    await sleep(1400);
  };
  const d = await (await fetch("/api/diagnosis")).json();
  resetFeed(true);
  document.body.classList.add("in-call");
  els.statusSub.classList.add("hidden");
  els.status.textContent = "Preview (no call)";
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
