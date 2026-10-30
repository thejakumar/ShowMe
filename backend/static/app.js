// Apprentice web app: phone-first screens for assembly, machine training, car checks,
// concept lessons and the team passport. No build step; plain modules.

import { makeShot } from "./overlay.js";
import { playLesson } from "./lesson.js";
import { liveSupported, startLive } from "./live.js";
import { createViewer, webxrARSupported } from "./viewer3d.js";

const view = document.getElementById("view");
const cameraInput = document.getElementById("camera");
const app = { status: null, mode: load("mode", "cloud"), voice: load("voice", "on") === "on", cleanup: [] };

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

function speak(text) {
  if (!app.voice || !("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.02;
  speechSynthesis.speak(u);
}

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

const routes = { "": home, assemble, session: sessionView, car: carView, learn: learnView, team: teamView };

async function render() {
  app.cleanup.forEach((fn) => fn());
  app.cleanup = [];
  if ("speechSynthesis" in window) speechSynthesis.cancel();
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
  team: `<svg viewBox="0 0 64 64"><rect x="10" y="10" width="44" height="44" rx="4"/><path d="M18 24h28M18 34h28M18 44h16"/><path d="M42 42l4 4 8-9"/></svg>`,
};

async function home() {
  view.innerHTML = `
    <h1>Every product comes with an expert.</h1>
    <p class="lede">Point your phone at the real thing. Apprentice reads the manual, finds each part, and checks every step you do.</p>
    <ul class="uses">
      <li class="lead"><a href="#/assemble">${ICONS.stool}<div><h3>Assemble something</h3><p>Flat-pack furniture from a pile of loose parts to finished, with every part counted and every step checked.</p></div><span class="go" aria-hidden="true">›</span></a></li>
      <li><a href="#/assemble?use=training">${ICONS.machine}<div><h3>Train on a machine</h3><p>Learn a work machine from its manual. Each verified step goes on your skill passport.</p></div><span class="go" aria-hidden="true">›</span></a></li>
      <li><a href="#/car">${ICONS.car}<div><h3>Car checks</h3><p>Find the dipstick, coolant and washer fluid from your owner's manual, and read warning lights.</p></div><span class="go" aria-hidden="true">›</span></a></li>
      <li><a href="#/learn">${ICONS.orbit}<div><h3>Learn any idea</h3><p>Ask about orbits, electricity or interest. The objects in your room become the lesson.</p></div><span class="go" aria-hidden="true">›</span></a></li>
      <li><a href="#/team">${ICONS.team}<div><h3>Team passport</h3><p>Who is trained on what, every model call, and robot-ready training data.</p></div><span class="go" aria-hidden="true">›</span></a></li>
    </ul>`;
}

// ---------------------------------------------------------------- pick a manual and start

async function assemble(_, query) {
  const use = query.get("use") === "training" ? "training" : "assembly";
  const noun = use === "training" ? "machine" : "product";
  view.innerHTML = `
    <a class="back" href="#/">Home</a>
    <div class="stack-l">
      <div class="stack">
        <h2>${use === "training" ? "Which machine?" : "What are you building?"}</h2>
        <p class="muted">Pick a manual Apprentice has already read, or upload the PDF that came with your ${noun}.</p>
        <ul class="list" id="manuals"><li class="muted">Loading manuals</li></ul>
        ${use === "assembly" ? `<button class="btn" id="sample" type="button">Try the sample step stool</button>` : ""}
      </div>
      <form class="stack" id="upload">
        <h3>Upload a manual</h3>
        <div class="field"><label for="title">Name</label><input id="title" name="title" placeholder="${use === "training" ? "Espresso machine" : "Oak bookshelf"}" autocomplete="off"></div>
        <div class="field"><label for="pdf">Manual (PDF)</label><input id="pdf" name="file" type="file" accept="application/pdf" required></div>
        <input type="hidden" name="use_case" value="${use}">
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
    const mine = all.filter((m) => m.use_case === use);
    list.innerHTML = mine.length ? mine.map((m) => `
      <li>
        <div><div class="title">${esc(m.title)}</div>
          <div class="status ${m.status === "error" ? "error" : ""}">${m.status === "ready" ? `${m.page_count} pages, ready` : m.status === "error" ? esc(m.error || "Could not read this manual") : esc(m.progress)}</div></div>
        ${m.status === "ready" ? `<button class="btn" data-pick="${m.id}" data-title="${esc(m.title)}" type="button">Choose</button>` : m.status === "processing" ? `<span class="busy small" aria-label="Reading"></span>` : ""}
      </li>`).join("") : `<li class="muted">No ${noun} manuals yet. Upload one below${use === "assembly" ? ", or try the sample" : ""}.</li>`;
    list.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => pick(b.dataset.pick, b.dataset.title)));
    clearTimeout(timer);
    if (mine.some((m) => m.status === "processing")) timer = setTimeout(refresh, 1500);
  }
  app.cleanup.push(() => clearTimeout(timer));

  function pick(id, title) {
    chosen = id;
    $("#start").hidden = false;
    $("#start-title").textContent = title;
    $("#person").focus();
    $("#start").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  $("#sample")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const m = await api("/api/manuals/sample", { method: "POST" });
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
      location.hash = `#/session/${s.id}/${use === "assembly" ? "inventory" : "step/1"}`;
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
  if (stage === "inventory") return inventoryStage(session, plan);
  if (stage === "step") return stepStage(session, plan, Number(n) || 1);
  return doneStage(session, plan);
}

function progressBar(plan, current) {
  // segment 0 is the parts check; then one per step
  const total = plan.steps.length + 1;
  return `<div class="stepbar" aria-label="Progress">${Array.from({ length: total }, (_, i) =>
    `<span class="${i < current ? "done" : i === current ? "now" : ""}"></span>`).join("")}</div>`;
}

function inventoryStage(session, plan) {
  view.innerHTML = `
    <a class="back" href="#/assemble">Change product</a>
    ${progressBar(plan, 0)}
    <div class="stack">
      <h2>Check the parts</h2>
      <p class="muted">Lay every part out on the floor, spread apart, and take one photo from above. Apprentice counts each one against the manual's parts list.</p>
      ${plan.reference_url ? `<details><summary class="small">The manual's parts list</summary><img class="figure" src="${plan.reference_url}" alt="Parts drawings from the manual"></details>` : ""}
      <div id="area">
        <button class="capture" id="snap" type="button">${CAMERA_ICON}<strong>Take the parts photo</strong><span class="muted small">${plan.parts.length} part types, ${plan.parts.reduce((a, p) => a + (p.quantity || 1), 0)} pieces expected</span></button>
      </div>
      <div id="result"></div>
    </div>
    <div class="dock"><div class="inner">
      <a class="btn" href="#/session/${session.id}/step/1">Skip the parts check</a>
    </div></div>`;

  const area = $("#area"), result = $("#result"), dock = $(".dock .inner");
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

function stepStage(session, plan, order) {
  const step = plan.steps.find((s) => s.order === order) || plan.steps[0];
  const idx = plan.steps.indexOf(step);
  const last = idx === plan.steps.length - 1;
  const names = Object.fromEntries(plan.parts.map((p) => [p.label, p.name]));
  view.innerHTML = `
    <a class="back" href="#/session/${session.id}/${idx ? `step/${plan.steps[idx - 1].order}` : "inventory"}">${idx ? `Back to step ${plan.steps[idx - 1].order}` : "Back to the parts check"}</a>
    ${progressBar(plan, idx + 1)}
    <div class="stack">
      <div class="step-head"><span class="step-no">${step.order}</span><div><p class="muted small">Step ${idx + 1} of ${plan.steps.length}</p><h2>${esc(step.title)}</h2></div></div>
      <div class="viewer">
        <nav class="tabs view-tabs" aria-label="How to see this step">
          <button type="button" data-view="manual" aria-current="true">Manual</button>
          <button type="button" data-view="3d">3D</button>
          <button type="button" data-view="ar">AR</button>
        </nav>
        <div id="stage" class="stage"></div>
        <div id="stage-bar" class="stage-bar" hidden></div>
      </div>
      <p style="font-size:18px">${esc(step.instruction)}</p>
      ${step.parts_used?.length ? `<div class="uses-strip">${step.parts_used.map((u) => `<span><span class="callout">${esc(u.label)}</span>${u.quantity}x ${esc(names[u.label] || "")}</span>`).join("")}</div>` : ""}
      ${step.warning ? `<p class="warn">${esc(step.warning)}</p>` : ""}
      ${step.why ? `<p class="why">Why it matters: ${esc(step.why)}</p>` : ""}
      <p class="muted small">Done when: ${esc(step.done_when)}</p>
      <div class="row"><button class="btn quiet" id="voice" type="button">${app.voice ? "Stop reading aloud" : "Read steps aloud"}</button></div>
      <div id="area"></div>
      <div id="verdict"></div>
    </div>
    <div class="dock"><div class="inner">
      ${step.parts_used?.length ? `<button class="btn" id="parts" type="button">Show the parts</button>` : ""}
      <button class="btn primary" id="check" type="button">Check my work</button>
    </div></div>`;

  speak(`Step ${step.order}. ${step.instruction}`);
  $("#voice").addEventListener("click", (e) => {
    app.voice = !app.voice;
    save("voice", app.voice ? "on" : "off");
    e.target.textContent = app.voice ? "Stop reading aloud" : "Read steps aloud";
    if (!app.voice && "speechSynthesis" in window) speechSynthesis.cancel();
    else speak(step.instruction);
  });

  const area = $("#area"), verdictBox = $("#verdict"), dock = $(".dock .inner");
  let shot = null;

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
  const say = (text) => {
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
      viewer = createViewer(stage, data.animation, { onSay: say, onEnd });
      return;
    }
    // AR: real placement with WebXR where the phone supports it; otherwise the animation over the live camera
    if (await webxrARSupported()) {
      controlsBar(data, `<button class="btn primary" id="place" type="button">Place it in your room</button>`);
      viewer = createViewer(stage, data.animation, { onSay: say, onEnd });
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
    viewer = createViewer(layer, data.animation, { transparent: true, fill: true, onSay: say, onEnd });
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
    const stop = () => live?.stop();
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
          if (r.say && r.say !== lastSay) { speak(r.say); lastSay = r.say; }
          history.push({ observed: r.observed, say: r.say });
          if (r.verdict === "pass") { plan_.hidden = true; showVerdict(r); return true; }
          return false;
        },
        onError: (e) => { coach.querySelector(".meta").textContent = `That frame didn't go through (${e.message}). Trying again.`; },
      });
    } catch (e) {
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
      speak(last ? "All steps checked. Nice work." : "Step checked. On to the next one.");
      dock.innerHTML = `<a class="btn primary" href="${next}">${nextLabel}</a>`;
    } else if (r.verdict === "fail" || r.verdict === "progress") {
      speak(r.verdict === "progress"
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
        <h1>${row.verified ? `${esc(row.person)} built the ${esc(plan.product.toLowerCase())}.` : "Session saved."}</h1>
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
        <a class="btn primary" href="#/team">See the team passport</a>
        <a class="btn" href="/api/export/robot-data" download>Download robot data</a>
      </div>
    </div>`;
  speak(row.verified ? "Done. Every step is verified and saved to your skill passport." : "Session saved.");
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
        <p class="small muted">Long manuals are fine: Apprentice reads the pages about the engine bay and warning lights.</p>
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

// ---------------------------------------------------------------- team passport + live trace

async function teamView() {
  const rows = await api("/api/passport");
  const traces = await api("/api/traces?limit=60");
  view.innerHTML = `
    <a class="back" href="#/">Home</a>
    <div class="stack-l">
      <div class="stack">
        <h2>Team passport</h2>
        <p class="muted">Every step here was checked by the camera, or confirmed by the person when the camera wasn't sure.</p>
        ${rows.length ? `<div class="table-wrap"><table class="passport">
          <thead><tr><th>Person</th><th>Product or machine</th><th>Steps checked</th><th>Mistakes caught</th><th>Status</th></tr></thead>
          <tbody>${rows.map((r) => `<tr><td><strong>${esc(r.person)}</strong></td><td>${esc(r.product)}</td><td class="num">${r.steps_passed} of ${r.steps_total}</td><td class="num">${r.mistakes_caught}</td>
          <td>${r.verified ? `<span class="tag ok">Verified</span>` : `<span class="tag extra">In progress</span>`}</td></tr>`).join("")}</tbody></table></div>`
          : `<p>No sessions yet. <a href="#/assemble">Start one</a>.</p>`}
        <div class="row"><a class="btn" href="/api/export/robot-data" download>Download robot data</a><span class="small muted">One episode per session: steps, photos, verdicts. Ready to convert for robot learning.</span></div>
      </div>
      <div class="stack">
        <h2>Model trace</h2>
        <p class="muted">Every call to Meta's models, live. <span class="where cloud">cloud</span> sent to the Meta Model API, <span class="where local">local</span> stayed on your hardware${app.status?.mock ? `, <span class="where mock">mock</span> canned offline answer` : ""}.</p>
        <ul class="traces" id="traces">${traces.map(traceRow).join("") || `<li class="muted">No calls yet.</li>`}</ul>
      </div>
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

// ---------------------------------------------------------------- start

loadStatus().catch((e) => toast(e.message, "error")).finally(render);
