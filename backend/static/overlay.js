// Draws findings on top of a photo: outlines (polygon or box), circled part letters, names,
// and a red focus box for problems. Boxes arrive in 0..1 coordinates of the photo.

const HIVIS = "#FFC31F";
const HIVIS_INK = "#2E2500";
const INK = "#1D2422";
const FAIL = "#E5484D";
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

export function makeShot(parent, image, { badge = "", tappable = false } = {}) {
  const figure = document.createElement("figure");
  figure.className = "shot" + (tappable ? " tappable" : "");
  const img = new Image();
  img.alt = "Your photo";
  img.src = image.url;
  img.width = image.width;
  img.height = image.height;
  const canvas = document.createElement("canvas");
  canvas.setAttribute("aria-hidden", "true");
  figure.append(img, canvas);
  if (badge) {
    const b = document.createElement("span");
    b.className = "badge";
    b.textContent = badge;
    figure.append(b);
  }
  parent.append(figure);

  const state = { detections: [], focus: null, names: true, start: 0, raf: 0, extra: null };
  const loaded = new Promise((res) => (img.complete ? res() : img.addEventListener("load", res, { once: true })));
  // bring the result into view: on a phone it usually lands below the step's instructions
  loaded.then(() => figure.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" }));

  function size() {
    const r = img.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w: r.width, h: r.height };
  }

  function frame(now) {
    const { ctx, w, h } = size();
    ctx.clearRect(0, 0, w, h);
    const elapsed = REDUCED ? 1e9 : now - state.start;
    const n = state.detections.length;
    // pass 1: outlines and circled letters; pass 2: name tags placed around everything already drawn
    const drawn = [];
    state.detections.forEach((d, i) => {
      const appear = Math.min(1, Math.max(0, (elapsed - i * Math.min(60, 900 / Math.max(n, 1))) / 260));
      if (appear > 0) drawn.push(drawDetection(ctx, d, w, h, appear));
    });
    if (state.names) {
      const taken = drawn.map((g) => ({ x: g.cx - g.r, y: g.cy - g.r, w: g.r * 2, h: g.r * 2 }));
      drawn.forEach((g) => drawName(ctx, g, w, h, taken));
    }
    if (state.focus) drawFocus(ctx, state.focus, w, h, elapsed);
    if (state.extra) state.extra(ctx, w, h, now);
    const settling = elapsed < n * 60 + 400;
    if (settling || state.focus || state.extra) state.raf = requestAnimationFrame(frame);
  }

  function redraw() {
    cancelAnimationFrame(state.raf);
    state.raf = requestAnimationFrame(frame);
  }

  const ro = new ResizeObserver(redraw);
  ro.observe(img);

  return {
    figure, img, canvas, loaded,
    show(detections, { focus = null, names = true } = {}) {
      state.detections = detections || [];
      state.focus = focus;
      state.names = names;
      state.start = performance.now();
      loaded.then(redraw);
    },
    setExtra(fn) { state.extra = fn; loaded.then(redraw); },
    hit(clientX, clientY, boxes) {
      const r = img.getBoundingClientRect();
      const x = (clientX - r.left) / r.width, y = (clientY - r.top) / r.height;
      return boxes.find((b) => x >= b.box[0] && x <= b.box[2] && y >= b.box[1] && y <= b.box[3]);
    },
    destroy() { ro.disconnect(); cancelAnimationFrame(state.raf); },
  };
}

function drawDetection(ctx, d, w, h, t) {
  const [x0, y0, x1, y1] = d.box;
  const bx = x0 * w, by = y0 * h, bw = (x1 - x0) * w, bh = (y1 - y0) * h;
  const unsure = d.confidence !== undefined && d.confidence < 0.6;
  ctx.save();
  ctx.globalAlpha = t;
  ctx.lineJoin = "round";
  ctx.setLineDash(unsure ? [6, 5] : []);
  const outline = () => {
    ctx.beginPath();
    if (d.polygon && d.polygon.length > 2) {
      d.polygon.forEach(([px, py], i) => (i ? ctx.lineTo(px * w, py * h) : ctx.moveTo(px * w, py * h)));
      ctx.closePath();
    } else {
      roundRect(ctx, bx, by, bw, bh, Math.min(10, bw / 4, bh / 4));
    }
  };
  // dark halo first, so yellow reads on light and dark backgrounds
  outline(); ctx.lineWidth = 5; ctx.strokeStyle = "rgba(14,18,17,.55)"; ctx.stroke();
  outline(); ctx.lineWidth = 2.5; ctx.strokeStyle = HIVIS; ctx.stroke();
  ctx.fillStyle = "rgba(255,195,31,.12)"; ctx.fill();
  ctx.setLineDash([]);

  // circled letter, pinned to the top-left corner
  const r = 11;
  const cx = Math.max(r + 2, Math.min(w - r - 2, bx));
  const cy = Math.max(r + 2, Math.min(h - r - 2, by));
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = HIVIS; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = HIVIS_INK; ctx.stroke();
  ctx.fillStyle = HIVIS_INK;
  ctx.font = `700 ${d.label.length > 2 ? 9 : 12}px Archivo, system-ui, sans-serif`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(d.label, cx, cy + 0.5);
  ctx.restore();
  return { d, t, cx, cy, r, bx, by, bw, bh };
}

function drawName(ctx, g, w, h, taken) {
  const { d, t, cx, cy, r, bx, by, bw, bh } = g;
  if (!d.name || bw < 34) return;
  ctx.save();
  ctx.globalAlpha = t;
  ctx.font = "600 12px Archivo, system-ui, sans-serif";
  const text = d.name + (d.page ? `  p.${d.page}` : "");
  const tw = ctx.measureText(text).width + 12;
  const free = (x, y) => !taken.some((p) => x < p.x + p.w + 3 && x + tw + 3 > p.x && y < p.y + p.h + 2 && y + 22 > p.y);
  const clampX = (x) => Math.max(2, Math.min(x, w - tw - 2));
  // beside the letter, then along the outline's bottom edge, then below it; otherwise skip (the list names it)
  const spots = [
    [clampX(cx + r + 4), cy - 10],
    [clampX(bx + 4), Math.min(h - 22, by + bh - 24)],
    [clampX(bx), Math.min(h - 22, by + bh + 4)],
  ];
  const spot = spots.find(([x, y]) => free(x, y));
  if (spot) {
    const [tx, ty] = spot;
    taken.push({ x: tx, y: ty, w: tw, h: 20 });
    ctx.fillStyle = "rgba(14,18,17,.82)";
    ctx.beginPath(); roundRect(ctx, tx, ty, tw, 20, 10); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(text, tx + 6, ty + 10.5);
  }
  ctx.restore();
}

function drawFocus(ctx, box, w, h, elapsed) {
  const [x0, y0, x1, y1] = box;
  const pulse = REDUCED ? 0 : (Math.sin(elapsed / 260) + 1) / 2;
  ctx.save();
  ctx.lineWidth = 3 + pulse * 2;
  ctx.strokeStyle = FAIL;
  ctx.setLineDash([10, 6]);
  roundRect(ctx, x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h, 12);
  ctx.stroke();
  ctx.restore();
}

export function roundRect(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export { HIVIS, HIVIS_INK, INK, FAIL, REDUCED };
