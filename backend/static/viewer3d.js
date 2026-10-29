// 3D step animation: builds a scene of simple shapes from the step's animation data and plays its
// timeline (parts move in, screws spin, tools turn), with one spoken line per action.
// Scene units are metres (the data is in cm), so AR shows parts at real size.

import * as THREE from "three";
import { OrbitControls } from "/static/vendor/OrbitControls.js";

const CM = 0.01;
const HIVIS = 0xffc31f;
const BASE = { wood: 0xd8bd8f, metal: 0xb4bcc0, plastic: 0x3f4b53, fabric: 0x4d5863, rubber: 0x23282a };
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const rad = (d) => (d * Math.PI) / 180;
const ease = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const clamp01 = (v) => Math.max(0, Math.min(1, v));

function makeMaterial(part) {
  const color = part.color ? new THREE.Color(part.color) : new THREE.Color(BASE[part.material] ?? BASE.plastic);
  const metal = part.material === "metal";
  return new THREE.MeshStandardMaterial({
    color, transparent: true,
    metalness: metal ? 0.75 : 0.05,
    roughness: metal ? 0.32 : part.material === "wood" ? 0.72 : 0.6,
  });
}

function buildShape(part, material) {
  const s = part.size.map((v) => v * CM);
  const g = new THREE.Group();
  const mesh = (geo, x = 0, y = 0, z = 0) => {
    const m = new THREE.Mesh(geo, material);
    m.position.set(x, y, z);
    m.castShadow = true;
    m.receiveShadow = true;
    g.add(m);
    return m;
  };
  const a = s[0], b = s[1] ?? s[0], c = s[2] ?? s[0];
  switch (part.shape) {
    case "cylinder": mesh(new THREE.CylinderGeometry(a / 2, a / 2, b, 32)); break;
    case "cone": mesh(new THREE.CylinderGeometry(0.0001, a / 2, b, 32)); break;
    case "sphere": mesh(new THREE.SphereGeometry(a / 2, 28, 18)); break;
    case "tube": {
      const ro = a / 2, ri = Math.min((s[2] ?? a * 0.6) / 2, ro * 0.95), h = b;
      const pts = [[ri, -h / 2], [ro, -h / 2], [ro, h / 2], [ri, h / 2], [ri, -h / 2]].map(([x, y]) => new THREE.Vector2(x, y));
      mesh(new THREE.LatheGeometry(pts, 40));
      break;
    }
    case "screw": {
      const d = a, len = s[1] ?? d * 6;
      mesh(new THREE.CylinderGeometry(d, d * 0.95, d * 0.55, 24), 0, len / 2 + d * 0.27);
      mesh(new THREE.CylinderGeometry(d / 2, d * 0.18, len, 16));
      const rings = Math.min(12, Math.floor(len / (d * 0.7)));
      for (let i = 1; i < rings; i++) {
        const t = mesh(new THREE.TorusGeometry(d * 0.5, d * 0.09, 6, 18), 0, len / 2 - i * (len / rings));
        t.rotation.x = Math.PI / 2;
      }
      break;
    }
    case "wheel": { const w = mesh(new THREE.CylinderGeometry(a / 2, a / 2, b, 32)); w.rotation.z = Math.PI / 2; break; }
    case "lkey": {
      const t = Math.max(0.0035, a * 0.05);
      mesh(new THREE.BoxGeometry(a, t, t), a / 2, 0, 0);
      mesh(new THREE.BoxGeometry(t, a * 0.35, t), 0, a * 0.175, 0);
      break;
    }
    default: mesh(new THREE.BoxGeometry(a, b, c));
  }
  return g;
}

function labelSprite(text) {
  const size = 128;
  const cv = document.createElement("canvas");
  cv.width = cv.height = size;
  const ctx = cv.getContext("2d");
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - 8, 0, Math.PI * 2);
  ctx.fillStyle = "#FFC31F";
  ctx.fill();
  ctx.lineWidth = 8;
  ctx.strokeStyle = "#2E2500";
  ctx.stroke();
  ctx.fillStyle = "#2E2500";
  ctx.font = `700 ${text.length > 2 ? 44 : 60}px Archivo, system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, size / 2, size / 2 + 4);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  sprite.renderOrder = 10;
  sprite.scale.setScalar(0.045);
  return sprite;
}

function rotY(v, ang) {
  const c = Math.cos(ang), s = Math.sin(ang);
  return [v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c];
}

export function createViewer(container, anim, { transparent = false, fill = false, onSay, onEnd } = {}) {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.domElement.className = "stage3d";
  renderer.domElement.setAttribute("aria-label", "3D animation of this step. Drag to turn it.");
  container.append(renderer.domElement);

  const scene = new THREE.Scene();
  const bg = new THREE.Color(dark ? 0x1a201e : 0xf1f4f2);
  if (!transparent) scene.background = bg;

  const camera = new THREE.PerspectiveCamera(38, 4 / 3, 0.01, 60);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.49;

  scene.add(new THREE.HemisphereLight(0xffffff, dark ? 0x30383a : 0x9aa6ab, 1.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.7);
  sun.position.set(0.8, 1.6, 1.0);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  Object.assign(sun.shadow.camera, { left: -1.2, right: 1.2, top: 1.2, bottom: -1.2, near: 0.1, far: 5 });
  scene.add(sun);

  const root = new THREE.Group();
  scene.add(root);
  const floor = new THREE.Mesh(new THREE.CircleGeometry(1.4, 64), new THREE.ShadowMaterial({ opacity: dark ? 0.35 : 0.16 }));
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  root.add(floor);
  let ground = null;
  if (!transparent) {
    ground = new THREE.Mesh(new THREE.CircleGeometry(1.4, 64), new THREE.MeshBasicMaterial({ color: dark ? 0x222a28 : 0xe4e9e6 }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.0005;
    root.add(ground);
  }

  // ---- parts and instances
  const parts = Object.fromEntries(anim.parts.map((p) => [p.id, p]));
  const items = [];
  const labelled = new Set();
  for (const inst of anim.instances) {
    const part = parts[inst.part];
    if (!part) continue;
    for (let k = 0; k < inst.repeat; k++) {
      const yaw = (2 * Math.PI * k) / inst.repeat;
      const pose = (p) => p && { pos: rotY(p.pos, yaw), rot: [p.rot[0], p.rot[1] + (yaw * 180) / Math.PI, p.rot[2]] };
      const material = makeMaterial(part);
      const obj = buildShape(part, material);
      root.add(obj);
      const item = { id: inst.id, obj, material, start: pose(inst.start), end: pose(inst.end), events: [], label: null, guide: null };
      if (item.end) {
        // dashed yellow path from where the part waits to where it goes: small hardware stays easy to follow
        const geo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(...item.start.pos).multiplyScalar(CM), new THREE.Vector3(...item.end.pos).multiplyScalar(CM)]);
        item.guide = new THREE.Line(geo, new THREE.LineDashedMaterial({ color: HIVIS, dashSize: 0.012, gapSize: 0.008, depthTest: false, transparent: true }));
        item.guide.computeLineDistances();
        item.guide.renderOrder = 5;
        item.guide.visible = false;
        root.add(item.guide);
      }
      if (part.label && !labelled.has(part.id)) {
        labelled.add(part.id);
        item.label = labelSprite(part.label);
        root.add(item.label);
      }
      items.push(item);
    }
  }

  // ---- timeline
  const captions = [];
  let total = 0;
  for (const a of anim.actions) {
    let n = 0;
    for (const tid of a.targets) {
      for (const it of items.filter((x) => x.id === tid)) {
        const t0 = a.at + n * (a.stagger || 0);
        it.events.push({ type: a.type, t0, t1: t0 + a.duration, turns: a.turns || 4 });
        total = Math.max(total, t0 + a.duration);
        n += 1;
      }
    }
    if (a.say) captions.push({ t: a.at, say: a.say, done: false });
  }
  total += 1.2;
  items.forEach((it) => it.events.sort((x, y) => x.t0 - y.t0));

  const box = new THREE.Box3();
  function apply(t) {
    for (const it of items) {
      let pos = it.start.pos, rot = it.start.rot, spin = 0, glow = 0, opacity = 1;
      for (const ev of it.events) {
        const p = clamp01((t - ev.t0) / Math.max(0.01, ev.t1 - ev.t0));
        const e = ease(p);
        if ((ev.type === "move" || ev.type === "screw") && it.end) {
          pos = it.start.pos.map((v, i) => v + (it.end.pos[i] - v) * e);
          rot = it.start.rot.map((v, i) => v + (it.end.rot[i] - v) * e);
          if (t >= ev.t0 && t <= ev.t1) glow = Math.max(glow, 0.18);
        }
        if (ev.type === "screw" || ev.type === "rotate") spin += ev.turns * 2 * Math.PI * e;
        if (ev.type === "rotate" && it.end && !it.events.some((x) => x.type === "move")) { pos = it.end.pos; rot = it.end.rot; }
        if (ev.type === "appear") opacity = clamp01((t - ev.t0) / 0.5);
        if (ev.type === "highlight" && t >= ev.t0 && t <= ev.t1) glow = Math.max(glow, 0.3 + 0.3 * Math.sin((t - ev.t0) * 7));
      }
      it.obj.position.set(pos[0] * CM, pos[1] * CM, pos[2] * CM);
      it.obj.rotation.set(rad(rot[0]), rad(rot[1]), rad(rot[2]));
      if (spin) it.obj.rotateY(spin);
      it.obj.visible = opacity > 0.01;
      it.material.opacity = opacity;
      it.material.emissive.setHex(glow ? HIVIS : 0x000000);
      it.material.emissiveIntensity = glow;
      if (it.guide) {
        const mv = it.events.find((ev) => ev.type === "move" || ev.type === "screw");
        it.guide.visible = Boolean(mv) && t >= mv.t0 - 0.8 && t <= mv.t1;
      }
      if (it.label) {
        box.setFromObject(it.obj);
        it.label.position.set((box.min.x + box.max.x) / 2, box.max.y + 0.03, (box.min.z + box.max.z) / 2);
        it.label.visible = it.obj.visible;
      }
    }
  }

  // ---- frame the whole step from the suggested direction
  function frame() {
    apply(total);
    const b = new THREE.Box3();
    items.forEach((it) => b.expandByObject(it.obj));
    apply(0);
    items.forEach((it) => b.expandByObject(it.obj));
    const sphere = b.getBoundingSphere(new THREE.Sphere());
    const reach = Math.max(0.35, sphere.radius * 2.2) / 1.4;  // floor sized to the scene
    for (const m of [floor, ground]) if (m) { m.scale.setScalar(reach); m.position.x = sphere.center.x; m.position.z = sphere.center.z; }
    const want = new THREE.Vector3(...anim.camera.pos).multiplyScalar(CM);
    const look = new THREE.Vector3(...anim.camera.target).multiplyScalar(CM);
    const dir = want.clone().sub(look).normalize();
    if (!isFinite(dir.x) || dir.lengthSq() < 0.5) dir.set(0.55, 0.5, 0.75).normalize();
    const dist = (sphere.radius / Math.sin(rad(camera.fov / 2))) * 1.08;
    camera.position.copy(sphere.center).addScaledVector(dir, Math.max(dist, 0.25));
    controls.target.copy(sphere.center);
    controls.update();
  }

  function resize() {
    const w = container.clientWidth || 600;
    const h = fill && container.clientHeight ? container.clientHeight : Math.round(w * 0.72);
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  resize();
  frame();
  const ro = new ResizeObserver(resize);
  ro.observe(container);

  // ---- playback
  let t = 0, playing = !REDUCED, last = performance.now(), ended = false;
  let hitSource = null, reticle = null, xrSession = null;

  renderer.setAnimationLoop((now, xrFrame) => {
    const dt = Math.max(0, Math.min(0.1, (now - last) / 1000));
    last = now;
    if (playing) t += dt;
    if (t >= total) {
      t = total;
      if (!ended) { ended = true; playing = false; onEnd?.(); }
    }
    for (const c of captions) if (!c.done && t >= c.t) { c.done = true; onSay?.(c.say); }
    apply(REDUCED && !playing ? total : t);
    if (xrFrame && hitSource && reticle) {
      const hits = xrFrame.getHitTestResults(hitSource);
      if (hits.length) {
        const pose = hits[0].getPose(renderer.xr.getReferenceSpace());
        reticle.visible = !root.visible;
        reticle.matrix.fromArray(pose.transform.matrix);
      } else reticle.visible = false;
    }
    if (!renderer.xr.isPresenting) controls.update();
    renderer.render(scene, camera);
  });

  function restart() {
    t = 0; ended = false; playing = true;
    captions.forEach((c) => (c.done = false));
  }

  async function enterAR(overlayRoot, { onPlaced, onExit } = {}) {
    renderer.xr.enabled = true;
    renderer.xr.setReferenceSpaceType("local");
    const session = await navigator.xr.requestSession("immersive-ar", {
      requiredFeatures: ["hit-test"], optionalFeatures: ["dom-overlay"], domOverlay: { root: overlayRoot },
    });
    xrSession = session;
    await renderer.xr.setSession(session);
    scene.background = null;
    root.visible = false;
    root.position.set(0, 0, 0);
    reticle = new THREE.Mesh(new THREE.RingGeometry(0.05, 0.065, 40).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: HIVIS }));
    reticle.matrixAutoUpdate = false;
    reticle.visible = false;
    scene.add(reticle);
    const viewerSpace = await session.requestReferenceSpace("viewer");
    hitSource = await session.requestHitTestSource({ space: viewerSpace });
    session.addEventListener("select", () => {
      if (!reticle.visible) return;
      root.position.setFromMatrixPosition(reticle.matrix);
      root.visible = true;
      reticle.visible = false;
      restart();
      onPlaced?.();
    });
    session.addEventListener("end", () => {
      hitSource = null;
      xrSession = null;
      scene.remove(reticle);
      reticle = null;
      renderer.xr.enabled = false;
      if (!transparent) scene.background = bg;
      root.position.set(0, 0, 0);
      root.visible = true;
      resize();
      frame();
      onExit?.();
    });
  }

  return {
    total,
    play() { if (ended) restart(); else playing = true; },
    pause() { playing = false; },
    restart,
    get playing() { return playing; },
    enterAR,
    exitAR() { xrSession?.end(); },
    dispose() {
      xrSession?.end();
      ro.disconnect();
      renderer.setAnimationLoop(null);
      controls.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

export async function webxrARSupported() {
  try {
    return Boolean(navigator.xr && window.isSecureContext && (await navigator.xr.isSessionSupported("immersive-ar")));
  } catch {
    return false;
  }
}
