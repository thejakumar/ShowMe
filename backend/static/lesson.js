// Plays a concept lesson over the learner's own room photo. Each beat has a line to say and
// one visual: highlight, label, arrow, orbit, flow, stack or compare, anchored to real objects.

import { HIVIS, HIVIS_INK, REDUCED, roundRect } from "./overlay.js";

const CYAN = "#3EC1F0";

export function playLesson(shot, objects) {
  const byId = Object.fromEntries(objects.map((o) => [o.id, o]));
  let beat = null;
  let started = performance.now();
  let quiz = null; // {answer, picked}

  const center = (o, w, h) => [((o.box[0] + o.box[2]) / 2) * w, ((o.box[1] + o.box[3]) / 2) * h];

  shot.setExtra((ctx, w, h, now) => {
    const t = REDUCED ? 1 : Math.min(1, (now - started) / 700);
    const time = REDUCED ? 0 : (now - started) / 1000;
    if (quiz) return drawQuiz(ctx, w, h);
    if (!beat) return;
    const v = beat.visual || {};
    const targets = (v.targets || []).map((id) => byId[id]).filter(Boolean);
    if (!targets.length) return;

    // dim everything except the cast of this beat
    ctx.save();
    ctx.fillStyle = `rgba(10,12,12,${0.38 * t})`;
    ctx.beginPath();
    ctx.rect(0, 0, w, h);
    targets.forEach((o) => roundRect(ctx, o.box[0] * w - 6, o.box[1] * h - 6, (o.box[2] - o.box[0]) * w + 12, (o.box[3] - o.box[1]) * h + 12, 12));
    ctx.fill("evenodd");
    ctx.restore();

    if (v.type === "orbit" && targets.length >= 2) drawOrbit(ctx, targets[0], targets[1], w, h, time, t, v.text);
    else if (v.type === "arrow" && targets.length >= 2) drawArrow(ctx, center(targets[0], w, h), center(targets[1], w, h), t, v.text);
    else if (v.type === "flow" && targets.length >= 2) drawFlow(ctx, center(targets[0], w, h), center(targets[1], w, h), time, t, v.text);
    else if (v.type === "stack") drawStack(ctx, targets[0], w, h, t, v.values || [], v.text);
    else if (v.type === "compare" && targets.length >= 2) { targets.forEach((o) => outline(ctx, o, w, h, time)); chip(ctx, ...mid(center(targets[0], w, h), center(targets[1], w, h)), v.text || "vs"); }
    else { targets.forEach((o) => outline(ctx, o, w, h, time)); if (v.text) chip(ctx, center(targets[0], w, h)[0], targets[0].box[1] * h - 16, v.text); }
  });

  function drawQuiz(ctx, w, h) {
    objects.forEach((o) => {
      const right = quiz.picked && o.id === quiz.answer;
      const wrong = quiz.picked === o.id && o.id !== quiz.answer;
      ctx.save();
      ctx.setLineDash(quiz.picked ? [] : [7, 6]);
      ctx.lineWidth = right || wrong ? 4 : 2.5;
      ctx.strokeStyle = right ? "#2FBF63" : wrong ? "#E5484D" : "rgba(255,255,255,.9)";
      ctx.beginPath();
      roundRect(ctx, o.box[0] * w, o.box[1] * h, (o.box[2] - o.box[0]) * w, (o.box[3] - o.box[1]) * h, 10);
      ctx.stroke();
      ctx.restore();
    });
  }

  return {
    showBeat(b) { beat = b; quiz = null; started = performance.now(); },
    startQuiz(answer) { quiz = { answer, picked: null }; beat = null; },
    pick(id) { if (quiz) quiz.picked = id; },
    clear() { beat = null; quiz = null; },
  };
}

function mid(a, b) { return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; }

function outline(ctx, o, w, h, time) {
  const pulse = (Math.sin(time * 4) + 1) / 2;
  ctx.save();
  ctx.lineWidth = 3 + pulse * 2;
  ctx.strokeStyle = HIVIS;
  ctx.shadowColor = HIVIS;
  ctx.shadowBlur = 12 + pulse * 10;
  ctx.beginPath();
  roundRect(ctx, o.box[0] * w, o.box[1] * h, (o.box[2] - o.box[0]) * w, (o.box[3] - o.box[1]) * h, 12);
  ctx.stroke();
  ctx.restore();
}

function chip(ctx, x, y, text) {
  if (!text) return;
  ctx.save();
  ctx.font = "700 15px Archivo, system-ui, sans-serif";
  const tw = ctx.measureText(text).width + 20;
  const cx = Math.max(tw / 2 + 4, Math.min(ctx.canvas.clientWidth - tw / 2 - 4, x));
  const cy = Math.max(18, y);
  ctx.fillStyle = HIVIS;
  ctx.strokeStyle = HIVIS_INK;
  ctx.lineWidth = 2;
  ctx.beginPath();
  roundRect(ctx, cx - tw / 2, cy - 15, tw, 30, 15);
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = HIVIS_INK;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, cx, cy + 1);
  ctx.restore();
}

function drawArrow(ctx, a, b, t, text) {
  const [ax, ay] = a, [bx, by] = b;
  const ex = ax + (bx - ax) * 0.82 * t, ey = ay + (by - ay) * 0.82 * t;
  const ang = Math.atan2(ey - ay, ex - ax);
  ctx.save();
  ctx.strokeStyle = HIVIS; ctx.fillStyle = HIVIS; ctx.lineWidth = 6; ctx.lineCap = "round";
  ctx.shadowColor = "rgba(0,0,0,.5)"; ctx.shadowBlur = 6;
  ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(ex, ey); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(ex + Math.cos(ang) * 14, ey + Math.sin(ang) * 14);
  ctx.lineTo(ex + Math.cos(ang + 2.5) * 16, ey + Math.sin(ang + 2.5) * 16);
  ctx.lineTo(ex + Math.cos(ang - 2.5) * 16, ey + Math.sin(ang - 2.5) * 16);
  ctx.closePath(); ctx.fill();
  ctx.restore();
  if (t > 0.6) chip(ctx, (ax + bx) / 2, (ay + by) / 2 - 26, text);
}

function drawFlow(ctx, a, b, time, t, text) {
  const [ax, ay] = a, [bx, by] = b;
  ctx.save();
  ctx.strokeStyle = "rgba(62,193,240,.45)"; ctx.lineWidth = 3; ctx.setLineDash([4, 8]);
  ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
  ctx.setLineDash([]);
  for (let i = 0; i < 9; i++) {
    const p = ((time * 0.45 + i / 9) % 1);
    ctx.beginPath();
    ctx.arc(ax + (bx - ax) * p, ay + (by - ay) * p, 6, 0, Math.PI * 2);
    ctx.fillStyle = CYAN; ctx.globalAlpha = t * (0.4 + 0.6 * Math.sin(p * Math.PI));
    ctx.fill();
  }
  ctx.restore();
  chip(ctx, (ax + bx) / 2, (ay + by) / 2 - 24, text);
}

function drawOrbit(ctx, sun, mover, w, h, time, t, text) {
  const [sx, sy] = [((sun.box[0] + sun.box[2]) / 2) * w, ((sun.box[1] + sun.box[3]) / 2) * h];
  const [mx, my] = [((mover.box[0] + mover.box[2]) / 2) * w, ((mover.box[1] + mover.box[3]) / 2) * h];
  // keep the whole orbit inside the photo where possible, so the travelling object stays visible
  const room = Math.min(sx, w - sx) - 24;
  const rx = Math.max(70, Math.min(Math.hypot(mx - sx, my - sy), room > 70 ? room : w * 0.45));
  const ry = Math.min(rx * 0.42, Math.max(40, Math.min(sy, h - sy) - 20));
  const start = Math.atan2((my - sy) / (ry / rx), mx - sx);
  const ang = start + time * 0.9;
  ctx.save();
  // the path
  ctx.strokeStyle = HIVIS; ctx.lineWidth = 2.5; ctx.setLineDash([8, 8]); ctx.globalAlpha = t;
  ctx.beginPath(); ctx.ellipse(sx, sy, rx, ry, 0, 0, Math.PI * 2); ctx.stroke();
  ctx.setLineDash([]);
  // the sun glows
  const g = ctx.createRadialGradient(sx, sy, 4, sx, sy, 90);
  g.addColorStop(0, "rgba(255,195,31,.75)"); g.addColorStop(1, "rgba(255,195,31,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(sx, sy, 90, 0, Math.PI * 2); ctx.fill();
  // the mover travels: draw its own pixels cut from the photo
  const img = ctx.canvas.previousElementSibling;
  const px = sx + Math.cos(ang) * rx, py = sy + Math.sin(ang) * ry;
  const bw = (mover.box[2] - mover.box[0]) * w, bh = (mover.box[3] - mover.box[1]) * h;
  const scale = 0.55 + 0.25 * Math.sin(ang); // nearer looks bigger
  if (img && img.naturalWidth) {
    const nw = img.naturalWidth, nh = img.naturalHeight;
    ctx.globalAlpha = 1;
    ctx.shadowColor = "rgba(0,0,0,.6)"; ctx.shadowBlur = 14;
    ctx.drawImage(img, mover.box[0] * nw, mover.box[1] * nh, (mover.box[2] - mover.box[0]) * nw, (mover.box[3] - mover.box[1]) * nh,
      px - (bw * scale) / 2, py - (bh * scale) / 2, bw * scale, bh * scale);
  }
  // gravity line from mover to sun
  ctx.shadowBlur = 0; ctx.strokeStyle = "rgba(255,255,255,.7)"; ctx.lineWidth = 1.5; ctx.setLineDash([3, 5]);
  ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(sx, sy); ctx.stroke();
  ctx.restore();
  chip(ctx, sx, sy - ry - 22, text);
}

function drawStack(ctx, o, w, h, t, values, text) {
  if (!values.length) values = [1, 2, 3, 4];
  const max = Math.max(...values);
  const base = o.box[3] * h, left = o.box[2] * w + 12;
  const barW = 26, gap = 8, maxH = Math.min(h * 0.55, base - 30);
  ctx.save();
  values.forEach((v, i) => {
    const grow = Math.min(1, Math.max(0, t * values.length - i));
    const bh = (v / max) * maxH * grow;
    const x = Math.min(left + i * (barW + gap), w - barW - 4);
    ctx.fillStyle = HIVIS; ctx.strokeStyle = HIVIS_INK; ctx.lineWidth = 1.5;
    ctx.beginPath(); roundRect(ctx, x, base - bh, barW, bh, 4); ctx.fill(); ctx.stroke();
    if (grow >= 1) {
      ctx.fillStyle = "#fff"; ctx.font = "700 12px Archivo, system-ui, sans-serif"; ctx.textAlign = "center";
      ctx.shadowColor = "#000"; ctx.shadowBlur = 4;
      ctx.fillText(String(Math.round(v)), x + barW / 2, base - bh - 6);
      ctx.shadowBlur = 0;
    }
  });
  ctx.restore();
  outline(ctx, o, w, h, 0);
  chip(ctx, ((o.box[0] + o.box[2]) / 2) * w, o.box[1] * h - 16, text);
}
