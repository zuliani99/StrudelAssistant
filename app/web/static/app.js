/* Strudel Assistant - transport, pattern panels, chat.
 *
 * @strudel/web is loaded as a plain global by index.html. initStrudel()
 * resolves to the repl, whose scheduler.now() reports the current CYCLE
 * position - that is what drives the lane in the rail, so the sweep is the
 * real clock rather than an animation at a guessed tempo.
 */

const $ = (id) => document.getElementById(id);

const feed = $("feed");
const led = $("led");
const lane = $("lane");
const laneTicks = $("laneTicks");
const laneHead = $("laneHead");
const readout = $("readout");
const stopBtn = $("stopAll");
const composer = $("composer");
const input = $("input");
const sendBtn = $("send");

const editorPanel = $("editorPanel");
const editorInput = $("editorInput");
const editorHl = $("editorHl").querySelector("code");
const editorRun = $("editorRun");
const editorStop = $("editorStop");
const editorLamp = $("editorLamp");
const editorHint = $("editorHint");
const editorToggle = $("editorToggle");
const editorClose = $("editorClose");
const editorBackdrop = $("editorBackdrop");

const STEPS = 16; // 4 beats of 4 - the subdivision the lane is marked in
const audioAvailable = typeof window.initStrudel === "function";

/* ============================== transport ============================== */

let repl = null;
let initing = null;
let playing = false;
let pending = false;
let activeBtn = null;
let laneWidth = 0;

for (let i = 0; i < STEPS; i++) {
  const tick = document.createElement("span");
  tick.className = "tick";
  if (i % 4 === 0) tick.dataset.beat = "1";
  laneTicks.appendChild(tick);
}

const measureLane = () => { laneWidth = lane.clientWidth; };
measureLane();
window.addEventListener("resize", measureLane);

/* initStrudel()'s default prebake only registers the synths - the drum banks
   every example pattern uses (bd, sd, hh) come from dirt-samples, so they have
   to be loaded explicitly or playback fails with "sound bd not found". This is
   the download that makes the first Play take a few seconds. */
async function ensureStrudel() {
  if (repl) return repl;
  if (!initing) {
    initing = Promise.resolve(
      window.initStrudel({
        prebake: () => {
          const samples = window.strudel && window.strudel.samples;
          return samples ? samples("github:tidalcycles/dirt-samples") : undefined;
        },
      })
    );
  }
  repl = await initing;
  return repl;
}

function refreshLed() {
  led.dataset.state = playing ? "live" : pending ? "busy" : "idle";
}

function setTransport(live) {
  playing = live;
  lane.dataset.state = live ? "live" : "idle";
  readout.dataset.state = live ? "live" : "idle";
  stopBtn.disabled = !live;
  editorStop.disabled = !live;
  if (!live) readout.textContent = "cps —";
  refreshLed();
}

/* One engine, so only one thing is ever the source. A pattern panel shows that
   by flipping its button to Stop; the editor shows it with its lamp, because
   Run has to stay Run - you re-run the buffer to swap the pattern live. */
function setActive(btn) {
  if (activeBtn && activeBtn !== btn) {
    if (activeBtn === editorRun) editorLamp.dataset.state = "idle";
    else {
      activeBtn.dataset.state = "idle";
      activeBtn.textContent = "Play";
    }
  }
  activeBtn = btn;
  if (!btn) {
    editorLamp.dataset.state = "idle";
    return;
  }
  if (btn === editorRun) {
    editorLamp.dataset.state = "live";
  } else {
    btn.dataset.state = "live";
    btn.textContent = "Stop";
  }
}

function cyclePosition() {
  try {
    if (repl && repl.scheduler && typeof repl.scheduler.now === "function") {
      return repl.scheduler.now();
    }
    if (window.strudel && typeof window.strudel.getTime === "function") {
      return window.strudel.getTime();
    }
  } catch (_) { /* clock not up yet */ }
  return 0;
}

function currentCps() {
  const cps = repl && repl.scheduler ? repl.scheduler.cps : null;
  return typeof cps === "number" && isFinite(cps) ? cps : null;
}

/* Strudel reports parse and scheduler failures as `strudel.log` events rather
   than by rejecting evaluate(), so a bad pattern would otherwise light the
   transport and play nothing. Show what went wrong instead. */
let errorUntil = 0;

document.addEventListener("strudel.log", (ev) => {
  const detail = ev.detail || {};
  const message = String(detail.message || "");
  if (detail.type === "error" || /\berror\b/i.test(message)) {
    readout.dataset.state = "error";
    readout.textContent = message.length > 46 ? message.slice(0, 46) + "…" : message;
    errorUntil = performance.now() + 5000;
    if (activeBtn) note(activeBtn, "Error");
  }
});

function frame() {
  const now = performance.now();
  if (errorUntil && now >= errorUntil) {
    errorUntil = 0;
    readout.dataset.state = playing ? "live" : "idle";
  }
  if (playing && !errorUntil) {
    const cycle = cyclePosition();
    const phase = ((cycle % 1) + 1) % 1;       // 0..1 through the current cycle
    laneHead.style.transform = `translateX(${phase * laneWidth}px)`;

    const cps = currentCps();
    readout.textContent = cps ? `cyc ${Math.floor(cycle)} · cps ${cps.toFixed(2)}` : `cyc ${Math.floor(cycle)}`;
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

async function playPattern(code, btn) {
  const isEditor = btn === editorRun;
  const label = isEditor ? "Run" : "Play";
  btn.textContent = "Loading";
  btn.disabled = true;
  try {
    const r = await ensureStrudel();
    await r.evaluate(code, true);   // while playing this hot-swaps the pattern
    btn.disabled = false;
    if (isEditor) btn.textContent = label;
    setActive(btn);
    measureLane();
    setTransport(true);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = label;
    console.error(err);
    note(btn, "Won't play");
  }
}

function stopAll() {
  try {
    if (window.strudel && typeof window.strudel.hush === "function") window.strudel.hush();
    else if (repl) repl.stop();
  } catch (err) { console.error(err); }
  setActive(null);
  setTransport(false);
}

stopBtn.addEventListener("click", stopAll);

/* Transient label on a button, then back to what it said. */
function note(btn, text) {
  const was = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { if (btn.textContent === text) btn.textContent = was; }, 1400);
}

/* ========================== pattern highlighting ======================== */

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const esc = (s) => s.replace(/[&<>"]/g, (c) => ESCAPES[c]);

const MINI = "~*<>[],/!@?"; // Strudel mini-notation operators

/* Strings hold the mini-notation, so they carry the accent colour; the
   operators inside them are dimmed so the sound names stay legible. */
function highlightString(raw) {
  let out = "";
  let buf = "";
  for (const ch of raw) {
    if (MINI.includes(ch)) {
      if (buf) { out += esc(buf); buf = ""; }
      out += `<span class="t-mini">${esc(ch)}</span>`;
    } else {
      buf += ch;
    }
  }
  return out + esc(buf);
}

const TOKEN = new RegExp(
  [
    "(\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/)",                             // comment
    "(\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'|`(?:[^`\\\\]|\\\\.)*`)", // string
    "(\\b\\d+(?:\\.\\d+)?\\b)",                                           // number
    "([A-Za-z_$][\\w$]*)(?=\\s*\\()",                                     // callable
    "([(){}\\[\\].,;:+\\-*/=<>|&!?])",                                    // punctuation
  ].join("|"),
  "g"
);

function highlight(code) {
  let out = "";
  let last = 0;
  let m;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(code)) !== null) {
    out += esc(code.slice(last, m.index));
    if (m[1]) out += `<span class="t-com">${esc(m[1])}</span>`;
    else if (m[2]) out += `<span class="t-str">${highlightString(m[2])}</span>`;
    else if (m[3]) out += `<span class="t-num">${esc(m[3])}</span>`;
    else if (m[4]) out += `<span class="t-fn">${esc(m[4])}</span>`;
    else if (m[5]) out += `<span class="t-punc">${esc(m[5])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(code.slice(last));
}

/* Build the playable module around a block of pattern code. */
function buildPatch(code) {
  const patch = document.createElement("div");
  patch.className = "patch";
  patch.innerHTML =
    '<div class="patch__head">' +
      '<span class="micro patch__label">Pattern</span>' +
      '<button class="btn btn--play" type="button" data-state="idle">Play</button>' +
      '<button class="btn btn--edit" type="button">Edit</button>' +
      '<button class="btn btn--copy" type="button">Copy</button>' +
    "</div>" +
    '<pre class="patch__code"><code></code></pre>';

  patch.querySelector("code").innerHTML = highlight(code);

  const play = patch.querySelector(".btn--play");
  if (!audioAvailable) {
    play.remove();
  } else {
    play.addEventListener("click", () => {
      if (play.dataset.state === "live") stopAll();
      else playPattern(code, play);
    });
  }

  // Hand the pattern to the buffer so it can be changed and re-run.
  patch.querySelector(".btn--edit").addEventListener("click", () => {
    setEditorCode(code);
    openEditor();
    editorInput.focus();
  });

  patch.querySelector(".btn--copy").addEventListener("click", async (ev) => {
    try {
      await navigator.clipboard.writeText(code);
      note(ev.currentTarget, "Copied");
    } catch (_) {
      note(ev.currentTarget, "Blocked");
    }
  });

  return patch;
}

/* Every fenced block the model returns becomes a pattern module. */
function decoratePatches(container) {
  container.querySelectorAll("pre > code").forEach((codeEl) => {
    const code = codeEl.textContent.replace(/\s+$/, "");
    codeEl.parentElement.replaceWith(buildPatch(code));
  });
}

/* ============================== rendering ============================== */

/* codeberg.org/uzu/strudel/src/branch/main/packages/core/controls.mjs is not a
   readable link label; the tail of the path is. */
function sourceLabel(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.replace(/\/+$/, "");
    const tail = path.split("/").filter(Boolean).slice(-2).join("/");
    return tail ? `${u.hostname}/${tail}` : u.hostname;
  } catch (_) {
    return url;
  }
}

function buildSources(sources) {
  const details = document.createElement("details");
  details.className = "sources";
  details.innerHTML =
    '<summary class="sources__summary">' +
      '<span class="sources__chev">&rsaquo;</span>' +
      `<span class="micro">Sources ~ ${sources.length}</span>` +
    "</summary>" +
    '<ul class="sources__list"></ul>';

  const list = details.querySelector(".sources__list");
  sources.forEach((src) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = src;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = sourceLabel(src);
    a.title = src;
    li.appendChild(a);
    list.appendChild(li);
  });

  return details;
}

function scrollDown() {
  requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }));
}

function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg msg--user";
  el.textContent = text;
  feed.appendChild(el);
  scrollDown();
}

function addThinking() {
  const el = document.createElement("div");
  el.className = "msg msg--bot thinking";
  el.innerHTML = '<span class="micro">Retrieving</span><span class="thinking__bar"></span>';
  feed.appendChild(el);
  scrollDown();
  return el;
}

function addAnswer(html, sources) {
  const el = document.createElement("div");
  el.className = "msg msg--bot";

  const prose = document.createElement("div");
  prose.className = "prose";
  prose.innerHTML = html;          // server-rendered markdown, raw HTML escaped
  decoratePatches(prose);
  el.appendChild(prose);

  if (sources && sources.length) el.appendChild(buildSources(sources));

  feed.appendChild(el);
  scrollDown();
}

function addError(message) {
  const el = document.createElement("div");
  el.className = "msg msg--bot msg--error";
  el.innerHTML = '<span class="micro">Failed</span>';
  const p = document.createElement("p");
  p.style.margin = "8px 0 0";
  p.textContent = message;
  el.appendChild(p);
  feed.appendChild(el);
  scrollDown();
}

/* ================================ chat ================================= */

const history = [];   // prior turns only; the pending question is sent separately
let inFlight = false;

async function ask(text) {
  if (inFlight || !text.trim()) return;
  inFlight = true;
  pending = true;
  refreshLed();
  sendBtn.disabled = true;

  // The starter panel is an invitation, not history: it goes as soon as there
  // is a conversation, however the question was asked.
  const start = feed.querySelector(".start");
  if (start) start.remove();

  addUser(text);
  const thinking = addThinking();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history }),
    });
    const data = await res.json().catch(() => ({}));
    thinking.remove();

    if (!res.ok || data.error) {
      addError(data.error || `The server returned ${res.status}. Check the terminal running the app.`);
    } else {
      addAnswer(data.answer_html, data.sources);
      history.push({ role: "user", content: text });
      history.push({ role: "assistant", content: data.answer_md });
    }
  } catch (err) {
    thinking.remove();
    addError("Couldn't reach the server. Is it still running in your terminal?");
    console.error(err);
  } finally {
    inFlight = false;
    pending = false;
    refreshLed();
    sendBtn.disabled = false;
  }
}

composer.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autoGrow();
  ask(text);
});

input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    composer.requestSubmit();
  }
});

function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 168) + "px";
}
input.addEventListener("input", autoGrow);

/* The full prompt wraps to a clipped second line on a phone, so the narrow
   layout gets a placeholder that fits on one. */
const PLACEHOLDER_FULL = "Ask about a function, or describe a pattern to build.";
const PLACEHOLDER_SHORT = "Ask about Strudel…";

function fitPlaceholder() {
  input.placeholder = window.innerWidth < 560 ? PLACEHOLDER_SHORT : PLACEHOLDER_FULL;
}
fitPlaceholder();
window.addEventListener("resize", fitPlaceholder);

/* =============================== editor ================================ */

const DEMO = 's("bd sd [~ bd] sd, hh*8")\n  .lpf(sine.range(400, 3000).slow(4))\n  .room(0.3)';
const BUFFER_KEY = "strudel-assistant:buffer";
const OPEN_KEY = "strudel-assistant:editor-open";

const isApple = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");

function saveBuffer() {
  try { localStorage.setItem(BUFFER_KEY, editorInput.value); } catch (_) { /* private mode */ }
}

/* Repaint the highlighted layer under the transparent textarea. A buffer that
   ends in a newline needs a spare character, or `pre` drops that last row and
   the two layers drift apart while you type at the end. */
function syncHighlight() {
  const text = editorInput.value;
  editorHl.innerHTML = highlight(text.endsWith("\n") ? text + " " : text);
  editorHl.parentElement.scrollTop = editorInput.scrollTop;
}

function setEditorCode(code) {
  editorInput.value = code;
  syncHighlight();
  saveBuffer();
}

const wideQuery = window.matchMedia("(min-width: 1024px)");

/* No `data-editor` attribute means "no explicit choice", and the stylesheet
   docks or hides the panel by width on its own. Only a deliberate open/close
   sets the attribute and stores a preference. */
function isEditorOpen() {
  const explicit = document.body.dataset.editor;
  return explicit ? explicit === "open" : wideQuery.matches;
}

function refreshToggle() {
  const open = isEditorOpen();
  editorToggle.dataset.state = open ? "open" : "closed";
  editorToggle.setAttribute("aria-expanded", String(open));
}

function setEditorOpen(open, remember) {
  document.body.dataset.editor = open ? "open" : "closed";
  if (remember) {
    try { localStorage.setItem(OPEN_KEY, open ? "1" : "0"); } catch (_) { /* private mode */ }
  }
  refreshToggle();
}

/* Opening the panel to receive a pattern, or flicking the slide-over shut, is
   incidental - only the Editor and Close controls state a lasting preference. */
function openEditor() { setEditorOpen(true, false); }
function closeEditor() { setEditorOpen(false, false); }

function runEditor() {
  if (!editorInput.value.trim()) {
    note(editorRun, "Empty");
    return;
  }
  playPattern(editorInput.value, editorRun);
}

editorInput.addEventListener("input", () => { syncHighlight(); saveBuffer(); });
editorInput.addEventListener("scroll", () => {
  editorHl.parentElement.scrollTop = editorInput.scrollTop;
});

editorInput.addEventListener("keydown", (ev) => {
  // The live-coding keystroke: re-run the buffer, swapping the pattern without
  // stopping the transport.
  if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
    ev.preventDefault();
    runEditor();
    return;
  }
  if (ev.key === "Tab") {
    ev.preventDefault();
    const start = editorInput.selectionStart;
    const end = editorInput.selectionEnd;
    editorInput.value = editorInput.value.slice(0, start) + "  " + editorInput.value.slice(end);
    editorInput.selectionStart = editorInput.selectionEnd = start + 2;
    syncHighlight();
    saveBuffer();
  }
});

editorRun.addEventListener("click", runEditor);
editorStop.addEventListener("click", stopAll);
editorClose.addEventListener("click", () => setEditorOpen(false, true));
editorBackdrop.addEventListener("click", closeEditor);
editorToggle.addEventListener("click", () => setEditorOpen(!isEditorOpen(), true));

document.addEventListener("keydown", (ev) => {
  // Escape only dismisses the slide-over; docked it has nowhere to go.
  if (ev.key === "Escape" && isEditorOpen() && !wideQuery.matches) closeEditor();
});

function initEditor() {
  let saved = null;
  try { saved = localStorage.getItem(BUFFER_KEY); } catch (_) { /* private mode */ }
  editorInput.value = saved || DEMO;
  syncHighlight();

  if (!audioAvailable) {
    editorRun.disabled = true;
    editorHint.textContent = "audio unavailable";
  } else {
    editorHint.textContent = (isApple ? "⌘" : "Ctrl+") + "⏎ runs";
  }

  // Docked by default where there is room for both; a slide-over stays shut
  // until asked for, so it never lands on top of the conversation. Absent a
  // stored choice the attribute stays off and the stylesheet decides.
  let pref = null;
  try { pref = localStorage.getItem(OPEN_KEY); } catch (_) { /* private mode */ }
  // A stored "open" only applies where the panel docks - a slide-over must
  // never start on top of the conversation on a phone.
  if (pref === "1" && wideQuery.matches) setEditorOpen(true, false);
  else if (pref === "0") setEditorOpen(false, false);
  else refreshToggle();

  wideQuery.addEventListener("change", refreshToggle);
}

/* ============================= empty state ============================= */

const STARTERS = [
  "What does lpf do?",
  "How does mini-notation work?",
  "Build me a rolling techno groove",
  "Add reverb and a filter sweep to a bassline",
];

function renderStart() {
  const start = document.createElement("div");
  start.className = "start";
  start.innerHTML =
    '<span class="micro">Retrieval-augmented · Strudel docs</span>' +
    '<h1 class="start__title">Ask the docs.<br />Then <em>hear</em> the answer.</h1>' +
    '<p class="start__lede">Every reply is grounded in the Strudel documentation and cites the pages it used. ' +
    "Patterns come back playable, and any of them can go to the editor to be reworked and re-run.</p>" +
    '<div class="chips"></div>';

  const chips = start.querySelector(".chips");
  STARTERS.forEach((text) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.type = "button";
    chip.textContent = text;
    chip.addEventListener("click", () => ask(text));
    chips.appendChild(chip);
  });

  feed.appendChild(start);
}

initEditor();
renderStart();
input.focus();
