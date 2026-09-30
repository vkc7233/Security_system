/* SiteGuard dashboard */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local time
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const time = (ts) => (ts ? ts.slice(11, 16) : "—");
  const dateTime = (ts) => {
    if (!ts) return "—";
    const d = new Date(ts);
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + " " + ts.slice(11, 16);
  };
  const initials = (n) => n.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  const avatar = (p) => p.photo ? `<div class="avatar"><img src="/faces/${encodeURIComponent(p.photo)}" alt=""></div>` : `<div class="avatar">${esc(initials(p.name))}</div>`;

  async function api(url, opts = {}) {
    const res = await fetch(url, opts);
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON */ }
    if (!res.ok) throw new Error(data.status || `Request failed (${res.status})`);
    return data;
  }

  function toast(msg, kind = "ok") {
    const t = document.createElement("div");
    t.className = `toast ${kind}`;
    t.textContent = msg;
    $("#toasts").appendChild(t);
    setTimeout(() => t.remove(), kind === "error" ? 7000 : 4000);
  }

  /* ---------- navigation ---------- */
  const loaders = {};
  function show(view) {
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
    if (view !== "enrol") stopCam();
    loaders[view]?.();
    if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  }
  $$(".nav-item").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  document.addEventListener("click", (e) => {
    const g = e.target.closest("[data-goto]");
    if (g) show(g.dataset.goto);
    const z = e.target.closest("[data-zoom]");
    if (z) { $("#modal-img").src = z.dataset.zoom; $("#modal").hidden = false; }
  });
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.id === "modal-close") $("#modal").hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });

  setInterval(() => { $("#clock").textContent = new Date().toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "medium" }); }, 1000);

  /* ---------- alert sound (only after user interaction) ---------- */
  let audioCtx = null;
  document.addEventListener("click", () => { audioCtx ??= new (window.AudioContext || window.webkitAudioContext)(); }, { once: true });
  function beep(critical) {
    if (!audioCtx) return;
    const pattern = critical ? [0, 0.25, 0.5] : [0];
    pattern.forEach((t) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = critical ? 1050 : 760;
      g.gain.setValueAtTime(0.15, audioCtx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + t + 0.2);
      o.connect(g).connect(audioCtx.destination);
      o.start(audioCtx.currentTime + t); o.stop(audioCtx.currentTime + t + 0.2);
    });
  }

  /* ---------- live monitor ---------- */
  let armed = false, lastAlertId = null, lastOnSite = [];
  async function poll() {
    let s;
    try { s = await api("/api/status"); } catch (_) { $("#status-pill").textContent = "Server offline"; $("#status-pill").className = "status-pill err"; return; }
    const e = s.engine, k = s.stats;
    armed = e.armed;
    const btn = $("#btn-arm");
    btn.textContent = armed ? "Disarm System" : "Arm System";
    btn.classList.toggle("armed", armed);
    const pill = $("#status-pill"), dot = $("#side-dot");
    if (armed && e.camera_ok) { pill.textContent = "Armed · Monitoring"; pill.className = "status-pill on"; dot.className = "dot on"; }
    else if (armed) { pill.textContent = "Armed · Camera offline"; pill.className = "status-pill err"; dot.className = "dot err"; }
    else { pill.textContent = "Disarmed"; pill.className = "status-pill"; dot.className = "dot"; }
    $("#rec").hidden = !(armed && e.camera_ok);
    $("#m-fps").textContent = `${e.fps} fps`;
    $("#m-faces").textContent = `${e.faces_in_frame} face${e.faces_in_frame === 1 ? "" : "s"}`;
    $("#m-cam").textContent = armed ? (e.camera_ok ? "Camera online" : "Camera offline") : "Camera idle";

    const warn = [];
    if (!e.face_lib) warn.push("Face recognition library is not installed on the server — install requirements.txt to arm the system.");
    if (!e.telegram) warn.push("Telegram alerts are not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID). Alerts will show on this dashboard only.");
    if (e.last_error) warn.push(e.last_error);
    if (!e.gallery_size) warn.push("No people enrolled yet — every face will be treated as Unknown.");
    $("#engine-warning").hidden = !warn.length;
    $("#engine-warning").innerHTML = warn.map(esc).join("<br>");

    $("#k-persons").textContent = k.persons;
    $("#k-onsite").textContent = s.on_site.length;
    $("#k-entries").textContent = k.entries_today;
    $("#k-unknown").textContent = k.unknown_today;
    $("#k-open").textContent = k.open_alerts;
    $("#k-critical").textContent = k.critical_today;
    const nb = $("#nav-alerts");
    nb.hidden = !k.open_alerts; nb.textContent = k.open_alerts;

    renderAlerts(s.alerts);
    lastOnSite = s.on_site;
    $("#onsite-list").innerHTML = s.on_site.length
      ? s.on_site.map((p) => `<li>${avatar(p)}<div class="grow"><div>${esc(p.name)}</div><div class="muted small">${esc(p.category)}</div></div><span class="muted small">${time(p.last_seen)}</span></li>`).join("")
      : `<li class="empty">Nobody recorded</li>`;
    if ($("#view-muster").classList.contains("active")) renderMuster();

    const newest = s.alerts[0];
    if (newest && lastAlertId !== null && newest.id > lastAlertId) {
      beep(newest.severity === "Critical");
      toast(`${newest.severity}: ${newest.reason} — ${newest.name}`, newest.severity === "Critical" ? "error" : "warn");
    }
    if (newest) lastAlertId = Math.max(lastAlertId ?? 0, newest.id);
    else if (lastAlertId === null) lastAlertId = 0;
  }

  function renderAlerts(alerts) {
    $("#alert-list").innerHTML = alerts.length ? alerts.map((a) => `
      <li class="sev-${esc(a.severity)}">
        ${a.snapshot ? `<img src="/snapshots/${esc(a.snapshot)}" data-zoom="/snapshots/${esc(a.snapshot)}" alt="">` : `<div class="avatar">!</div>`}
        <div class="grow"><div class="title">${esc(a.reason)}</div><div class="muted small">${esc(a.name)} · ${dateTime(a.ts)} · <span class="sev sev-${esc(a.severity)}">${esc(a.severity)}</span></div></div>
        <button class="btn btn-sm" data-ack="${a.id}">Ack</button>
      </li>`).join("") : `<li class="empty">No open alerts</li>`;
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-ack]");
    if (!b) return;
    const note = prompt("Action taken (optional), e.g. 'Visitor escorted, gate pass issued':") ?? null;
    if (note === null) return;
    try { await api(`/api/events/${b.dataset.ack}/ack`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note }) }); toast("Alert acknowledged"); poll(); if ($("#view-events").classList.contains("active")) loadEvents(); }
    catch (err) { toast(err.message, "error"); }
  });

  $("#btn-arm").addEventListener("click", async () => {
    const btn = $("#btn-arm"); btn.disabled = true;
    try {
      const r = await api(armed ? "/api/security/stop" : "/api/security/start", { method: "POST" });
      toast(r.status);
      $("#feed").src = `/video_feed?t=${Date.now()}`;
    } catch (err) { toast(err.message, "error"); }
    btn.disabled = false;
    setTimeout(poll, 600);
  });

  /* ---------- enrolment ---------- */
  let stream = null;
  const samples = []; // {blob, url}
  let useLive = false; // add the current armed-camera frame on save
  async function startCam() {
    if (stream) return;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 } });
      $("#video").srcObject = stream;
      $("#btn-cam").textContent = "Stop Camera";
    } catch (err) { toast("Camera access denied or unavailable: " + err.message, "error"); }
  }
  function stopCam() {
    stream?.getTracks().forEach((t) => t.stop());
    stream = null;
    $("#btn-cam").textContent = "Start Camera";
  }
  $("#btn-cam").addEventListener("click", () => (stream ? stopCam() : startCam()));
  function addSample(blob) {
    if (samples.length >= 5) return toast("Maximum 5 photos per enrolment", "warn");
    samples.push({ blob, url: URL.createObjectURL(blob) });
    renderSamples();
  }
  function renderSamples() {
    $("#samples").innerHTML = samples.map((s, i) => `<div class="sample"><img src="${s.url}" alt="Sample ${i + 1}"><button type="button" data-rm="${i}" aria-label="Remove">×</button></div>`).join("");
  }
  $("#samples").addEventListener("click", (e) => {
    const i = e.target.dataset.rm;
    if (i === undefined) return;
    URL.revokeObjectURL(samples[i].url); samples.splice(i, 1); renderSamples();
  });
  $("#btn-snap").addEventListener("click", () => {
    const v = $("#video");
    if (!stream || !v.videoWidth) return toast("Start the camera first", "warn");
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    c.toBlob((b) => addSample(b), "image/jpeg", 0.92);
  });
  $("#btn-live").addEventListener("click", () => {
    if (!armed) return toast("Arm the system first to use the security camera", "warn");
    useLive = !useLive;
    $("#btn-live").classList.toggle("btn-primary", useLive);
    toast(useLive ? "The current security-camera frame will be added on save" : "Security camera frame removed");
  });
  $("#file-input").addEventListener("change", (e) => { [...e.target.files].forEach(addSample); e.target.value = ""; });
  const dropZone = $(".drop");
  dropZone.addEventListener("dragover", (e) => e.preventDefault());
  dropZone.addEventListener("drop", (e) => { e.preventDefault(); [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/")).forEach(addSample); });
  $$(".seg-btn").forEach((b) => b.addEventListener("click", () => {
    $$(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    $("#src-webcam").hidden = b.dataset.src !== "webcam";
    $("#src-upload").hidden = b.dataset.src !== "upload";
    if (b.dataset.src === "webcam") startCam(); else stopCam();
  }));
  $("#btn-clear").addEventListener("click", () => { samples.splice(0).forEach((s) => URL.revokeObjectURL(s.url)); useLive = false; $("#btn-live").classList.remove("btn-primary"); renderSamples(); });

  const catHints = {
    Blacklisted: "Blacklisted people trigger a CRITICAL alert and are excluded from the muster list.",
    Visitor: "Set an 'Access valid until' date — sightings after expiry raise a High alert.",
    Labour: "Labour seen outside working hours raises a Medium alert.",
    Vendor: "Vendors seen outside working hours raise a Medium alert.",
    Contractor: "Set 'Access valid until' to the contract end date.",
  };
  $("[name=category]").addEventListener("change", (e) => {
    const h = $("#cat-hint");
    h.textContent = catHints[e.target.value] || "";
    h.classList.toggle("danger", e.target.value === "Blacklisted");
  });

  $("#enrol-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!samples.length && !useLive) return toast("Capture or upload at least one photo", "warn");
    const fd = new FormData(e.target);
    samples.forEach((s, i) => fd.append("image", s.blob, `sample_${i}.jpg`));
    if (useLive) fd.append("source", "live");
    const btn = $("#btn-enrol"); btn.disabled = true; btn.textContent = "Processing…";
    try {
      const r = await api("/api/persons", { method: "POST", body: fd });
      toast(r.status);
      e.target.reset(); $("#btn-clear").click(); $("#cat-hint").textContent = "";
    } catch (err) { toast(err.message, "error"); }
    btn.disabled = false; btn.textContent = "Save Person";
  });
  loaders.enrol = () => { if (!$("#src-webcam").hidden) startCam(); };

  /* ---------- people ---------- */
  let people = [];
  loaders.people = async () => { people = await api("/api/persons"); renderPeople(); };
  function renderPeople() {
    const q = $("#people-search").value.toLowerCase(), cat = $("#people-cat").value;
    const list = people.filter((p) => (!cat || p.category === cat) && (!q || `${p.name} ${p.project}`.toLowerCase().includes(q)));
    $("#people-count").textContent = `${list.length} of ${people.length} people`;
    const expired = (p) => p.valid_until && p.valid_until < today();
    $("#people-grid").innerHTML = list.length ? list.map((p) => `
      <div class="card person ${p.active ? "" : "inactive"}">
        <div class="person-top">${avatar(p)}<div><div class="person-name">${esc(p.name)}</div><span class="chip cat-${esc(p.category)}">${esc(p.category)}</span>${p.active ? "" : ' <span class="chip">Suspended</span>'}${expired(p) ? ' <span class="chip sev-High">Pass expired</span>' : ""}</div></div>
        <div class="person-meta">
          <span>Project</span><span>${esc(p.project || "—")}</span>
          <span>Phone</span><span>${esc(p.phone || "—")}</span>
          <span>Valid until</span><span>${esc(p.valid_until || "—")}</span>
          <span>Samples</span><span>${p.samples}</span>
          <span>Last seen</span><span>${dateTime(p.last_seen)}</span>
          ${p.notes ? `<span>Notes</span><span>${esc(p.notes)}</span>` : ""}
        </div>
        <div class="person-actions">
          <select data-cat="${p.id}" aria-label="Change category">${[...$("#people-cat").options].slice(1).map((o) => `<option ${o.value === p.category ? "selected" : ""}>${esc(o.value)}</option>`).join("")}</select>
          <button class="btn btn-sm btn-ghost" data-toggle="${p.id}" data-active="${p.active}">${p.active ? "Suspend" : "Reactivate"}</button>
          <label class="btn btn-sm btn-ghost">Add photo<input type="file" accept="image/*" multiple hidden data-addphoto="${p.id}"></label>
          <button class="btn btn-sm btn-ghost btn-danger" data-del="${p.id}">Delete</button>
        </div>
      </div>`).join("") : `<div class="muted">No people match.</div>`;
  }
  $("#people-search").addEventListener("input", renderPeople);
  $("#people-cat").addEventListener("change", renderPeople);
  $("#people-grid").addEventListener("change", async (e) => {
    const t = e.target;
    try {
      if (t.dataset.cat) {
        await api(`/api/persons/${t.dataset.cat}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ category: t.value }) });
        toast(`Category changed to ${t.value}`);
      } else if (t.dataset.addphoto) {
        const fd = new FormData();
        [...t.files].forEach((f) => fd.append("image", f));
        const r = await api(`/api/persons/${t.dataset.addphoto}/samples`, { method: "POST", body: fd });
        toast(r.status, r.ok ? "ok" : "warn");
      }
    } catch (err) { toast(err.message, "error"); }
    loaders.people();
  });
  $("#people-grid").addEventListener("click", async (e) => {
    const del = e.target.closest("[data-del]"), tog = e.target.closest("[data-toggle]");
    try {
      if (del) {
        if (!confirm("Delete this person and all their face samples? Past events are kept.")) return;
        await api(`/api/persons/${del.dataset.del}`, { method: "DELETE" }); toast("Person deleted");
      } else if (tog) {
        const active = tog.dataset.active === "1" ? 0 : 1;
        await api(`/api/persons/${tog.dataset.toggle}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active }) });
        toast(active ? "Access reactivated" : "Access suspended — person will now be treated as Unknown");
      } else return;
    } catch (err) { toast(err.message, "error"); }
    loaders.people();
  });

  /* ---------- events ---------- */
  async function loadEvents() {
    const p = new URLSearchParams({ date: $("#ev-date").value, severity: $("#ev-sev").value });
    const rows = await api(`/api/events?${p}`);
    $("#ev-body").innerHTML = rows.length ? rows.map((r) => `
      <tr>
        <td>${dateTime(r.ts)}</td>
        <td>${r.snapshot ? `<img class="thumb" src="/snapshots/${esc(r.snapshot)}" data-zoom="/snapshots/${esc(r.snapshot)}" alt="Snapshot">` : "—"}</td>
        <td>${esc(r.name)}${r.confidence ? ` <span class="muted small">${Math.round(r.confidence * 100)}%</span>` : ""}</td>
        <td><span class="chip cat-${esc(r.category)}">${esc(r.category)}</span></td>
        <td><span class="sev sev-${esc(r.severity)}">${esc(r.severity)}</span></td>
        <td>${esc(r.reason)}</td>
        <td>${r.severity === "Low" ? '<span class="muted">Logged</span>' : r.acknowledged ? `<span class="muted" title="${esc(r.ack_note)}">Closed${r.ack_note ? ": " + esc(r.ack_note) : ""}</span>` : `<button class="btn btn-sm" data-ack="${r.id}">Acknowledge</button>`}</td>
      </tr>`).join("") : `<tr><td colspan="7" class="muted">No events for this filter.</td></tr>`;
  }
  loaders.events = () => loadEvents().catch((e) => toast(e.message, "error"));
  $("#ev-date").value = today();
  ["#ev-date", "#ev-sev"].forEach((s) => $(s).addEventListener("change", loaders.events));
  $("#ev-refresh").addEventListener("click", loaders.events);

  /* ---------- site register ---------- */
  loaders.register = async () => {
    const day = $("#reg-date").value || today();
    $("#reg-csv").href = `/api/register.csv?date=${day}`;
    const r = await api(`/api/register?date=${day}`);
    const max = Math.max(1, ...r.hourly.map((h) => h.known + h.unknown));
    const [ws, we] = [+document.body.dataset.ws || 0, +document.body.dataset.we || 24];
    $("#reg-chart").innerHTML = r.hourly.map((h) => `
      <div class="bar ${h.hour < ws || h.hour >= we ? "off" : ""}" title="${String(h.hour).padStart(2, "0")}:00 — ${h.known} identified, ${h.unknown} unknown">
        <div class="k" style="height:${(h.known / max) * 100}%"></div><div class="u" style="height:${(h.unknown / max) * 100}%"></div>
        <span class="lbl">${h.hour % 3 === 0 ? String(h.hour).padStart(2, "0") : ""}</span>
      </div>`).join("");
    const byCat = r.rows.reduce((a, x) => ((a[x.category] = (a[x.category] || 0) + 1), a), {});
    const avg = r.rows.length ? (r.rows.reduce((s, x) => s + x.hours_on_site, 0) / r.rows.length).toFixed(1) : "0.0";
    $("#reg-summary").innerHTML = `
      <div class="kpi"><span class="kpi-label">People on register</span><span class="kpi-value">${r.rows.length}</span></div>
      <div class="kpi"><span class="kpi-label">Avg. hours on site</span><span class="kpi-value">${avg}</span></div>
      <div class="kpi"><span class="kpi-label">By category</span><span class="small">${Object.entries(byCat).map(([k, v]) => `${esc(k)}: <b>${v}</b>`).join(" · ") || "—"}</span></div>`;
    $("#reg-body").innerHTML = r.rows.length ? r.rows.map((x, i) => `
      <tr><td>${i + 1}</td><td>${esc(x.name)}</td><td><span class="chip cat-${esc(x.category)}">${esc(x.category)}</span></td><td>${esc(x.project || "—")}</td>
      <td>${time(x.first_in)}</td><td>${time(x.last_seen)}</td><td>${x.hours_on_site.toFixed(2)}</td><td>${x.sightings}</td></tr>`).join("")
      : `<tr><td colspan="8" class="muted">No identified entries on this date.</td></tr>`;
  };
  $("#reg-date").value = today();
  $("#reg-date").addEventListener("change", () => loaders.register().catch((e) => toast(e.message, "error")));

  /* ---------- emergency muster ---------- */
  const accounted = new Set();
  function renderMuster() {
    const list = lastOnSite;
    const ok = list.filter((p) => accounted.has(p.person_id)).length;
    $("#mu-total").textContent = list.length;
    $("#mu-ok").textContent = ok;
    $("#mu-missing").textContent = list.length - ok;
    $("#mu-progress").style.width = list.length ? `${(ok / list.length) * 100}%` : "0";
    $("#muster-grid").innerHTML = list.length ? list.map((p) => `
      <div class="card muster-item ${accounted.has(p.person_id) ? "ok" : ""}" data-muster="${p.person_id}" role="checkbox" aria-checked="${accounted.has(p.person_id)}" tabindex="0">
        ${avatar(p)}<div><div>${esc(p.name)}</div><div class="muted small">${esc(p.category)} · seen ${time(p.last_seen)}</div></div>
        <span class="check">${accounted.has(p.person_id) ? "✓" : ""}</span>
      </div>`).join("") : `<div class="muted">Nobody has been recorded on site in the last ${window.SG.onSiteWindow} minutes.</div>`;
  }
  $("#muster-grid").addEventListener("click", (e) => {
    const m = e.target.closest("[data-muster]");
    if (!m) return;
    const id = +m.dataset.muster;
    accounted.has(id) ? accounted.delete(id) : accounted.add(id);
    renderMuster();
  });
  $("#muster-grid").addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.dataset.muster) { e.preventDefault(); e.target.click(); }
  });
  $("#muster-reset").addEventListener("click", () => { accounted.clear(); renderMuster(); });
  loaders.muster = renderMuster;

  /* ---------- boot ---------- */
  poll();
  setInterval(poll, 3000);
  const fromHash = () => { const v = location.hash.slice(1); if (v && $(`#view-${v}`)) show(v); };
  window.addEventListener("hashchange", fromHash);
  fromHash();
})();
