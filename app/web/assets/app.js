/* minutes — web app SPA (vanilla, no bundler). Talks to /api/*, session-cookie authed.
   Look: Freddie Spirit Dev (fs-*) + minutes app kit (m-*). Design ref: Claude Design ea8373d3. */
"use strict";

const root = document.getElementById("root");
const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const RTL = new Set(["fa", "ar", "he", "ur", "ps"]);
const isRtl = (l) => RTL.has((l || "").slice(0, 2));

const LOGO = (n = 26) =>
  `<svg width="${n}" height="${n}" viewBox="0 0 64 64" fill="none"><rect width="64" height="64" rx="16" fill="#1B1A17"/><rect x="16" y="20" width="32" height="6" rx="3" fill="#DD5A1A"/><rect x="16" y="29" width="22" height="6" rx="3" fill="#F4F2EC"/><rect x="16" y="38" width="28" height="6" rx="3" fill="#97948A"/></svg>`;
const COPY_ICON =
  `<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="3.5" y="3.5" width="7.5" height="7.5" rx="1.5"/><path d="M3 8H2.2A1.2 1.2 0 0 1 1 6.8V2.2A1.2 1.2 0 0 1 2.2 1h4.6A1.2 1.2 0 0 1 8 2.2V3"/></svg>`;

// ---------- API ----------
async function rawFetch(method, path, body) {
  const init = { method, credentials: "include", headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return fetch("/api" + path, init);
}
async function api(method, path, body, opts = {}) {
  let r = await rawFetch(method, path, body);
  if (r.status === 401 && !opts.noRefresh && !path.startsWith("/auth/")) {
    const rf = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
    if (rf.ok) r = await rawFetch(method, path, body);
  }
  if (opts.raw) return r;
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { /* ignore */ }
    const e = new Error(detail || "http " + r.status);
    e.status = r.status;
    e.detail = detail; // consumers surface e.detail (server reason) with a local fallback
    throw e;
  }
  return r.status === 204 ? null : r.json();
}

// ---------- tiny utilities ----------
const node = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
function toast(msg, kind = "info") {
  const t = node(`<div class="m-banner m-banner--${kind}" style="position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:50;box-shadow:var(--fs-shadow-md)">${esc(msg)}</div>`);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}
function relTime(iso) {
  const d = new Date(iso), s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return d.toLocaleDateString();
}
function clockOf(seg) {
  if (seg.started_at) return new Date(seg.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const ms = seg.start_ms != null ? seg.start_ms : (seg.start_ts != null ? seg.start_ts * 1000 : null);
  if (ms == null) return "··:··";
  const t = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}
const initials = (email) => (email || "?").slice(0, 2).toUpperCase();
const platformBadge = (p) => {
  const c = { meet: "#1a73e8", teams: "#5b5fc7", upload: "#DD5A1A" }[p] || "#97948A";
  return `<span class="m-meeting__icon" style="background:${c};display:grid;place-items:center;color:#fff;font-weight:600;font-size:13px">${esc((p || "?")[0].toUpperCase())}</span>`;
};

// ---------- state ----------
let me = null;
let meetings = [];
let selId = null;
let ws = null;
const segOrder = []; // segment ids in display order for the open meeting

// ============================================================ LOGIN
function renderLogin() {
  root.innerHTML = `
    <div style="height:100%;display:flex;align-items:center;justify-content:center;background:var(--fs-bg);padding:24px">
      <div style="width:360px">
        <div style="display:flex;flex-direction:column;align-items:center;gap:14px;margin-bottom:28px">
          ${LOGO(46)}<div style="font-weight:600;font-size:24px;letter-spacing:-0.03em">minutes</div>
        </div>
        <form id="login" class="fs-card" style="padding:24px">
          <div class="fs-field fs-field--block" style="margin-bottom:14px">
            <label class="fs-label">Email</label>
            <input class="fs-input" id="email" type="email" autocomplete="username" required />
          </div>
          <div class="fs-field fs-field--block" style="margin-bottom:20px">
            <label class="fs-label">Password</label>
            <input class="fs-input" id="password" type="password" autocomplete="current-password" required />
          </div>
          <div id="loginerr" style="color:var(--fs-danger);font-size:var(--fs-text-sm);margin-bottom:12px;display:none"></div>
          <button class="fs-btn fs-btn--primary fs-btn--lg" style="width:100%" type="submit">Sign in</button>
        </form>
        <div style="text-align:center;font-size:12px;color:var(--fs-ink-muted);margin-top:18px;line-height:1.6">No account? Ask your administrator.</div>
      </div>
    </div>`;
  root.querySelector("#login").onsubmit = async (e) => {
    e.preventDefault();
    const err = root.querySelector("#loginerr");
    err.style.display = "none";
    try {
      await api("POST", "/auth/login", {
        email: root.querySelector("#email").value.trim(),
        password: root.querySelector("#password").value,
      });
      await start();
    } catch (ex) {
      err.textContent = ex.detail || "Invalid email or password";
      err.style.display = "block";
    }
  };
}

// ============================================================ APP SHELL
function renderApp() {
  root.innerHTML = `
    <div class="m-topbar">
      <div class="m-brand">${LOGO(26)}<span>minutes</span></div>
      <div class="m-topbar__spacer"></div>
      <div class="m-account" id="acct"><span class="m-account__avatar">${esc(initials(me.email))}</span><span>${esc(me.email)}</span></div>
    </div>
    <div class="m-body">
      <div class="m-col m-col--nav">
        <div class="m-col__head"><span class="m-col__title">Meetings</span>
          <button class="fs-btn fs-btn--sm fs-btn--ghost" id="uploadbtn" title="Upload audio">↑ Upload</button>
        </div>
        <div class="m-col__scroll" id="meetinglist"></div>
        <input type="file" id="fileinput" accept="audio/*,video/*" style="display:none" />
      </div>
      <div class="m-col m-col--mid">
        <div class="m-col__head" id="midhead"><span class="m-col__title">Transcript</span></div>
        <div class="m-col__scroll" id="transcript"><div class="m-empty"><div class="m-empty__title">No meeting selected</div><div class="m-empty__sub">Pick a meeting on the left, or upload an audio file to transcribe.</div></div></div>
        <div id="interim" style="padding:8px 18px;color:var(--fs-ink-secondary);font-style:italic;min-height:1.4em;border-top:1px solid var(--fs-border);display:none"></div>
      </div>
      <div class="m-col">
        <div class="m-col__head" id="tlhead"><span class="m-col__title">Translation</span></div>
        <div class="m-col__scroll" id="translation"></div>
      </div>
    </div>`;
  root.querySelector("#acct").onclick = openAccountMenu;
  root.querySelector("#uploadbtn").onclick = () => root.querySelector("#fileinput").click();
  root.querySelector("#fileinput").onchange = onUpload;
  renderMeetingList();
  if (selId) {
    const m = meetings.find((x) => x.id === selId);
    if (m) selectMeeting(m);
  }
}

function openAccountMenu() {
  const existing = document.getElementById("acctmenu");
  if (existing) { existing.remove(); return; }
  const menu = node(`<div id="acctmenu" class="fs-card" style="position:absolute;top:50px;right:18px;z-index:40;padding:6px;min-width:180px;box-shadow:var(--fs-shadow-md)">
    <button class="fs-btn fs-btn--ghost fs-btn--sm" id="m-settings" style="width:100%;justify-content:flex-start">Settings</button>
    <button class="fs-btn fs-btn--ghost fs-btn--sm" id="m-logout" style="width:100%;justify-content:flex-start">Sign out</button>
  </div>`);
  document.body.appendChild(menu);
  menu.querySelector("#m-settings").onclick = () => { menu.remove(); renderSettings(); };
  menu.querySelector("#m-logout").onclick = async () => { menu.remove(); await api("POST", "/auth/logout").catch(() => {}); location.reload(); };
  setTimeout(() => document.addEventListener("click", function close(e) {
    if (!menu.contains(e.target) && e.target.id !== "acct") { menu.remove(); document.removeEventListener("click", close); }
  }), 0);
}

// ============================================================ MEETING LIST
function renderMeetingList() {
  const list = root.querySelector("#meetinglist");
  if (!list) return;
  if (!meetings.length) {
    list.innerHTML = `<div class="m-empty" style="padding:24px"><div class="m-empty__sub">No meetings yet. Join a call with the extension, or upload audio.</div></div>`;
    return;
  }
  list.innerHTML = "";
  for (const m of meetings) {
    const row = node(`<div class="m-meeting ${m.id === selId ? "is-selected" : ""}" data-id="${m.id}">
      ${platformBadge(m.platform)}
      <div class="m-meeting__main"><div class="m-meeting__name">${esc(m.title || m.external_meeting_id)}</div>
      <div class="m-meeting__sub"><span class="m-meeting__status" style="color:var(--fs-ink-muted)"><span class="m-dot m-dot--idle"></span>${esc(m.platform)}</span><span>· ${esc(relTime(m.created_at))}</span></div></div></div>`);
    row.onclick = () => selectMeeting(m);
    list.appendChild(row);
  }
}

// ============================================================ TRANSCRIPT VIEW
async function selectMeeting(m) {
  selId = m.id;
  renderMeetingList();
  if (ws) { ws.close(); ws = null; }
  segOrder.length = 0;
  const detail = await api("GET", "/meetings/" + m.id).catch(() => m);
  meetings = meetings.map((x) => (x.id === m.id ? detail : x));
  renderMidHead(detail);
  renderTlHead(detail);
  const tx = root.querySelector("#transcript");
  const tl = root.querySelector("#translation");
  tx.innerHTML = "";
  tl.innerHTML = "";
  let segs = [];
  try { segs = await api("GET", "/meetings/" + m.id + "/transcript"); } catch (e) { tx.innerHTML = `<div class="m-empty"><div class="m-empty__sub">${esc(e.detail || "Could not load transcript")}</div></div>`; return; }
  for (const s of segs) addSegment(s);
  if (!segs.length) tx.innerHTML = `<div class="m-empty"><div class="m-empty__sub">No transcript yet.</div></div>`;
  connectLive(m.id);
}

function renderMidHead(m) {
  const head = root.querySelector("#midhead");
  head.innerHTML = `
    <span class="m-col__title" id="mtitle" title="Click to rename" style="cursor:text">${esc(m.title || m.external_meeting_id)}</span>
    <div style="display:flex;gap:6px;align-items:center">
      <button class="fs-btn fs-btn--sm fs-btn--ghost" id="exportbtn">Export</button>
      <button class="fs-btn fs-btn--sm fs-btn--ghost" id="sharebtn">Share</button>
      <button class="fs-btn fs-btn--sm fs-btn--ghost fs-btn--icon" id="delbtn" title="Delete">🗑</button>
    </div>`;
  head.querySelector("#mtitle").onclick = () => beginRename(m);
  head.querySelector("#exportbtn").onclick = (e) => openExportMenu(e, m);
  head.querySelector("#sharebtn").onclick = () => toggleSharePanel(m);
  head.querySelector("#delbtn").onclick = () => deleteMeeting(m);
}

function beginRename(m) {
  const span = root.querySelector("#mtitle");
  const input = node(`<input class="fs-input fs-input--sm" style="max-width:280px" value="${esc(m.title || "")}" placeholder="${esc(m.external_meeting_id)}" />`);
  span.replaceWith(input);
  input.focus();
  const save = async () => {
    const title = input.value.trim();
    try { const upd = await api("PUT", "/meetings/" + m.id, { title }); Object.assign(m, upd); meetings = meetings.map((x) => x.id === m.id ? upd : x); } catch (e) { toast(e.detail || "Rename failed", "error"); }
    renderMidHead(meetings.find((x) => x.id === m.id) || m);
    renderMeetingList();
  };
  input.onblur = save;
  input.onkeydown = (e) => { if (e.key === "Enter") input.blur(); if (e.key === "Escape") renderMidHead(m); };
}

function openExportMenu(ev, m) {
  document.getElementById("exportmenu")?.remove();
  const menu = node(`<div id="exportmenu" class="fs-card" style="position:absolute;z-index:40;padding:6px;min-width:160px;box-shadow:var(--fs-shadow-md)">
    ${["txt", "md", "json"].map((f) => `<button class="fs-btn fs-btn--ghost fs-btn--sm exp" data-f="${f}" style="width:100%;justify-content:flex-start">Export .${f}</button>`).join("")}
  </div>`);
  document.body.appendChild(menu);
  const r = ev.target.getBoundingClientRect();
  menu.style.top = r.bottom + 6 + "px";
  menu.style.left = r.left + "px";
  menu.querySelectorAll(".exp").forEach((b) => b.onclick = () => {
    window.open("/api/meetings/" + m.id + "/export?format=" + b.dataset.f + "&include=both&timestamps=true", "_blank");
    menu.remove();
  });
  setTimeout(() => document.addEventListener("click", function c(e) { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", c); } }), 0);
}

function toggleSharePanel(m) {
  const head = root.querySelector("#midhead");
  document.getElementById("sharepanel")?.remove();
  const panel = node(`<div id="sharepanel" class="fs-card" style="position:absolute;top:104px;right:18px;z-index:40;padding:14px;width:340px;box-shadow:var(--fs-shadow-md)">
    <div style="font-weight:600;margin-bottom:10px">Public share link</div><div id="sharebody"></div></div>`);
  document.body.appendChild(panel);
  const draw = (mm) => {
    const body = panel.querySelector("#sharebody");
    if (mm.share && mm.share.enabled) {
      const url = location.origin + "/shared/" + mm.share.token;
      body.innerHTML = `<div class="m-sharechip" style="margin-bottom:10px"><span class="m-sharechip__url">${esc(url)}</span><button class="fs-btn fs-btn--sm fs-btn--ghost fs-btn--icon" id="cp">${COPY_ICON}</button></div>
        <div style="display:flex;gap:8px"><button class="fs-btn fs-btn--sm fs-btn--ghost" id="rot">Rotate</button><button class="fs-btn fs-btn--sm fs-btn--danger" id="off">Disable</button></div>
        <div style="font-size:var(--fs-text-xs);color:var(--fs-ink-muted);margin-top:10px">Anyone with the link can read this transcript. Rotating invalidates the old link.</div>`;
      body.querySelector("#cp").onclick = () => { navigator.clipboard?.writeText(url); toast("Link copied"); };
      body.querySelector("#rot").onclick = async () => { const u = await api("POST", "/meetings/" + mm.id + "/share", { rotate: true }); sync(u); draw(u); };
      body.querySelector("#off").onclick = async () => { const u = await api("DELETE", "/meetings/" + mm.id + "/share"); sync(u); draw(u); };
    } else {
      body.innerHTML = `<button class="fs-btn fs-btn--primary fs-btn--sm" id="on" style="width:100%">Enable share link</button>
        <div style="font-size:var(--fs-text-xs);color:var(--fs-ink-muted);margin-top:10px">Creates a read-only public link to this transcript.</div>`;
      body.querySelector("#on").onclick = async () => { const u = await api("POST", "/meetings/" + mm.id + "/share", {}); sync(u); draw(u); };
    }
  };
  const sync = (u) => { Object.assign(m, u); meetings = meetings.map((x) => x.id === m.id ? u : x); };
  draw(m);
  setTimeout(() => document.addEventListener("click", function c(e) { if (!panel.contains(e.target) && e.target.id !== "sharebtn") { panel.remove(); document.removeEventListener("click", c); } }), 0);
}

async function deleteMeeting(m) {
  if (!confirm(`Delete "${m.title || m.external_meeting_id}" and its audio? This cannot be undone.`)) return;
  try { await api("DELETE", "/meetings/" + m.id); } catch (e) { return toast(e.detail || "Delete failed", "error"); }
  meetings = meetings.filter((x) => x.id !== m.id);
  selId = null;
  renderApp();
  toast("Meeting deleted");
}

// ---- translation column header (config) ----
function renderTlHead(m) {
  const head = root.querySelector("#tlhead");
  const lang = m.translation?.output_language;
  head.innerHTML = `<span class="m-col__title">Translation${m.translation?.enabled && lang ? " → " + esc(lang) : ""}</span>
    <button class="fs-btn fs-btn--sm fs-btn--ghost fs-btn--icon" id="tlcfg" title="Translation settings">⚙</button>`;
  head.querySelector("#tlcfg").onclick = () => toggleTlConfig(m);
}
function toggleTlConfig(m) {
  document.getElementById("tlcfgpanel")?.remove();
  const t = m.translation || {};
  const panel = node(`<div id="tlcfgpanel" class="fs-card" style="position:absolute;top:104px;right:18px;z-index:40;padding:16px;width:300px;box-shadow:var(--fs-shadow-md)">
    <div style="font-weight:600;margin-bottom:12px">Translation</div>
    <label class="fs-switch ${t.enabled ? "is-on" : ""}" id="tlsw" style="margin-bottom:14px"><span class="fs-switch__track"><span class="fs-switch__thumb"></span></span> Enabled</label>
    <div class="fs-field fs-field--block" style="margin-bottom:12px"><label class="fs-label">Output language</label>
      <select class="fs-select" id="tllang">${["", "en", "de", "fa"].map((l) => `<option value="${l}" ${l === (t.output_language || "") ? "selected" : ""}>${l ? l : "—"}</option>`).join("")}</select></div>
    <button class="fs-btn fs-btn--primary fs-btn--sm" id="tlsave" style="width:100%">Save</button>
    <div style="font-size:var(--fs-text-xs);color:var(--fs-ink-muted);margin-top:10px">Uses your Anthropic key. Applies to the next session + on-demand lines.</div></div>`);
  document.body.appendChild(panel);
  const sw = panel.querySelector("#tlsw");
  sw.onclick = () => sw.classList.toggle("is-on");
  panel.querySelector("#tlsave").onclick = async () => {
    try {
      const upd = await api("PUT", "/meetings/" + m.id + "/translation", {
        enabled: sw.classList.contains("is-on"),
        output_language: panel.querySelector("#tllang").value || null,
      });
      Object.assign(m, upd); meetings = meetings.map((x) => x.id === m.id ? upd : x);
      renderTlHead(upd); panel.remove(); toast("Translation updated");
    } catch (e) { toast(e.detail || "Update failed", "error"); }
  };
  setTimeout(() => document.addEventListener("click", function c(e) { if (!panel.contains(e.target) && e.target.id !== "tlcfg") { panel.remove(); document.removeEventListener("click", c); } }), 0);
}

// ---- segment + translation rows ----
function addSegment(s) {
  const tx = root.querySelector("#transcript");
  if (tx.querySelector(".m-empty")) tx.innerHTML = "";
  const tl = root.querySelector("#translation");
  const id = s.id;
  let line = tx.querySelector(`[data-seg="${id}"]`);
  const rtl = isRtl(s.source_language);
  const lineHtml = `<div class="m-line" data-seg="${id}" ${rtl ? 'dir="rtl"' : ""}>
    <div class="m-line__time">${esc(clockOf(s))}</div>
    <div class="m-line__text">${s.source_language ? `<span class="m-line__lang">${esc(s.source_language)}</span>` : ""}${esc(s.text)}</div>
    <div class="m-line__copy" title="Copy">${COPY_ICON}</div></div>`;
  const newLine = node(lineHtml);
  newLine.querySelector(".m-line__copy").onclick = () => { navigator.clipboard?.writeText(s.text); toast("Copied"); };
  if (line) line.replaceWith(newLine); else { tx.appendChild(newLine); segOrder.push(id); }

  // translation row (right column), keyed by segment, kept in the same order
  let trow = tl.querySelector(`[data-seg="${id}"]`);
  const tr = (s.translations || []).find((t) => t.text || t.status);
  let inner;
  if (tr && tr.status === "ok" && tr.text) {
    const rtlT = isRtl(tr.target_language);
    inner = `<div class="m-line__time">${esc(clockOf(s))}</div><div class="m-line__text" ${rtlT ? 'dir="rtl"' : ""}>${esc(tr.text)}</div><div></div>`;
  } else if (tr && tr.status === "failed") {
    inner = `<div class="m-line__time"></div><div class="m-tl__failed">translation failed <button class="fs-tl__retry" data-retry="${id}">retry</button></div><div></div>`;
  } else {
    inner = `<div class="m-line__time"></div><div class="m-tl__ondemand"><span class="m-tl__link" data-retry="${id}">translate</span></div><div></div>`;
  }
  const newTrow = node(`<div class="m-line" data-seg="${id}">${inner}</div>`);
  const retry = newTrow.querySelector("[data-retry]");
  if (retry) retry.onclick = () => translateLine(id);
  if (trow) trow.replaceWith(newTrow); else tl.appendChild(newTrow);
}

async function translateLine(segId) {
  try {
    const res = await api("POST", "/meetings/" + selId + "/segments/" + segId + "/translate");
    const tl = root.querySelector("#translation");
    const row = tl.querySelector(`[data-seg="${segId}"]`);
    if (!row) return;
    if (res.status === "failed" || !res.text) {
      row.innerHTML = `<div class="m-line__time"></div><div class="m-tl__failed">translation failed <button class="fs-tl__retry">retry</button></div><div></div>`;
      row.querySelector("button").onclick = () => translateLine(segId);
    } else {
      const rtlT = isRtl(res.target_language);
      row.innerHTML = `<div class="m-line__time"></div><div class="m-line__text" ${rtlT ? 'dir="rtl"' : ""}>${esc(res.text)}</div><div></div>`;
    }
  } catch (e) { toast(e.detail || "Translation unavailable (set your Anthropic key in Settings)", "error"); }
}

// ---- live WS (session cookie rides along; no subprotocol) ----
function connectLive(meetingId, attempt = 0) {
  const interim = root.querySelector("#interim");
  const sock = new WebSocket(location.origin.replace(/^http/, "ws") + "/api/meetings/" + meetingId + "/live");
  ws = sock;
  sock.onclose = async (ev) => {
    if (interim) interim.style.display = "none";
    // Superseded (meeting switched) or a clean close: do nothing.
    if (ws !== sock || selId !== meetingId || ev.code === 1000 || attempt >= 1) return;
    // Likely the short-lived access cookie expired (close 1008) — refresh once, then reconnect.
    try { await fetch("/api/auth/refresh", { method: "POST", credentials: "include" }); } catch { /* ignore */ }
    if (ws === sock && selId === meetingId) connectLive(meetingId, attempt + 1);
  };
  sock.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (selId !== meetingId) return;
    if (ev.kind === "interim") { interim.style.display = "block"; interim.textContent = ev.text; }
    else if (ev.kind === "final") {
      interim.style.display = "none"; interim.textContent = "";
      addSegment({ id: ev.id, text: ev.text, source_language: ev.language, start_ms: ev.start_ms, translations: [] });
      const tx = root.querySelector("#transcript"); tx.scrollTop = tx.scrollHeight;
    } else if (ev.kind === "translation") {
      const tl = root.querySelector("#translation");
      const row = tl.querySelector(`[data-seg="${ev.segment_id}"]`);
      if (row) {
        if (ev.status === "failed") {
          row.innerHTML = `<div class="m-line__time"></div><div class="m-tl__failed">translation failed <button class="fs-tl__retry">retry</button></div><div></div>`;
          row.querySelector("button").onclick = () => translateLine(ev.segment_id);
        } else {
          const rtlT = isRtl(ev.target);
          row.innerHTML = `<div class="m-line__time"></div><div class="m-line__text" ${rtlT ? 'dir="rtl"' : ""}>${esc(ev.text)}</div><div></div>`;
        }
      }
    }
  };
}

// ============================================================ UPLOAD
async function onUpload(e) {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  toast("Uploading " + file.name + "…");
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/uploads", { method: "POST", credentials: "include", body: fd });
    if (!r.ok) { let d; try { d = (await r.json()).detail; } catch {} throw new Error(d || "upload failed"); }
    toast("Uploaded — transcription queued", "info");
    await loadMeetings();
    renderMeetingList();
  } catch (ex) { toast(ex.message || "Upload failed", "error"); }
}

// ============================================================ SETTINGS
function renderSettings() {
  const tabs = ["Account", "API keys", "Translation"];
  root.querySelector(".m-body")?.remove();
  let body = root.querySelector("#settingsbody");
  const shell = node(`<div class="m-body" id="settingswrap" style="display:block;overflow:auto">
    <div style="max-width:680px;margin:0 auto;padding:32px 24px">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
        <button class="fs-btn fs-btn--ghost fs-btn--sm" id="backbtn">← Back</button>
        <h2 style="margin:0;font-size:var(--fs-text-xl);font-weight:600">Settings</h2>
      </div>
      <div class="fs-tabs" id="stabs">${tabs.map((t, i) => `<button class="fs-tab ${i === 0 ? "is-active" : ""}" data-t="${i}">${t}</button>`).join("")}</div>
      <div id="settingsbody" style="margin-top:20px"></div>
    </div></div>`);
  root.appendChild(shell);
  shell.querySelector("#backbtn").onclick = () => renderApp();
  const tabsEl = shell.querySelectorAll(".fs-tab");
  tabsEl.forEach((b) => b.onclick = () => { tabsEl.forEach((x) => x.classList.remove("is-active")); b.classList.add("is-active"); drawTab(+b.dataset.t); });
  drawTab(0);
}
function drawTab(i) {
  const b = root.querySelector("#settingsbody");
  if (i === 0) {
    b.innerHTML = `<div class="fs-card" style="padding:8px 20px">
      <div class="m-srow"><div><div class="m-srow__label">Email</div><div class="m-srow__desc">${esc(me.email)}${me.is_admin ? " · admin" : ""}</div></div></div>
      <div class="m-srow"><div><div class="m-srow__label">Password</div><div class="m-srow__desc">Change your account password.</div></div>
        <div class="m-srow__control">
          <input class="fs-input" id="curpw" type="password" placeholder="Current password" />
          <input class="fs-input" id="newpw" type="password" placeholder="New password (≥12 chars, 3 classes)" />
          <button class="fs-btn fs-btn--primary fs-btn--sm" id="pwsave">Update password</button>
        </div></div></div>`;
    b.querySelector("#pwsave").onclick = async () => {
      try { await api("PUT", "/me/password", { current_password: b.querySelector("#curpw").value, new_password: b.querySelector("#newpw").value }); toast("Password updated"); b.querySelector("#curpw").value = ""; b.querySelector("#newpw").value = ""; }
      catch (e) { toast(e.detail || "Update failed", "error"); }
    };
  } else if (i === 1) {
    const ks = me.keys_set || {};
    b.innerHTML = `<div class="fs-card" style="padding:8px 20px">
      <div class="m-srow"><div><div class="m-srow__label">Soniox API key</div><div class="m-srow__desc">Speech-to-text. ${ks.soniox ? "✓ set" : "Not set — required for uploads."}</div></div>
        <div class="m-srow__control"><input class="fs-input" id="sk" type="password" placeholder="${ks.soniox ? "•••• replace" : "sk-…"}" /><button class="fs-btn fs-btn--primary fs-btn--sm" id="sksave">Save</button></div></div>
      <div class="m-srow"><div><div class="m-srow__label">Anthropic API key</div><div class="m-srow__desc">Translation. ${ks.anthropic ? "✓ set" : "Translation is off until set."}</div></div>
        <div class="m-srow__control"><input class="fs-input" id="ak" type="password" placeholder="${ks.anthropic ? "•••• replace" : "sk-ant-…"}" /><button class="fs-btn fs-btn--primary fs-btn--sm" id="aksave">Save</button></div></div></div>`;
    b.querySelector("#sksave").onclick = () => saveKey("soniox_key", b.querySelector("#sk"));
    b.querySelector("#aksave").onclick = () => saveKey("anthropic_key", b.querySelector("#ak"));
  } else {
    const d = me;
    b.innerHTML = `<div class="fs-card" style="padding:8px 20px">
      <div class="m-srow"><div><div class="m-srow__label">Translate new meetings by default</div><div class="m-srow__desc">Applied when a meeting starts; you can override per meeting.</div></div>
        <div class="m-srow__control"><label class="fs-switch ${d.default_translation_on ? "is-on" : ""}" id="dsw"><span class="fs-switch__track"><span class="fs-switch__thumb"></span></span></label></div></div>
      <div class="m-srow"><div><div class="m-srow__label">Default output language</div></div>
        <div class="m-srow__control"><select class="fs-select" id="dlang">${["", "en", "de", "fa"].map((l) => `<option value="${l}" ${l === (d.default_output_language || "") ? "selected" : ""}>${l || "—"}</option>`).join("")}</select></div></div>
      <div class="m-srow"><div></div><div class="m-srow__control"><button class="fs-btn fs-btn--primary fs-btn--sm" id="dsave">Save defaults</button></div></div></div>`;
    const sw = b.querySelector("#dsw");
    sw.onclick = () => sw.classList.toggle("is-on");
    b.querySelector("#dsave").onclick = async () => {
      try { const upd = await api("PUT", "/me/settings", { default_translation_on: sw.classList.contains("is-on"), default_output_language: b.querySelector("#dlang").value || null }); Object.assign(me, upd); toast("Defaults saved"); }
      catch (e) { toast(e.detail || "Save failed", "error"); }
    };
  }
}
async function saveKey(field, input) {
  if (!input.value.trim()) return;
  try { const upd = await api("PUT", "/me/keys", { [field]: input.value.trim() }); me.keys_set = upd.keys_set || me.keys_set; toast("Key saved"); drawTab(1); }
  catch (e) { toast(e.detail || "Save failed", "error"); }
}

// ============================================================ PUBLIC SHARED VIEWER
async function renderShared(token) {
  root.innerHTML = `<div class="m-topbar"><div class="m-brand">${LOGO(26)}<span>minutes</span></div></div>
    <div style="flex:1;overflow:auto"><div id="sharedbody" style="max-width:820px;margin:0 auto;padding:28px 24px"></div></div>`;
  const body = root.querySelector("#sharedbody");
  let meta;
  try { meta = await api("GET", "/shared/" + encodeURIComponent(token), undefined, { noRefresh: true }); }
  catch { body.innerHTML = `<div class="m-empty"><div class="m-empty__title">Link not found</div><div class="m-empty__sub">This share link is invalid or has been revoked.</div></div>`; return; }
  let segs = [];
  try { segs = await api("GET", "/shared/" + encodeURIComponent(token) + "/transcript", undefined, { noRefresh: true }); } catch {}
  const lang = meta.translation?.output_language;
  body.innerHTML = `<h1 style="font-size:var(--fs-text-2xl);font-weight:600;letter-spacing:-0.02em;margin:0 0 6px">${esc(meta.title || "Shared transcript")}</h1>
    <div style="font-family:var(--fs-font-mono);font-size:12px;color:var(--fs-ink-muted);margin-bottom:6px">${esc(meta.platform)} · ${esc(new Date(meta.created_at).toLocaleString())}</div>
    <div style="margin-bottom:18px"><a class="fs-btn fs-btn--ghost fs-btn--sm" href="/api/shared/${encodeURIComponent(token)}/export?format=txt&include=both" target="_blank">Export .txt</a></div>
    <div class="m-stream" id="sstream"></div>`;
  const stream = body.querySelector("#sstream");
  if (!segs.length) { stream.innerHTML = `<div class="m-empty__sub">No transcript content.</div>`; return; }
  for (const s of segs) {
    const rtl = isRtl(s.source_language);
    const tr = (s.translations || []).find((t) => t.target_language === lang && t.text);
    stream.appendChild(node(`<div class="m-line" ${rtl ? 'dir="rtl"' : ""}>
      <div class="m-line__time">${esc(clockOf(s))}</div>
      <div class="m-line__text">${s.source_language ? `<span class="m-line__lang">${esc(s.source_language)}</span>` : ""}${esc(s.text)}${tr ? `<div style="color:var(--fs-ink-secondary);margin-top:3px" ${isRtl(tr.target_language) ? 'dir="rtl"' : ""}>${esc(tr.text)}</div>` : ""}</div>
      <div></div></div>`));
  }
}

// ============================================================ BOOT
async function loadMeetings() {
  meetings = await api("GET", "/meetings").catch(() => []);
}
async function start() {
  try { me = await api("GET", "/me", undefined, { noRefresh: false }); }
  catch { me = null; }
  if (!me) return renderLogin();
  await loadMeetings();
  renderApp();
}
async function boot() {
  if (location.pathname.startsWith("/shared/")) {
    return renderShared(decodeURIComponent(location.pathname.slice("/shared/".length)));
  }
  await start();
}
boot();
