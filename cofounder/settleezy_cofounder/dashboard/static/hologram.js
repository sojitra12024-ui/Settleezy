// Settleezy voice hologram: a projected point-cloud sphere with orbit rings, a voice waveform halo,
// scanlines and flicker. Reacts to state (idle / listening / thinking / speaking) and to loudness,
// either from a precomputed envelope (voice assistant) or a live AnalyserNode (audio played in the page).
(function () {
  const PALETTE = {
    idle:      { core: [94, 242, 255], glow: [42, 120, 214] },
    listening: { core: [120, 255, 214], glow: [27, 175, 122] },
    thinking:  { core: [170, 160, 255], glow: [74, 58, 167] },
    speaking:  { core: [94, 242, 255], glow: [57, 135, 229] },
  };
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));

  class Hologram {
    constructor(canvas, opts = {}) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.compact = !!opts.compact;
      this.state = "idle";
      this.level = 0;
      this.target = 0;
      this.history = new Array(72).fill(0);
      this.envelope = null;
      this.levelSource = null;
      this.color = { core: PALETTE.idle.core.slice(), glow: PALETTE.idle.glow.slice() };
      this.points = this._sphere(this.compact ? 260 : 520);
      this.t0 = performance.now();
      this.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
      this._resize = this._resize.bind(this);
      new ResizeObserver(this._resize).observe(canvas);
      this._resize();
      this._tick = this._frame.bind(this);
      requestAnimationFrame(this._tick);
    }

    _sphere(n) {
      const pts = [], g = Math.PI * (3 - Math.sqrt(5));
      for (let i = 0; i < n; i++) {
        const y = 1 - (i / (n - 1)) * 2, r = Math.sqrt(1 - y * y), th = g * i;
        pts.push([Math.cos(th) * r, y, Math.sin(th) * r, Math.random()]);
      }
      return pts;
    }

    _resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const { width, height } = this.canvas.getBoundingClientRect();
      this.w = Math.max(1, width); this.h = Math.max(1, height);
      this.canvas.width = this.w * dpr; this.canvas.height = this.h * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    setState(state) { if (PALETTE[state]) this.state = state; if (state !== "speaking") { this.envelope = null; } }
    speakEnvelope(env, frameMs, startedAt) {
      this.state = "speaking";
      this.envelope = { env, frameMs: frameMs || 40, start: startedAt || performance.now() };
    }
    setLevelSource(fn) { this.levelSource = fn; }

    _currentTarget(now) {
      if (this.levelSource) { const v = this.levelSource(); if (v != null) return v; }
      if (this.envelope) {
        const i = Math.floor((now - this.envelope.start) / this.envelope.frameMs);
        if (i >= 0 && i < this.envelope.env.length) return this.envelope.env[i];
        if (i >= this.envelope.env.length) { this.envelope = null; this.state = "idle"; }
        return 0;
      }
      const t = (now - this.t0) / 1000;
      if (this.state === "speaking") return 0.35 + 0.3 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 3.1));
      if (this.state === "listening") return 0.12 + 0.06 * Math.sin(t * 5);
      if (this.state === "thinking") return 0.1;
      return 0.04 + 0.03 * Math.sin(t * 1.4);
    }

    _frame(now) {
      requestAnimationFrame(this._tick);
      const ctx = this.ctx, w = this.w, h = this.h, t = (now - this.t0) / 1000;
      this.target = this._currentTarget(now);
      this.level += (this.target - this.level) * (this.target > this.level ? 0.45 : 0.15);
      this.history.push(this.level); this.history.shift();
      const pal = PALETTE[this.state];
      this.color.core = mix(this.color.core, pal.core, 0.08);
      this.color.glow = mix(this.color.glow, pal.glow, 0.08);
      const core = this.color.core, glow = this.color.glow;
      const flicker = this.reduced ? 1 : 0.92 + Math.random() * 0.08;
      const L = this.level;

      ctx.clearRect(0, 0, w, h);
      const cx = w / 2, cy = h * (this.compact ? 0.46 : 0.44);
      const R = Math.min(w, h) * (this.compact ? 0.27 : 0.25) * (1 + 0.12 * L);

      // projector beam + emitter
      const baseY = cy + R * 1.75;
      const beam = ctx.createLinearGradient(0, baseY, 0, cy - R);
      beam.addColorStop(0, rgba(glow, 0.32 * flicker)); beam.addColorStop(1, rgba(glow, 0));
      ctx.fillStyle = beam;
      ctx.beginPath(); ctx.moveTo(cx - R * 0.35, baseY); ctx.lineTo(cx - R * 1.25, cy - R * 0.4);
      ctx.lineTo(cx + R * 1.25, cy - R * 0.4); ctx.lineTo(cx + R * 0.35, baseY); ctx.closePath(); ctx.fill();
      for (let i = 0; i < 3; i++) {
        ctx.strokeStyle = rgba(core, (0.55 - i * 0.15) * flicker);
        ctx.lineWidth = 2 - i * 0.5;
        ctx.beginPath(); ctx.ellipse(cx, baseY + i * 4, R * (0.42 + i * 0.12), R * (0.08 + i * 0.02), 0, 0, Math.PI * 2); ctx.stroke();
      }

      // halo glow
      const halo = ctx.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 1.9);
      halo.addColorStop(0, rgba(glow, 0.28 + 0.25 * L)); halo.addColorStop(1, rgba(glow, 0));
      ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(cx, cy, R * 1.9, 0, Math.PI * 2); ctx.fill();

      // voice waveform halo (recent loudness as radial bars)
      const bars = this.history.length;
      ctx.lineWidth = this.compact ? 1.5 : 2; ctx.lineCap = "round";
      for (let i = 0; i < bars; i++) {
        const a = (i / bars) * Math.PI * 2 - Math.PI / 2 + t * 0.15;
        const v = this.history[(i * 7) % bars];
        const r0 = R * 1.28, r1 = r0 + R * (0.04 + 0.3 * v);
        ctx.strokeStyle = rgba(core, 0.25 + 0.6 * v);
        ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
        ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); ctx.stroke();
      }

      // orbit rings
      const speed = this.state === "thinking" ? 1.8 : this.state === "listening" ? 1.1 : 0.5;
      for (let k = 0; k < 3; k++) {
        const tilt = [0.32, -0.55, 1.1][k], rr = R * [1.08, 1.16, 0.96][k];
        ctx.save(); ctx.translate(cx, cy); ctx.rotate(tilt + t * speed * [0.25, -0.18, 0.12][k]);
        ctx.setLineDash(k === 1 ? [4, 8] : k === 2 ? [1, 6] : []);
        ctx.strokeStyle = rgba(core, (0.35 + 0.3 * L) * flicker); ctx.lineWidth = k === 0 ? 1.4 : 1;
        ctx.beginPath(); ctx.ellipse(0, 0, rr, rr * 0.28, 0, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
      }
      ctx.setLineDash([]);

      // thinking: orbiting comets
      if (this.state === "thinking") {
        for (let k = 0; k < 3; k++) {
          const a = t * 2.4 + (k * Math.PI * 2) / 3;
          const x = cx + Math.cos(a) * R * 1.12, y = cy + Math.sin(a) * R * 0.34;
          const g = ctx.createRadialGradient(x, y, 0, x, y, 9); g.addColorStop(0, rgba(core, 0.95)); g.addColorStop(1, rgba(core, 0));
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.fill();
        }
      }

      // point-cloud sphere, rippling with the voice
      const rotY = t * (0.35 + L * 0.8), rotX = 0.35 + Math.sin(t * 0.3) * 0.08;
      const cY = Math.cos(rotY), sY = Math.sin(rotY), cX = Math.cos(rotX), sX = Math.sin(rotX);
      for (const [px, py, pz, seed] of this.points) {
        const ripple = 1 + L * 0.22 * Math.sin(py * 9 + t * 9 + seed * 6) + L * 0.08 * Math.sin(px * 14 - t * 6);
        let x = px * ripple, y = py * ripple, z = pz * ripple;
        const x1 = x * cY + z * sY, z1 = -x * sY + z * cY;
        const y1 = y * cX - z1 * sX, z2 = y * sX + z1 * cX;
        const depth = (z2 + 1.3) / 2.3;
        const size = (this.compact ? 0.8 : 1.1) + depth * (this.compact ? 1.2 : 1.8);
        ctx.fillStyle = rgba(core, (0.15 + 0.75 * depth) * flicker);
        ctx.fillRect(cx + x1 * R - size / 2, cy + y1 * R - size / 2, size, size);
      }

      // inner core
      const coreG = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * (0.45 + 0.35 * L));
      coreG.addColorStop(0, rgba([255, 255, 255], 0.5 + 0.4 * L)); coreG.addColorStop(0.35, rgba(core, 0.35 + 0.3 * L)); coreG.addColorStop(1, rgba(core, 0));
      ctx.fillStyle = coreG; ctx.beginPath(); ctx.arc(cx, cy, R * (0.45 + 0.35 * L), 0, Math.PI * 2); ctx.fill();

      // scanlines
      if (!this.reduced) {
        ctx.fillStyle = rgba(core, 0.06);
        const off = (t * 30) % 4;
        for (let y = cy - R * 1.9 + off; y < baseY; y += 4) ctx.fillRect(cx - R * 1.9, y, R * 3.8, 1);
        if (Math.random() < 0.03) { ctx.fillStyle = rgba(core, 0.12); ctx.fillRect(cx - R * 1.6, cy - R + Math.random() * R * 2, R * 3.2, 2); }
      }
    }
  }

  // Subscribe to the voice assistant's state stream (from `sz voice` or the dashboard's Ask box).
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
