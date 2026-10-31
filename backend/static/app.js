// ShowMe web app: every product comes with an expert. Phone-first screens for assembling things and
// learning machines and devices, plus QR guides for brands and training records for employers.
// (Car checks and concept lessons still live at #/car and #/learn, off the main path.) No build step.

import { makeShot } from "./overlay.js";
import { playLesson } from "./lesson.js";
import { captureFrame, liveSupported, startLive } from "./live.js";
import { createViewer, webxrARSupported } from "./viewer3d.js";
import { dictate, listen, voiceSupported } from "./voice.js";
import { parseGuideCode, readPhoto, startScanner } from "./scan.js";

const view = document.getElementById("view");
const cameraInput = document.getElementById("camera");
const app = {
  status: null, mode: load("mode", "cloud"), voice: load("voice", "on") === "on",
  handsFree: load("handsfree", "off") === "on", cleanup: [], ledgers: {},
};
let activeVoice = null; // the hands-free listener, paused while the app speaks

// ---------------------------------------------------------------- small helpers

function load(key, fallback) {
  try { return localStorage.getItem("apprentice." + key) ?? fallback; } catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem("apprentice." + key, value); } catch { /* storage blocked: keep in memory */ }
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, root = view) => root.querySelector(sel);

async function api(path, { method = "GET", json, form } = {}) {
  const opts = { method, headers: {} };
  if (json) { opts.body = JSON.stringify(json); opts.headers["Content-Type"] = "application/json"; }
  if (form) opts.body = form;
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.detail || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(message, kind = "") {
  const t = document.getElementById("toast");
  t.textContent = message;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), kind === "error" ? 7000 : 3500);
}

function takePhoto() {
  return new Promise((resolve) => {
    cameraInput.value = "";
    const done = () => { cleanupListeners(); resolve(cameraInput.files[0] || null); };
    const focusBack = () => setTimeout(() => { if (!cameraInput.files.length) { cleanupListeners(); resolve(null); } }, 800);
    const cleanupListeners = () => { cameraInput.removeEventListener("change", done); window.removeEventListener("focus", focusBack); };
    cameraInput.addEventListener("change", done, { once: true });
    window.addEventListener("focus", focusBack, { once: true });
    cameraInput.click();
  });
}

const STATE_NAMES = {
  not_started: "Not started", in_progress: "In progress", fixing: "Fixing it",
  wrong: "Something's wrong", done: "Done", cant_see: "Can't see it",
};

// Speaks one line; resolves when it has been said (or right away when reading aloud is off).
function speak(text) {
  return new Promise((resolve) => {
    if (!app.voice || !("speechSynthesis" in window) || !text) return resolve();
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.02;
    let finished = false;
    const done = () => { if (finished) return; finished = true; activeVoice?.resume(); resolve(); };
    u.onend = done;
    u.onerror = done;
    activeVoice?.pause();
    speechSynthesis.speak(u);
    setTimeout(done, Math.min(25000, 1500 + text.length * 75)); // some browsers never fire onend
  });
}

function setHandsFree(on) {
  app.handsFree = on;
  save("handsfree", on ? "on" : "off");
  if (on && !app.voice) { app.voice = true; save("voice", "on"); }
}

const withTimeout = (promise, ms, fallback = null) =>
  Promise.race([promise.catch(() => fallback), new Promise((r) => setTimeout(() => r(fallback), ms))]);

function busy(node, text) {
  node.innerHTML = `<div class="busy" role="status">${esc(text)}</div>`;
}

const CAMERA_ICON = `<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M6 15h8l3-5h14l3 5h8v23H6z"/><circle cx="24" cy="25" r="8"/></svg>`;

// ---------------------------------------------------------------- header: engine + privacy switch

async function loadStatus() {
  app.status = await api("/api/status");
  const engine = document.getElementById("engine");
  engine.hidden = false;
  engine.className = "engine" + (app.status.mock ? " mock" : "");
  engine.textContent = app.status.mock ? "Mock mode" : "Live on Muse";
  document.getElementById("mockband").hidden = !app.status.mock;
  engine.title = app.status.mock
    ? "Canned answers, no API calls. Add MODEL_API_KEY to .env to go live."
    : `Spark: ${app.status.models.spark}, Wonder: ${app.status.models.wonder}, vision: ${app.status.vision_backend}`;
  const hybrid = document.querySelector('.privacy [data-mode="hybrid"]');
  if (!app.status.private_available) {
    hybrid.disabled = true;
    hybrid.title = "Hybrid mode needs SAM 3.1 on your own GPU. See the README.";
    if (app.mode === "hybrid") setMode("cloud");
  }
  paintMode();
}

function paintMode() {
  document.querySelectorAll(".privacy button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === app.mode)));
}

async function setMode(mode) {
  app.mode = mode;
  save("mode", mode);
  paintMode();
  const m = location.hash.match(/^#\/session\/([^/]+)/);
  if (m) {
    try { await api(`/api/sessions/${m[1]}/mode`, { method: "POST", json: { mode } }); } catch (e) { toast(e.message, "error"); }
  }
  toast(mode === "hybrid" ? "Hybrid: camera frames stay on your own hardware." : "Cloud: Muse Spark sees the photos for the best accuracy.");
}

document.querySelectorAll(".privacy button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

// ---------------------------------------------------------------- router

const routes = {
  "": home, assemble, session: sessionView, car: carView, learn: learnView, team: teamView,
  device: deviceView, pack: packView, brand: brandView, scan: scanView, link: linkView,
};

async function render() {
  app.cleanup.forEach((fn) => fn());
  app.cleanup = [];
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  document.body.classList.remove("printing");
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  const handler = routes[parts[0] || ""] || home;
  try {
    await handler(parts.slice(1), new URLSearchParams(query || ""));
  } catch (e) {
    view.innerHTML = `<a class="back" href="#/">Home</a><h2>That didn't load</h2><p class="muted">${esc(e.message)}</p>`;
  }
  view.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", render);

// ---------------------------------------------------------------- home

const ICONS = {
  stool: `<svg viewBox="0 0 64 64"><rect class="accent" x="10" y="14" width="44" height="8" rx="2"/><path d="M16 22v30M48 22v30M16 38h32"/><circle cx="22" cy="18" r="1"/><circle cx="42" cy="18" r="1"/></svg>`,
  machine: `<svg viewBox="0 0 64 64"><rect x="12" y="10" width="40" height="44" rx="4"/><rect x="20" y="18" width="24" height="12" rx="2"/><path d="M26 42h12M32 36v12"/></svg>`,
  car: `<svg viewBox="0 0 64 64"><path d="M8 40l6-14h36l6 14v10H8z"/><circle cx="18" cy="50" r="5"/><circle cx="46" cy="50" r="5"/><path d="M20 26l4-8h16l4 8"/></svg>`,
  orbit: `<svg viewBox="0 0 64 64"><ellipse cx="32" cy="34" rx="26" ry="11"/><circle cx="32" cy="34" r="7"/><circle cx="54" cy="30" r="4"/></svg>`,
  device: `<svg viewBox="0 0 64 64"><rect x="16" y="6" width="32" height="52" rx="5"/><circle class="accent" cx="32" cy="22" r="7"/><path d="M24 38h16M24 45h16"/><circle cx="32" cy="52" r="1.5"/></svg>`,
  scan: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M8 20V8h12M44 8h12v12M56 44v12H44M20 56H8V44"/><rect class="accent" x="20" y="20" width="9" height="9"/><rect x="35" y="20" width="9" height="9"/><rect x="20" y="35" width="9" height="9"/><path d="M35 35h9v9"/></svg>`,
  qr: `<svg viewBox="0 0 64 64"><rect x="8" y="8" width="18" height="18"/><rect x="38" y="8" width="18" height="18"/><rect x="8" y="38" width="18" height="18"/><rect class="accent" x="14" y="14" width="6" height="6"/><path d="M38 38h6v6h-6zM50 38h6M38 50h6M50 50h6v6M44 44h6v6"/></svg>`,
  team: `<svg viewBox="0 0 64 64"><rect x="10" y="10" width="44" height="44" rx="4"/><path d="M18 24h28M18 34h28M18 44h16"/><path d="M42 42l4 4 8-9"/></svg>`,
};

async function home() {
  view.innerHTML = `
    <h1>Every product comes with an expert.</h1>
    <p class="lede">ShowMe reads the manual, watches you work through your phone's camera, and talks you through every step, catching mistakes before they matter.</p>
    <a class="scan-cta" href="#/scan">${ICONS.scan}<span><strong>Scan the code on the box or machine</strong><span class="small muted">Opens its guide straight away. No manual to find.</span></span><span class="go" aria-hidden="true">›</span></a>
    <p class="link-cta"><a href="#/link">Bought it online? Paste the Amazon or store link</a> and ShowMe finds its setup guide.</p>
    <ul class="uses">
      <li class="lead"><a href="#/assemble">${ICONS.stool}<div><h3>Assemble something</h3><p>From a pile of loose parts to finished: every part counted, every step checked, help the moment something goes wrong.</p></div><span class="go" aria-hidden="true">›</span></a></li>
      <li><a href="#/assemble?use=machine">${ICONS.machine}<div><h3>Learn a machine or device</h3><p>A machine at work or an appliance at home: what every control does, "how do I…" answered from its manual, then guided and checked.</p></div><span class="go" aria-hidden="true">›</span></a></li>
    </ul>
    <h2 class="for-who">For businesses</h2>
    <ul class="uses compact">
      <li><a href="#/brand">${ICONS.qr}<div><h3>Brands: a guide in every box</h3><p>Publish your manual as a QR code. See which step people get wrong and what they ask.</p></div><span class="go" aria-hidden="true">›</span></a></li>
      <li><a href="#/team">${ICONS.team}<div><h3>Employers: train staff on your machinery</h3><p>Every step verified by the camera. Training records show who is signed off on what.</p></div><span class="go" aria-hidden="true">›</span></a></li>
    </ul>`;
}

// ---------------------------------------------------------------- pick a manual and start

async function assemble(_, query) {
  // Machines (at work) and devices (at home) share one path; "training" / "device" is just where it's used.
  const asked = query.get("use") || "assembly";
  const use = ["machine", "training", "device"].includes(asked) ? "machine" : "assembly";
  const kinds = use === "machine" ? ["training", "device"] : ["assembly"];
  const noun = use === "machine" ? "machine or device" : "product";
  const heading = use === "machine" ? "Which machine or device?" : "What are you building?";
  const placeholder = use === "machine" ? "Label printer, espresso machine, Wi-Fi router" : "Oak bookshelf";
  view.innerHTML = `
    <a class="back" href="#/">Home</a>
    <div class="stack-l">
      <div class="stack">
        <h2>${heading}</h2>
        <p class="muted">Pick a manual ShowMe has already read, or upload the PDF that came with your ${noun}.</p>
        <ul class="list" id="manuals"><li class="muted">Loading manuals</li></ul>
        ${use === "assembly" ? `<button class="btn" id="sample" type="button">Try the sample step stool</button>`
          : `<button class="btn" id="sample" type="button" data-kind="device">Try the sample coffee maker</button>`}
      </div>
      <form class="stack" id="upload">
        <h3>Upload a manual</h3>
        <div class="field"><label for="title">Name</label><input id="title" name="title" placeholder="${placeholder}" autocomplete="off"></div>
        <div class="field"><label for="pdf">Manual: a PDF, or photos of each page</label><input id="pdf" name="file" type="file" accept="application/pdf,image/*" multiple required><span class="small muted">Lost the PDF? Photograph each page of the paper booklet, in order.</span></div>
        ${use === "machine" ? `<fieldset class="field choice"><legend>Where is it used?</legend>
          <label><input type="radio" name="use_case" value="training" ${asked !== "device" ? "checked" : ""}> <span><strong>At work</strong><br><span class="small muted">A machine your team operates. Sessions go on training records.</span></span></label>
          <label><input type="radio" name="use_case" value="device" ${asked === "device" ? "checked" : ""}> <span><strong>At home</strong><br><span class="small muted">A device or appliance you own.</span></span></label>
        </fieldset>` : `<input type="hidden" name="use_case" value="assembly">`}
        <button class="btn primary" type="submit">Upload and read the manual</button>
      </form>
      <form class="stack" id="start" hidden>
        <h3 id="start-title"></h3>
        <div class="field"><label for="person">Who's learning?</label><input id="person" name="person" value="${esc(load("person", ""))}" autocomplete="name" required></div>
        <button class="btn primary" type="submit">Start</button>
      </form>
    </div>`;

  let chosen = null;
  let timer = null;
  const list = $("#manuals");

  async function refresh() {
    const all = await api("/api/manuals");
    const mine = all.filter((m) => kinds.includes(m.use_case));
    list.innerHTML = mine.length ? mine.map((m) => `
      <li>
        <div><div class="title">${esc(m.title)}</div>
          <div class="status ${m.status === "error" ? "error" : ""}">${m.status === "ready" ? `${use === "machine" ? (m.use_case === "training" ? "At work · " : "At home · ") : ""}${m.page_count ? `${m.page_count} pages, ready` : "Ready"}` : m.status === "error" ? esc(m.error || "Could not read this manual") : esc(m.progress)}</div></div>
        ${m.status === "ready" ? `<button class="btn" data-pick="${m.id}" data-title="${esc(m.title)}" type="button">Choose</button>` : m.status === "processing" ? `<span class="busy small" aria-label="Reading"></span>` : ""}
      </li>`).join("") : `<li class="muted">No ${noun} manuals yet. Upload one below, or try the sample.</li>`;
    list.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => pick(b.dataset.pick, b.dataset.title)));
    clearTimeout(timer);
    if (mine.some((m) => m.status === "processing")) timer = setTimeout(refresh, 1500);
  }
  app.cleanup.push(() => clearTimeout(timer));

  function pick(id, title) {
    if (use === "machine") { location.hash = `#/device/${id}`; return; } // machines and devices open their page
    chosen = id;
    $("#start").hidden = false;
    $("#start-title").textContent = title;
    $("#person").focus();
    $("#start").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  $("#sample")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const m = await api(`/api/manuals/sample${e.target.dataset.kind ? `?kind=${e.target.dataset.kind}` : ""}`, { method: "POST" });
      await refresh();
      if (m.status === "ready") pick(m.id, m.title);
      else toast("Reading the sample manual with Muse Spark");
    } catch (err) { toast(err.message, "error"); }
    e.target.disabled = false;
  });

  $("#upload").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button");
    btn.disabled = true;
    try {
      await api("/api/manuals", { method: "POST", form: new FormData(e.target) });
      e.target.reset();
      toast("Uploaded. Muse Spark is reading each page; Muse Wonder writes the plan.");
      await refresh();
    } catch (err) { toast(err.message, "error"); }
    btn.disabled = false;
  });

  $("#start").addEventListener("submit", async (e) => {
    e.preventDefault();
    const person = $("#person").value.trim();
    save("person", person);
    try {
      const s = await api("/api/sessions", { method: "POST", json: { person, manual_id: chosen, mode: app.mode } });
      location.hash = `#/session/${s.id}/inventory`;
    } catch (err) { toast(err.message, "error"); }
  });

  await refresh();
}

// ---------------------------------------------------------------- a training session

async function sessionView([sid, stage = "inventory", n]) {
  const session = await api(`/api/sessions/${sid}`);
  const manual = await api(`/api/manuals/${session.manual_id}`);
  const plan = manual.plan;
  if (session.mode !== app.mode) { app.mode = session.mode; paintMode(); }
  if (stage === "inventory" && (plan.use_case || manual.use_case) !== "assembly") stage = "step";
  if (stage === "inventory") return inventoryStage(session, plan);
  if (stage === "step") {
    const ledger = app.ledgers[session.manual_id] ||= await api(`/api/manuals/${session.manual_id}/ledger`).catch(() => null);
    return stepStage(session, plan, Number(n) || plan.steps[0]?.order || 1, ledger);
  }
  return doneStage(session, plan);
}

// Where each step stands, from the session's results: pass, fail (a mistake still open) or progress.
function stepStatuses(session) {
  const out = {};
  for (const r of session.results || []) out[r.step_order] = r.final;
  return out;
}

const STATUS_WORDS = { pass: "checked", fail: "needs fixing", progress: "in progress", unsure: "not sure yet" };

// One segment per step (plus the parts check for assembly); every segment jumps straight to its step.
function progressBar(plan, current, session) {
  const st = session ? stepStatuses(session) : {};
  const withParts = (plan.use_case || "assembly") === "assembly";
  const seg = (href, cls, label) => `<a class="${cls}" href="${href}" aria-label="${esc(label)}" title="${esc(label)}"></a>`;
  const parts = withParts && session ? [seg(`#/session/${session.id}/inventory`, current === 0 ? "now" : "done", "Parts check")] : [];
  const steps = plan.steps.map((s, i) => {
    const status = st[s.order];
    const cls = [i + 1 === current ? "now" : "", status === "pass" ? "done" : "", status === "fail" ? "bad" : ""].join(" ").trim();
    return session ? seg(`#/session/${session.id}/step/${s.order}`, cls, `Step ${s.order}: ${s.title}${status ? ` (${STATUS_WORDS[status] || status})` : ""}`)
      : `<span class="${cls}"></span>`;
  });
  return `<nav class="stepbar" aria-label="Steps: tap any step to go there">${parts.join("")}${steps.join("")}</nav>`;
}

function allStepsList(plan, session, currentOrder) {
  const st = stepStatuses(session);
  return `<details class="all-steps"><summary>All ${plan.steps.length} steps <span class="muted small">(go to any step)</span></summary>
    <ol>${plan.steps.map((s) => {
      const status = st[s.order];
      return `<li class="${s.order === currentOrder ? "here" : ""}"><a href="#/session/${session.id}/step/${s.order}">
        <span class="mark ${status || "none"}" aria-hidden="true">${status === "pass" ? "✓" : status === "fail" ? "!" : status === "progress" ? "→" : s.order}</span>
        <span><strong>${esc(s.title)}</strong>${status ? `<span class="small muted"> ${STATUS_WORDS[status] || status}</span>` : ""}</span></a></li>`;
    }).join("")}</ol></details>`;
}

// ---- where am I: one picture of the work, and ShowMe finds your place in the whole procedure
async function grabWorkPhoto(area) {
  if (liveSupported()) {
    try { return await captureFrame(area, { label: "Take the picture" }); } catch { /* no camera: fall back to a photo */ }
  }
  return takePhoto();
}

async function whereAmI(session, plan, area, out, { auto = false } = {}) {
  const blob = await grabWorkPhoto(area);
  if (!blob) return null;
  busy(out, "Muse Spark is comparing your work with every step");
  const form = new FormData();
  form.append("photo", blob, "where.jpg");
  let r;
  try { r = await api(`/api/sessions/${session.id}/where`, { method: "POST", form }); }
  catch (e) { out.innerHTML = ""; toast(e.message, "error"); return null; }
  const go = r.current_step ? `#/session/${session.id}/step/${r.current_step}` : r.all_done ? `#/session/${session.id}/done` : null;
  out.innerHTML = `
    <section class="verdict ${r.wrong.length ? "fail" : r.sure ? "progress" : "unsure"} whereami" aria-live="polite">
      <h3>${r.all_done ? "✓ Everything looks done" : r.current_step ? `→ You're on step ${r.current_step}: ${esc(r.current_title)}` : "? I can't tell from this picture"}</h3>
      ${r.observed ? `<p>${esc(r.observed)}</p>` : ""}
      ${r.done_steps.length ? `<p class="small">Already done: step${r.done_steps.length > 1 ? "s" : ""} ${r.done_steps.join(", ")}${r.recorded_passes.length ? `. Checked off from this picture.` : "."}</p>` : ""}
      ${r.wrong.map((w) => `<p><strong>Step ${w.step} needs fixing:</strong> ${esc(w.observed)} ${esc(w.fix)}</p>`).join("")}
      ${r.next_steps.length ? `<h4>Next, from where you are</h4><ol class="next">${r.next_steps.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>` : ""}
      ${!r.sure && !r.current_step ? `<p class="small muted">Get the whole piece in the picture, in good light, and try again.</p>` : ""}
      ${go ? `<p><a class="btn primary" href="${go}">${r.all_done ? "Finish" : `Go to step ${r.current_step}`}</a></p>` : ""}
    </section>`;
  out.scrollIntoView({ behavior: "smooth", block: "start" });
  await speak(r.say || (r.current_step ? `You're on step ${r.current_step}.` : "I can't tell from this picture."));
  if (auto && go && app.handsFree) location.hash = go;
  return r;
}


function inventoryStage(session, plan) {
  view.innerHTML = `
    <a class="back" href="#/assemble">Change product</a>
    ${progressBar(plan, 0, session)}
    <div class="stack">
      <h2>Check the parts</h2>
      <p class="muted">Lay every part out on the floor, spread apart, and take one photo from above. ShowMe counts each one against the manual's parts list.</p>
      ${plan.reference_url ? `<details><summary class="small">The manual's parts list</summary><img class="figure" src="${plan.reference_url}" alt="Parts drawings from the manual"></details>` : ""}
      <div id="area">
        <button class="capture" id="snap" type="button">${CAMERA_ICON}<strong>Take the parts photo</strong><span class="muted small">${plan.parts.length} part types, ${plan.parts.reduce((a, p) => a + (p.quantity || 1), 0)} pieces expected</span></button>
      </div>
      <div id="result"></div>
    </div>
    <div class="dock"><div class="inner">
      <button class="btn" id="where" type="button">Already started? Find my place</button>
      <a class="btn" href="#/session/${session.id}/step/${plan.steps[0]?.order || 1}">Skip the parts check</a>
    </div></div>`;

  const area = $("#area"), result = $("#result"), dock = $(".dock .inner");
  $("#where").addEventListener("click", () => whereAmI(session, plan, area, result));
  const snap = async () => {
    const file = await takePhoto();
    if (!file) return;
    busy(area, app.mode === "hybrid" ? "SAM 3.1 is finding every part on your hardware" : "Finding and counting every part");
    result.innerHTML = "";
    const form = new FormData();
    form.append("photo", file);
    try {
      const inv = await api(`/api/sessions/${session.id}/inventory`, { method: "POST", form });
      area.innerHTML = "";
      const total = inv.detections.length;
      const shot = makeShot(area, inv.image, { badge: `${total} pieces found` });
      shot.show(inv.detections, { names: false });
      app.cleanup.push(() => shot.destroy());
      result.innerHTML = `
        <div class="stack">
          <p><strong>${esc(inv.summary)}</strong></p>
          <table class="counts">
            <thead><tr><th>Part</th><th>Found</th><th></th></tr></thead>
            <tbody>${inv.rows.map((r) => `
              <tr><td><span class="part"><span class="callout">${esc(r.label)}</span>${esc(r.name)}</span></td>
              <td class="num">${r.found} of ${r.needed}${r.unsure ? ` <span class="muted small">(${r.unsure} unsure)</span>` : ""}</td>
              <td><span class="tag ${r.status}">${r.status === "ok" ? "All here" : r.status === "missing" ? `${r.needed - r.found} missing` : `${r.found - r.needed} extra`}</span></td></tr>`).join("")}
            </tbody>
          </table>
          ${inv.notes ? `<p class="muted small">${esc(inv.notes)}</p>` : ""}
        </div>`;
      speak(inv.summary);
      dock.innerHTML = `<button class="btn" id="again" type="button">Retake photo</button><a class="btn primary" href="#/session/${session.id}/step/1">Start step 1</a>`;
      $("#again", dock).addEventListener("click", snap);
    } catch (e) {
      area.innerHTML = "";
      area.append(snapButton(snap));
      toast(e.message, "error");
    }
  };
  $("#snap").addEventListener("click", snap);
}

function snapButton(fn, label = "Take the photo again") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "capture";
  b.innerHTML = `${CAMERA_ICON}<strong>${esc(label)}</strong>`;
  b.addEventListener("click", fn);
  return b;
}

function stepStage(session, plan, order, ledger) {
  const step = plan.steps.find((s) => s.order === order) || plan.steps[0];
  const idx = plan.steps.indexOf(step);
  const last = idx === plan.steps.length - 1;
  const names = Object.fromEntries(plan.parts.map((p) => [p.label, p.name]));
  const hub = plan.use_case === "task" ? plan.parent_manual_id : plan.use_case !== "assembly" ? session.manual_id : null;
  const firstBack = hub ? [`#/device/${hub}`, `Back to the ${plan.use_case === "training" ? "machine" : "device"}`]
    : [`#/session/${session.id}/inventory`, "Back to the parts check"];
  // Loose hardware this step uses up, and what should be left afterwards
  const hw = new Set((ledger?.hardware || []).map((h) => h.label));
  const leftAfter = ledger?.after?.find((a) => a.order === step.order)?.left || {};
  const usesHardware = (step.parts_used || []).filter((u) => hw.has(u.label));
  const prev = idx ? plan.steps[idx - 1] : null;
  view.innerHTML = `
    <a class="back" href="${prev ? `#/session/${session.id}/step/${prev.order}` : firstBack[0]}">${prev ? `Back to step ${prev.order}` : firstBack[1]}</a>
    ${plan.question ? `<p class="task-q">${esc(plan.question)}</p>` : ""}
    ${plan.source?.kind === "listing" ? `<p class="tag extra source-tag">General step from the store listing, not the official manual</p>` : ""}
    ${progressBar(plan, idx + 1, session)}
    <div class="stack">
      <div class="step-head"><span class="step-no">${step.order}</span><div><p class="muted small">Step ${idx + 1} of ${plan.steps.length}</p><h2>${esc(step.title)}</h2></div></div>
      ${allStepsList(plan, session, step.order)}
      <div class="viewer">
        <nav class="tabs view-tabs" aria-label="How to see this step">
          <button type="button" data-view="manual" aria-current="true">Manual</button>
          <button type="button" data-view="3d">3D</button>
          <button type="button" data-view="ar">AR</button>
        </nav>
        <div id="stage" class="stage"></div>
        <div id="stage-bar" class="stage-bar" hidden></div>
      </div>
      <p class="headsup" id="headsup" hidden></p>
      <p style="font-size:18px">${esc(step.instruction)}</p>
      ${step.parts_used?.length ? `<div class="uses-strip">${step.parts_used.map((u) => `<span><span class="callout">${esc(u.label)}</span>${u.quantity}x ${esc(names[u.label] || "")}</span>`).join("")}</div>` : ""}
      ${step.warning ? `<p class="warn">${esc(step.warning)}</p>` : ""}
      ${step.why ? `<p class="why">Why it matters: ${esc(step.why)}</p>` : ""}
      ${usesHardware.length ? `<p class="ledger-line">After this step you should have ${usesHardware.map((u) => `<b>${leftAfter[u.label] ?? "?"}</b> ${esc(names[u.label] || "")} <span class="callout">${esc(u.label)}</span>`).join(" and ")} left over.</p>` : ""}
      <p class="muted small">Done when: ${esc(step.done_when)}</p>
      <div class="row">
        <button class="btn quiet" id="voice" type="button" aria-pressed="${app.voice}">${app.voice ? "Reading aloud" : "Read aloud: off"}</button>
        <button class="btn quiet" id="handsfree" type="button" aria-pressed="${app.handsFree}">${app.handsFree ? "Hands-free: on" : "Hands-free: off"}</button>
        <button class="btn quiet" id="whereami" type="button">Where am I?</button>
        ${hw.size && idx ? `<button class="btn quiet" id="ledger" type="button">Count leftover hardware</button>` : ""}
      </div>
      <p class="listening small" id="listening" hidden></p>
      <div id="area"></div>
      <div id="verdict"></div>
    </div>
    <div class="dock"><div class="inner">
      ${step.parts_used?.length ? `<button class="btn" id="parts" type="button">Show the parts</button>` : ""}
      <button class="btn primary" id="check" type="button">Check my work</button>
    </div></div>`;

  const area = $("#area"), verdictBox = $("#verdict"), dock = $(".dock .inner");
  let shot = null, liveRunning = false, passed = false, lastLine = "", stopLive = null;
  const say = (text) => { lastLine = text; return speak(text); };

  // ---- heads-up from earlier builders, then the instruction; hands-free starts the camera afterwards
  const headsUp = api(`/api/manuals/${session.manual_id}/steps/${step.order}/heads-up`).then((h) => {
    if (h?.tip) {
      const box = $("#headsup");
      box.hidden = false;
      box.innerHTML = `<strong>Heads-up</strong> ${esc(h.tip)} <span class="small muted">${h.mistakes} of ${h.builders} earlier ${h.builders === 1 ? "person" : "people"} got this step wrong.</span>`;
    }
    return h;
  }).catch(() => null);
  (async () => {
    const h = await withTimeout(headsUp, 2500);
    const line = `Step ${step.order}. ${step.instruction}${h?.tip ? ` Heads-up: ${h.tip}` : ""}`;
    await say(line);
    if (app.handsFree && liveSupported() && !liveRunning && !passed && location.hash.includes(`/step/${step.order}`)) liveCheck();
  })();

  $("#voice").addEventListener("click", (e) => {
    app.voice = !app.voice;
    save("voice", app.voice ? "on" : "off");
    e.target.textContent = app.voice ? "Reading aloud" : "Read aloud: off";
    e.target.setAttribute("aria-pressed", String(app.voice));
    if (!app.voice && "speechSynthesis" in window) speechSynthesis.cancel();
    else say(step.instruction);
  });
  $("#handsfree").addEventListener("click", (e) => {
    setHandsFree(!app.handsFree);
    e.target.textContent = app.handsFree ? "Hands-free: on" : "Hands-free: off";
    e.target.setAttribute("aria-pressed", String(app.handsFree));
    $("#voice").textContent = app.voice ? "Reading aloud" : "Read aloud: off";
    if (app.handsFree) { startVoice(); if (liveSupported() && !liveRunning && !passed) liveCheck(); }
    else { activeVoice?.stop(); activeVoice = null; $("#listening").hidden = true; }
  });

  // ---- voice commands while hands are busy
  function startVoice() {
    if (activeVoice) return;
    const hint = $("#listening");
    activeVoice = listen({
      onState: (st) => {
        hint.hidden = false;
        hint.textContent = st === "blocked" ? "The microphone is blocked, so voice commands are off. The camera still coaches you."
          : `Listening. Say "next", "go to step 3", "where am I", "repeat", "check", "show me" or "parts".`;
      },
      onHeard: (t) => { hint.textContent = `Heard: "${t}"`; },
      onCommand: (cmd) => handleCommand(cmd),
    });
    if (!activeVoice) {
      hint.hidden = false;
      hint.textContent = "Voice commands need Chrome or Edge over https (or on this PC). The camera still coaches you out loud.";
    }
  }
  function handleCommand(cmd) {
    if (cmd.startsWith("goto:")) {
      const n = Number(cmd.slice(5));
      const target = plan.steps.find((s) => s.order === n);
      if (target) location.hash = `#/session/${session.id}/step/${n}`;
      else say(`There's no step ${n}. This guide has ${plan.steps.length} steps.`);
      return;
    }
    if (cmd === "where") { stopLive?.(); whereAmI(session, plan, area, verdictBox, { auto: true }); return; }
    if (cmd === "next") {
      if (passed) location.hash = next;
      else say("This step isn't checked yet. Say skip to move on anyway, or check to look again.");
    } else if (cmd === "skip") location.hash = next;
    else if (cmd === "back") location.hash = prev ? `#/session/${session.id}/step/${prev.order}` : firstBack[0];
    else if (cmd === "repeat") say(lastLine || step.instruction);
    else if (cmd === "check") { if (!liveRunning) (liveSupported() ? liveCheck() : check()); else say("I'm watching. Keep going."); }
    else if (cmd === "stop") { stopLive?.(); say("Camera off. Say check when you want me to look again."); }
    else if (cmd === "show") { showView("3d"); }
    else if (cmd === "parts") say(step.parts_used?.length ? `You need ${step.parts_used.map((u) => `${u.quantity} ${names[u.label] || u.label} ${u.label}`).join(", ")}.` : "No parts for this step.");
    else say(`Say next, back, go to step and a number, where am I, repeat, check, show me, or parts.`);
  }
  if (app.handsFree) startVoice();
  app.cleanup.push(() => { activeVoice?.stop(); activeVoice = null; });

  // ---- Manual / 3D / AR views of the step
  const stage = $("#stage"), stageBar = $("#stage-bar");
  const figureHtml = step.figure_url
    ? `<div class="figure"><img src="${step.figure_url}" alt="The manual's diagram for step ${step.order}"></div>`
    : `<p class="muted">The manual has no diagram for this step.</p>`;
  let viewer = null, camStream = null, animData = null, current = "manual";
  const clearStage = () => {
    viewer?.dispose(); viewer = null;
    camStream?.getTracks().forEach((t) => t.stop()); camStream = null;
    document.querySelectorAll(".ar-overlay").forEach((el) => el.remove());
    stage.innerHTML = ""; stage.className = "stage";
    stageBar.hidden = true; stageBar.innerHTML = "";
  };
  app.cleanup.push(clearStage);
  const say3d = (text) => {
    document.querySelectorAll(".say3d").forEach((el) => (el.textContent = text));
    speak(text);
  };
  const loadAnim = async (refresh = false) => {
    if (!animData || refresh) {
      animData = await api(`/api/manuals/${session.manual_id}/steps/${step.order}/animation${refresh ? "?refresh=true" : ""}`);
    }
    return animData;
  };
  function controlsBar(data, extra = "") {
    stageBar.hidden = false;
    const by = data.source === "Muse Wonder"
      ? "Built by Muse Wonder from the manual's diagram. Drag to turn it, pinch or scroll to zoom."
      : "Simple preview: Muse Wonder couldn't build the full 3D model just now.";
    stageBar.innerHTML = `
      <p class="say3d" aria-live="polite"></p>
      <div class="row"><button class="btn" id="replay" type="button">Replay</button><button class="btn" id="pause" type="button">Pause</button>${extra}</div>
      <p class="meta"><span id="by">${by}</span> <button class="btn quiet" id="rebuild" type="button">Rebuild</button></p>`;
    $("#replay", stageBar).addEventListener("click", () => { viewer?.restart(); $("#pause", stageBar).textContent = "Pause"; });
    $("#pause", stageBar).addEventListener("click", (e) => {
      if (!viewer) return;
      if (viewer.playing) { viewer.pause(); e.target.textContent = "Play"; } else { viewer.play(); e.target.textContent = "Pause"; }
    });
    $("#rebuild", stageBar).addEventListener("click", async () => {
      animData = null;
      clearStage();
      busy(stage, "Muse Wonder is rebuilding the 3D model");
      try { await loadAnim(true); } catch (err) { toast(err.message, "error"); }
      showView(current);
    });
  }
  async function showView(v) {
    current = v;
    view.querySelectorAll(".view-tabs button").forEach((b) => (b.dataset.view === v ? b.setAttribute("aria-current", "true") : b.removeAttribute("aria-current")));
    clearStage();
    if (v === "manual") { stage.innerHTML = figureHtml; return; }
    stage.classList.add("is3d");
    busy(stage, animData ? "Loading the 3D model" : "Muse Wonder is building a 3D model of this step from the manual's diagram");
    let data;
    try { data = await loadAnim(); } catch (err) { toast(err.message, "error"); return showView("manual"); }
    if (current !== v) return;
    stage.innerHTML = "";
    const onEnd = () => { const p = $("#pause", stageBar); if (p) p.textContent = "Play"; };
    if (v === "3d") {
      controlsBar(data);
      viewer = createViewer(stage, data.animation, { onSay: say3d, onEnd });
      return;
    }
    // AR: real placement with WebXR where the phone supports it; otherwise the animation over the live camera
    if (await webxrARSupported()) {
      controlsBar(data, `<button class="btn primary" id="place" type="button">Place it in your room</button>`);
      viewer = createViewer(stage, data.animation, { onSay: say3d, onEnd });
      viewer.pause();
      const overlay = document.createElement("div");
      overlay.className = "ar-overlay";
      overlay.innerHTML = `<p class="say3d">Point at the floor or a table, then tap the yellow ring to place the step.</p><button class="btn" type="button">Exit AR</button>`;
      document.body.append(overlay);
      overlay.querySelector("button").addEventListener("click", () => viewer?.exitAR());
      $("#place", stageBar).addEventListener("click", async () => {
        try { await viewer.enterAR(overlay, { onExit: () => viewer?.pause() }); }
        catch (err) { toast(`AR didn't start (${err.message}).`, "error"); }
      });
      return;
    }
    if (!liveSupported()) {
      toast("AR needs the camera, which browsers allow only over https or on this PC. Showing 3D instead.");
      return showView("3d");
    }
    stage.classList.add("camera");
    const video = document.createElement("video");
    video.playsInline = true; video.muted = true; video.autoplay = true;
    stage.append(video);
    try {
      camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      video.srcObject = camStream;
      await video.play().catch(() => {});
      await new Promise((res) => (video.videoWidth ? res() : video.addEventListener("loadedmetadata", res, { once: true })));
    } catch (err) {
      toast(`The camera didn't start (${err.message}). Showing 3D instead.`, "error");
      return showView("3d");
    }
    const layer = document.createElement("div");
    layer.className = "ar-layer";
    stage.append(layer);
    controlsBar(data);
    viewer = createViewer(layer, data.animation, { transparent: true, fill: true, onSay: say3d, onEnd });
    $("#by", stageBar).textContent = "Camera view: the animation plays over your camera. Placing it on a real table needs an ARCore phone in Chrome over https.";
  }
  view.querySelectorAll(".view-tabs button").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  showView("manual");
  const showShot = (image, badge) => {
    area.innerHTML = "";
    shot?.destroy();
    shot = makeShot(area, image, { badge });
    app.cleanup.push(() => shot?.destroy());
    return shot;
  };

  $("#parts")?.addEventListener("click", async () => {
    const file = await takePhoto();
    if (!file) return;
    busy(area, "Finding this step's parts");
    verdictBox.innerHTML = "";
    const form = new FormData();
    form.append("photo", file);
    try {
      const r = await api(`/api/sessions/${session.id}/steps/${step.order}/parts`, { method: "POST", form });
      showShot(r.image, "This step's parts").show(r.detections);
      if (!r.detections.length) toast("Couldn't spot this step's parts. Move closer and try again.");
    } catch (e) { area.innerHTML = ""; toast(e.message, "error"); }
  });

  const check = async () => {
    const file = await takePhoto();
    if (!file) return;
    busy(area, app.mode === "hybrid" ? `Checking step ${step.order} on your hardware` : `Muse Spark is checking step ${step.order}`);
    verdictBox.innerHTML = "";
    const form = new FormData();
    form.append("photo", file);
    try {
      const r = await api(`/api/sessions/${session.id}/steps/${step.order}/check`, { method: "POST", form });
      showShot(r.image, `Attempt ${r.attempt}`).show(r.detections || [], { focus: r.verdict === "fail" || r.verdict === "progress" ? r.box : null });
      showVerdict(r);
    } catch (e) { area.innerHTML = ""; toast(e.message, "error"); }
  };
  // Live mode: the camera keeps watching and coaches one instruction at a time until the step is right.
  const liveCheck = async () => {
    verdictBox.innerHTML = "";
    area.innerHTML = "";
    shot?.destroy();
    shot = null;
    const coach = document.createElement("section");
    coach.className = "coach";
    coach.setAttribute("aria-live", "polite");
    coach.innerHTML = `<p class="say">Point the camera at your work.</p>
      <div class="meter" hidden><span></span></div>
      <p class="meta">${app.mode === "hybrid" ? "Checking on your own hardware" : "Muse Spark"} looks every couple of seconds and tells you what to do next. Start anywhere: half done or mid-fix is fine.</p>`;
    const plan_ = document.createElement("section");
    plan_.className = "coach-plan";
    plan_.setAttribute("aria-live", "polite");
    plan_.hidden = true;
    const liveId = Math.random().toString(36).slice(2);
    const history = [];
    let lastSay = "";
    let live = null;
    const stop = () => { live?.stop(); liveRunning = false; };
    liveRunning = true;
    stopLive = stop;
    try {
      live = await startLive(area, {
        send: (blob, frame) => {
          const form = new FormData();
          form.append("photo", blob, `frame${frame}.jpg`);
          form.append("frame", String(frame));
          form.append("live_id", liveId);
          form.append("history", JSON.stringify(history.slice(-3)));
          return api(`/api/sessions/${session.id}/steps/${step.order}/live`, { method: "POST", form });
        },
        onResult: (r) => {
          coach.className = `coach ${r.verdict}`;
          coach.querySelector(".say").textContent = r.say || r.observed || "Hold steady.";
          coach.querySelector(".meta").textContent = `${STATE_NAMES[r.state] || ""}${r.observed && r.observed !== r.say ? ` · ${r.observed}` : ""}`;
          const meter = coach.querySelector(".meter");
          meter.hidden = r.progress == null;
          if (r.progress != null) meter.firstElementChild.style.width = `${Math.round(r.progress * 100)}%`;
          paintPlan(plan_, r);
          if (r.say && r.say !== lastSay) { say(r.say); lastSay = r.say; } // speak only when the advice changes
          history.push({ observed: r.observed, say: r.say });
          if (r.verdict === "pass") { liveRunning = false; plan_.hidden = true; showVerdict(r); return true; }
          return false;
        },
        onError: (e) => { coach.querySelector(".meta").textContent = `That frame didn't go through (${e.message}). Trying again.`; },
      });
    } catch (e) {
      liveRunning = false;
      area.innerHTML = "";
      toast(`The camera didn't start (${e.message}). Take a photo instead.`, "error");
      return check();
    }
    live.figure.append(coach); // the instruction sits on the video, where the person is looking
    live.figure.after(plan_); // what's done and what's next, under the video
    app.cleanup.push(stop);
    dock.innerHTML = `<button class="btn" id="photo" type="button">Use a photo instead</button><button class="btn primary" id="stop" type="button">Stop the camera</button>`;
    $("#stop", dock).addEventListener("click", () => { stop(); render(); });
    $("#photo", dock).addEventListener("click", () => { stop(); area.innerHTML = ""; check(); });
  };

  $("#check").addEventListener("click", () => (liveSupported() ? liveCheck() : check()));

  // ---- where am I: works from any step, mid-way through anything
  $("#whereami").addEventListener("click", () => { stopLive?.(); whereAmI(session, plan, area, verdictBox); });

  // ---- parts ledger: count the loose hardware and compare with what should be left before this step
  $("#ledger")?.addEventListener("click", async () => {
    const file = await takePhoto();
    if (!file) return;
    stopLive?.();
    busy(area, "Counting your leftover hardware");
    verdictBox.innerHTML = "";
    const form = new FormData();
    form.append("photo", file);
    form.append("before_step", String(step.order));
    try {
      const r = await api(`/api/sessions/${session.id}/ledger`, { method: "POST", form });
      showShot(r.image, "Leftover hardware").show(r.detections || [], { names: false });
      verdictBox.innerHTML = `
        <section class="verdict ${r.ok ? "pass" : "fail"}" aria-live="polite">
          <h3>${r.ok ? "✓ Hardware adds up" : "! Hardware doesn't add up"}</h3>
          <table class="counts"><thead><tr><th>Part</th><th>Should be left</th><th>Found</th></tr></thead>
            <tbody>${r.rows.map((row) => `<tr><td><span class="part"><span class="callout">${esc(row.label)}</span>${esc(row.name)}</span></td><td class="num">${row.expected}</td><td class="num">${row.found}</td></tr>`).join("")}</tbody></table>
          ${r.rows.filter((row) => row.message).map((row) => `<p>${esc(row.message)}${row.blame ? ` <a href="#/session/${session.id}/step/${row.blame.order}">Go to step ${row.blame.order}</a>` : ""}</p>`).join("")}
        </section>`;
      say(r.summary);
    } catch (e) { area.innerHTML = ""; toast(e.message, "error"); }
  });
  if (!liveSupported()) {
    $("#check").title = "Live camera needs https or localhost; this opens your camera app for a photo instead.";
  }

  // Where the work is: what's already right, the next actions from here, and a jump if they're on another step.
  function paintPlan(box, r) {
    const done = r.done_items || [], todo = r.next_steps || [];
    const jump = r.seen_step && r.seen_step.order !== step.order ? r.seen_step : null;
    box.hidden = !done.length && !todo.length && !jump;
    if (box.hidden) { box.innerHTML = ""; return; }
    const ahead = jump && jump.order > step.order;
    box.innerHTML = `
      ${jump ? `<p class="jump">${ahead ? "You look ahead of this step:" : "This looks like an earlier step:"} step ${jump.order}, ${esc(jump.title)}. <a class="btn" href="#/session/${session.id}/step/${jump.order}">Go to step ${jump.order}</a></p>` : ""}
      ${todo.length ? `<h4>Next</h4><ol class="next">${todo.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>` : ""}
      ${done.length ? `<h4>Already done</h4><ul class="done">${done.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}`;
  }

  const next = last ? `#/session/${session.id}/done` : `#/session/${session.id}/step/${plan.steps[idx + 1].order}`;
  const nextLabel = last ? "Finish" : `Next: step ${plan.steps[idx + 1].order}`;

  function showVerdict(r) {
    const heads = { pass: "Step checked", fail: "Not right yet", progress: "On the way", unsure: "Not sure from this photo" };
    const icons = { pass: "✓", fail: "!", progress: "→", unsure: "?" };
    const todo = r.verdict === "pass" ? [] : (r.next_steps || []);
    verdictBox.innerHTML = `
      <section class="verdict ${r.verdict}" aria-live="polite">
        <h3>${icons[r.verdict] || "?"} ${heads[r.verdict] || heads.unsure}${r.progress != null && r.verdict !== "pass" ? ` <span class="pct">${Math.round(r.progress * 100)}% done</span>` : ""}</h3>
        <dl>
          ${r.observed ? `<dt>What the camera sees</dt><dd>${esc(r.observed)}</dd>` : ""}
          ${r.done_items?.length && r.verdict !== "pass" ? `<dt>Already done</dt><dd><ul class="done">${r.done_items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></dd>` : ""}
          ${todo.length ? `<dt>Next, from where you are</dt><dd><ol class="next">${todo.map((t) => `<li>${esc(t)}</li>`).join("")}</ol></dd>` : r.fix ? `<dt>How to fix it</dt><dd>${esc(r.fix)}</dd>` : ""}
          ${r.reason ? `<dt>Why it matters</dt><dd>${esc(r.reason)}</dd>` : ""}
        </dl>
        ${r.seen_step && r.seen_step.order !== step.order ? `<p class="jump">This photo looks like step ${r.seen_step.order}, ${esc(r.seen_step.title)}. <a class="btn" href="#/session/${session.id}/step/${r.seen_step.order}">Go to step ${r.seen_step.order}</a></p>` : ""}
        <p class="meta">${esc(r.method)}, confidence ${Math.round(r.confidence * 100)}%</p>
      </section>`;
    if (r.verdict === "pass") {
      passed = true;
      dock.innerHTML = `<a class="btn primary" href="${next}">${nextLabel}</a>`;
      if (app.handsFree) {
        say(last ? "All steps checked. Nice work." : `Step checked. Moving to step ${plan.steps[idx + 1].order}.`)
          .then(() => { if (location.hash.includes(`/step/${step.order}`)) location.hash = next; });
      } else say(last ? "All steps checked. Nice work." : "Step checked. On to the next one.");
    } else if (r.verdict === "fail" || r.verdict === "progress") {
      say(r.verdict === "progress"
        ? `Good progress. Next: ${todo[0] || r.fix || "keep going"}`
        : `Not yet. ${r.reason} ${todo[0] ? `First: ${todo[0]}` : r.fix}`);
      dock.innerHTML = `<button class="btn primary" id="again" type="button">Check again</button>`;
      $("#again", dock).addEventListener("click", check);
    } else {
      speak("I'm not sure from this photo. Retake it, or confirm it yourself.");
      dock.innerHTML = `<button class="btn" id="again" type="button">Retake photo</button><button class="btn primary" id="confirm" type="button">It's done right</button>`;
      $("#again", dock).addEventListener("click", check);
      $("#confirm", dock).addEventListener("click", async () => {
        await api(`/api/results/${r.result_id}/override`, { method: "POST", json: { verdict: "pass" } });
        toast("Confirmed by you. Logged as a person's check, not the camera's.");
        location.hash = next;
      });
    }
  }
}

async function doneStage(session, plan) {
  const row = await api(`/api/sessions/${session.id}/finish`, { method: "POST" });
  view.innerHTML = `
    <div class="stack-l">
      <div class="stack">
        <h1>${row.verified ? (plan.use_case === "task" ? `Done: ${esc(plan.question.replace(/\?$/, "").replace(/^how do i /i, ""))}.` : plan.use_case === "assembly" || !plan.use_case ? `${esc(row.person)} built the ${esc(plan.product.toLowerCase())}.` : `${esc(plan.product)} is set up.`) : "Session saved."}</h1>
        <p class="lede">${row.steps_passed} of ${row.steps_total} steps checked by the camera${row.mistakes_caught ? `, ${row.mistakes_caught} mistake${row.mistakes_caught > 1 ? "s" : ""} caught and fixed along the way` : ""}.</p>
      </div>
      <table class="counts">
        <tbody>
          <tr><td>Parts check</td><td class="num">${row.inventory_complete === true ? "Complete" : row.inventory_complete === false ? "Parts were missing" : "Skipped"}</td></tr>
          <tr><td>Steps verified</td><td class="num">${row.steps_passed} of ${row.steps_total}</td></tr>
          <tr><td>Photos checked</td><td class="num">${row.attempts}</td></tr>
          <tr><td>Confirmed by the person</td><td class="num">${row.overrides}</td></tr>
          <tr><td>Privacy mode</td><td class="num">${row.mode === "hybrid" ? "Hybrid" : "Cloud"}</td></tr>
        </tbody>
      </table>
      <div class="row">
        ${plan.use_case === "task" || (plan.use_case && plan.use_case !== "assembly") ? `<a class="btn primary" href="#/device/${plan.parent_manual_id || session.manual_id}${session.pack_code ? `?pack=${session.pack_code}` : ""}">Back to the ${plan.use_case === "training" ? "machine" : "device"}</a>` : ""}
        <a class="btn ${plan.use_case === "task" || (plan.use_case && plan.use_case !== "assembly") ? "" : "primary"}" href="#/team">See training records</a>
      </div>
      ${session.pack_code ? `<p class="small muted">Thanks: the mistakes you fixed now warn the next person at the same step.</p>` : ""}
    </div>`;
  speak(row.verified ? "Done. Every step is verified and saved to your training record." : "Session saved.");
}

// ---------------------------------------------------------------- car checks

async function carView(_, query) {
  const tab = query.get("tab") === "lights" ? "lights" : "bay";
  const manuals = (await api("/api/manuals")).filter((m) => m.use_case === "car" && m.status === "ready");
  const chosen = load("carManual", "");
  view.innerHTML = `
    <a class="back" href="#/">Home</a>
    <div class="stack">
      <h2>Car checks</h2>
      <p class="warn">Engine off, parked on level ground, and cool before you open anything. Never open the coolant cap when the engine is hot.</p>
      <div class="field"><label for="carmanual">Owner's manual</label>
        <select id="carmanual"><option value="">Standard parts list (no manual)</option>
          ${manuals.map((m) => `<option value="${m.id}" ${m.id === chosen ? "selected" : ""}>${esc(m.title)}</option>`).join("")}
        </select>
        <p class="small muted">Add your car's manual on the <a href="#/car?tab=${tab}&upload=1">upload form below</a> to get page numbers.</p>
      </div>
      <nav class="tabs" aria-label="Car check">
        <a href="#/car" ${tab === "bay" ? 'aria-current="page"' : ""}>Engine bay</a>
        <a href="#/car?tab=lights" ${tab === "lights" ? 'aria-current="page"' : ""}>Warning lights</a>
      </nav>
      <h3>${tab === "bay" ? "Open the hood and take one photo from the front." : "Turn the ignition on and photograph the dashboard."}</h3>
      <div id="area"></div>
      <div id="result"></div>
      <form class="stack" id="upload" ${query.get("upload") ? "" : "hidden"}>
        <h3>Add an owner's manual</h3>
        <div class="field"><label for="ctitle">Car</label><input id="ctitle" name="title" placeholder="2019 hatchback" required></div>
        <div class="field"><label for="cpdf">Manual (PDF)</label><input id="cpdf" name="file" type="file" accept="application/pdf" required></div>
        <input type="hidden" name="use_case" value="car">
        <button class="btn" type="submit">Upload and read</button>
        <p class="small muted">Long manuals are fine: ShowMe reads the pages about the engine bay and warning lights.</p>
      </form>
    </div>`;

  $("#carmanual").addEventListener("change", (e) => save("carManual", e.target.value));
  $("#upload").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/manuals", { method: "POST", form: new FormData(e.target) });
      toast("Uploaded. It appears in the list once it's read.");
      e.target.reset();
    } catch (err) { toast(err.message, "error"); }
  });

  const area = $("#area"), result = $("#result");
  const go = async () => {
    const file = await takePhoto();
    if (!file) return;
    busy(area, tab === "bay" ? "Labeling the engine bay" : "Reading the warning lights");
    result.innerHTML = "";
    const form = new FormData();
    form.append("photo", file);
    form.append("manual_id", $("#carmanual").value);
    form.append("mode", app.mode);
    try {
      if (tab === "bay") {
        const r = await api("/api/car/engine-bay", { method: "POST", form });
        area.innerHTML = "";
        const shot = makeShot(area, r.image, { badge: `${r.detections.length} parts labeled` });
        shot.show(r.detections);
        app.cleanup.push(() => shot.destroy());
        result.innerHTML = `<table class="counts"><tbody>${r.detections.map((d) =>
          `<tr><td><span class="part"><span class="callout">${esc(d.label)}</span>${esc(d.name)}</span></td><td class="num">${d.page ? `Manual p.${d.page}` : ""}</td></tr>`).join("")}</tbody></table>
          ${r.not_found.length ? `<p class="small muted">Not visible in this photo: ${esc(r.not_found.join(", "))}.</p>` : ""}`;
      } else {
        const r = await api("/api/car/warning-lights", { method: "POST", form });
        area.innerHTML = "";
        const shot = makeShot(area, r.image);
        shot.show([]);
        app.cleanup.push(() => shot.destroy());
        result.innerHTML = r.lights.length ? r.lights.map((l) => `
          <section class="verdict ${l.severity === "stop" ? "fail" : l.severity === "caution" ? "unsure" : "pass"}">
            <h3>${esc(l.name)}${l.color ? ` <span class="small muted">${esc(l.color)}</span>` : ""}</h3>
            <dl><dt>What it means</dt><dd>${esc(l.meaning)}</dd><dt>What to do</dt><dd>${esc(l.action)}</dd></dl>
            ${l.manual_page ? `<p class="meta">Owner's manual p.${l.manual_page}</p>` : ""}
          </section>`).join("") : `<p>No warning lights are lit.</p>`;
        if (r.lights[0]) speak(`${r.lights[0].name}. ${r.lights[0].action}`);
      }
    } catch (e) { area.innerHTML = ""; area.append(snapButton(go)); toast(e.message, "error"); }
  };
  area.append(snapButton(go, tab === "bay" ? "Photograph the engine bay" : "Photograph the dashboard"));
}

// ---------------------------------------------------------------- learn any idea

async function learnView() {
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  view.innerHTML = `
    <a class="back" href="#/">Home</a>
    <div class="stack">
      <h2>Learn any idea</h2>
      <p class="muted">Say what you want to understand, then photograph the room around you. The objects in it become the lesson.</p>
      <div class="field"><label for="concept">What do you want to understand?</label>
        <div class="row" style="flex-wrap:nowrap"><input id="concept" style="flex:1" placeholder="Why the Moon doesn't fall" autocomplete="off">
        ${Rec ? `<button class="btn" id="mic" type="button" aria-label="Say it">Say it</button>` : ""}</div></div>
      <div class="chips">${["orbits", "photosynthesis", "compound interest", "electric current"].map((c) => `<button class="chip" type="button" data-c="${c}">${c}</button>`).join("")}</div>
      <div id="area"></div>
      <div id="lesson"></div>
    </div>
    <div class="dock"><div class="inner"><button class="btn primary" id="go" type="button">Photograph your room</button></div></div>`;

  const input = $("#concept");
  view.querySelectorAll("[data-c]").forEach((b) => b.addEventListener("click", () => { input.value = b.dataset.c; }));
  if (Rec) {
    $("#mic").addEventListener("click", () => {
      const r = new Rec();
      r.lang = navigator.language || "en-US";
      r.onresult = (e) => { input.value = e.results[0][0].transcript; };
      r.onerror = () => toast("Couldn't hear that. Type it instead.");
      r.start();
      toast("Listening");
    });
  }

  const area = $("#area"), box = $("#lesson"), dock = $(".dock .inner");
  $("#go").addEventListener("click", async () => {
    const concept = input.value.trim();
    if (!concept) { input.focus(); toast("Type or say what you want to learn first."); return; }
    const file = await takePhoto();
    if (!file) return;
    busy(area, "Muse Spark is looking at your room, then Muse Wonder writes and fact-checks your lesson");
    box.innerHTML = "";
    const form = new FormData();
    form.append("concept", concept);
    form.append("mode", app.mode);
    form.append("photo", file);
    try {
      const r = await api("/api/lessons", { method: "POST", form });
      if (r.error) { area.innerHTML = ""; toast(r.error, "error"); return; }
      area.innerHTML = "";
      runLesson(r);
    } catch (e) { area.innerHTML = ""; toast(e.message, "error"); }
  });

  function runLesson(r) {
    const L = r.lesson;
    const shot = makeShot(area, r.image, { badge: L.title, tappable: true });
    shot.show([]);
    const player = playLesson(shot, r.objects);
    app.cleanup.push(() => shot.destroy());
    let i = 0;

    const cast = L.cast.map((c) => { const o = r.objects.find((x) => x.id === c.object_id); return o ? `${esc(o.name)} as ${esc(c.role)}` : ""; }).filter(Boolean).join(", ");

    const draw = () => {
      const b = L.beats[i];
      player.showBeat(b);
      box.innerHTML = `
        <div class="stack">
          <p class="beat-count">${i + 1} of ${L.beats.length}${cast ? `. Cast: ${cast}` : ""}</p>
          <p class="beat">${esc(b.say)}</p>
        </div>`;
      speak(b.say);
      dock.innerHTML = `<button class="btn" id="prev" type="button" ${i ? "" : "disabled"}>Back</button><button class="btn primary" id="next" type="button">${i < L.beats.length - 1 ? "Next" : L.quiz ? "Quiz me" : "Finish"}</button>`;
      $("#prev", dock).addEventListener("click", () => { i = Math.max(0, i - 1); draw(); });
      $("#next", dock).addEventListener("click", () => { if (i < L.beats.length - 1) { i++; draw(); } else if (L.quiz) quiz(); else wrap(); });
    };

    const quiz = () => {
      player.startQuiz(L.quiz.answer_object_id);
      box.innerHTML = `<div class="stack"><p class="beat">${esc(L.quiz.question)}</p><p class="muted small">Tap an object in the photo.</p></div>`;
      speak(L.quiz.question);
      dock.innerHTML = `<button class="btn" id="skip" type="button">Show the answer</button>`;
      const onTap = (e) => {
        const hit = shot.hit(e.clientX, e.clientY, r.objects);
        if (!hit) return;
        player.pick(hit.id);
        const right = hit.id === L.quiz.answer_object_id;
        box.insertAdjacentHTML("beforeend", `<section class="verdict ${right ? "pass" : "fail"}"><h3>${right ? "✓ Right" : "! Not that one"}</h3><p>${esc(L.quiz.explanation)}</p></section>`);
        speak((right ? "Right. " : "Not quite. ") + L.quiz.explanation);
        shot.canvas.removeEventListener("click", onTap);
        dock.innerHTML = `<button class="btn primary" id="end" type="button">Finish</button>`;
        $("#end", dock).addEventListener("click", wrap);
      };
      shot.canvas.addEventListener("click", onTap);
      $("#skip", dock).addEventListener("click", () => { player.pick(L.quiz.answer_object_id); box.insertAdjacentHTML("beforeend", `<p>${esc(L.quiz.explanation)}</p>`); dock.innerHTML = `<button class="btn primary" id="end" type="button">Finish</button>`; $("#end", dock).addEventListener("click", wrap); });
    };

    const wrap = () => {
      player.clear();
      box.innerHTML = `
        <div class="stack limits">
          <h3>Where this picture stops working</h3>
          <p>${esc(L.analogy_limits)}</p>
          <p class="meta">${r.review.approved ? "Fact-checked by Muse Wonder before you saw it." : `Muse Wonder corrected the first draft: ${esc(r.review.issues.join(" "))}`}</p>
        </div>`;
      speak(L.analogy_limits);
      dock.innerHTML = `<button class="btn" id="replay" type="button">Play again</button><a class="btn primary" href="#/learn">Learn something else</a>`;
      $("#replay", dock).addEventListener("click", () => { i = 0; draw(); });
    };

    draw();
  }
}

// ---------------------------------------------------------------- training records (employers) + proof it runs on Muse

const KIND_WORDS = { training: "Machine", device: "Device", assembly: "Assembly", task: "Task", car: "Car" };

async function teamView() {
  const rows = await api("/api/passport");
  const traces = await api("/api/traces?limit=60");
  // one card per person: what they're signed off on, and what's still in progress
  const people = new Map();
  for (const r of rows) {
    if (!people.has(r.person)) people.set(r.person, []);
    people.get(r.person).push(r);
  }
  const fmt = (iso) => (iso ? new Date(iso).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "");
  const verifiedCount = rows.filter((r) => r.verified).length;
  view.innerHTML = `
    <a class="back" href="#/">Home</a>
    <div class="stack-l">
      <div class="stack">
        <h2>Training records</h2>
        <p class="muted">Who is signed off on what. A sign-off means the camera checked every step against the manual; anything a person confirmed themselves is counted separately.</p>
        <div class="kpis">
          <div><b>${people.size}</b><span>${people.size === 1 ? "person" : "people"} trained</span></div>
          <div><b>${verifiedCount}</b><span>sign-offs</span></div>
          <div><b>${rows.reduce((a, r) => a + r.mistakes_caught, 0)}</b><span>mistakes caught and fixed</span></div>
        </div>
        <div class="row"><a class="btn" href="#/assemble?use=machine">Add a machine</a><a class="btn" href="#/brand">Put a QR guide on a machine</a><a class="btn quiet" href="/api/passport.csv" download>Download as a spreadsheet (CSV)</a></div>
      </div>
      ${people.size ? `<ul class="people">${[...people.entries()].map(([person, items]) => `
        <li class="person">
          <h3>${esc(person)} <span class="small muted">${items.filter((i) => i.verified).length} signed off</span></h3>
          <ul>${items.map((r) => `
            <li class="${r.verified ? "ok" : ""}">
              <span class="mark ${r.verified ? "pass" : "none"}" aria-hidden="true">${r.verified ? "✓" : "…"}</span>
              <span class="what"><strong>${esc(r.product)}</strong><span class="small muted">${KIND_WORDS[r.use_case] || ""}${r.use_case ? " · " : ""}${r.steps_passed} of ${r.steps_total} steps${r.mistakes_caught ? ` · ${r.mistakes_caught} mistake${r.mistakes_caught > 1 ? "s" : ""} caught` : ""}${r.overrides ? ` · ${r.overrides} confirmed by the person` : ""}</span></span>
              <span class="when small">${r.verified ? `Signed off ${fmt(r.finished || r.started)}` : `<span class="tag extra">In progress</span>`}</span>
            </li>`).join("")}</ul>
        </li>`).join("")}</ul>` : `<p>No training yet. <a href="#/assemble?use=machine">Add a machine</a> or <a href="#/assemble">assemble something</a>.</p>`}
      <details class="proof">
        <summary><strong>Proof it runs on Muse</strong> <span class="muted small">every call to Meta's models, live</span></summary>
        <p class="muted small"><span class="where cloud">cloud</span> sent to the Meta Model API, <span class="where local">local</span> stayed on your hardware${app.status?.mock ? `, <span class="where mock">mock</span> canned offline answer` : ""}. Also saved to <code>traces/</code>.</p>
        <ul class="traces" id="traces">${traces.map(traceRow).join("") || `<li class="muted">No calls yet.</li>`}</ul>
        <p class="small"><a href="/api/export/robot-data" download>Export every session's steps, photos and verdicts (JSON)</a></p>
      </details>
    </div>`;

  const list = $("#traces");
  const es = new EventSource("/api/traces/stream");
  es.onmessage = (e) => {
    const entry = JSON.parse(e.data);
    if (list.firstElementChild?.classList.contains("muted")) list.innerHTML = "";
    list.insertAdjacentHTML("afterbegin", traceRow(entry));
    list.firstElementChild.classList.add("flash");
  };
  app.cleanup.push(() => es.close());
}

function traceRow(t) {
  const time = new Date(t.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const what = String(t.purpose || "").replaceAll("_", " ");
  return `<li><span class="when">${time}</span>
    <span class="what"><b>${esc(t.model)}</b> ${esc(what)}<span class="where ${esc(t.tag)}">${esc(t.tag)}</span>${t.ok === false ? ` <span class="tag missing">${esc(t.error || "failed")}</span>` : ""}</span>
    <span class="ms">${t.latency_ms ?? "?"} ms</span></li>`;
}

// ---------------------------------------------------------------- machines and devices: tour, ask, guided tasks

async function startSession(manualId, packCode) {
  const person = load("person", "") || "Guest";
  const s = await api("/api/sessions", { method: "POST", json: { person, manual_id: manualId, mode: app.mode, pack_code: packCode || null } });
  return s;
}

async function deviceView([mid], query) {
  const manual = await api(`/api/manuals/${mid}`);
  if (manual.status !== "ready") throw new Error("This manual is still being read. Try again in a moment.");
  const plan = manual.plan;
  const pack = query.get("pack") || "";
  const kindNoun = manual.use_case === "training" ? "machine" : "device";
  view.innerHTML = `
    <a class="back" href="${pack ? `#/pack/${pack}` : `#/assemble?use=machine`}">${pack ? "Back" : `Change ${kindNoun}`}</a>
    <div class="stack-l">
      <div class="stack">
        <p class="muted small">${manual.use_case === "training" ? "Machine training: at work" : "Device guide: at home"}</p>
        <h1>${esc(plan.product)}</h1>
        ${plan.summary ? `<p class="lede">${esc(plan.summary)}</p>` : ""}
        ${plan.source?.kind === "listing" ? `<p class="tag extra source-tag">General guide from the ${esc(plan.source.retailer || "store")} listing, not the official manual</p>` : ""}
      </div>

      ${manual.use_case === "training" ? `<div class="field who"><label for="who">Your name <span class="muted small">(for training records)</span></label>
        <input id="who" value="${esc(load("person", ""))}" autocomplete="name" placeholder="First and last name"></div>` : ""}
      <section class="stack hub-ask">
        <h2>How do I…</h2>
        <form id="ask" class="ask-row">
          <label class="sr-only" for="q">Your question</label>
          <input id="q" name="q" placeholder="How do I descale it?" autocomplete="off">
          ${voiceSupported() ? `<button class="btn" id="mic" type="button" aria-label="Say your question">Say it</button>` : ""}
          <button class="btn primary" type="submit">Ask</button>
        </form>
        ${plan.tasks?.length ? `<div class="chips">${plan.tasks.map((t) => `<button type="button" class="chip" data-q="${esc(t)}">${esc(t)}</button>`).join("")}</div>` : ""}
        <div id="answer"></div>
      </section>

      <section class="stack">
        <h2>What does each button do?</h2>
        <p class="muted">Take one photo of the ${kindNoun}. ShowMe labels every control the manual names, so you can tap any of them.</p>
        <div id="tour-area"><button class="capture" id="tour" type="button">${CAMERA_ICON}<strong>Photograph the ${kindNoun}</strong><span class="muted small">${plan.parts.length} controls in the manual</span></button></div>
        <ul class="controls" id="controls"></ul>
      </section>

      ${plan.steps.length ? `<section class="stack">
        <h2>${manual.use_case === "training" ? "Start-up procedure" : "First-time setup"}</h2>
        <p class="muted">${plan.steps.length} steps, each checked by the camera.</p>
        <ol class="mini-steps">${plan.steps.map((st) => `<li>${esc(st.title)}</li>`).join("")}</ol>
        <div><button class="btn" id="setup" type="button">Guide me through setup</button></div>
      </section>` : ""}
    </div>`;

  $("#who")?.addEventListener("change", (e) => save("person", e.target.value.trim()));
  // ---- ask
  const answer = $("#answer");
  async function ask(question) {
    if (!question.trim()) { $("#q").focus(); return; }
    $("#q").value = question;
    busy(answer, "Muse Wonder is looking it up in the manual");
    try {
      const r = await api(`/api/manuals/${mid}/ask`, { method: "POST", json: { question } });
      answer.innerHTML = `
        <section class="answer ${r.in_manual ? "" : "not-in"}" aria-live="polite">
          <p class="a-text">${esc(r.answer)}</p>
          ${r.pages?.length ? `<p class="small muted">From the manual, page${r.pages.length > 1 ? "s" : ""} ${r.pages.join(", ")}.${r.cached ? " Asked before, so this answer was instant." : ""}</p>` : ""}
          ${r.safety?.length ? `<p class="warn">${r.safety.map(esc).join(" ")}</p>` : ""}
          ${r.steps.length ? `<ol class="mini-steps">${r.steps.map((st) => `<li><strong>${esc(st.title)}.</strong> ${esc(st.instruction)}</li>`).join("")}</ol>
            <div class="row"><button class="btn primary" id="guide" type="button">Guide me through it</button><span class="small muted">The camera checks each step as you do it.</span></div>` : ""}
          ${r.in_manual ? "" : `<p class="small muted">ShowMe only answers from this ${kindNoun}'s manual, so it won't guess.</p>`}
        </section>`;
      speak(r.answer);
      $("#guide", answer)?.addEventListener("click", async () => {
        try {
          const s = await startSession(r.manual_id, pack);
          location.hash = `#/session/${s.id}/step/1`;
        } catch (e) { toast(e.message, "error"); }
      });
    } catch (e) { answer.innerHTML = ""; toast(e.message, "error"); }
  }
  $("#ask").addEventListener("submit", (e) => { e.preventDefault(); ask($("#q").value); });
  view.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => ask(c.dataset.q)));
  $("#mic")?.addEventListener("click", async (e) => {
    e.target.textContent = "Listening…";
    const said = await dictate();
    e.target.textContent = "Say it";
    if (said) ask(said);
  });

  // ---- tour
  const tourArea = $("#tour-area"), list = $("#controls");
  let shot = null;
  const tour = async () => {
    const file = await takePhoto();
    if (!file) return;
    busy(tourArea, `Finding every control on the ${kindNoun}`);
    const form = new FormData();
    form.append("photo", file);
    form.append("mode", app.mode);
    try {
      const r = await api(`/api/manuals/${mid}/tour`, { method: "POST", form });
      tourArea.innerHTML = "";
      shot?.destroy();
      shot = makeShot(tourArea, r.image, { badge: `${r.detections.length} controls found` });
      app.cleanup.push(() => shot?.destroy());
      shot.show(r.detections);
      list.innerHTML = r.controls.map((c) => `
        <li><button type="button" data-label="${esc(c.label)}" ${c.found ? "" : "disabled"}>
          <span class="callout ${c.found ? "filled" : ""}">${esc(c.label)}</span>
          <span><strong>${esc(c.name)}</strong>${c.description ? `<span class="muted"> ${esc(c.description)}</span>` : ""}${c.found ? "" : `<span class="small muted"> Not in this photo.</span>`}</span>
        </button></li>`).join("");
      list.querySelectorAll("button[data-label]").forEach((b) => b.addEventListener("click", () => {
        const det = r.detections.find((d) => d.label === b.dataset.label);
        const c = r.controls.find((x) => x.label === b.dataset.label);
        shot.show(r.detections, { focus: det?.box || null });
        tourArea.scrollIntoView({ behavior: "smooth", block: "start" });
        speak(`${c.name}. ${c.description}`);
      }));
      const again = snapButton(tour, "Take the photo again");
      again.classList.add("small-capture");
      tourArea.append(again);
      speak(`Found ${r.detections.length} controls. Tap any of them to hear what it does.`);
    } catch (e) {
      tourArea.innerHTML = "";
      tourArea.append(snapButton(tour));
      toast(e.message, "error");
    }
  };
  $("#tour").addEventListener("click", tour);

  $("#setup")?.addEventListener("click", async () => {
    try {
      const s = await startSession(mid, pack);
      location.hash = `#/session/${s.id}/step/${plan.steps[0].order}`;
    } catch (e) { toast(e.message, "error"); }
  });
}

// ---------------------------------------------------------------- QR landing: what scanning the box opens

async function packView([code]) {
  const p = await api(`/api/packs/${encodeURIComponent(code)}`);
  const kind = { assembly: "Assembly guide", training: "Machine training", device: "Device guide", car: "Car guide" }[p.kind] || "Guide";
  view.innerHTML = `
    <div class="stack-l pack-landing">
      <div class="stack">
        <p class="pack-brand">${p.brand ? esc(p.brand) : "ShowMe"} · ${kind}</p>
        <h1>${esc(p.product)}</h1>
        ${p.summary ? `<p class="lede">${esc(p.summary)}</p>` : ""}
        <ul class="pack-facts">
          ${p.steps ? `<li><b>${p.steps}</b> steps, each checked by your camera</li>` : ""}
          ${p.median_minutes >= 1 ? `<li>People finish in about <b>${Math.round(p.median_minutes)} min</b></li>` : ""}
          ${p.tips ? `<li><b>${p.tips}</b> heads-up${p.tips > 1 ? "s" : ""} from people who built this before you</li>` : ""}
          ${p.scenes?.finished ? `<li>3D animation for every step, ready</li>` : ""}
        </ul>
      </div>
      <form class="stack" id="go">
        <div class="field"><label for="person">Your first name <span class="muted small">(optional)</span></label><input id="person" value="${esc(load("person", ""))}" autocomplete="given-name"></div>
        <label class="toggle"><input type="checkbox" id="hf" ${app.handsFree || !load("handsfree", "") ? "checked" : ""}> <span><strong>Hands-free</strong><br><span class="small muted">Prop your phone up. It talks you through each step, watches your work, and moves on when it's right. Say "next", "repeat" or "check".</span></span></label>
        <button class="btn primary big" type="submit">${p.kind === "assembly" ? "Start building" : "Start"}</button>
      </form>
      ${p.safety?.length ? `<p class="warn">${p.safety.map(esc).join(" ")}</p>` : ""}
      <p class="small muted">Guide code ${esc(p.code)}. Built from the manual by Meta's Muse models.</p>
    </div>`;
  $("#go").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#person").value.trim();
    if (name) save("person", name);
    setHandsFree($("#hf").checked);
    if (p.kind !== "assembly") { location.hash = `#/device/${p.manual_id}?pack=${p.code}`; return; }
    try {
      const s = await startSession(p.manual_id, p.code);
      location.hash = `#/session/${s.id}/inventory`;
    } catch (err) { toast(err.message, "error"); }
  });
}

// ---------------------------------------------------------------- brands: publish QR guides, see where people struggle

async function brandView([code, sub]) {
  if (code && sub === "print") return printLabel(code);
  if (code) return brandPack(code);
  const [packs, manuals] = await Promise.all([api("/api/packs"), api("/api/manuals")]);
  const published = new Set(packs.map((p) => p.manual_id));
  const ready = manuals.filter((m) => m.status === "ready" && m.use_case !== "task" && !published.has(m.id));
  view.innerHTML = `
    <a class="back" href="#/">Home</a>
    <div class="stack-l">
      <div class="stack">
        <h2>QR guides</h2>
        <p class="muted">Publish a manual once. Print the QR code on the box, or stick it on the machine. Anyone who scans it starts straight away: no PDF, no upload, every 3D scene already built.</p>
        ${packs.length ? `<ul class="list packs">${packs.map((p) => `
          <li><a class="pack-row" href="#/brand/${p.code}">
            <img src="${p.qr_url}" alt="" width="56" height="56">
            <div><div class="title">${esc(p.product)}</div>
              <div class="status">${esc(p.brand || "No brand set")} · ${p.builders} ${p.builders === 1 ? "person" : "people"} · ${p.finished} finished${p.median_minutes ? ` · about ${Math.round(p.median_minutes)} min` : ""}</div></div>
            <span class="go" aria-hidden="true">›</span></a></li>`).join("")}</ul>` : `<p class="muted">Nothing published yet.</p>`}
      </div>
      <form class="stack" id="publish">
        <h3>Publish a manual as a QR guide</h3>
        ${ready.length ? `
        <div class="field"><label for="pm">Manual</label><select id="pm">${ready.map((m) => `<option value="${m.id}">${esc(m.title)} (${esc(m.use_case)})</option>`).join("")}</select></div>
        <div class="field"><label for="pb">Brand name <span class="muted small">(shown to shoppers)</span></label><input id="pb" placeholder="Nordlund Home" autocomplete="organization"></div>
        <button class="btn primary" type="submit">Publish and make the QR code</button>
        <p class="small muted">Publishing builds the 3D scene for every step in the background, so nobody waits later.</p>`
        : `<p class="muted">Every manual that's ready is already published. Add one under <a href="#/assemble">Assemble something</a> or <a href="#/assemble?use=machine">Learn a machine or device</a>.</p>`}
      </form>
    </div>`;
  $("#publish").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!$("#pm")) return;
    try {
      const p = await api("/api/packs", { method: "POST", json: { manual_id: $("#pm").value, brand: $("#pb").value } });
      location.hash = `#/brand/${p.code}`;
    } catch (err) { toast(err.message, "error"); }
  });
}

async function brandPack(code) {
  const [p, st] = await Promise.all([api(`/api/packs/${code}`), api(`/api/packs/${code}/stats`)]);
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  const maxRate = Math.max(0.0001, ...st.steps.map((r) => r.rate));
  view.innerHTML = `
    <a class="back" href="#/brand">All QR guides</a>
    <div class="stack-l">
      <div class="pack-head">
        <div class="stack">
          <p class="muted small">${esc(p.brand || "No brand set")} · guide ${esc(p.code)}</p>
          <h2>${esc(p.product)}</h2>
          <p class="small"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.url)}</a></p>
          <p class="small muted">${p.scenes?.total ? (p.scenes.finished ? `3D scenes ready: ${p.scenes.wonder} of ${p.scenes.total} built by Muse Wonder.` : `Building 3D scenes: ${p.scenes.done} of ${p.scenes.total}…`) : ""}</p>
          <div class="row"><a class="btn" href="#/pack/${p.code}">Open as a shopper</a><a class="btn" href="#/brand/${p.code}/print">Print the box label</a></div>
        </div>
        <img class="qr" src="${p.qr_url}" alt="QR code that opens this guide">
      </div>

      <div class="kpis">
        <div><b>${st.sessions}</b><span>people started</span></div>
        <div><b>${st.finished}</b><span>finished, every step checked</span></div>
        <div><b>${st.median_minutes == null ? "–" : st.median_minutes < 1 ? "<1 min" : `${Math.round(st.median_minutes)} min`}</b><span>typical ${st.kind === "assembly" ? "build" : "session"} time</span></div>
        <div><b>${st.mistakes_caught}</b><span>mistakes caught before they mattered</span></div>
      </div>

      ${st.hardest_step ? `<section class="verdict fail"><h3>Where people struggle: step ${st.hardest_step.order}, ${esc(st.hardest_step.title)}</h3>
        <p>${pct(st.hardest_step.rate)} of people who reached it got it wrong at first.${st.hardest_step.tip ? ` ${esc(st.hardest_step.tip)}` : ""}</p>
        <p class="small muted">Worth fixing in the printed manual: clearer drawing, or a callout at this step.</p></section>` : ""}

      <section class="stack">
        <h3>Mistakes by step</h3>
        <p class="small muted">Share of people who reached each step and got it wrong at least once. New shoppers hear the heads-up before the step.</p>
        <ol class="bars">${st.steps.map((r) => `
          <li><span class="b-label">${r.order}. ${esc(r.title)}</span>
            <span class="b-track"><span class="b-fill" style="width:${Math.round((r.rate / maxRate) * 100)}%"></span></span>
            <span class="b-num">${r.builders ? `${pct(r.rate)} <span class="muted">(${r.mistakes}/${r.builders})</span>` : `<span class="muted">no data</span>`}</span>
            ${r.tip ? `<span class="b-tip">Heads-up given: ${esc(r.tip)}</span>` : ""}</li>`).join("")}</ol>
      </section>

      ${st.questions.length ? `<section class="stack">
        <h3>Questions people asked</h3>
        <p class="small muted">What owners wanted to do that the manual didn't make obvious.</p>
        <ul class="list">${st.questions.map((q) => `<li><div><div class="title">${esc(q.question)}</div><div class="status">${q.in_manual ? `${q.guided} guided session${q.guided === 1 ? "" : "s"}` : "Not covered by the manual"}</div></div></li>`).join("")}</ul>
      </section>` : ""}
    </div>`;
  if (p.scenes?.total && !p.scenes.finished) {
    const t = setTimeout(() => { if (location.hash === `#/brand/${code}`) render(); }, 3000);
    app.cleanup.push(() => clearTimeout(t));
  }
}

async function printLabel(code) {
  const p = await api(`/api/packs/${code}`);
  document.body.classList.add("printing");
  view.innerHTML = `
    <div class="label-sheet">
      <div class="label">
        <img src="${p.qr_url}" alt="QR code">
        <div>
          <p class="l-kicker">${esc(p.brand || "")}</p>
          <p class="l-title">Scan to ${p.kind === "assembly" ? "build" : "learn"} it with a live guide</p>
          <p class="l-sub">${esc(p.product)}. Your phone's camera checks every step.</p>
          <p class="l-code">Code ${esc(p.code)}</p>
        </div>
      </div>
      <div class="row no-print"><button class="btn primary" id="print" type="button">Print</button><a class="btn" href="#/brand/${p.code}">Back</a></div>
    </div>`;
  $("#print").addEventListener("click", () => window.print());
}


// ---------------------------------------------------------------- scan a ShowMe code on a box

async function scanView() {
  view.innerHTML = `
    <a class="back" href="#/">Home</a>
    <div class="stack">
      <h2>Scan the code on the box</h2>
      <p class="muted" id="scan-hint">${liveSupported() ? "Point the camera at the ShowMe QR code. It opens by itself." : "Take a photo of the ShowMe QR code."}</p>
      <div id="scan-area"></div>
      <p id="scan-result" aria-live="polite"></p>
      <form class="stack" id="code-form">
        <div class="field"><label for="code">Or type the code printed under it</label>
          <div class="ask-row"><input id="code" maxlength="12" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="6SC5B8" inputmode="text"><button class="btn primary" type="submit">Open</button></div>
        </div>
      </form>
    </div>`;
  const area = $("#scan-area"), result = $("#scan-result");
  let scanner = null;
  app.cleanup.push(() => scanner?.stop());

  const open = (code) => {
    scanner?.stop();
    speak("Found it. Opening the guide.");
    location.hash = `#/pack/${code}`;
  };
  const handle = (text) => {
    const code = parseGuideCode(text);
    if (code) { open(code); return true; }
    result.className = "verdict unsure";
    const isWeb = /^https?:\/\//i.test(String(text).trim());
    result.innerHTML = `That QR code isn't a ShowMe guide. It says: <span class="mono">${esc(String(text).slice(0, 140))}</span>
      ${isWeb ? `<span class="row" style="margin-top:10px"><a class="btn primary" href="#/link?u=${encodeURIComponent(String(text).trim())}">Get setup instructions from this link</a></span>` : ""}`;
    return false;
  };
  const photoButton = (label) => {
    const b = snapButton(async () => {
      const file = await takePhoto();
      if (!file) return;
      result.className = "";
      result.textContent = "Reading the code…";
      try {
        const text = await readPhoto(file);
        if (!text) { result.className = "verdict unsure"; result.textContent = "No QR code found in that photo. Fill the frame with the code and try again, or type the code below."; return; }
        handle(text);
      } catch (e) { result.className = "verdict unsure"; result.textContent = e.message; }
    }, label);
    return b;
  };

  if (liveSupported()) {
    try {
      scanner = await startScanner(area, { onText: handle });
      const alt = document.createElement("button");
      alt.type = "button";
      alt.className = "btn quiet";
      alt.textContent = "Use a photo instead";
      alt.addEventListener("click", () => { scanner?.stop(); area.innerHTML = ""; area.append(photoButton("Take a photo of the code")); });
      area.append(alt);
    } catch (e) {
      area.innerHTML = "";
      $("#scan-hint").textContent = `The camera didn't start (${e.message}). Take a photo of the code instead.`;
      area.append(photoButton("Take a photo of the code"));
    }
  } else {
    area.append(photoButton("Take a photo of the code"));
  }

  $("#code-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const code = parseGuideCode($("#code").value);
    if (code) open(code);
    else { result.className = "verdict unsure"; result.textContent = "That doesn't look like a ShowMe code. It's six letters and numbers, like 6SC5B8."; }
  });
}


// ---------------------------------------------------------------- bought online: a store link becomes a guide

const RETAILER_HINT = "Amazon, IKEA, Walmart, Flipkart, Best Buy or any shop";

async function linkView(_, query) {
  const preset = query.get("u") || "";
  view.innerHTML = `
    <a class="back" href="#/">Home</a>
    <div class="stack-l">
      <div class="stack">
        <h2>Set up something you bought online</h2>
        <p class="muted">Paste the product's link from ${RETAILER_HINT}. If the page has the official manual, ShowMe reads it and guides every step. If not, you get a general setup guide from the listing, clearly marked as such.</p>
      </div>
      <form class="stack" id="link-form">
        <div class="field"><label for="url">Product link</label>
          <div class="ask-row"><input id="url" value="${esc(preset)}" placeholder="https://www.amazon.com/…/dp/B0…" autocomplete="off" inputmode="url" spellcheck="false">
          ${navigator.clipboard?.readText ? `<button class="btn" id="paste" type="button">Paste</button>` : ""}</div>
        </div>
        <div class="field"><label for="pname">Product name <span class="muted small">(optional: helps when a store blocks reading its page)</span></label>
          <input id="pname" placeholder="Brand and model, e.g. Furinno 11055 corner shelf" autocomplete="off"></div>
        <button class="btn primary" type="submit">Get the setup guide</button>
      </form>
      <div id="link-out" aria-live="polite"></div>
    </div>`;
  const out = $("#link-out");
  let timer = null;
  app.cleanup.push(() => clearTimeout(timer));

  $("#paste")?.addEventListener("click", async () => {
    try { $("#url").value = (await navigator.clipboard.readText()).trim(); } catch { toast("Your browser didn't allow pasting. Long-press the box and paste."); }
  });

  async function watch(mid) {
    const m = await api(`/api/manuals/${mid}`);
    if (m.status === "processing") {
      out.innerHTML = `<div class="busy" role="status">${esc(m.progress || "Working")}</div>`;
      timer = setTimeout(() => watch(mid), 1200);
      return;
    }
    if (m.status === "error") {
      out.innerHTML = `<section class="verdict unsure"><h3>? Couldn't build a guide from that link</h3><p>${esc(m.error)}</p></section>`;
      $("#pname").focus();
      return;
    }
    showResult(m);
  }

  function showResult(m) {
    const plan = m.plan, src = plan.source || {};
    const official = src.kind === "manual_pdf";
    const kindWord = { assembly: "Assembly", device: "Device", training: "Machine" }[m.use_case] || "Guide";
    out.innerHTML = `
      <section class="product-card">
        ${src.image ? `<img src="${esc(src.image)}" alt="" referrerpolicy="no-referrer" loading="lazy" onerror="this.remove()">` : ""}
        <div class="stack">
          <p class="pack-brand">${esc(src.retailer || "Online store")}${src.product_id ? ` · ${esc(src.product_id)}` : ""} · ${kindWord}</p>
          <h3>${esc(plan.product)}</h3>
          <p class="${official ? "tag ok" : "tag extra"} source-tag">${official ? `Official manual found: ${m.page_count} page${m.page_count === 1 ? "" : "s"} read` : "From the store listing, not the official manual"}</p>
          ${plan.summary ? `<p class="muted">${esc(plan.summary)}</p>` : ""}
        </div>
      </section>
      ${!official && plan.note ? `<p class="warn">${esc(plan.note)}</p>` : ""}
      ${plan.needs_manual ? `<section class="verdict unsure"><h3>For the exact steps, add the instructions</h3>
        <p>The store page doesn't include ${m.use_case === "assembly" ? "the assembly order" : "the full instructions"}. Photograph each page of the paper instructions from the box (or upload the PDF) and ShowMe guides every step.</p>
        <a class="btn" href="#/assemble?use=${m.use_case === "assembly" ? "assembly" : m.use_case}">Add the instructions</a></section>` : ""}
      <ol class="mini-steps">${plan.steps.map((s) => `<li><strong>${esc(s.title)}.</strong> ${esc(s.instruction)}</li>`).join("")}</ol>
      <div class="row"><button class="btn primary" id="go" type="button">${m.use_case === "assembly" ? (official ? "Start building" : "Start preparing") : m.use_case === "training" ? "Open the machine guide" : "Open the device guide"}</button>
        ${src.url ? `<a class="btn quiet" href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">Store page</a>` : ""}</div>`;
    speak(official ? `Found the official manual for the ${plan.product}.` : `Here's a general setup guide for the ${plan.product}, from the store listing.`);
    $("#go", out).addEventListener("click", async () => {
      if (m.use_case !== "assembly") { location.hash = `#/device/${m.id}`; return; }
      try {
        const s = await startSession(m.id, null);
        location.hash = `#/session/${s.id}/${official ? "inventory" : `step/${plan.steps[0].order}`}`;
      } catch (e) { toast(e.message, "error"); }
    });
  }

  $("#link-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("#url").value.trim();
    if (!url) { $("#url").focus(); return; }
    clearTimeout(timer);
    busy(out, "Reading the product page");
    try {
      const r = await api("/api/links", { method: "POST", json: { url, name: $("#pname").value.trim() } });
      watch(r.manual_id);
    } catch (err) {
      out.innerHTML = `<section class="verdict unsure"><h3>? That link didn't work</h3><p>${esc(err.message)}</p></section>`;
    }
  });
  if (preset) $("#link-form").requestSubmit();
}

// ---------------------------------------------------------------- start

loadStatus().catch((e) => toast(e.message, "error")).finally(render);
