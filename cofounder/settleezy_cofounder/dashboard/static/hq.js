// Market, Contracts and Autopilot pages (uses the helpers defined in index.html: $, esc, post, toast, kpi, fmt, hbar, md, safeUrl).
(function () {
  const HQ = (window.HQ = {});
  const sevPill = (s) => `<span class="pill ${s === "alert" ? "high" : s === "warn" ? "med" : ""}">${esc(s)}</span>`;
  const when = (iso) => (iso ? String(iso).slice(0, 16).replace("T", " ") : "never");
  const AGENT_NAMES = { hermes: "Hermes", atlas: "Atlas", scout: "Scout", hunter: "Hunter", nova: "Nova", quant: "Quant", chrono: "Chrono",
    campus: "Campus", sentinel: "Sentinel", lex: "Lex", vega: "Vega", bridge: "Bridge", setz: "Setz" };
  const an = (k) => AGENT_NAMES[k] || k;
  let MK = null, CT = null, loaded = {};

  /* ---------- MARKET ---------- */
  async function loadMarket(ai) {
    const url = "/api/market" + (ai ? "?ai=1" : "");
    MK = await (await fetch(url, ai ? { headers: { "X-SZ": "1" } } : {})).json();
    const pa = MK.partners, ca = MK.competitors, nx = MK.onboard_next;
    const gap = pa.mix.find((g) => g.gap > 0.05);
    $("#mk-kpis").innerHTML = [
      kpi("Live partners", pa.totals.live, `${pa.totals.partners} in total`),
      kpi("Redemptions", fmt(pa.totals.redemptions), "all live partners"),
      kpi("Biggest gap", gap ? gap.bucket : "–", gap ? `${Math.round(gap.share * 100)}% vs ${Math.round(gap.target * 100)}% target` : "mix on target"),
      kpi("Warm leads", ca.warm_leads.length, "already discount elsewhere"),
      kpi("Competitors tracked", ca.platforms.length, ca.last_scan ? "scan " + when(ca.last_scan) : "no scan yet"),
      kpi("Top pick", nx[0] ? nx[0].name : "–", nx[0] ? nx[0].bucket : ""),
    ].join("");
    const sel = $("#mk-bucket");
    if (sel.options.length < 2) sel.innerHTML += pa.mix.map((g) => `<option>${esc(g.bucket)}</option>`).join("");
    renderNext(nx);
    $("#mk-summary").innerHTML = MK.summary ? md(MK.summary) : "";
    $("#mk-mix").innerHTML = pa.mix.map((g) => {
      const pct = Math.round(g.share * 100), tgt = Math.round(g.target * 100);
      return `<div class="hbar" title="target ${tgt}%"><span>${esc(g.bucket)}</span><div class="bar" style="position:relative"><i style="width:${Math.max(pct, g.partners ? 2 : 0)}%;background:${g.gap > 0.05 ? "var(--series-2)" : "var(--accent)"}"></i>
        <b style="position:absolute;left:${tgt}%;top:-2px;bottom:-2px;border-left:2px dashed var(--text-muted)"></b></div><span class="n">${pct}%</span></div>`;
    }).join("") + `<div class="muted" style="font-size:11.5px;margin-top:4px">Dashed line = target. Orange = under-represented.</div>`;
    const ins = [...pa.insights, ...ca.insights];
    $("#mk-insights").innerHTML = ins.length ? `<ul style="margin:0;padding-left:18px">${ins.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : `<div class="empty">Add partners and run the competitor scan.</div>`;
    $("#mk-scan").textContent = ca.last_scan ? "last scan " + when(ca.last_scan) + (ca.stale ? " · out of date" : "") : "not scanned yet";
    $("#mk-comp").innerHTML = ca.platforms.length ? `<tr><th>Platform</th><th class="num">Listings</th><th>Strongest in</th><th>Also has your partners</th></tr>` +
      ca.platforms.map((p) => `<tr><td><b>${esc(p.platform)}</b></td><td class="num">${p.listings}</td><td>${p.top_categories.slice(0, 3).map(([b, n]) => `${esc(b)} <span class="muted">${n}</span>`).join(", ")}</td>
        <td>${p.overlap_with_partners.length ? `<span class="pill med">${p.overlap_with_partners.length}</span> ${esc(p.overlap_with_partners.slice(0, 3).join(", "))}` : "–"}</td></tr>`).join("")
      : `<tr><td class="empty">No competitor listings yet. <button data-job="scrape">Scan competitors</button></td></tr>`;
    $("#mk-warm").innerHTML = ca.warm_leads.length ? `<tr><th>Venue</th><th>On</th><th>Category</th><th>Status</th></tr>` +
      ca.warm_leads.map((w) => `<tr><td><b>${esc(w.name)}</b>${w.campus ? `<div class="muted" style="font-size:11.5px">${esc(w.campus)}${w.distance_m != null ? " · " + w.distance_m + " m" : ""}</div>` : ""}</td>
        <td>${w.on.map((o) => `<span class="pill">${esc(o)}</span>`).join(" ")}</td><td>${esc(w.category)}</td><td>${esc(w.status)}</td></tr>`).join("")
      : `<tr><td class="empty">None yet.</td></tr>`;
    const live = pa.partners.filter((p) => p.status === "live" || p.status === "onboarding").sort((a, b) => (b.per_week ?? -1) - (a.per_week ?? -1));
    const maxW = Math.max(1, ...live.map((p) => p.per_week || 0));
    $("#mk-perf").innerHTML = live.length ? `<tr><th>Partner</th><th>Category</th><th>Per week</th><th class="num">Total</th><th>Health</th><th>Also on</th></tr>` +
      live.map((p) => `<tr><td><b>${esc(p.name)}</b><div class="muted" style="font-size:11.5px">${esc(p.stage)}</div></td><td>${esc(p.bucket)}</td>
        <td style="min-width:140px">${p.per_week == null ? '<span class="muted">not live</span>' : `<div class="bar" style="height:8px"><i style="width:${Math.round(100 * p.per_week / maxW)}%;background:var(--series-3)"></i></div><span style="font-size:12px">${fmt(p.per_week)}</span>`}</td>
        <td class="num">${p.redemptions}</td><td><span class="pill ${p.health.label === "at risk" ? "high" : p.health.label === "watch" ? "med" : ""}">${esc(p.health.label)}</span></td>
        <td>${esc(p.also_on.join(", ")) || "–"}</td></tr>`).join("") : `<tr><td class="empty">No partners yet.</td></tr>`;
    loadCampuses().catch(() => {});
  }

  function renderNext(rows) {
    $("#mk-next").innerHTML = rows.length ? rows.map((r, i) => `<div class="mk-row">
      <div class="mk-rank">${i + 1}</div>
      <div style="min-width:0"><b>${esc(r.name)}</b> <span class="pill">${esc(r.bucket)}</span> <span class="pill">${esc(r.status)}</span>
        <div class="muted" style="font-size:12px">${esc(r.category)}${r.campus ? " · " + esc(r.campus) : ""}${r.distance_m != null ? " · " + r.distance_m + " m" : ""}
          ${r.email ? " · " + esc(r.email) : r.phone ? " · " + esc(r.phone) : r.instagram ? " · @" + esc(r.instagram) : ""}</div>
        <div style="font-size:12.5px;margin-top:3px">${r.reasons.map((x) => `<span class="why">${esc(x)}</span>`).join("")}</div>
        <details style="margin-top:4px"><summary class="muted" style="font-size:12px;cursor:pointer">Pitch angle</summary><div style="font-size:12.5px">${esc(r.pitch)}</div></details></div>
      <div style="text-align:right"><div class="mk-score">${Math.round(r.priority)}</div><div class="muted" style="font-size:11px">${Math.round(r.join_likelihood * 100)}% join</div>
        ${r.status === "new" ? `<button class="small" data-seqstart="${r.id}" title="Start the outreach sequence (to-dos + drafts)">Start outreach</button>` : ""}</div></div>`).join("")
      : `<div class="empty">No open leads yet. Find universities, scan campuses and competitors first.</div>`;
  }

  async function loadCampuses() {
    const c = await (await fetch("/api/campuses")).json();
    const rows = c.coverage.slice().sort((a, b) => b.found - a.found);
    $("#mk-campuses").innerHTML = `<div class="muted" style="font-size:12px;margin-bottom:6px">${c.built_in} built-in + ${c.discovered.length} found on OSM · last search ${when(c.last_discovery)}</div>` +
      rows.map((r) => `<div class="hbar"><span title="${esc(r.campus)}">${esc(r.campus)}</span><div class="bar"><i style="width:${Math.min(100, r.found)}%;background:${r.partners ? "var(--series-3)" : "var(--accent)"}"></i></div><span class="n">${r.found}${r.partners ? " · " + r.partners + "✓" : ""}</span></div>`).join("");
  }

  /* ---------- CONTRACTS ---------- */
  async function loadContracts() {
    CT = await (await fetch("/api/contracts")).json();
    const tpl = $("#ct-tpl"), cur = tpl.value;
    tpl.innerHTML = CT.templates.map((t) => `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join("");
    if (cur) tpl.value = cur;
    const ps = $("#ct-partner"), pcur = ps.value;
    ps.innerHTML = `<option value="">– none –</option>` + CT.partners.map((p) => `<option value="${p.id}">${esc(p.name)} (${esc(p.stage)})</option>`).join("");
    ps.value = pcur;
    renderFields();
    const n = CT.contracts.filter((c) => c.status === "draft" && c.missing.length).length;
    $("#c-contracts").textContent = n || "";
    $("#ct-list").innerHTML = CT.contracts.length ? `<tr><th>Contract</th><th>Status</th><th>Score</th><th>Missing</th><th>Updated</th><th></th></tr>` +
      CT.contracts.map((c) => `<tr><td><b>${esc(c.title)}</b></td>
        <td><select data-ctstatus="${c.id}" aria-label="Status">${["draft", "review", "sent", "signed", "cancelled"].map((s) => `<option ${s === c.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
        <td>${c.score == null ? "–" : `<span class="pill ${c.score < 60 ? "high" : c.score < 85 ? "med" : ""}">${c.score}/100</span>`}${c.risks ? ` <span class="muted">${c.risks} risk(s)</span>` : ""}</td>
        <td style="max-width:280px">${c.missing.length ? esc(c.missing.map((m) => m.replace(/_/g, " ")).join(", ")) : "–"}</td><td>${when(c.updated_at)}</td>
        <td style="white-space:nowrap"><button class="small" data-ctopen="${c.id}">Open</button>
          ${c.path_docx ? `<a class="small" href="/api/contracts/${c.id}/download?fmt=docx">.docx</a>` : ""}${c.missing.length ? ` <button class="small" data-ctfill="${c.id}">Fill in</button>` : ""}</td></tr>`).join("")
      : `<tr><td class="empty">No contracts yet. When a partner agrees, Lex drafts the agreement automatically.</td></tr>`;
  }

  function renderFields(prefill) {
    const t = CT && CT.templates.find((x) => x.key === $("#ct-tpl").value);
    if (!t) return;
    const auto = new Set(["date", "start_date", "term_months", "renewal_months", "notice_weeks", "offer_conditions", "student_group", "services", "fee_terms", "purpose", "start_semester"]);
    const fields = t.fields.filter((f) => !auto.has(f) || (prefill && prefill[f]));
    $("#ct-fields").innerHTML = fields.map((f) => `<label>${esc(f.replace(/_/g, " "))}<input name="f_${esc(f)}" value="${esc((prefill || {})[f] || "")}" placeholder="${f.startsWith("our_") ? "from config.toml [contracts]" : "auto from Impressum / partner"}"></label>`).join("")
      + `<details style="grid-column:1/-1"><summary class="muted" style="font-size:12px;cursor:pointer">Term, notice, conditions (defaults)</summary><div class="form-grid">${t.fields.filter((f) => auto.has(f) && !(prefill && prefill[f])).map((f) => `<label>${esc(f.replace(/_/g, " "))}<input name="f_${esc(f)}" placeholder="default"></label>`).join("")}</div></details>`;
  }

  function showAnalysis(a, el) {
    el.innerHTML = `<div class="ct-score"><b style="font-size:26px">${a.score}</b><span class="muted">/100</span> <span style="margin-left:8px">${esc(a.verdict)}</span></div>
      <div class="ct-grid"><div><h3>Clauses</h3>${a.clauses.map((c) => `<div>${c.present ? "✓" : `<b style="color:var(--critical-text)">✗</b>`} ${esc(c.label)}</div>`).join("")}</div>
      <div><h3>Risks</h3>${a.risks.length ? a.risks.map((r) => `<div style="margin-bottom:8px">${sevPill(r.severity)} ${esc(r.why)}<div class="muted" style="font-size:12px">→ ${esc(r.suggestion)}</div>${r.excerpt ? `<div style="font-size:11.5px;border-left:2px solid var(--border);padding-left:6px;margin-top:2px">“${esc(r.excerpt)}”</div>` : ""}</div>`).join("") : '<div class="muted">None found.</div>'}
      <h3>Terms</h3><div style="font-size:12.5px">${a.terms.term ? `Term: ${a.terms.term.value} ${esc(a.terms.term.unit)}<br>` : ""}${a.terms.notice ? `Notice: ${a.terms.notice.value} ${esc(a.terms.notice.unit)}<br>` : ""}
        ${a.terms.percentages.length ? "Percent: " + esc(a.terms.percentages.join(", ")) + "<br>" : ""}${a.terms.amounts.length ? "Amounts: " + esc(a.terms.amounts.join(", ")) + "<br>" : ""}${a.terms.dates.length ? "Dates: " + esc(a.terms.dates.join(", ")) : ""}</div></div></div>
      ${a.ai ? `<h3>Claude's review</h3><div class="md">${md(a.ai)}</div>` : ""}<p class="muted" style="font-size:11.5px">${esc(a.disclaimer)}</p>`;
  }

  /* ---------- AUTOPILOT ---------- */
  async function loadAutopilot() {
    const [ap, cc, br, sh] = await Promise.all([fetch("/api/autopilot").then((r) => r.json()), fetch("/api/council").then((r) => r.json()),
      fetch("/api/bridge").then((r) => r.json()), fetch("/api/share/inbox").then((r) => r.json())]);
    $("#c-approvals").textContent = (ap.pending.length + sh.length) || "";
    $("#ap-pending").innerHTML = ap.pending.length ? ap.pending.map((a) => `<div class="ap-item"><div><b>${esc(a.title)}</b><div class="muted" style="font-size:12.5px">${esc(a.detail)}</div>
      <div class="muted" style="font-size:11px">${esc(a.rule.replace(/_/g, " "))} · until ${when(a.expires_at)}</div></div>
      <div style="display:flex;gap:6px"><button class="primary small" data-approve="${a.id}">Approve</button><button class="small" data-reject="${a.id}">Skip</button></div></div>`).join("")
      : `<div class="empty">Nothing waiting. Setz asks here before anything reaches another person.</div>`;
    $("#ap-rules").innerHTML = ap.rules.map((r) => `<div class="ap-rule"><span class="mode ${r.mode}">${r.mode}</span><span>${esc(r.label)}</span></div>`).join("");
    $("#ap-history").innerHTML = ap.history.length ? ap.history.map((h) => `<div style="font-size:12.5px;padding:4px 0;border-bottom:1px solid var(--border)"><span class="pill ${h.status === "failed" ? "high" : ""}">${esc(h.status)}</span> ${esc(h.title)}${h.result ? `<div class="muted" style="font-size:11.5px">${esc(h.result)}</div>` : ""}</div>`).join("") : `<div class="empty">No decisions yet.</div>`;
    $("#sh-inbox").innerHTML = sh.length ? sh.map((it) => `<div class="ap-item"><div style="min-width:0"><b>${esc(it.title || it.url || (it.file || "").replace(/^[0-9a-f]+-/, "") || "Shared text")}</b>
      <div class="muted" style="font-size:12px;overflow-wrap:anywhere">${esc((it.text || it.url || "").slice(0, 220))}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px">${it.suggestions.map((s) => `<button class="small${s.action === "discard" ? "" : " primary"}" data-share="${esc(it.id)}" data-act="${esc(s.action)}">${esc(s.label)}</button>`).join("")}</div></div></div>`).join("")
      : `<div class="empty">Share a link, an Instagram profile, an email or a contract to “Setz” from any app on your phone.</div>`;
    $("#cc-msgs").innerHTML = cc.messages.length ? cc.messages.map((m) => `<div class="cc-msg ${m.severity}"><div class="cc-route"><b>${esc(an(m.from_agent))}</b> → <b>${esc(an(m.to_agent))}</b> <span class="pill">${esc(m.kind)}</span>${m.count > 1 ? ` <span class="muted" style="font-size:11px">seen ${m.count}×</span>` : ""}</div>
      <div><b>${esc(m.title)}</b></div><div class="muted" style="font-size:12.5px;white-space:pre-wrap">${esc(m.detail)}</div>
      <div style="display:flex;gap:6px;margin-top:6px">${m.fix_label ? `<button class="primary small" data-ccfix="${m.id}">${esc(m.fix_label)}</button>` : ""}<button class="small" data-ccdismiss="${m.id}">Dismiss</button></div></div>`).join("")
      : `<div class="empty">No open issues between agents. Peer review runs with every agent report.</div>`;
    $("#cc-brains").innerHTML = cc.brains.map((b) => `<div class="cc-brain"><b>${esc(an(b.agent))}</b><span class="muted" style="font-size:12px">tracking ${b.tracking} · memories ${b.memories} · inbox ${b.inbox} · resolved ${b.resolved}${b.avg_days_to_resolve != null ? ` (≈${b.avg_days_to_resolve} d)` : ""}</span></div>`).join("");
    $("#br-status").innerHTML = br.configured
      ? `<div>Connected to <code>${esc(br.base_url)}</code> · last sync ${when(br.last_sync)} · webhooks ${br.webhooks ? "on" : "off"}</div>
         <div style="margin:6px 0">${br.linked}/${br.partners} partners linked</div>
         ${br.diffs.length ? `<h3>Differences</h3>${br.diffs.slice(0, 6).map((d) => `<div style="font-size:12.5px">• ${esc(d.text)}</div>`).join("")}` : ""}
         ${br.requests.length ? `<h3>Members ask for</h3>${br.requests.slice(0, 8).map((r) => `<span class="pill">${esc(r.venue || r.category)} ${r.count || 1}×</span>`).join(" ")}` : ""}`
      : `<div class="empty" style="padding-top:0">Not connected yet. Your website's admin panel exposes a small API (partners, member numbers, venue requests) and Setz reads it every hour: members, trials, redemptions and what students ask for flow into the agents and “Onboard next”. Setup: <code>docs/ADMIN_BRIDGE.md</code>.</div>`;
    const local = ["localhost", "127.0.0.1"].includes(location.hostname);
    $("#phone-help").innerHTML = `<ol style="margin:0;padding-left:18px;font-size:13px">
      <li><b>USB cable (simplest):</b> enable USB debugging on the Galaxy, plug it in, run <code>.\\scripts\\phone_usb.ps1 -Watch</code>. The phone opens Setz at <code>localhost:8765</code>.</li>
      <li><b>Anywhere:</b> Tailscale on laptop + phone, <code>tailscale serve --bg 8765</code>, open the https link.</li>
      <li>In Chrome: ⋮ → <b>Install app</b>. Long-press the icon for <i>Talk</i>, <i>Approvals</i>, <i>Onboard next</i>.</li>
      <li>Side button: Settings → Advanced features → Side button → Double press → Open app → <b>Setz</b>.</li>
      <li>Share anything to <b>Setz</b> from Instagram, Gmail or Files.</li></ol>
      <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap"><a class="small" href="/talk"><button class="small">Open push-to-talk</button></a>${local ? "" : `<span class="pill">connected from this phone</span>`}</div>`;
  }

  /* ---------- WRITING (Scribe) ---------- */
  const SAMPLES = {
    reschedule: ["Ingo Schneider", "Hello Mr. Sojitra,\n\nunfortunately something came up and I can't make it to our meeting today. Could we move it to another day?\n\nBest regards,\nIngo"],
    doubt: ["Carolina", "Hi Mruddual,\n\nthanks for the call. Before we fill in the form: how does the discount work exactly, and is there any fee or commission for us? We are also not sure how students prove they are members.\n\nBest,\nCarolina"],
    contract: ["Yasmin", "Dear Mruddual,\n\nplease find our comments on the agreement attached. We would like to change the representative name and adjust the clause about event cancellation.\n\nKind regards,\nYasmin"],
  };
  async function loadWriting() {
    const r = await (await fetch("/api/scribe")).json();
    const en = (r.style || {}).en || {}, de = (r.style || {}).de || {};
    $("#wr-learned").textContent = r.learned_at ? "learned " + when(r.learned_at) + " from " + r.emails + " emails" : (r.seed ? "from the analysis of your mail" : "not learned yet: run sz scribe learn");
    $("#wr-kpis").innerHTML = [
      kpi("Emails learned from", fmt(r.emails), r.learned_at ? "last 90 days" : "run sz scribe learn"),
      kpi("Typical length", en.median_words ? en.median_words + " words" : "–", "English replies"),
      kpi("Greeting", en.greetings && en.greetings[0] ? en.greetings[0][0] : "–", "most used"),
      kpi("Sign-off", en.sign_offs && en.sign_offs[0] ? en.sign_offs[0][0] : "–", "most used"),
      kpi("Drafts you kept", r.drafts_compared ? Math.round(100 * r.avg_similarity) + "%" : "–", r.drafts_compared ? r.drafts_compared + " compared with what you sent" : "learns from your edits"),
    ].join("");
    const block = (lang, st) => !st.emails ? "" : `<h3>${lang}</h3><div style="font-size:13px">
      <div><b>Greetings:</b> ${esc((st.greetings || []).slice(0, 4).map(([g, n]) => `${g} (${n})`).join(" · "))}</div>
      ${st.openers && st.openers.length ? `<div><b>Openers:</b> ${esc(st.openers.slice(0, 3).join(" · "))}</div>` : ""}
      <div><b>Sign-off:</b> ${esc((st.sign_offs || []).slice(0, 3).map(([g, n]) => `${g} (${n})`).join(" · "))}</div>
      <div><b>Length:</b> about ${st.median_words} words · bullets in ${Math.round(100 * (st.bullets_rate || 0))}% · exclamation marks in ${Math.round(100 * (st.exclamation_rate || 0))}%</div>
      ${st.du_vs_sie ? `<div><b>du / Sie:</b> ${st.du_vs_sie.du} / ${st.du_vs_sie.Sie}</div>` : ""}
      ${st.phrases && st.phrases.length ? `<div style="margin-top:6px"><b>Phrases you reuse:</b> ${st.phrases.slice(0, 14).map((p) => `<span class="why">${esc(p)}</span>`).join("")}</div>` : ""}
      ${st.never && st.never.length ? `<div style="margin-top:6px"><b>You delete:</b> ${st.never.slice(0, 10).map((p) => `<span class="why">${esc(p)}</span>`).join("")}</div>` : ""}</div>`;
    const ss = r.seed_style;
    const seedBlock = !ss ? "" : `<h3>From the analysis of your mail</h3><div style="font-size:13px">
      <div><b>Greeting:</b> ${esc(((ss.greetings || {}).en || {}).first || "")} · quick: ${esc(((ss.greetings || {}).en || {}).quick || "")} · "Hi" only if they did</div>
      <div><b>Sign-off:</b> ${esc((ss.sign_off || {}).en || "")}</div>
      <ul style="margin:6px 0 0;padding-left:18px">${(ss.rules || []).slice(0, 8).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <div style="margin-top:6px"><b>Your phrases:</b> ${(ss.phrases || []).slice(0, 12).map((x) => `<span class="why">${esc(x)}</span>`).join("")}</div></div>`;
    $("#wr-style").innerHTML = (block("English", en) + block("Deutsch", de) + seedBlock) || `<div class="empty">Setz learns your style from your sent mail. On your laptop run <code>sz scribe learn</code> (it also runs every night).</div>`;
    $("#wr-decisions").innerHTML = `<tr><th>Situation</th><th>What you do</th><th class="num">Examples</th><th>Length</th><th>Based on</th></tr>` +
      r.decisions.filter((d) => d.moves.length || d.examples).map((d) => `<tr><td><b>${esc(d.situation)}</b></td>
        <td>${d.moves.map((m) => `<span class="why">${esc(m.label.split(" (")[0])}${m.freq ? " · " + Math.round(100 * m.freq) + "%" : ""}</span>`).join("")}</td>
        <td class="num">${d.examples}</td><td>${d.median_words ? d.median_words + " words" : "–"}</td><td class="muted" style="font-size:12px">${esc(d.source)}</td></tr>`).join("");
  }
  function showDraft(o) {
    const p = o.plan;
    $("#wr-out").innerHTML = `<div class="wr-plan"><div><b>${esc(p.situation)}</b> <span class="pill">${p.lang === "de" ? "Deutsch" : "English"}</span>
      <span class="pill ${p.confidence < 0.6 ? "med" : ""}">confidence ${Math.round(p.confidence * 100)}%</span></div>
      <div style="margin-top:4px">${p.move_labels.map((m) => `<span class="why">${esc(m)}</span>`).join("") || '<span class="muted">short reply</span>'}</div>
      <div class="muted" style="font-size:12px;margin-top:4px">${esc(p.policy_source)}</div>
      ${p.checks.length ? `<div style="margin-top:6px">${p.checks.map((c) => `<div>⚑ ${esc(c)}</div>`).join("")}</div>` : ""}</div>
      <pre class="wr-draft">${esc(o.text)}</pre><div class="muted" style="font-size:12px">On your laptop this goes straight into Outlook → Drafts. Nothing is sent.</div>`;
  }

  /* ---------- events ---------- */
  document.addEventListener("click", async (e) => {
    const t = (s) => e.target.closest(s);
    try {
      if (t("[data-wrsample]")) { const [n, txt] = SAMPLES[t("[data-wrsample]").dataset.wrsample]; $("#wr-sender").value = n; $("#wr-text").value = txt; return; }
      if (t("#mk-ai")) { e.target.disabled = true; toast("Asking Claude…"); await loadMarket(true); e.target.disabled = false; return; }
      if (t("#mk-unis")) { e.target.disabled = true; toast("Searching OpenStreetMap for universities…"); const r = await post("/api/leadgen/universities");
        toast(`${r.institutions} institutions, ${r.added_campuses} campuses added, ${r.new_university_leads} new university leads`); e.target.disabled = false; return loadMarket(); }
      if (t("[data-approve]") || t("[data-reject]")) { const b = t("[data-approve]") || t("[data-reject]"); b.disabled = true;
        const r = await post(`/api/approvals/${b.dataset.approve || b.dataset.reject}`, { approve: !!b.dataset.approve });
        toast(r.status === "done" ? "Done: " + r.result : r.status === "failed" ? "Failed: " + r.result : "Skipped"); return loadAutopilot(); }
      if (t("#ap-run")) { toast("Autopilot running…"); const r = await post("/api/run/autopilot"); poll && poll("autopilot"); return; }
      if (t("#cc-run")) { const r = await post("/api/council/run"); toast(`${r.messages} open · ${r.new.length} new · ${r.resolved} resolved`); return loadAutopilot(); }
      if (t("[data-ccfix]")) { const r = await post(`/api/council/messages/${t("[data-ccfix]").dataset.ccfix}`, { action: "fix" }); toast(r.result); return loadAutopilot(); }
      if (t("[data-ccdismiss]")) { await post(`/api/council/messages/${t("[data-ccdismiss]").dataset.ccdismiss}`, { action: "dismiss" }); return loadAutopilot(); }
      if (t("[data-share]")) { const b = t("[data-share]"); b.disabled = true; const r = await post(`/api/share/${b.dataset.share}`, { action: b.dataset.act }); toast(r.done || "Done"); return loadAutopilot(); }
      if (t("[data-ctopen]")) { const c = await (await fetch(`/api/contracts/${t("[data-ctopen]").dataset.ctopen}`)).json();
        $("#dlg-title").textContent = c.title; const body = $("#dlg-body"); body.innerHTML = `<div id="ct-dlg-a"></div><h3>Text</h3><div class="md">${md(c.text || "_(file stored at " + (c.path_md || "") + ")_")}</div>`;
        if (c.analysis && c.analysis.clauses) showAnalysis(c.analysis, $("#ct-dlg-a")); dlg.showModal(); return; }
      if (t("[data-ctfill]")) { const c = await (await fetch(`/api/contracts/${t("[data-ctfill]").dataset.ctfill}`)).json();
        $("#ct-tpl").value = c.template; $("#ct-partner").value = c.partner_id || ""; renderFields(c.fields); $("#ct-form").dataset.id = c.id;
        $("#ct-fields").querySelector("input[value='']")?.focus(); toast("Fill in the missing fields, then Prepare draft"); return; }
      if (t("#ct-analyse")) {
        const f = $("#ct-file").files[0], txt = $("#ct-text").value.trim(); if (!f && !txt) return toast("Choose a file or paste the text");
        e.target.disabled = true; $("#ct-result").innerHTML = '<div class="empty">Lex is reading…</div>';
        const ai = $("#ct-ai").checked ? 1 : 0;
        const r = await fetch(`/api/contracts/analyse?ai=${ai}`, f ? { method: "POST", headers: { "X-SZ": "1", "X-Filename": encodeURIComponent(f.name).replace(/%/g, "_"), "Content-Type": "application/octet-stream" }, body: f }
          : { method: "POST", headers: { "X-SZ": "1", "Content-Type": "application/json" }, body: JSON.stringify({ text: txt }) });
        const a = await r.json(); e.target.disabled = false;
        if (!r.ok) { $("#ct-result").innerHTML = `<div class="empty">${esc(a.detail || "Could not read it")}</div>`; return; }
        showAnalysis(a, $("#ct-result")); return loadContracts();
      }
    } catch (err) { toast(err.message); if (e.target.tagName === "BUTTON") e.target.disabled = false; }
  });
  document.addEventListener("change", async (e) => {
    if (e.target.id === "mk-bucket") { const r = await (await fetch("/api/market/onboard-next?limit=25&bucket=" + encodeURIComponent(e.target.value))).json(); return renderNext(r); }
    if (e.target.id === "ct-tpl") return renderFields();
    if (e.target.dataset && e.target.dataset.ctstatus) { try { await post(`/api/contracts/${e.target.dataset.ctstatus}/status`, { status: e.target.value }); toast(e.target.value === "signed" ? "Signed: partner moved forward" : "Status saved"); loadContracts(); } catch (err) { toast(err.message); } }
  });
  document.addEventListener("submit", async (e) => {
    if (e.target.id === "wr-form") {
      e.preventDefault(); const b = e.target.querySelector("button.primary"); b.disabled = true; $("#wr-out").innerHTML = '<div class="empty">Setz is thinking like you…</div>';
      try { showDraft(await post("/api/scribe/try", { text: $("#wr-text").value, sender: $("#wr-sender").value })); } catch (err) { $("#wr-out").innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
      b.disabled = false; return;
    }
    if (e.target.id !== "ct-form") return;
    e.preventDefault();
    const f = new FormData(e.target), fields = {};
    for (const [k, v] of f.entries()) if (k.startsWith("f_") && v) fields[k.slice(2)] = v;
    try {
      const r = await post("/api/contracts", { template: f.get("template"), partner_id: f.get("partner_id") || null, fields, id: e.target.dataset.id || null });
      delete e.target.dataset.id;
      toast(r.missing.length ? `Draft ready · still missing: ${r.missing.join(", ").replace(/_/g, " ")}` : "Draft ready: complete");
      loadContracts();
    } catch (err) { toast(err.message); }
  });

  /* ---------- page hooks ---------- */
  const loaders = { market: () => loadMarket(), contracts: loadContracts, approvals: loadAutopilot, writing: loadWriting };
  HQ.show = (p) => { if (loaders[p]) loaders[p]().then(() => (loaded[p] = Date.now())).catch((err) => toast(err.message)); };
  HQ.load = () => {
    fetch("/api/autopilot").then((r) => r.json()).then((ap) => fetch("/api/share/inbox").then((r) => r.json()).then((sh) => { $("#c-approvals").textContent = (ap.pending.length + sh.length) || ""; })).catch(() => {});
    if (loaders[page]) HQ.show(page);
  };
  const css = document.createElement("style");
  css.textContent = `.mk-row{display:grid;grid-template-columns:28px 1fr auto;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);align-items:start}
  .mk-rank{width:24px;height:24px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:600;font-size:12px}
  .mk-score{font-size:20px;font-weight:700;line-height:1}.why{display:inline-block;background:var(--surface-2);border-radius:6px;padding:1px 7px;margin:2px 4px 0 0;font-size:11.5px}
  .ap-item{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--border);align-items:flex-start}
  .ap-rule{display:flex;gap:8px;align-items:center;padding:4px 0;font-size:13px}.mode{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;border-radius:6px;padding:1px 6px;background:var(--surface-2);min-width:38px;text-align:center}
  .mode.auto{background:var(--accent-soft);color:var(--accent)}.mode.ask{background:rgba(250,178,25,.18);color:var(--warn-text)}
  .cc-msg{border-left:3px solid var(--border);padding:8px 10px;margin-bottom:8px;background:var(--surface-2);border-radius:0 8px 8px 0}.cc-msg.alert{border-left-color:var(--critical)}.cc-msg.warn{border-left-color:var(--warning)}
  .cc-route{font-size:12px;color:var(--text-secondary);margin-bottom:2px}.cc-brain{display:grid;padding:6px 0;border-bottom:1px solid var(--border)}
  .ct-grid{display:grid;grid-template-columns:1fr 1.4fr;gap:16px}.ct-grid h3,#page-approvals h3,#page-contracts h3{font-size:12.5px;margin:10px 0 4px;color:var(--text-secondary)}.ct-score{margin:10px 0}
  .wr-plan{margin-top:12px;padding:10px 12px;border-radius:10px;background:var(--surface-2)}.wr-draft{white-space:pre-wrap;font:13.5px/1.55 Inter,system-ui,sans-serif;background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin:10px 0 6px;max-width:100%;overflow-wrap:anywhere}
  #ct-list a.small{font-size:12px;margin-left:4px}#ct-ai{width:auto;padding:0;margin:0}#page-contracts .toolbar label{white-space:nowrap}nav.tabsbar .count:empty{display:none}
  @media (max-width:700px){.ct-grid{grid-template-columns:1fr}.mk-row{grid-template-columns:24px 1fr}.mk-row>div:last-child{grid-column:2;text-align:left;display:flex;gap:10px;align-items:center}.ap-item{flex-direction:column}}`;
  document.head.appendChild(css);
  if (window.SetzLive) SetzLive.onRefresh((what) => { if (["autopilot", "bridge", "council", "contracts", "agents", "universities"].includes(what) && loaders[page]) HQ.show(page); });
})();
