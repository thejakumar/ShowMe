// Guided hints: a short tour of each screen the first time someone opens it, and a "?" button in the
// header that replays the tips for whatever screen is showing. Steps whose element isn't on the page
// (a button that only appears for some products, say) are skipped.

const TOURS = {
  home: [
    { title: "Welcome to ShowMe", text: "ShowMe reads a product's manual, watches you work through your phone's camera, and talks you through every step. Here's a 30-second tour. You can replay it any time with the ? button at the top." },
    { sel: ".scan-cta", title: "Scan the code on the box", text: "The fastest way in. Point your camera at a ShowMe QR code on a box or a machine and its guide opens. No app, no manual to find." },
    { sel: ".link-cta", title: "Bought it online?", text: "Paste an Amazon, IKEA or any store link. ShowMe finds the official manual if the page has one, or writes a general setup guide from the listing and says so." },
    { sel: ".uses li.lead a", title: "Assemble something", text: "Upload the manual PDF (or photos of the paper booklet), or try the sample step stool. ShowMe counts your parts and checks every step with the camera." },
    { sel: ".uses:not(.compact) li:nth-child(2) a", title: "Learn a machine or device", text: "For an appliance at home or a machine at work: what every control does, answers to \"how do I…\" from its manual, then a guided, checked walkthrough." },
    { sel: ".uses.compact li:nth-child(1) a", title: "For brands", text: "Publish your manual as a QR code for the box, and see which step people get wrong and what they ask." },
    { sel: ".uses.compact li:nth-child(2) a", title: "For employers", text: "Train staff on your machinery. Every step is verified by the camera, and training records show who is signed off on what." },
    { sel: ".uses.compact li:nth-child(3) a", title: "For robotics", text: "Checked builds become robot training data, the manual becomes robot steps, and the camera check becomes a robot's success signal." },
    { sel: ".privacy", title: "Cloud or Hybrid", text: "Cloud sends camera frames to Meta's Muse models. Hybrid finds parts on your own computer first, when that's set up." },
    { sel: "#help", title: "Tips any time", text: "Tap ? on any screen to see what each button there does." },
  ],
  link: [
    { sel: "#url", title: "Paste the product link", text: "Any store works: Amazon, IKEA, Walmart, Best Buy and more. Sponsored and short links (amzn.to) are fine too." },
    { sel: "#paste", title: "One-tap paste", text: "Copies the link from your clipboard into the box." },
    { sel: "#pname", title: "Product name (optional)", text: "Some stores block page reads. Typing the brand and model, as shown on the store page, makes sure the guide is for the right product." },
    { sel: "#link-form button[type=submit]", title: "Get the guide", text: "ShowMe reads the page, looks for the official manual, and builds the guide. It takes up to a minute." },
  ],
  assemble: [
    { sel: "#manuals", title: "Manuals already read", text: "Pick one ShowMe has already read to start straight away." },
    { sel: "#sample", title: "Just looking?", text: "Try the sample to see the whole flow without uploading anything." },
    { sel: "#upload", title: "Upload your manual", text: "A PDF, or photos of each page of the paper booklet in order. ShowMe reads every page, the parts list and the diagrams, and writes the step-by-step guide." },
    { sel: "fieldset.choice", title: "At work or at home?", text: "At work puts sessions on training records. At home keeps it personal." },
  ],
  session_inventory: [
    { sel: "#snap", title: "Check the parts first", text: "Lay every part out on the floor and take one photo from above. ShowMe counts them against the manual's parts list." },
    { sel: "#where", title: "Already started?", text: "Take a photo of what you've built so far and ShowMe works out which step you're on." },
    { sel: ".dock a.btn, #area ~ * a.btn", title: "Skip ahead", text: "Go straight to step 1 if you've already checked the parts." },
  ],
  session_step: [
    { sel: ".step-head", title: "One step at a time", text: "The step number, its title, and arrows to go back or forward." },
    { sel: ".view-tabs", title: "Manual, 3D or AR", text: "See the step as the manual's own diagram, as a 3D animation you can turn, or in AR on your camera." },
    { sel: ".donewhen", title: "Done when", text: "What the finished step looks like. This is exactly what the camera checks for." },
    { sel: "#voice", title: "Read aloud", text: "ShowMe reads each step and its feedback out loud." },
    { sel: "#handsfree", title: "Hands-free", text: "Prop your phone up. ShowMe watches your work, talks you through it and moves on when each step is right. Say \"next\", \"repeat\" or \"check\"." },
    { sel: "#whereami", title: "Where am I?", text: "Lost your place? Take a photo and ShowMe finds which step you're on." },
    { sel: "#ledger", title: "Count leftovers", text: "Photograph the loose screws and fittings to make sure the right number is left at this point." },
    { sel: "#parts", title: "Show the parts", text: "Highlights the parts this step uses on your parts photo." },
    { sel: "#check", title: "Check my work", text: "Starts the live camera check. About every two seconds ShowMe compares what it sees with the manual and tells you what to fix, with a box around the problem." },
  ],
  device: [
    { sel: "#ask", title: "Ask \"how do I…\"", text: "Type or say a question. The answer comes from this product's manual, with the page it's on." },
    { sel: "#tour-area", title: "What does each button do?", text: "Photograph the machine and ShowMe labels its controls on your photo." },
    { sel: "#setup, #guide", title: "Guide me", text: "A step-by-step walkthrough, checked by the camera as you go." },
  ],
  pack: [
    { sel: "#person", title: "Your name (optional)", text: "So training records and sign-offs show who did it." },
    { sel: "label.toggle", title: "Hands-free", text: "Prop the phone up and ShowMe talks you through every step and moves on by itself when each one is right." },
    { sel: "#go button[type=submit], #go .btn.primary", title: "Start", text: "Opens the first step." },
  ],
  scan: [
    { sel: "#scan-area", title: "Point at the code", text: "Hold the ShowMe QR code in view. It opens by itself." },
    { sel: "#code-form", title: "No camera?", text: "Type the 6-character code printed under the QR code instead." },
  ],
  brand: [
    { sel: "#publish", title: "Publish a guide", text: "Pick a manual and your brand name. ShowMe makes a QR code to print on the box." },
  ],
  team: [
    { sel: ".kpis", title: "At a glance", text: "People trained, sign-offs, and mistakes the camera caught and helped fix." },
    { sel: ".stack-l .row", title: "Next steps", text: "Add a machine, put a QR guide on it, download the records as a spreadsheet, or turn the builds into robot training data." },
  ],
  robot: [
    { sel: "#rplan", title: "The manual as robot steps", text: "Each step broken into actions a robot arm can run: pick, place, screw, check." },
    { sel: "#rcheck, #try", title: "A success check", text: "Your robot sends a photo after each step and gets success or not yet, plus what to fix: the same check people get." },
  ],
};

const SEEN = "tour-seen:";
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: tips just show again */ } },
};

let active = null;

function visible(el) {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
}

export function tourKey() {
  const [path] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  if (!parts.length) return "home";
  if (parts[0] === "session") return parts[2] === "step" ? "session_step" : parts[2] === "done" ? "" : "session_inventory";
  return parts[0];
}

export function hasTour(key = tourKey()) {
  return !!TOURS[key];
}

export function stopTour() {
  if (!active) return;
  active.cleanup();
  active = null;
}

export function startTour(key = tourKey(), { auto = false } = {}) {
  stopTour();
  const steps = (TOURS[key] || []).filter((s) => !s.sel || visible(document.querySelector(s.sel)));
  if (!steps.length) return false;
  store.set(SEEN + key, "1");

  const hole = document.createElement("div");
  hole.className = "tour-hole";
  const card = document.createElement("div");
  card.className = "tour-card";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-live", "polite");
  card.setAttribute("aria-label", "Tips for this screen");
  const shade = document.createElement("div");
  shade.className = "tour-shade";
  document.body.append(shade, hole, card);
  let i = 0, target = null;

  function place() {
    const s = steps[i];
    target = s.sel ? document.querySelector(s.sel) : null;
    const pad = 8, gap = 12, vw = innerWidth, vh = innerHeight;
    if (target) {
      const r = target.getBoundingClientRect();
      Object.assign(hole.style, { display: "block", left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
      shade.style.display = "none";
      const cw = Math.min(360, vw - 32), ch = card.offsetHeight || 180;
      let top = r.bottom + pad + gap;
      if (top + ch > vh - 12) top = Math.max(12, r.top - pad - gap - ch);
      const left = Math.min(Math.max(16, r.left + r.width / 2 - cw / 2), vw - cw - 16);
      Object.assign(card.style, { left: `${left}px`, top: `${top}px`, width: `${cw}px`, transform: "none" });
    } else {
      hole.style.display = "none";
      shade.style.display = "block";
      Object.assign(card.style, { left: "50%", top: "50%", width: `${Math.min(400, vw - 32)}px`, transform: "translate(-50%, -50%)" });
    }
  }

  function show(n) {
    i = Math.max(0, Math.min(steps.length - 1, n));
    const s = steps[i];
    card.innerHTML = `
      <p class="tour-count">${i + 1} of ${steps.length}</p>
      <h3>${s.title}</h3>
      <p>${s.text}</p>
      <div class="tour-actions">
        <button type="button" class="btn quiet" data-t="skip">${i === steps.length - 1 ? "Close" : "Skip tips"}</button>
        <span></span>
        ${i ? `<button type="button" class="btn" data-t="back">Back</button>` : ""}
        <button type="button" class="btn primary" data-t="next">${i === steps.length - 1 ? "Got it" : "Next"}</button>
      </div>`;
    const el = s.sel ? document.querySelector(s.sel) : null;
    if (el) {
      const r = el.getBoundingClientRect();
      if (r.top < 70 || r.bottom > innerHeight - 220) el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    place();
    setTimeout(place, 350);
    card.querySelector('[data-t="next"]').focus({ preventScroll: true });
  }

  function onClick(e) {
    const t = e.target.closest("[data-t]")?.dataset.t;
    if (t === "next") { if (i === steps.length - 1) stopTour(); else show(i + 1); }
    else if (t === "back") show(i - 1);
    else if (t === "skip") stopTour();
  }
  function onKey(e) {
    if (e.key === "Escape") stopTour();
    else if (e.key === "ArrowRight") show(i + 1);
    else if (e.key === "ArrowLeft") show(i - 1);
  }
  const onMove = () => place();
  card.addEventListener("click", onClick);
  shade.addEventListener("click", stopTour);
  document.addEventListener("keydown", onKey);
  addEventListener("resize", onMove);
  addEventListener("scroll", onMove, { passive: true });
  active = {
    cleanup() {
      document.removeEventListener("keydown", onKey);
      removeEventListener("resize", onMove);
      removeEventListener("scroll", onMove);
      hole.remove(); card.remove(); shade.remove();
    },
  };
  show(0);
  return true;
}

// Called after every screen renders: shows that screen's tips once, the first time it's opened.
export function autoTour() {
  stopTour();
  const key = tourKey();
  if (!TOURS[key] || store.get(SEEN + key) || store.get("tour-off")) return;
  setTimeout(() => { if (tourKey() === key && !active) startTour(key, { auto: true }); }, 900);
}
