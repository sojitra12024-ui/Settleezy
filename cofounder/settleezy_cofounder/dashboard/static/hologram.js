// Setz hologram: a low-poly wireframe robot (rounded head, dark visor, glowing ring eyes, ear discs,
// shield body and fins) rendered with a tiny 3D engine on <canvas>.
//
// Motion: floats, breathes, blinks, looks around and follows your cursor, tilts its head while listening
// (and waves hello), looks up with orbiting dots while thinking, and while speaking its eyes, mouth-wave and
// fins move with the actual loudness of the voice. Optional "hub" mode draws your channels flowing into Setz.
(function () {
  const PALETTE = {
    idle:      { core: [94, 232, 255], glow: [42, 120, 214] },
    listening: { core: [120, 255, 214], glow: [27, 160, 122] },
    thinking:  { core: [176, 166, 255], glow: [84, 70, 190] },
    speaking:  { core: [110, 236, 255], glow: [57, 135, 229] },
  };
  const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${Math.max(0, Math.min(1, a))})`;
  const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
  const lerp = (a, b, t) => a + (b - a) * t;
  const hash = (i) => { const x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

  // ---------- geometry ----------
  function lathe(profile, segs, sx, sz, jitter, seed) {
    // profile: [[y, r], ...] top to bottom; r = 0 makes a pole. Small jitter gives the faceted low-poly look.
    const v = [], f = [], rings = [];
    profile.forEach(([y, r], ri) => {
      if (r === 0) { rings.push([v.length]); v.push([0, y, 0]); return; }
      const ring = [];
      for (let k = 0; k < segs; k++) {
        const a = (k / segs) * Math.PI * 2 + (ri % 2) * Math.PI / segs;
        const j = 1 + (hash(seed + ri * 31 + k) - 0.5) * jitter;
        ring.push(v.length);
        v.push([Math.sin(a) * r * sx * j, y + (hash(seed + k * 17 + ri) - 0.5) * jitter * 0.4, Math.cos(a) * r * sz * j]);
      }
      rings.push(ring);
    });
    for (let i = 0; i < rings.length - 1; i++) {
      const A = rings[i], B = rings[i + 1];
      if (A.length === 1) { for (let k = 0; k < B.length; k++) f.push([A[0], B[k], B[(k + 1) % B.length]]); continue; }
      if (B.length === 1) { for (let k = 0; k < A.length; k++) f.push([A[k], A[(k + 1) % A.length], B[0]]); continue; }
      for (let k = 0; k < A.length; k++) {
        const a0 = A[k], a1 = A[(k + 1) % A.length], b0 = B[k], b1 = B[(k + 1) % B.length];
        f.push([a0, b0, a1]); f.push([a1, b0, b1]);
      }
    }
    return { v, f };
  }
  function ellipsoid(rx, ry, rz, lat, lon, jitter, seed) {
    const prof = [];
    for (let i = 0; i <= lat; i++) { const phi = Math.PI / 2 - (i / lat) * Math.PI; prof.push([Math.sin(phi) * ry, i === 0 || i === lat ? 0 : Math.cos(phi)]); }
    return lathe(prof, lon, rx, rz, jitter, seed);
  }
  const mapMesh = (m, fn) => ({ v: m.v.map(fn), f: m.f });

  const HEAD_R = [1.18, 0.98, 1.0];
  const HEAD = ellipsoid(...HEAD_R, 7, 14, 0.06, 1);
  const EAR = lathe([[0.1, 0], [0.1, 0.34], [-0.1, 0.34], [-0.1, 0]], 9, 1, 1, 0.03, 9);
  const EAR_L = mapMesh(EAR, ([x, y, z]) => [-1.13 - y, x * 0.95, z]);
  const EAR_R = mapMesh(EAR, ([x, y, z]) => [1.13 + y, x * 0.95, z]);
  const BODY = lathe([[-1.0, 0], [-1.05, 0.55], [-1.3, 0.92], [-1.65, 0.98], [-2.05, 0.82], [-2.45, 0.5], [-2.8, 0.18], [-2.95, 0]], 12, 1, 0.78, 0.07, 5);
  const FIN = { v: [[0, 0, 0.16], [0, 0, -0.16], [-0.62, -1.05, 0], [-0.14, -0.78, 0.05]], f: [[0, 2, 3], [1, 3, 2], [0, 3, 1], [0, 1, 2]] };
  const FIN_L = FIN, FIN_R = mapMesh(FIN, ([x, y, z]) => [-x, y, z]);
  const SHOULDER_L = [-0.98, -1.3, 0], SHOULDER_R = [0.98, -1.3, 0];

  // ---------- 3D helpers ----------
  const rotX = ([x, y, z], a) => [x, y * Math.cos(a) - z * Math.sin(a), y * Math.sin(a) + z * Math.cos(a)];
  const rotY = ([x, y, z], a) => [x * Math.cos(a) + z * Math.sin(a), y, -x * Math.sin(a) + z * Math.cos(a)];
  const rotZ = ([x, y, z], a) => [x * Math.cos(a) - y * Math.sin(a), x * Math.sin(a) + y * Math.cos(a), z];
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const norm = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
  const LIGHT = norm([-0.45, 0.65, 0.75]);
  const onHead = (th, ph, s) => [HEAD_R[0] * s * Math.cos(ph) * Math.sin(th), HEAD_R[1] * s * Math.sin(ph), HEAD_R[2] * s * Math.cos(ph) * Math.cos(th)];

  // ---------- channel icons (hub mode) ----------
  const ICONS = {
    outlook(c, r) { c.strokeRect(-r * 0.6, -r * 0.4, r * 1.2, r * 0.8); c.beginPath(); c.moveTo(-r * 0.6, -r * 0.4); c.lineTo(0, r * 0.08); c.lineTo(r * 0.6, -r * 0.4); c.stroke(); },
    instagram(c, r) { c.beginPath(); c.roundRect(-r * 0.55, -r * 0.55, r * 1.1, r * 1.1, r * 0.3); c.stroke(); c.beginPath(); c.arc(0, 0, r * 0.26, 0, 7); c.stroke(); c.beginPath(); c.arc(r * 0.3, -r * 0.3, r * 0.06, 0, 7); c.fill(); },
    calendar(c, r) {
      c.strokeRect(-r * 0.55, -r * 0.45, r * 1.1, r * 0.95); c.beginPath(); c.moveTo(-r * 0.55, -r * 0.18); c.lineTo(r * 0.55, -r * 0.18);
      c.moveTo(-r * 0.3, -r * 0.6); c.lineTo(-r * 0.3, -r * 0.35); c.moveTo(r * 0.3, -r * 0.6); c.lineTo(r * 0.3, -r * 0.35); c.stroke();
      for (let i = 0; i < 3; i++) for (let j = 0; j < 2; j++) c.fillRect(-r * 0.36 + i * r * 0.28, r * 0.02 + j * r * 0.22, r * 0.12, r * 0.1);
    },
    web(c, r) { c.beginPath(); c.arc(0, 0, r * 0.55, 0, 7); c.stroke(); c.beginPath(); c.ellipse(0, 0, r * 0.24, r * 0.55, 0, 0, 7); c.moveTo(-r * 0.55, 0); c.lineTo(r * 0.55, 0); c.stroke(); },
    partners(c, r) {
      c.beginPath(); c.arc(-r * 0.2, -r * 0.18, r * 0.2, 0, 7); c.moveTo(r * 0.4, -r * 0.18); c.arc(r * 0.22, -r * 0.18, r * 0.18, 0, 7); c.stroke();
      c.beginPath(); c.arc(-r * 0.2, r * 0.45, r * 0.36, Math.PI * 1.1, Math.PI * 1.9); c.stroke(); c.beginPath(); c.arc(r * 0.24, r * 0.45, r * 0.3, Math.PI * 1.15, Math.PI * 1.9); c.stroke();
    },
    brain(c, r) {
      c.beginPath(); c.arc(-r * 0.18, 0, r * 0.38, Math.PI * 0.5, Math.PI * 1.5); c.arc(r * 0.18, 0, r * 0.38, Math.PI * 1.5, Math.PI * 0.5); c.closePath(); c.stroke();
      [[-0.2, -0.15], [0.15, -0.2], [0, 0.12], [-0.25, 0.2], [0.25, 0.15]].forEach(([x, y]) => { c.beginPath(); c.arc(x * r, y * r, r * 0.06, 0, 7); c.fill(); });
    },
    chip(c, r) {
      c.strokeRect(-r * 0.35, -r * 0.35, r * 0.7, r * 0.7); c.beginPath();
      for (let i = -1; i <= 1; i++) { c.moveTo(i * r * 0.2, -r * 0.35); c.lineTo(i * r * 0.2, -r * 0.55); c.moveTo(i * r * 0.2, r * 0.35); c.lineTo(i * r * 0.2, r * 0.55); c.moveTo(-r * 0.35, i * r * 0.2); c.lineTo(-r * 0.55, i * r * 0.2); c.moveTo(r * 0.35, i * r * 0.2); c.lineTo(r * 0.55, i * r * 0.2); }
      c.stroke();
    },
    voice(c, r) { c.beginPath(); [-0.45, -0.22, 0, 0.22, 0.45].forEach((x, i) => { const hh = [0.2, 0.42, 0.6, 0.42, 0.2][i] * r; c.moveTo(x * r, -hh); c.lineTo(x * r, hh); }); c.stroke(); },
  };

  class Hologram {
    constructor(canvas, opts = {}) {
      this.canvas = canvas; this.ctx = canvas.getContext("2d");
      this.compact = !!opts.compact; this.hub = !!opts.hub; this.channels = [];
      this.state = "idle";
      this.level = 0; this.history = new Array(24).fill(0);
      this.envelope = null; this.levelSource = null;
      this.color = { core: PALETTE.idle.core.slice(), glow: PALETTE.idle.glow.slice() };
      this.head = { yaw: 0, pitch: 0, roll: 0 }; this.look = { yaw: 0, pitch: 0 }; this.idleLook = { yaw: 0, pitch: 0, until: 0 };
      this.mouse = null; this.mouseAt = 0;
      this.blinkAt = performance.now() + 2500; this.waveUntil = 0;
      this.dust = Array.from({ length: this.compact ? 26 : 70 }, (_, i) => ({ x: hash(i) - 0.5, y: hash(i + 99), s: 0.3 + hash(i + 7) }));
      this.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.t0 = performance.now();
      window.addEventListener("pointermove", (e) => {
        const r = this.canvas.getBoundingClientRect();
        this.mouse = [(e.clientX - r.left) / Math.max(1, r.width) - 0.5, (e.clientY - r.top) / Math.max(1, r.height) - 0.45];
        this.mouseAt = performance.now();
      });
      new ResizeObserver(() => this._resize()).observe(canvas); this._resize();
      this._tick = this._frame.bind(this); requestAnimationFrame(this._tick);
    }

    _resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const { width, height } = this.canvas.getBoundingClientRect();
      this.w = Math.max(1, width); this.h = Math.max(1, height);
      this.canvas.width = this.w * dpr; this.canvas.height = this.h * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    setState(state) {
      if (!PALETTE[state]) return;
      if (state === "listening" && this.state !== "listening") this.waveUntil = performance.now() + 1300;   // waves hello
      this.state = state;
      if (state !== "speaking") this.envelope = null;
    }
    speakEnvelope(env, frameMs, startedAt) { this.setState("speaking"); this.envelope = { env, frameMs: frameMs || 40, start: startedAt || performance.now() }; }
    setLevelSource(fn) { this.levelSource = fn; }
    setChannels(list) { this.channels = list || []; }

    _target(now) {
      if (this.levelSource) { const v = this.levelSource(); if (v != null) return v; }
      if (this.envelope) {
        const i = Math.floor((now - this.envelope.start) / this.envelope.frameMs);
        if (i >= 0 && i < this.envelope.env.length) return this.envelope.env[i];
        if (i >= this.envelope.env.length) { this.envelope = null; this.state = "idle"; }
        return 0;
      }
      const t = (now - this.t0) / 1000;
      return this.state === "speaking" ? 0.3 + 0.35 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 2.9)) : 0;
    }

    _frame(now) {
      requestAnimationFrame(this._tick);
      const ctx = this.ctx, w = this.w, h = this.h, t = (now - this.t0) / 1000;
      const target = this._target(now);
      this.level += (target - this.level) * (target > this.level ? 0.5 : 0.18);
      this.history.push(this.level); this.history.shift();
      const pal = PALETTE[this.state];
      this.color.core = mix(this.color.core, pal.core, 0.07); this.color.glow = mix(this.color.glow, pal.glow, 0.07);
      const core = this.color.core, glow = this.color.glow, L = this.level;
      const flicker = this.reduced ? 1 : 0.93 + Math.random() * 0.07;
      const motion = this.reduced ? 0.25 : 1;

      // where Setz looks: your cursor, otherwise it glances around on its own
      let ty, tp, tr = 0;
      if (this.mouse && now - this.mouseAt < 4000) { ty = this.mouse[0] * 0.9; tp = -this.mouse[1] * 0.6; }
      else {
        if (now > this.idleLook.until) this.idleLook = { yaw: (hash(now) - 0.5) * 0.7, pitch: (hash(now + 3) - 0.5) * 0.3, until: now + 2200 + hash(now + 9) * 3500 };
        ty = this.idleLook.yaw; tp = this.idleLook.pitch;
      }
      if (this.state === "listening") { tr = 0.2; tp += 0.06; ty *= 0.4; }
      if (this.state === "thinking") { ty = 0.32 + Math.sin(t * 0.8) * 0.08; tp = 0.28; tr = -0.06; }
      if (this.state === "speaking") { ty *= 0.35; tp = 0.04 + L * 0.12 * Math.sin(t * 9); tr = Math.sin(t * 1.7) * 0.05; }
      const hd = this.head;
      hd.yaw = lerp(hd.yaw, ty * motion, 0.06); hd.pitch = lerp(hd.pitch, tp * motion, 0.06); hd.roll = lerp(hd.roll, tr * motion, 0.05);
      this.look.yaw = lerp(this.look.yaw, ty * 0.35, 0.12); this.look.pitch = lerp(this.look.pitch, tp * 0.35, 0.12);

      const float = Math.sin(t * 1.3) * 0.12 * motion, sway = Math.sin(t * 0.9) * 0.035 * motion, bodyYaw = hd.yaw * 0.3;
      const breathe = 1 + Math.sin(t * 2.1) * 0.012 * motion;
      if (now > this.blinkAt + 160) this.blinkAt = now + 2200 + hash(now) * 4200;
      const blinkP = now > this.blinkAt ? Math.sin(Math.min(1, (now - this.blinkAt) / 160) * Math.PI) : 0;
      let finL = 0.08 * Math.sin(t * 1.6) * motion, finR = -finL;
      if (this.state === "speaking") { finL += L * 0.45 * Math.sin(t * 6); finR -= L * 0.45 * Math.sin(t * 6 + 1); }
      if (this.state === "thinking") finR = 0.55;                                 // hand raised, pondering
      if (now < this.waveUntil) finR = 1.9 + 0.4 * Math.sin(t * 15);            // waves hello

      // camera
      ctx.clearRect(0, 0, w, h);
      const hubOn = this.hub && w > 760 && this.channels.length > 0;
      const k = Math.min(h * (this.compact ? 0.7 : hubOn ? 0.56 : 0.64) / 4.2, w * (hubOn ? 0.3 : 0.62) / 3.6);
      const D = 9, F = k * D, cx = w / 2, oy = h * (this.compact ? 0.4 : 0.42) - 0.98 * k;
      const proj = (p) => { const s = F / (D - p[2]); return [cx + p[0] * s, oy - p[1] * s, s, p[2]]; };
      const world = (p) => add(rotY(rotZ(p, sway), bodyYaw), [0, float, 0]);
      const headT = (p) => world(rotY(rotX(rotZ([p[0] * breathe, p[1] * breathe, p[2] * breathe], hd.roll), -hd.pitch), hd.yaw));
      const finT = (shoulder, a) => (p) => world(add(rotZ(p, a), shoulder));
      this._headT = headT;

      const base = proj(world([0, -3.35, 0]));
      this._stage(ctx, base, k, core, glow, flicker, L, t, cx, hubOn ? proj(world([0, -0.95, 0])) : null);

      // meshes, painter-sorted
      const faces = [];
      const pushMesh = (mesh, T, kind) => {
        const tv = mesh.v.map(T), c = tv.reduce((a, v) => add(a, v), [0, 0, 0]).map((x) => x / tv.length);
        for (const f of mesh.f) {
          const a = tv[f[0]], b = tv[f[1]], d = tv[f[2]];
          let n = norm(cross(sub(b, a), sub(d, a)));
          const fc = [(a[0] + b[0] + d[0]) / 3, (a[1] + b[1] + d[1]) / 3, (a[2] + b[2] + d[2]) / 3];
          if (dot(n, sub(fc, c)) < 0) n = n.map((x) => -x);
          faces.push({ p: [proj(a), proj(b), proj(d)], z: fc[2], n, kind });
        }
        return tv;
      };
      pushMesh(FIN_L, finT(SHOULDER_L, finL), "fin"); pushMesh(FIN_R, finT(SHOULDER_R, finR), "fin");
      pushMesh(BODY, world, "body");
      pushMesh(EAR_L, headT, "ear"); pushMesh(EAR_R, headT, "ear");
      const headV = pushMesh(HEAD, headT, "head");
      faces.sort((a, b) => a.z - b.z);
      ctx.lineJoin = "round";
      for (const f of faces) {
        const front = f.n[2] > 0, lam = Math.max(0, dot(f.n, LIGHT));
        ctx.beginPath(); ctx.moveTo(f.p[0][0], f.p[0][1]); ctx.lineTo(f.p[1][0], f.p[1][1]); ctx.lineTo(f.p[2][0], f.p[2][1]); ctx.closePath();
        if (front) {
          const tone = f.kind === "ear" ? 0.75 : 1;
          ctx.fillStyle = rgba(mix(glow, core, 0.25 + 0.55 * lam).map((x) => x * tone), (0.16 + 0.34 * lam) * flicker);
          ctx.fill();
          ctx.strokeStyle = rgba(mix(core, [255, 255, 255], 0.35 * lam), (0.38 + 0.4 * lam) * flicker);
          ctx.lineWidth = this.compact ? 0.7 : 1;
        } else { ctx.strokeStyle = rgba(core, 0.1 * flicker); ctx.lineWidth = 0.6; }
        ctx.stroke();
      }
      ctx.fillStyle = rgba([235, 252, 255], 0.75 * flicker);       // glowing wireframe nodes
      headV.forEach((v, i) => { if (v[2] > 0.25 && i % 2 === 0) { const p = proj(v); ctx.beginPath(); ctx.arc(p[0], p[1], this.compact ? 0.9 : 1.4, 0, 7); ctx.fill(); } });

      this._face(ctx, proj, core, flicker, L, blinkP);
      if (this.state === "thinking") this._thinkingDots(ctx, proj, world, core, t);
      this._dust(ctx, base, k, core);
      if (!this.reduced) this._scan(ctx, core, t, w, h);
    }

    _face(ctx, proj, core, flicker, L, blinkP) {
      const headT = this._headT;
      // visor: a rounded panel on the front of the head (a superellipse traced on the head's surface)
      const pts = [];
      for (let i = 0; i < 56; i++) {
        const a = (i / 56) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
        pts.push(proj(headT(onHead(0.92 * Math.sign(c) * Math.pow(Math.abs(c), 0.45), 0.44 * Math.sign(s) * Math.pow(Math.abs(s), 0.45) - 0.04, 1.012))));
      }
      ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]))); ctx.closePath();
      const c0 = proj(headT(onHead(0, 0, 1)));
      const g = ctx.createRadialGradient(c0[0], c0[1] - c0[2] * 0.2, 0, c0[0], c0[1], c0[2] * 1.2);
      g.addColorStop(0, "rgba(10,30,52,0.94)"); g.addColorStop(1, "rgba(3,10,20,0.96)");
      ctx.fillStyle = g; ctx.fill();
      ctx.strokeStyle = rgba(core, 0.85 * flicker); ctx.lineWidth = this.compact ? 1.4 : 2.2; ctx.stroke();
      ctx.save(); ctx.clip(); ctx.strokeStyle = rgba(core, 0.08); ctx.lineWidth = 1;
      for (let i = -3; i <= 3; i++) { const y = c0[1] + i * c0[2] * 0.09; ctx.beginPath(); ctx.moveTo(c0[0] - c0[2], y); ctx.lineTo(c0[0] + c0[2], y); ctx.stroke(); }
      ctx.restore();

      // eyes: glowing rings that follow the gaze, blink, and pulse with the voice
      const big = this.state === "listening" ? 1.15 : this.state === "thinking" ? 0.85 : 1;
      for (const side of [-1, 1]) {
        const th = side * 0.37 + this.look.yaw * 0.5, ph = 0.05 + this.look.pitch * 0.45 + (this.state === "thinking" ? 0.08 : 0);
        const p = proj(headT(onHead(th, ph, 1.02)));
        const fore = Math.max(0.35, Math.cos(th + this.head.yaw));
        const r = p[2] * 0.2 * big * (1 + L * 0.18), ry = r * Math.max(0.08, 1 - blinkP);
        const halo = ctx.createRadialGradient(p[0], p[1], r * 0.4, p[0], p[1], r * 2.4);
        halo.addColorStop(0, rgba(core, 0.5 + L * 0.3)); halo.addColorStop(1, rgba(core, 0));
        ctx.fillStyle = halo; ctx.beginPath(); ctx.ellipse(p[0], p[1], r * 2.4 * fore, ry * 2.4, 0, 0, 7); ctx.fill();
        ctx.strokeStyle = rgba([235, 255, 255], 0.95 * flicker); ctx.lineWidth = Math.max(1.5, p[2] * 0.07);
        ctx.beginPath(); ctx.ellipse(p[0], p[1], r * fore, ry, 0, 0, 7); ctx.stroke();
      }
      // mouth: a little voice wave while speaking
      if (this.state === "speaking" || L > 0.05) {
        const n = 9; ctx.strokeStyle = rgba(core, 0.9 * flicker); ctx.lineCap = "round";
        for (let i = 0; i < n; i++) {
          const th = (i / (n - 1) - 0.5) * 0.52, v = this.history[(i * 3 + 5) % this.history.length];
          const hgt = 0.02 + v * 0.11 * (1 - Math.abs(i / (n - 1) - 0.5));
          const a = proj(headT(onHead(th, -0.27 + hgt, 1.02))), b = proj(headT(onHead(th, -0.27 - hgt, 1.02)));
          ctx.lineWidth = Math.max(1.2, a[2] * 0.035); ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        }
      }
    }

    _thinkingDots(ctx, proj, world, core, t) {
      for (let i = 0; i < 3; i++) {
        const a = t * 2.6 + i * 2.094, p = proj(world([Math.cos(a) * 1.25, 1.22 + Math.sin(t * 3 + i) * 0.06, Math.sin(a) * 0.6]));
        const g = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], p[2] * 0.16);
        g.addColorStop(0, rgba([255, 255, 255], 0.95)); g.addColorStop(0.4, rgba(core, 0.8)); g.addColorStop(1, rgba(core, 0));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p[0], p[1], p[2] * 0.16, 0, 7); ctx.fill();
      }
    }

    _stage(ctx, base, k, core, glow, flicker, L, t, cx, hubCenter) {
      const [bx, by] = base, R = k * 1.05;
      if (hubCenter) this._hub(ctx, hubCenter[0], hubCenter[1], k, core, t);
      const beam = ctx.createLinearGradient(0, by, 0, by - k * 4.6);
      beam.addColorStop(0, rgba(glow, 0.3 * flicker)); beam.addColorStop(1, rgba(glow, 0));
      ctx.fillStyle = beam; ctx.beginPath(); ctx.moveTo(bx - R * 0.5, by); ctx.lineTo(bx - R * 1.6, by - k * 4.6); ctx.lineTo(bx + R * 1.6, by - k * 4.6); ctx.lineTo(bx + R * 0.5, by); ctx.closePath(); ctx.fill();
      for (let i = 0; i < 3; i++) {
        ctx.strokeStyle = rgba(core, (0.6 - i * 0.16) * flicker); ctx.lineWidth = this.compact ? 1.2 : 2 - i * 0.4;
        const pr = R * (0.55 + i * 0.22) * (1 + (i === 2 ? L * 0.25 : 0) + 0.02 * Math.sin(t * 2 + i));
        ctx.beginPath(); ctx.ellipse(bx, by + i * 3, pr, pr * 0.2, 0, 0, 7); ctx.stroke();
      }
      const disc = ctx.createRadialGradient(bx, by, 0, bx, by, R * 0.6);
      disc.addColorStop(0, rgba([255, 255, 255], 0.55)); disc.addColorStop(0.3, rgba(core, 0.4)); disc.addColorStop(1, rgba(core, 0));
      ctx.fillStyle = disc; ctx.beginPath(); ctx.ellipse(bx, by, R * 0.6, R * 0.13, 0, 0, 7); ctx.fill();
    }

    _hub(ctx, cx, cy, k, core, t) {
      const w = this.w, h = this.h, ch = this.channels, half = Math.ceil(ch.length / 2);
      const nodeR = Math.max(20, Math.min(32, h * 0.035));
      for (let i = 0; i < 3; i++) {             // orb rings behind Setz
        ctx.strokeStyle = rgba(core, 0.18 - i * 0.04); ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(cx, cy, k * (2.25 + i * 0.22) * (1 + 0.01 * Math.sin(t * 1.5 + i)), 0, 7); ctx.stroke();
      }
      ch.forEach((c, i) => {
        const left = i < half, idx = left ? i : i - half, count = left ? half : ch.length - half;
        const y = h * 0.14 + (idx + 0.5) * (h * 0.68 / count);
        const bulge = Math.abs(idx - (count - 1) / 2) * w * 0.02;
        const x = left ? w * 0.09 + bulge : w * 0.91 - bulge;
        const ex = cx + (left ? -1 : 1) * k * 2.2, ey = cy + (y - cy) * 0.25;
        const col = c.ok === false ? [255, 110, 110] : c.ok == null ? [120, 150, 170] : core;
        const strength = c.ok === false ? 0.35 : c.ok == null ? 0.25 : 0.75;
        const sx = x + (left ? nodeR : -nodeR), c1x = x + (left ? 1 : -1) * w * 0.15, c2x = ex + (left ? -1 : 1) * w * 0.12;
        const bez = (u) => { const m = 1 - u; return [m * m * m * sx + 3 * m * m * u * c1x + 3 * m * u * u * c2x + u * u * u * ex, m * m * m * y + 3 * m * m * u * y + 3 * m * u * u * ey + u * u * u * ey]; };
        ctx.strokeStyle = rgba(col, strength * 0.6); ctx.lineWidth = 1.6;
        ctx.beginPath(); for (let u = 0; u <= 1.001; u += 0.04) { const p = bez(u); u ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); } ctx.stroke();
        if (c.ok !== false) {                  // data flowing into Setz
          for (let j = 0; j < 7; j++) {
            const u = (t * (0.22 + hash(i) * 0.15) + j / 7 + hash(i * 3)) % 1, p = bez(u);
            ctx.fillStyle = rgba(col, strength * (0.4 + 0.6 * Math.sin(u * Math.PI)));
            ctx.beginPath(); ctx.arc(p[0], p[1], 1.6 + 1.2 * Math.sin(u * Math.PI), 0, 7); ctx.fill();
          }
        }
        const g = ctx.createRadialGradient(x, y, nodeR * 0.6, x, y, nodeR * 1.7);
        g.addColorStop(0, rgba(col, 0.18 * strength)); g.addColorStop(1, rgba(col, 0));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, nodeR * 1.7, 0, 7); ctx.fill();
        ctx.fillStyle = "rgba(5,14,26,0.92)"; ctx.strokeStyle = rgba(col, 0.9 * Math.max(0.5, strength)); ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(x, y, nodeR, 0, 7); ctx.fill(); ctx.stroke();
        ctx.save(); ctx.translate(x, y); ctx.strokeStyle = "rgba(230,248,255,0.95)"; ctx.fillStyle = "rgba(230,248,255,0.95)"; ctx.lineWidth = 1.5;
        (ICONS[c.icon] || ICONS.web)(ctx, nodeR * 0.75); ctx.restore();
        ctx.textAlign = "center";
        ctx.fillStyle = "rgba(210,240,250,0.9)"; ctx.font = "500 12px Inter, system-ui, sans-serif"; ctx.fillText(c.label, x, y + nodeR + 16);
        if (c.value) { ctx.fillStyle = rgba(col, 0.9); ctx.font = "11px Inter, system-ui, sans-serif"; ctx.fillText(c.value, x, y + nodeR + 31); }
      });
    }

    _dust(ctx, base, k, core) {
      const [bx, by] = base;
      for (const d of this.dust) {
        d.y += 0.0016 * d.s; if (d.y > 1) { d.y = 0; d.x = Math.random() - 0.5; }
        const x = bx + d.x * k * 2.4 * (0.4 + d.y), y = by - d.y * k * 4.4;
        ctx.fillStyle = rgba(core, 0.5 * Math.sin(d.y * Math.PI)); ctx.fillRect(x, y, 1.5, 1.5);
      }
    }

    _scan(ctx, core, t, w, h) {
      ctx.fillStyle = rgba(core, 0.045);
      const off = (t * 26) % 4;
      for (let y = off; y < h; y += 4) ctx.fillRect(0, y, w, 1);
      const band = ((t * 0.25) % 1.4 - 0.2) * h;
      const g = ctx.createLinearGradient(0, band - 40, 0, band + 40);
      g.addColorStop(0, rgba(core, 0)); g.addColorStop(0.5, rgba(core, 0.06)); g.addColorStop(1, rgba(core, 0));
      ctx.fillStyle = g; ctx.fillRect(0, band - 40, w, 80);
      if (Math.random() < 0.025) {            // hologram glitch: nudge a thin strip sideways
        const dpr = this.canvas.width / w, y = Math.random() * h, hh = 3 + Math.random() * 8;
        const img = ctx.getImageData(0, y * dpr, this.canvas.width, hh * dpr);
        ctx.putImageData(img, (Math.random() - 0.5) * 14 * dpr, y * dpr);
      }
    }
  }

  // Subscribe to Setz's state stream (from `sz voice` or the dashboard's Ask box).
  function connectVoiceStream(holo, onEvent) {
    let es;
    const open = () => {
      es = new EventSource("/api/voice/stream");
      es.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        if (ev.state === "speaking" && ev.envelope && ev.envelope.length) {
          const age = Date.now() / 1000 - (ev.at || 0);
          if (age < 120) holo.speakEnvelope(ev.envelope, ev.frame_ms, performance.now() - Math.max(0, age) * 1000);
        } else holo.setState(ev.state);
        onEvent && onEvent(ev);
      };
      es.onerror = () => { es.close(); setTimeout(open, 3000); };
    };
    open();
  }

  window.Hologram = Hologram;
  window.connectVoiceStream = connectVoiceStream;
})();
