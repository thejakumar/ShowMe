// Live camera check: streams the camera, sends a frame every couple of seconds, and shows
// (and speaks) one short instruction at a time until the step is right.
// Browsers allow camera streams only on https or localhost; callers fall back to photo mode.

import { REDUCED, roundRect } from "./overlay.js";

const FAIL = "#E5484D";
const NEXT = "#FFC31F"; // where to work next on a half-done step
const MAX_SIDE = 896;

export function liveSupported() {
  return Boolean(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function startLive(container, { send, onResult, onError, intervalMs = 1800 }) {
  const figure = document.createElement("figure");
  figure.className = "shot live";
  const video = document.createElement("video");
  video.playsInline = true;
  video.muted = true;
  video.autoplay = true;
  video.setAttribute("aria-label", "Live camera");
  const canvas = document.createElement("canvas");
  canvas.setAttribute("aria-hidden", "true");
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = "Starting camera";
  figure.append(video, canvas, badge);
  container.append(figure);

  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false,
  });
  video.srcObject = stream;
  await video.play().catch(() => {});
  await new Promise((res) => (video.videoWidth ? res() : video.addEventListener("loadedmetadata", res, { once: true })));
  figure.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });

  let running = true;
  let focus = null;
  let focusColor = FAIL;
  let raf = 0;
  const started = performance.now();
  const grabber = document.createElement("canvas");

  function draw(now) {
    const r = video.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    if (focus) {
      const [x0, y0, x1, y1] = focus;
      const pulse = REDUCED ? 0 : (Math.sin((now - started) / 260) + 1) / 2;
      ctx.lineWidth = 3 + pulse * 2;
      ctx.strokeStyle = focusColor;
      ctx.setLineDash([10, 6]);
      ctx.beginPath();
      roundRect(ctx, x0 * r.width, y0 * r.height, (x1 - x0) * r.width, (y1 - y0) * r.height, 12);
      ctx.stroke();
    }
    if (running) raf = requestAnimationFrame(draw);
  }
  raf = requestAnimationFrame(draw);

  function grab() {
    const w = video.videoWidth, h = video.videoHeight;
    const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
    grabber.width = Math.round(w * scale);
    grabber.height = Math.round(h * scale);
    grabber.getContext("2d").drawImage(video, 0, 0, grabber.width, grabber.height);
    return new Promise((res) => grabber.toBlob(res, "image/jpeg", 0.82));
  }

  let lastBlob = null;
  (async () => {
    let frame = 0;
    while (running) {
      const t0 = performance.now();
      frame += 1;
      badge.textContent = "Checking";
      const blob = await grab();
      if (!blob || !running) break;
      lastBlob = blob;
      let result;
      try {
        result = await send(blob, frame);
      } catch (err) {
        if (!running) break;
        onError?.(err);
        badge.textContent = "Retrying";
        await sleep(2500);
        continue;
      }
      if (!running) break;
      focus = result.verdict === "fail" || result.verdict === "progress" ? result.box : null;
      focusColor = result.verdict === "fail" ? FAIL : NEXT;
      badge.textContent = result.verdict === "pass" ? "Done" : "Watching";
      if (onResult(result) === true) { freeze(); break; }
      await sleep(Math.max(500, intervalMs - (performance.now() - t0)));
    }
  })();

  function stopStream() {
    running = false;
    cancelAnimationFrame(raf);
    stream.getTracks().forEach((t) => t.stop());
  }

  function freeze() {
    // keep the frame that passed on screen, as the proof
    stopStream();
    focus = null;
    canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
    if (lastBlob) {
      const img = new Image();
      img.alt = "The frame that passed the check";
      img.src = URL.createObjectURL(lastBlob);
      video.replaceWith(img);
    }
  }

  return { figure, stop: stopStream, freeze };
}
