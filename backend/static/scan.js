// Scans a ShowMe QR code from inside the app.
// Live camera where the browser allows it (https or this PC), reading frames with the browser's own
// BarcodeDetector when it has one, else the bundled jsQR. On plain-http phones, where live camera is
// blocked, a photo of the code works instead.

let jsqrLoading = null;

function loadJsQR() {
  if (window.jsQR) return Promise.resolve(window.jsQR);
  jsqrLoading ||= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "/static/vendor/jsQR.js";
    s.onload = () => resolve(window.jsQR);
    s.onerror = () => reject(new Error("The QR reader didn't load."));
    document.head.append(s);
  });
  return jsqrLoading;
}

// Returns async (source) => text | null, for a <video>, <img> or ImageBitmap.
async function makeReader(maxSide) {
  if ("BarcodeDetector" in window) {
    try {
      const formats = await window.BarcodeDetector.getSupportedFormats();
      if (formats.includes("qr_code")) {
        const detector = new window.BarcodeDetector({ formats: ["qr_code"] });
        return async (source) => (await detector.detect(source))[0]?.rawValue || null;
      }
    } catch { /* fall back to jsQR */ }
  }
  const jsQR = await loadJsQR();
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  return async (source) => {
    const w = source.videoWidth || source.naturalWidth || source.width;
    const h = source.videoHeight || source.naturalHeight || source.height;
    if (!w || !h) return null;
    const scale = Math.min(1, maxSide / Math.max(w, h));
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
    return jsQR(pixels.data, canvas.width, canvas.height, { inversionAttempts: "attemptBoth" })?.data || null;
  };
}

// A ShowMe code from what a QR says: a guide link (any host, so a code printed for the Wi-Fi
// address still opens here) or the bare six-character code printed under it.
export function parseGuideCode(text) {
  const t = String(text || "").trim();
  const link = t.match(/\/p\/([A-Za-z0-9]{4,12})(?:[/?#]|$)/);
  if (link) return link[1].toUpperCase();
  const bare = t.replace(/[\s-]+/g, "").toUpperCase();
  return /^[2-9A-HJ-NP-Z]{6}$/.test(bare) ? bare : null;
}

export async function readPhoto(file) {
  const read = await makeReader(1600);
  const bitmap = await createImageBitmap(file);
  try {
    return await read(bitmap);
  } finally {
    bitmap.close?.();
  }
}

// Live scanning into `container`. Calls onText(text) once per new QR seen; return true from it to stop.
export async function startScanner(container, { onText, onState } = {}) {
  const figure = document.createElement("figure");
  figure.className = "scanner";
  const video = document.createElement("video");
  video.playsInline = true;
  video.muted = true;
  video.autoplay = true;
  video.setAttribute("aria-label", "Camera view for scanning");
  const frame = document.createElement("div");
  frame.className = "finder";
  frame.setAttribute("aria-hidden", "true");
  figure.append(video, frame);
  container.append(figure);

  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false,
  });
  video.srcObject = stream;
  await video.play().catch(() => {});
  await new Promise((r) => (video.videoWidth ? r() : video.addEventListener("loadedmetadata", r, { once: true })));
  const read = await makeReader(1024);
  onState?.("scanning");

  let running = true, last = "";
  const stop = () => {
    running = false;
    stream.getTracks().forEach((t) => t.stop());
  };
  (async () => {
    while (running) {
      let text = null;
      try { text = await read(video); } catch { /* a frame that can't be read: try the next */ }
      if (!running) break;
      if (text && text !== last) {
        last = text;
        figure.classList.add("hit");
        if (onText?.(text) === true) { stop(); break; }
        setTimeout(() => figure.classList.remove("hit"), 600);
      }
      await new Promise((r) => setTimeout(r, 180));
    }
  })();
  return { stop, figure };
}
