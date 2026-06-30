/* minutes — web app SPA (vanilla, no bundler). Talks to /api/*, session-cookie authed.
   Look: Freddie Spirit Dev (fs-*) + minutes app kit (m-*). Design ref: Claude Design ea8373d3. */
"use strict";

const root = document.getElementById("root");
const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const RTL = new Set(["fa", "ar", "he", "ur", "ps"]);
const isRtl = (l) => RTL.has((l || "").slice(0, 2));

// Output languages + translation models offered in the UI. Model ids are Anthropic API ids;
// "" means the server default (Haiku). Translating to the source language is a no-op.
const LANGS = [
  { code: "", label: "—" },
  { code: "en", label: "English" },
  { code: "de", label: "German" },
  { code: "fa", label: "Persian (فارسی)" },
];
const MODELS = [
  { id: "", label: "Default (Haiku — fast)" },
  { id: "claude-haiku-4-5-20251001", label: "Haiku 4.5 — fastest, cheapest" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6 — balanced" },
  { id: "claude-opus-4-8", label: "Opus 4.8 — best quality" },
];
const langOptions = (sel) => LANGS.map((l) => `<option value="${l.code}" ${l.code === (sel || "") ? "selected" : ""}>${esc(l.label)}</option>`).join("");
const modelOptions = (sel) => MODELS.map((m) => `<option value="${m.id}" ${m.id === (sel || "") ? "selected" : ""}>${esc(m.label)}</option>`).join("");

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
// Dual-source: lines are tagged data-source and shown/hidden by CSS on the selected source — no
// re-fetch / socket teardown on switch. seenSources tracks which sources this meeting has produced.
let selectedSource = "tab";
const seenSources = new Set();
const SRC_LABEL = { tab: "Online stream", mic: "Host mic", upload: "Upload" };

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
  if (isMobile()) return renderMobileApp();
  root.innerHTML = `
    <div class="m-topbar">
      <div class="m-brand">${LOGO(26)}<span>minutes</span></div>
      <div class="m-topbar__spacer"></div>
      <div class="m-account" id="acct"><span class="m-account__avatar">${esc(initials(me.email))}</span><span>${esc(me.email)}</span></div>
    </div>
    <div class="m-body">
      <div class="m-col m-col--nav">
        <div class="m-col__head"><span class="m-col__title">Transcriptions</span>
          <div style="display:flex;gap:6px;align-items:center">
            <button class="fs-btn fs-btn--sm fs-btn--ghost fs-btn--icon" id="reloadbtn" title="Reload transcriptions" aria-label="Reload transcriptions"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/></svg></button>
            <button class="fs-btn fs-btn--sm fs-btn--ghost" id="uploadbtn" title="Upload audio">↑ Upload</button>
          </div>
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
  root.querySelector("#reloadbtn").onclick = reloadMeetings;
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
    list.innerHTML = `<div class="m-empty" style="padding:24px"><div class="m-empty__sub">No transcriptions yet. Capture a tab with the extension, or upload audio.</div></div>`;
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

// Refresh the transcriptions list from the server (the reload button next to the list title).
async function reloadMeetings() {
  const btn = root.querySelector("#reloadbtn");
  if (btn) btn.classList.add("is-loading");
  try {
    await loadMeetings();
    renderMeetingList();
  } finally {
    if (btn) btn.classList.remove("is-loading");
  }
}

// ============================================================ TRANSCRIPT VIEW
async function selectMeeting(m) {
  selId = m.id;
  renderMeetingList();
  if (ws) { ws.close(); ws = null; }
  segOrder.length = 0;
  seenSources.clear();
  selectedSource = "tab";
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
  // Default to the tab ("Online stream") source if present, else the first one seen.
  if (!seenSources.has(selectedSource)) selectedSource = [...seenSources][0] || "tab";
  renderSourceBar();
  applySourceFilter();
  connectLive(m.id);
}

// Source selector (tab/mic) shown when a meeting has more than one source. Switching only flips a
// CSS data-attribute — every line stays in the DOM tagged data-source, so there's no re-fetch.
function renderSourceBar() {
  const bar = root.querySelector("#srcbar");
  if (!bar) return;
  const srcs = [...seenSources];
  if (srcs.length <= 1) { bar.innerHTML = ""; return; }
  bar.innerHTML = `<div class="fs-segmented">${srcs
    .map((s) => `<button class="fs-segmented__item ${s === selectedSource ? "is-selected" : ""}" data-src="${esc(s)}">${esc(SRC_LABEL[s] || s)}</button>`)
    .join("")}</div>`;
  bar.querySelectorAll("[data-src]").forEach((b) => (b.onclick = () => switchSource(b.dataset.src)));
}

function switchSource(src) {
  selectedSource = src;
  applySourceFilter();
  renderSourceBar();
}

function applySourceFilter() {
  const val = seenSources.size > 1 ? selectedSource : ""; // "" = show all (single source)
  for (const id of ["#transcript", "#translation"]) {
    const el = root.querySelector(id);
    if (el) el.setAttribute("data-src", val);
  }
}

function renderMidHead(m) {
  const head = root.querySelector("#midhead");
  head.innerHTML = `
    <span class="m-col__title" id="mtitle" title="Click to rename" style="cursor:text">${esc(m.title || m.external_meeting_id)}</span>
    <span id="srcbar"></span>
    <div style="display:flex;gap:6px;align-items:center;margin-left:auto">
      <button class="fs-btn fs-btn--sm fs-btn--ghost" id="exportbtn">Export</button>
      <button class="fs-btn fs-btn--sm fs-btn--ghost" id="sharebtn">Share</button>
      <button class="fs-btn fs-btn--sm fs-btn--ghost fs-btn--icon" id="delbtn" title="Delete">🗑</button>
    </div>`;
  head.querySelector("#mtitle").onclick = () => beginRename(m);
  head.querySelector("#exportbtn").onclick = () => openExportDialog(m);
  head.querySelector("#sharebtn").onclick = () => toggleSharePanel(m);
  head.querySelector("#delbtn").onclick = () => deleteMeeting(m);
  renderSourceBar();
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

function openExportDialog(m) {
  document.getElementById("exportdlg")?.remove();
  const hasTl = !!(m.translation && m.translation.output_language);
  const dlg = node(`<div id="exportdlg" style="position:fixed;inset:0;z-index:60;background:rgba(27,26,23,.45);display:flex;align-items:center;justify-content:center;padding:24px">
    <div class="fs-card" id="exportcard" style="width:380px;padding:22px;box-shadow:var(--fs-shadow-lg)">
      <div style="font-weight:600;font-size:var(--fs-text-lg);margin-bottom:4px">Export transcript</div>
      <div style="font-size:var(--fs-text-sm);color:var(--fs-ink-secondary);margin-bottom:16px">Choose what to include, then download.</div>
      <div class="fs-field fs-field--block" style="margin-bottom:16px"><label class="fs-label">Format</label>
        <select class="fs-select" id="exfmt">
          <option value="txt">Plain text (.txt)</option>
          <option value="md">Markdown (.md)</option>
          <option value="json">JSON (.json)</option>
        </select></div>
      ${seenSources.size > 1 ? `<div class="fs-field fs-field--block" style="margin-bottom:16px"><label class="fs-label">Audio source</label>
        <select class="fs-select" id="exsrc">
          <option value="both">Both sources</option>
          ${[...seenSources].map((s) => `<option value="${esc(s)}">${esc(SRC_LABEL[s] || s)}</option>`).join("")}
        </select></div>` : ""}
      <label class="fs-switch is-on" id="exts" style="display:flex;margin-bottom:12px"><span class="fs-switch__track"><span class="fs-switch__thumb"></span></span> Include timestamps</label>
      <label class="fs-switch ${hasTl ? "is-on" : "is-disabled"}" id="extr" style="display:flex;margin-bottom:6px"><span class="fs-switch__track"><span class="fs-switch__thumb"></span></span> Include translation</label>
      ${hasTl ? "" : `<div style="font-size:var(--fs-text-xs);color:var(--fs-ink-muted);margin-bottom:8px">No translation configured for this meeting.</div>`}
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:18px">
        <button class="fs-btn fs-btn--ghost fs-btn--sm" id="excancel">Cancel</button>
        <button class="fs-btn fs-btn--primary fs-btn--sm" id="exgo">Export</button>
      </div>
    </div></div>`);
  document.body.appendChild(dlg);
  const close = () => { dlg.remove(); document.removeEventListener("keydown", onEsc); };
  const onEsc = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onEsc);
  dlg.onclick = close; // backdrop
  dlg.querySelector("#exportcard").onclick = (e) => e.stopPropagation();
  const ts = dlg.querySelector("#exts");
  ts.onclick = () => ts.classList.toggle("is-on");
  const tr = dlg.querySelector("#extr");
  if (hasTl) tr.onclick = () => tr.classList.toggle("is-on");
  dlg.querySelector("#excancel").onclick = close;
  dlg.querySelector("#exgo").onclick = () => {
    const fmt = dlg.querySelector("#exfmt").value;
    const timestamps = ts.classList.contains("is-on");
    const include = (hasTl && tr.classList.contains("is-on")) ? "both" : "transcript";
    const srcSel = dlg.querySelector("#exsrc");
    const src = srcSel ? `&source=${encodeURIComponent(srcSel.value)}` : "";
    window.open(
      `/api/meetings/${m.id}/export?format=${fmt}&include=${include}&timestamps=${timestamps}${src}`,
      "_blank",
    );
    close();
  };
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
      <select class="fs-select" id="tllang">${langOptions(t.output_language)}</select></div>
    <div class="fs-field fs-field--block" style="margin-bottom:12px"><label class="fs-label">Model</label>
      <select class="fs-select" id="tlmodel">${modelOptions(t.model)}</select></div>
    <button class="fs-btn fs-btn--primary fs-btn--sm" id="tlsave" style="width:100%">Save</button>
    <div style="font-size:var(--fs-text-xs);color:var(--fs-ink-muted);margin-top:10px">Uses your Anthropic key. Translating to the spoken language is skipped — pick a different output language. Applies to the next session + on-demand lines.</div></div>`);
  document.body.appendChild(panel);
  const sw = panel.querySelector("#tlsw");
  sw.onclick = () => sw.classList.toggle("is-on");
  panel.querySelector("#tlsave").onclick = async () => {
    try {
      const upd = await api("PUT", "/meetings/" + m.id + "/translation", {
        enabled: sw.classList.contains("is-on"),
        output_language: panel.querySelector("#tllang").value || null,
        model: panel.querySelector("#tlmodel").value || null,
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
  const src = s.source || "tab";
  let line = tx.querySelector(`[data-seg="${id}"]`);
  const rtl = isRtl(s.source_language);
  const lineHtml = `<div class="m-line" data-seg="${id}" data-source="${esc(src)}" ${rtl ? 'dir="rtl"' : ""}>
    <div class="m-line__time">${esc(clockOf(s))}</div>
    <div class="m-line__text">${s.source_language ? `<span class="m-line__lang">${esc(s.source_language)}</span>` : ""}${esc(s.text)}</div>
    <div class="m-line__copy" title="Copy">${COPY_ICON}</div></div>`;
  const newLine = node(lineHtml);
  newLine.querySelector(".m-line__copy").onclick = () => { navigator.clipboard?.writeText(s.text); toast("Copied"); };
  if (line) line.replaceWith(newLine); else { tx.appendChild(newLine); segOrder.push(id); }

  // translation row (right column), keyed by segment, kept in the same order
  let trow = tl.querySelector(`[data-seg="${id}"]`);
  const tr = (s.translations || []).find((t) => t.text || t.status);
  const outLang = meetings.find((x) => x.id === selId)?.translation?.output_language;
  const sameLang = outLang && s.source_language && outLang === s.source_language;
  let inner;
  if (tr && tr.status === "ok" && tr.text) {
    const rtlT = isRtl(tr.target_language);
    inner = `<div class="m-line__time">${esc(clockOf(s))}</div><div class="m-line__text" ${rtlT ? 'dir="rtl"' : ""}>${esc(tr.text)}</div><div></div>`;
  } else if (sameLang) {
    // Output language equals the spoken language — nothing to translate (not a failure).
    inner = `<div class="m-line__time"></div><div class="m-tl__nokey" style="opacity:.6">— already ${esc(outLang)}</div><div></div>`;
  } else if (tr && tr.status === "failed") {
    inner = `<div class="m-line__time"></div><div class="m-tl__failed">translation failed <button class="fs-tl__retry" data-retry="${id}">retry</button></div><div></div>`;
  } else {
    inner = `<div class="m-line__time"></div><div class="m-tl__ondemand"><span class="m-tl__link" data-retry="${id}">translate</span></div><div></div>`;
  }
  const newTrow = node(`<div class="m-line" data-seg="${id}" data-source="${esc(src)}">${inner}</div>`);
  const retry = newTrow.querySelector("[data-retry]");
  if (retry) retry.onclick = () => translateLine(id);
  if (trow) trow.replaceWith(newTrow); else tl.appendChild(newTrow);

  // First time we see this source -> reveal the source selector + (re)apply the filter.
  if (!seenSources.has(src)) { seenSources.add(src); renderSourceBar(); applySourceFilter(); }
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
    if (ev.kind === "interim") {
      // Only the selected source's interim occupies the shared interim line.
      if (!ev.source || ev.source === selectedSource) { interim.style.display = "block"; interim.textContent = ev.text; }
    } else if (ev.kind === "final") {
      if (!ev.source || ev.source === selectedSource) { interim.style.display = "none"; interim.textContent = ""; }
      addSegment({ id: ev.id, text: ev.text, source_language: ev.language, start_ms: ev.start_ms, source: ev.source, translations: [] });
      if ((ev.source || "tab") === selectedSource) { const tx = root.querySelector("#transcript"); tx.scrollTop = tx.scrollHeight; }
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
  const tabs = ["Account", "API keys", "Translation", "Danger zone"];
  root.querySelector(".m-body")?.remove();
  const shell = node(`<div class="m-body" id="settingswrap" style="display:block;overflow:auto">
    <div style="max-width:720px;margin:0 auto;padding:32px 24px">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:22px">
        <button class="fs-btn fs-btn--ghost fs-btn--sm" id="backbtn">← Back</button>
        <h1 style="margin:0;font-size:var(--fs-text-2xl);font-weight:600;letter-spacing:-0.02em">Settings</h1>
      </div>
      <div class="fs-tabs">
        <div class="fs-tabs__list" id="stabs">${tabs.map((t, i) => `<button class="fs-tab ${i === 0 ? "is-selected" : ""}" data-t="${i}">${esc(t)}</button>`).join("")}</div>
        <div id="settingsbody" style="margin-top:22px"></div>
      </div>
    </div></div>`);
  root.appendChild(shell);
  shell.querySelector("#backbtn").onclick = () => renderApp();
  const tabsEl = shell.querySelectorAll(".fs-tab");
  tabsEl.forEach((bn) => bn.onclick = () => { tabsEl.forEach((x) => x.classList.remove("is-selected")); bn.classList.add("is-selected"); drawTab(+bn.dataset.t); });
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
    const region = me.soniox_region || "us";
    b.innerHTML = `<div class="fs-card" style="padding:8px 20px">
      <div class="m-srow"><div><div class="m-srow__label">Soniox API key</div><div class="m-srow__desc">Speech-to-text. ${ks.soniox ? "✓ set" : "Not set — required for uploads."}</div></div>
        <div class="m-srow__control"><input class="fs-input" id="sk" type="password" placeholder="${ks.soniox ? "•••• replace" : "sk-…"}" /><button class="fs-btn fs-btn--primary fs-btn--sm" id="sksave">Save</button></div></div>
      <div class="m-srow"><div><div class="m-srow__label">Soniox region</div><div class="m-srow__desc">Where your audio is processed. Must match your key's project region — <b>EU</b> keeps audio in the EU.</div></div>
        <div class="m-srow__control"><select class="fs-select" id="sregion">
          <option value="us"${region === "us" ? " selected" : ""}>United States · api.soniox.com</option>
          <option value="eu"${region === "eu" ? " selected" : ""}>European Union · api.eu.soniox.com</option>
        </select></div></div>
      <div class="m-srow"><div><div class="m-srow__label">Anthropic API key</div><div class="m-srow__desc">Translation. ${ks.anthropic ? "✓ set" : "Translation is off until set."}</div></div>
        <div class="m-srow__control"><input class="fs-input" id="ak" type="password" placeholder="${ks.anthropic ? "•••• replace" : "sk-ant-…"}" /><button class="fs-btn fs-btn--primary fs-btn--sm" id="aksave">Save</button></div></div></div>`;
    b.querySelector("#sksave").onclick = () => saveKey("soniox_key", b.querySelector("#sk"));
    b.querySelector("#aksave").onclick = () => saveKey("anthropic_key", b.querySelector("#ak"));
    const sr = b.querySelector("#sregion");
    sr.onchange = async () => {
      try { const upd = await api("PUT", "/me/keys", { soniox_region: sr.value }); me.soniox_region = upd.soniox_region || sr.value; toast("Region saved — " + (sr.value === "eu" ? "EU" : "US")); }
      catch (e) { toast(e.detail || "Save failed", "error"); sr.value = region; }
    };
  } else if (i === 2) {
    const d = me;
    b.innerHTML = `<div class="fs-card" style="padding:8px 20px">
      <div class="m-srow"><div><div class="m-srow__label">Translate new meetings by default</div><div class="m-srow__desc">Applied when a meeting starts; you can override per meeting.</div></div>
        <div class="m-srow__control"><label class="fs-switch ${d.default_translation_on ? "is-on" : ""}" id="dsw"><span class="fs-switch__track"><span class="fs-switch__thumb"></span></span></label></div></div>
      <div class="m-srow"><div><div class="m-srow__label">Default output language</div><div class="m-srow__desc">Translating to the spoken language is skipped — pick a different one.</div></div>
        <div class="m-srow__control"><select class="fs-select" id="dlang">${langOptions(d.default_output_language)}</select></div></div>
      <div class="m-srow"><div><div class="m-srow__label">Default model</div><div class="m-srow__desc">Anthropic model used for translation.</div></div>
        <div class="m-srow__control"><select class="fs-select" id="dmodel">${modelOptions(d.default_model)}</select></div></div>
      <div class="m-srow"><div></div><div class="m-srow__control"><button class="fs-btn fs-btn--primary fs-btn--sm" id="dsave">Save defaults</button></div></div></div>`;
    const sw = b.querySelector("#dsw");
    sw.onclick = () => sw.classList.toggle("is-on");
    b.querySelector("#dsave").onclick = async () => {
      try { const upd = await api("PUT", "/me/settings", { default_translation_on: sw.classList.contains("is-on"), default_output_language: b.querySelector("#dlang").value || null, default_model: b.querySelector("#dmodel").value || null }); Object.assign(me, upd); toast("Defaults saved"); }
      catch (e) { toast(e.detail || "Save failed", "error"); }
    };
  } else {
    // Danger zone
    b.innerHTML = `<div class="fs-card" style="padding:8px 20px;border-color:var(--fs-danger,#c0392b)">
      <div class="m-srow"><div><div class="m-srow__label" style="color:var(--fs-danger,#c0392b)">Delete account</div><div class="m-srow__desc">Permanently delete your account. Meetings you own become unowned (admins can still see them); this can't be undone.</div></div>
        <div class="m-srow__control"><button class="fs-btn fs-btn--danger fs-btn--sm" id="delacct">Delete my account</button></div></div></div>`;
    b.querySelector("#delacct").onclick = async () => {
      const typed = prompt(`This permanently deletes your account (${me.email}). Type your email to confirm:`);
      if (typed == null) return;
      if (typed.trim().toLowerCase() !== me.email.toLowerCase()) { toast("Email didn't match — not deleted.", "error"); return; }
      try { await api("DELETE", "/me"); toast("Account deleted."); setTimeout(() => location.reload(), 800); }
      catch (e) { toast(e.detail || "Delete failed", "error"); }
    };
  }
}
async function saveKey(field, input) {
  if (!input.value.trim()) return;
  try { const upd = await api("PUT", "/me/keys", { [field]: input.value.trim() }); me.keys_set = upd.keys_set || me.keys_set; if (upd.soniox_region) me.soniox_region = upd.soniox_region; toast("Key saved"); drawTab(1); }
  catch (e) { toast(e.detail || "Save failed", "error"); }
}

// ============================================================ PUBLIC SHARED VIEWER
// ============================================================ MOBILE WEB APP
// At <=760px the 3-column workspace collapses into a navigable single column
// (list -> meeting detail with a Transcript/Translation toggle -> settings),
// with bottom sheets for the account menu, meeting actions, and translation.
// Reuses the same data/API layer (api, loadMeetings, drawTab, openExportDialog,
// deleteMeeting); only the render + the live socket are mobile-specific.
const isMobile = () => window.matchMedia("(max-width: 760px)").matches;
let mView = "list";      // list | detail | settings
let mSeg = "tx";         // tx | tl  (which stream the detail shows)
let mSource = "tab";     // tab | mic  (which source the detail shows, when >1)
let mSegs = [];          // segments of the open meeting (each tagged .source)
let mSettingsTab = 0;
const mSeenSources = () => [...new Set(mSegs.map((s) => s.source || "tab"))];
const M_BACK = `<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M11 4 6 9l5 5"/></svg>`;
const M_UP = `<svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M10 13V4M6.5 7.5 10 4l3.5 3.5M4 14.5v1A1.5 1.5 0 0 0 5.5 17h9a1.5 1.5 0 0 0 1.5-1.5v-1"/></svg>`;
const M_MORE = `<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><circle cx="10" cy="4" r="1.6"/><circle cx="10" cy="10" r="1.6"/><circle cx="10" cy="16" r="1.6"/></svg>`;
const M_GEAR = `<svg width="19" height="19" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="10" cy="10" r="2.6"/><path d="M10 2.5v2M10 15.5v2M17.5 10h-2M4.5 10h-2M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4M15.3 15.3l-1.4-1.4M6.1 6.1 4.7 4.7"/></svg>`;
const M_CHEV = `<svg class="mrow__chev" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m6 4 4 4-4 4"/></svg>`;

const mtClass = (p) => (["upload", "teams", "web", "meet"].includes(p) ? p : "ink");
const mtLetter = (p) => (p || "?")[0].toUpperCase();

function renderMobileApp() {
  if (mView === "settings") return renderMobileSettings();
  if (mView === "detail" && meetings.find((x) => x.id === selId)) return renderMobileDetail();
  mView = "list";
  return renderMobileList();
}

function mTop() {
  return `<div class="mtop">
    <span class="mtop__brand">${LOGO(26)}minutes</span>
    <span class="mtop__sp"></span>
    <button class="icon-btn" id="mupload" aria-label="Upload audio">${M_UP}</button>
    <span class="avatar" id="mavatar">${esc(initials(me.email))}</span>
  </div>`;
}

function renderMobileList() {
  root.innerHTML = `<div class="mob">
    ${mTop()}
    <div class="mob__scroll">
      <div class="mlhead"><h1>Transcriptions</h1>
        <div class="mlhead__actions">
          <button class="icon-btn" id="mreload" aria-label="Reload"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/></svg></button>
        </div>
      </div>
      <div class="mlist" id="mlist"></div>
    </div>
  </div>
  <input type="file" id="mfileinput" accept="audio/*,video/*" style="display:none" />`;
  const fi = root.querySelector("#mfileinput");
  fi.onchange = async (e) => { await onUpload(e); if (isMobile()) renderMobileApp(); };
  root.querySelector("#mupload").onclick = () => fi.click();
  root.querySelector("#mavatar").onclick = mAccountSheet;
  root.querySelector("#mreload").onclick = async (e) => {
    const b = e.currentTarget; b.classList.add("is-loading");
    try { await loadMeetings(); mRenderList(); } finally { b.classList.remove("is-loading"); }
  };
  mRenderList();
}

function mRenderList() {
  const list = root.querySelector("#mlist");
  if (!list) return;
  if (!meetings.length) {
    list.innerHTML = `<div class="m-empty" style="padding:48px 24px;text-align:center">
      ${LOGO(48)}
      <div class="m-empty__title" style="margin-top:14px">No transcriptions yet</div>
      <div class="m-empty__sub">Upload an audio file, or capture any tab that plays audio with the minutes browser extension.</div>
      <button class="fs-btn fs-btn--primary fs-btn--lg" id="memptyup" style="margin-top:14px">${M_UP} Upload audio</button>
    </div>`;
    list.querySelector("#memptyup").onclick = () => root.querySelector("#mfileinput").click();
    return;
  }
  list.innerHTML = "";
  for (const m of meetings) {
    const row = node(`<div class="mrow ${m.id === selId ? "is-selected" : ""}">
      <span class="mt-ic mt-ic--${mtClass(m.platform)}">${esc(mtLetter(m.platform))}</span>
      <div class="mrow__main"><div class="mrow__name">${esc(m.title || m.external_meeting_id)}</div>
        <div class="mrow__sub"><span class="m-dot m-dot--idle"></span>${esc(m.platform)} · ${esc(relTime(m.created_at))}</div></div>
      ${M_CHEV}</div>`);
    row.onclick = () => mOpenMeeting(m);
    list.appendChild(row);
  }
}

async function mOpenMeeting(m) {
  selId = m.id; mView = "detail"; mSeg = "tx"; mSource = "tab"; mSegs = [];
  if (ws) { ws.close(); ws = null; }
  const detail = await api("GET", "/meetings/" + m.id).catch(() => m);
  meetings = meetings.map((x) => (x.id === m.id ? detail : x));
  renderMobileDetail();
  try { mSegs = await api("GET", "/meetings/" + m.id + "/transcript"); } catch { mSegs = []; }
  if (!mSeenSources().includes(mSource)) mSource = mSeenSources()[0] || "tab";
  renderMobileDetail(); // sources known now -> render the source chips
  mRenderStream();
  mConnectLive(m.id);
}

function renderMobileDetail() {
  const m = meetings.find((x) => x.id === selId);
  if (!m) { mView = "list"; return renderMobileList(); }
  const lang = m.translation?.output_language;
  root.innerHTML = `<div class="mob">
    <div class="mdhead">
      <div class="mdhead__top">
        <button class="mdback" id="mback">${M_BACK}</button>
        <div class="mdhead__title"><b>${esc(m.title || m.external_meeting_id)}</b><span><span class="m-dot m-dot--idle"></span>${esc(m.platform)}</span></div>
        <button class="icon-btn" id="mdmore" aria-label="${mSeg === "tl" ? "Translation settings" : "More"}">${mSeg === "tl" ? M_GEAR : M_MORE}</button>
      </div>
      ${mSeenSources().length > 1 ? `<div class="mchips" style="padding:0 12px 10px">
        ${mSeenSources().map((s) => `<button class="mchip ${s === mSource ? "is-selected" : ""}" data-msrc="${esc(s)}">${esc(SRC_LABEL[s] || s)}</button>`).join("")}
      </div>` : ""}
      <div class="mseg"><div class="mseg__track">
        <button class="mseg__item ${mSeg === "tx" ? "is-selected" : ""}" id="segtx">Transcript</button>
        <button class="mseg__item ${mSeg === "tl" ? "is-selected" : ""}" id="segtl">Translation ${lang ? `<span class="tgt">→ ${esc(lang)}</span>` : ""}</button>
      </div></div>
    </div>
    <div class="mob__scroll"><div class="mstream" id="mstream"></div></div>
    <div class="minterim" id="minterim" style="display:none"><span class="caret"></span><span id="minterimtxt">listening…</span></div>
  </div>`;
  root.querySelector("#mback").onclick = () => { if (ws) { ws.close(); ws = null; } mView = "list"; renderMobileApp(); };
  root.querySelector("#segtx").onclick = () => { mSeg = "tx"; renderMobileDetail(); mRenderStream(); };
  root.querySelector("#segtl").onclick = () => { mSeg = "tl"; renderMobileDetail(); mRenderStream(); };
  root.querySelectorAll("[data-msrc]").forEach((b) => (b.onclick = () => { mSource = b.dataset.msrc; renderMobileDetail(); mRenderStream(); }));
  root.querySelector("#mdmore").onclick = () => (mSeg === "tl" ? mTranslationSheet(m) : mActionsSheet(m));
}

function mLine(s) {
  const rtl = isRtl(s.source_language);
  return `<div class="mline" ${rtl ? 'dir="rtl"' : ""} data-seg="${s.id}">
    <div class="mline__meta"><span class="mline__lang">${esc((s.source_language || "·").toUpperCase())}</span><span class="mline__time">${esc(clockOf(s))}</span></div>
    <div class="mline__text">${esc(s.text)}</div></div>`;
}

function mTrLine(s) {
  const tr = (s.translations || []).find((t) => t.text || t.status);
  const out = meetings.find((x) => x.id === selId)?.translation?.output_language;
  const src = (s.source_language || "").toUpperCase();
  const same = out && s.source_language && out === s.source_language;
  let body, badge = src;
  if (tr && tr.status === "ok" && tr.text) {
    const rtlT = isRtl(tr.target_language);
    badge = `${src}→${(tr.target_language || "").toUpperCase()}`;
    body = `<div class="mline__text" ${rtlT ? 'dir="rtl"' : ""}>${esc(tr.text)}</div>`;
  } else if (same) {
    body = `<div class="mline__already">— already ${esc(out)}</div>`;
  } else if (tr && tr.status === "failed") {
    body = `<div class="mline__failed">translation failed <button data-retry="${s.id}">retry</button></div>`;
  } else {
    body = `<div class="mline__link" data-retry="${s.id}">translate</div>`;
  }
  return `<div class="mline mline--tr" data-seg="${s.id}">
    <div class="mline__meta"><span class="mline__lang">${esc(badge)}</span><span class="mline__time">${esc(clockOf(s))}</span></div>
    ${body}</div>`;
}

function mRenderStream() {
  const el = root.querySelector("#mstream");
  if (!el) return;
  const multi = mSeenSources().length > 1;
  const segs = multi ? mSegs.filter((s) => (s.source || "tab") === mSource) : mSegs;
  if (!segs.length) { el.innerHTML = `<div class="m-empty" style="padding:40px 0"><div class="m-empty__sub">No transcript yet.</div></div>`; return; }
  el.innerHTML = segs.map((s) => (mSeg === "tx" ? mLine(s) : mTrLine(s))).join("");
  el.querySelectorAll("[data-retry]").forEach((b) => (b.onclick = () => mTranslateLine(b.getAttribute("data-retry"))));
  const sc = root.querySelector(".mob__scroll");
  if (sc) sc.scrollTop = sc.scrollHeight;
}

async function mTranslateLine(segId) {
  try {
    const res = await api("POST", "/meetings/" + selId + "/segments/" + segId + "/translate");
    const seg = mSegs.find((s) => s.id === segId);
    if (seg) seg.translations = [{ text: res.text, status: res.status, target_language: res.target_language }];
    if (mSeg === "tl") mRenderStream();
  } catch (e) { toast(e.detail || "Translation unavailable (set your Anthropic key in Settings)", "error"); }
}

function mConnectLive(meetingId, attempt = 0) {
  const sock = new WebSocket(location.origin.replace(/^http/, "ws") + "/api/meetings/" + meetingId + "/live");
  ws = sock;
  sock.onclose = async (ev) => {
    if (ws !== sock || selId !== meetingId || ev.code === 1000 || attempt >= 1) return;
    try { await fetch("/api/auth/refresh", { method: "POST", credentials: "include" }); } catch { /* ignore */ }
    if (ws === sock && selId === meetingId) mConnectLive(meetingId, attempt + 1);
  };
  sock.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (selId !== meetingId) return;
    const interim = root.querySelector("#minterim");
    if (ev.kind === "interim") {
      if (interim && (!ev.source || ev.source === mSource)) {
        interim.style.display = "flex";
        const t = root.querySelector("#minterimtxt"); if (t) t.textContent = ev.text;
      }
    } else if (ev.kind === "final") {
      if (interim && (!ev.source || ev.source === mSource)) interim.style.display = "none";
      const before = mSeenSources().length;
      mSegs.push({ id: ev.id, text: ev.text, source_language: ev.language, start_ms: ev.start_ms, source: ev.source, translations: [] });
      if (mSeenSources().length !== before) renderMobileDetail(); // reveal a new source chip
      mRenderStream();
    } else if (ev.kind === "translation") {
      const seg = mSegs.find((s) => s.id === ev.segment_id);
      if (seg) seg.translations = [{ text: ev.text, status: ev.status, target_language: ev.target }];
      if (mSeg === "tl") mRenderStream();
    }
  };
}

// ---- bottom sheets ----
function mShowSheet(inner) {
  const mob = root.querySelector(".mob");
  if (!mob) return null;
  mCloseSheet();
  mob.classList.add("mob--sheet");
  const dim = node(`<div class="mob__dim"></div>`);
  const sheet = node(`<div class="msheet"><div class="msheet__grab"></div>${inner}<div class="msheet__pad"></div></div>`);
  dim.onclick = mCloseSheet;
  mob.appendChild(dim);
  mob.appendChild(sheet);
  return sheet;
}
function mCloseSheet() {
  const mob = root.querySelector(".mob");
  if (!mob) return;
  mob.classList.remove("mob--sheet");
  mob.querySelectorAll(".mob__dim, .msheet").forEach((n) => n.remove());
}

function mAccountSheet() {
  const s = mShowSheet(`
    <div class="mmenu__head"><span class="avatar">${esc(initials(me.email))}</span>
      <div><b>${esc(me.email.split("@")[0])}</b><span>${esc(me.email)}${me.is_admin ? " · admin" : ""}</span></div></div>
    <button class="mact" id="ms-set">${M_GEAR}Settings</button>
    <button class="mact" id="ms-out"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9 3h7a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H9"/><path d="M12 10H3m0 0 3-3m-3 3 3 3"/></svg>Log out</button>`);
  if (!s) return;
  s.querySelector("#ms-set").onclick = () => { mCloseSheet(); mSettingsTab = 0; mView = "settings"; renderMobileApp(); };
  s.querySelector("#ms-out").onclick = async () => { await api("POST", "/auth/logout").catch(() => {}); location.reload(); };
}

function mActionsSheet(m) {
  const s = mShowSheet(`
    <div class="msheet__title">${esc(m.title || m.external_meeting_id)}</div>
    <button class="mact" id="a-ren"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 14.5 13.5 4a2 2 0 0 1 2.8 2.8L5.8 17l-3.3.8z"/></svg>Rename</button>
    <button class="mact" id="a-exp"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M10 3v9m0 0 3.2-3.2M10 12 6.8 8.8M4 14v1.5A1.5 1.5 0 0 0 5.5 17h9a1.5 1.5 0 0 0 1.5-1.5V14"/></svg>Export transcript<span class="mact__sub">txt · md · json</span></button>
    <button class="mact" id="a-share"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="5" cy="10" r="2"/><circle cx="14.5" cy="5" r="2"/><circle cx="14.5" cy="15" r="2"/><path d="M6.7 9 12.8 6M6.7 11l6.1 3"/></svg><span id="a-share-lbl">${m.share?.enabled ? "Public link" : "Create public link"}</span></button>
    <div class="msheet__sec" id="a-sharebox" style="display:none"></div>
    <button class="mact mact--danger" id="a-del"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 6h12M8 6V4.5A1.5 1.5 0 0 1 9.5 3h1A1.5 1.5 0 0 1 12 4.5V6m2 0v9.5A1.5 1.5 0 0 1 12.5 17h-5A1.5 1.5 0 0 1 6 15.5V6"/></svg>Delete meeting</button>`);
  if (!s) return;
  const syncMeeting = (u) => { Object.assign(m, u); meetings = meetings.map((x) => (x.id === m.id ? Object.assign({}, x, u) : x)); };
  const drawShare = (mm) => {
    const box = s.querySelector("#a-sharebox");
    const lbl = s.querySelector("#a-share-lbl");
    if (mm.share && mm.share.enabled) {
      const url = location.origin + "/shared/" + mm.share.token;
      lbl.textContent = "Public link";
      box.style.display = "block";
      box.innerHTML = `<div class="msharebox"><span class="msharebox__url">${esc(url)}</span><button class="fs-btn fs-btn--sm" id="a-copy">Copy</button></div>
        <div style="display:flex;gap:8px;margin-top:8px"><button class="fs-btn fs-btn--sm fs-btn--ghost" id="a-rot">Rotate</button><button class="fs-btn fs-btn--sm fs-btn--danger" id="a-off">Disable</button></div>`;
      box.querySelector("#a-copy").onclick = () => { navigator.clipboard?.writeText(url); toast("Link copied"); };
      box.querySelector("#a-rot").onclick = async () => { const u = await api("POST", "/meetings/" + mm.id + "/share", { rotate: true }); syncMeeting(u); drawShare(u); };
      box.querySelector("#a-off").onclick = async () => { const u = await api("DELETE", "/meetings/" + mm.id + "/share"); syncMeeting(u); drawShare(u); };
    } else {
      box.style.display = "none";
      lbl.textContent = "Create public link";
    }
  };
  if (m.share?.enabled) drawShare(m);
  s.querySelector("#a-ren").onclick = async () => {
    const title = prompt("Rename transcription", m.title || "");
    if (title == null) return;
    try { const u = await api("PUT", "/meetings/" + m.id, { title: title.trim() }); syncMeeting(u); mCloseSheet(); renderMobileDetail(); mRenderStream(); }
    catch (e) { toast(e.detail || "Rename failed", "error"); }
  };
  s.querySelector("#a-exp").onclick = () => { mCloseSheet(); openExportDialog(m); };
  s.querySelector("#a-share").onclick = async () => {
    if (m.share?.enabled) { drawShare(m); return; }
    try { const u = await api("POST", "/meetings/" + m.id + "/share", {}); syncMeeting(u); drawShare(u); }
    catch (e) { toast(e.detail || "Could not create link", "error"); }
  };
  s.querySelector("#a-del").onclick = () => { mCloseSheet(); mView = "list"; deleteMeeting(m); };
}

function mTranslationSheet(m) {
  const t = m.translation || {};
  const langChips = LANGS.filter((l) => l.code).map((l) => `<button class="mchip ${t.output_language === l.code ? "is-selected" : ""}" data-lang="${l.code}">${esc(l.label)}</button>`).join("");
  const modelChips = MODELS.filter((mm) => mm.id).map((mm) => `<button class="mchip ${t.model === mm.id ? "is-selected" : ""}" data-model="${mm.id}">${esc(mm.label)}</button>`).join("");
  const s = mShowSheet(`
    <div class="msheet__title">Translation<span class="sub">per-meeting</span></div>
    <div class="msheet__sec"><div class="msheet__lab">Output language</div><div class="mchips" id="t-langs">${langChips}</div></div>
    <div class="msheet__sec"><div class="msheet__lab">Model</div><div class="mchips" id="t-models">${modelChips}</div></div>
    <div class="msheet__sec"><button class="fs-btn fs-btn--lg" id="t-redo" style="width:100%">Re-translate loaded lines</button>
      <div class="msheet__lab" style="margin:10px 0 0;text-transform:none;letter-spacing:0">New lines translate automatically as they arrive. Translating to the spoken language is skipped.</div></div>`);
  if (!s) return;
  const save = async (patch) => {
    try {
      const u = await api("PUT", "/meetings/" + m.id + "/translation", Object.assign({ enabled: true, output_language: t.output_language, model: t.model }, patch));
      Object.assign(m, u); meetings = meetings.map((x) => (x.id === m.id ? Object.assign({}, x, u) : x));
      Object.assign(t, u.translation || {});
      mTranslationSheet(m); // re-open with updated selection
      renderMobileDetail(); mRenderStream();
    } catch (e) { toast(e.detail || "Update failed", "error"); }
  };
  s.querySelectorAll("[data-lang]").forEach((b) => (b.onclick = () => save({ output_language: b.getAttribute("data-lang") })));
  s.querySelectorAll("[data-model]").forEach((b) => (b.onclick = () => save({ model: b.getAttribute("data-model") })));
  s.querySelector("#t-redo").onclick = async () => {
    mCloseSheet();
    toast("Re-translating…");
    for (const seg of mSegs.slice()) {
      if (seg.source_language && t.output_language === seg.source_language) continue;
      await mTranslateLine(seg.id);
    }
    toast("Re-translated");
  };
}

function renderMobileSettings() {
  const tabs = ["Account", "API keys", "Translation", "Danger zone"];
  root.innerHTML = `<div class="mob"><div class="mob__scroll"><div class="msettings">
    <div class="msettings__head"><button class="mdback" id="ms-back">${M_BACK}Back</button><h1>Settings</h1></div>
    <div class="mtabs" id="mtabs">${tabs.map((t, i) => `<button class="mtab ${i === mSettingsTab ? "is-selected" : ""}" data-t="${i}">${esc(t)}</button>`).join("")}</div>
    <div id="settingsbody" style="padding:8px 0"></div>
  </div></div></div>`;
  root.querySelector("#ms-back").onclick = () => { mView = "list"; renderMobileApp(); };
  const tabsEl = root.querySelectorAll(".mtab");
  tabsEl.forEach((b) => (b.onclick = () => { mSettingsTab = +b.dataset.t; tabsEl.forEach((x) => x.classList.remove("is-selected")); b.classList.add("is-selected"); drawTab(mSettingsTab); }));
  drawTab(mSettingsTab);
}

// Re-render when crossing the mobile/desktop breakpoint so the right layout shows.
let _wasMobile = isMobile();
window.addEventListener("resize", () => {
  const m = isMobile();
  if (m !== _wasMobile) { _wasMobile = m; if (me) renderApp(); }
});

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
  const lineHtml = (s) => {
    const rtl = isRtl(s.source_language);
    const tr = (s.translations || []).find((t) => t.target_language === lang && t.text);
    return `<div class="m-line" ${rtl ? 'dir="rtl"' : ""}>
      <div class="m-line__time">${esc(clockOf(s))}</div>
      <div class="m-line__text">${s.source_language ? `<span class="m-line__lang">${esc(s.source_language)}</span>` : ""}${esc(s.text)}${tr ? `<div style="color:var(--fs-ink-secondary);margin-top:3px" ${isRtl(tr.target_language) ? 'dir="rtl"' : ""}>${esc(tr.text)}</div>` : ""}</div>
      <div></div></div>`;
  };
  const sources = [...new Set(segs.map((s) => s.source || "tab"))];
  if (sources.length <= 1) {
    stream.innerHTML = segs.map(lineHtml).join(""); // single column — byte-identical to before
  } else {
    // Two labeled columns ("Online stream" + "Host mic"), each its own source's lines.
    body.style.maxWidth = "1100px";
    const order = ["tab", "mic", "upload"].filter((s) => sources.includes(s));
    stream.className = "shared-cols";
    stream.innerHTML = order
      .map((src) => `<div class="shared-col"><div class="shared-col__h">${esc(SRC_LABEL[src] || src)}</div>
        <div class="m-stream">${segs.filter((s) => (s.source || "tab") === src).map(lineHtml).join("")}</div></div>`)
      .join("");
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
