// Popup: sign in (device token) + capture. The server URL lives in the options page (gear); the
// popup only signs in against the saved server and exchanges the device token for a short,
// meeting-scoped capability token via POST /api/capture/token before streaming to /ingest.
const $ = (id) => document.getElementById(id);
const view = $("view");
const foot = $("foot");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

$("gear").onclick = () => chrome.runtime.openOptionsPage();

// Defensive: always build API/WS URLs against the ORIGIN (strip any stray path on the saved base,
// which would otherwise send /api/... to the static handler -> HTTP 405).
const originOf = (base) => { try { return new URL(base).origin; } catch { return (base || "").replace(/\/+$/, ""); } };
const hostOf = (base) => { try { return new URL(base).host; } catch { return base; } };
const wsIngest = (base) => originOf(base).replace(/^http/, "ws") + "/ingest";

// Microsoft Teams web hosts: classic, the new teams.cloud.microsoft, personal, and GCC.
const TEAMS_HOSTS = new Set([
  "teams.microsoft.com",
  "teams.cloud.microsoft",
  "teams.live.com",
  "teams.microsoft.us",
]);

// Detect what's capturable on this tab. We stream the tab's *audio output*, so any http(s) tab
// works — Meet/Teams get a real meeting id + icon; anything else is a generic "web" capture named
// after the tab title. chrome://, the Web Store, etc. (non-http) aren't capturable.
function extractMeeting(tab) {
  try {
    const u = new URL(tab?.url || "");
    if (u.hostname === "meet.google.com") {
      const m = u.pathname.match(/([a-z]{3}-[a-z]{4}-[a-z]{3})/);
      return { platform: "meet", externalMeetingId: m ? m[1] : "", name: "Google Meet", sub: m ? m[1] : "current call", iconUrl: "brand/icon-meet.svg" };
    }
    if (TEAMS_HOSTS.has(u.hostname)) {
      // Classic URL embeds the thread id; the new teams.cloud.microsoft app does not (id may be
      // empty here — start() then mints a per-capture id so recording still works).
      const m = decodeURIComponent(u.href).match(/(19:meeting_[^@/]+@thread\.v2)/);
      return { platform: "teams", externalMeetingId: m ? m[1] : "", name: "Microsoft Teams", sub: m ? m[1] : "current call", iconUrl: "brand/icon-teams.svg" };
    }
    if (u.protocol === "http:" || u.protocol === "https:") {
      const host = u.hostname.replace(/^www\./, "");
      return { platform: "web", externalMeetingId: "", name: (tab?.title || host).slice(0, 120), sub: host, iconUrl: "brand/icon-web.svg" };
    }
  } catch { /* not a URL / non-http */ }
  return { platform: "", externalMeetingId: "", name: "", sub: "", iconUrl: "" };
}

let activeTabId = null;
let meeting = { platform: "", externalMeetingId: "", name: "", sub: "", iconUrl: "" };
let lastCapturing = null; // last rendered capture state (so the live listener only re-renders on transitions)
let capTab = false; // which sources THIS start requested (for the recording chips during startup grace)
let capMic = false;

function showFoot(email) {
  foot.hidden = false;
  foot.innerHTML = `<span></span><a id="signout">Sign out</a>`;
  foot.querySelector("span").textContent = email || "";
  $("signout").onclick = signOut;
}
function hideFoot() { foot.hidden = true; foot.innerHTML = ""; }

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTabId = tab?.id ?? null;
  meeting = extractMeeting(tab);
  const st = await chrome.storage.local.get([
    "backendBase", "deviceToken", "deviceEmail", "minutesCapture",
    "micEnabled", "micGranted", "micDeviceId", "micAec",
  ]);
  if (!st.backendBase) return renderNoServer();
  if (!st.deviceToken) return renderLogin(st);
  st.__active = await isRecording();
  return renderCapture(st);
}

// ---------- no server configured ----------
function renderNoServer() {
  hideFoot();
  view.innerHTML = `
    <div class="empty">
      <div class="empty__icon">⚙</div>
      <div class="empty__title">Set your server to begin</div>
      <div class="empty__sub">The extension needs your minutes instance URL before you can sign in.</div>
      <button class="btn primary" id="open">Open settings →</button>
    </div>`;
  $("open").onclick = () => chrome.runtime.openOptionsPage();
}

// ---------- signed-out: login ----------
function renderLogin(st) {
  hideFoot();
  view.innerHTML = `
    <div class="signin-target">Sign in to <b>${esc(hostOf(st.backendBase))}</b></div>
    <div><label>Email</label><input id="email" type="email" autocomplete="username" /></div>
    <div><label>Password</label><input id="password" type="password" autocomplete="current-password" /></div>
    <div class="err" id="err" style="display:none"></div>
    <button class="btn primary" id="signin">Sign in</button>
    <div class="signin-note">Your password is never stored.</div>`;
  $("password").addEventListener("keydown", (e) => { if (e.key === "Enter") $("signin").click(); });
  $("signin").onclick = () => signIn(st.backendBase);
}

async function signIn(base) {
  const err = $("err");
  const host = hostOf(base);
  err.style.display = "none";
  const btn = $("signin");
  btn.disabled = true; btn.textContent = "Signing in…";
  const fail = (msg) => {
    err.textContent = msg; err.style.display = "block";
    btn.disabled = false; btn.textContent = "Sign in";
  };
  const target = originOf(base) + "/api/auth/login";
  let r;
  try {
    r = await fetch(target, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("email").value.trim(), password: $("password").value, client: "device" }),
    });
  } catch {
    // Network error / wrong or unreachable server URL — the most common cause of a "failed" login.
    return fail(`Couldn't reach ${host}. Check the Server URL in Settings (⚙).`);
  }
  if (!r.ok) {
    if (r.status === 401) return fail("Invalid email or password.");
    let d; try { d = (await r.json()).detail; } catch { /* ignore */ }
    // 404/405/3xx etc. mean we hit the host but not the minutes API — almost always a wrong URL.
    return fail(d || `Sign-in failed (HTTP ${r.status}) at ${target}. Check the Server URL in Settings — it should be just https://your-domain (no /app or other path).`);
  }
  let data;
  try { data = await r.json(); } catch { data = {}; }
  if (!data.device_token) {
    // Reached something that isn't the minutes API (wrong URL / a proxy / the marketing page).
    return fail(`Unexpected response from ${host}. Is the Server URL correct in Settings (⚙)?`);
  }
  await chrome.storage.local.set({ deviceToken: data.device_token, deviceEmail: data.email });
  init();
}

async function signOut() {
  await chrome.runtime.sendMessage({ type: "stop" }).catch(() => {});
  await chrome.storage.local.remove(["deviceToken", "deviceEmail"]);
  init();
}

// ---------- signed-in: capture (idle / recording / no-meeting) ----------
function renderCapture(st) {
  showFoot(st.deviceEmail);
  const capturing = !!st.__active;
  lastCapturing = capturing;

  if (!meeting.platform && !capturing) {
    // This tab has no capturable audio (a new/blank tab, chrome:// page, etc). If the mic is ready we
    // can still capture it on its own ("Host mic" only) — e.g. dictation or an in-person talk.
    const micOn = !!st.micEnabled;
    const micReady = micOn && st.micGranted;
    view.innerHTML = `
      <div class="empty">
        ${micReady
          ? `<div style="font-size:30px;line-height:1">🎙</div>`
          : `<div style="display:flex;gap:8px;opacity:.5"><img src="brand/icon-meet.svg" width="30" height="30"/><img src="brand/icon-teams.svg" width="30" height="30"/></div>`}
        <div class="empty__title">${micReady ? "Capture your microphone" : "Nothing to capture here"}</div>
        <div class="empty__sub">${micReady
          ? "No capturable audio on this tab — minutes will record just your microphone (Host mic)."
          : "Open a Meet/Teams call or any tab playing audio — or turn on your mic to record just your voice."}</div>
      </div>
      <label class="microw"><input type="checkbox" id="micchk" ${micOn ? "checked" : ""} /><span>Capture my microphone (Host mic)</span></label>
      ${micOn && !st.micGranted ? `<button class="btn sm" id="micsetup">Grant microphone access…</button>` : ""}
      ${micReady
        ? `<div class="micnote">🎙 Mic ready — <a id="mictest">test / change</a></div>
           <button class="btn lg primary" id="start">● Start recording (mic only)</button>
           <div class="cap-note">Records your microphone only — nothing from this tab.</div>`
        : `<button class="btn lg" id="start" disabled style="background:var(--surface2);color:var(--muted)">● Start recording</button>`}`;
    const chk = $("micchk");
    if (chk) chk.onchange = async () => {
      await chrome.storage.local.set({ micEnabled: chk.checked });
      if (chk.checked && !st.micGranted) openMicSetup();
      else init();
    };
    if ($("micsetup")) $("micsetup").onclick = openMicSetup;
    if ($("mictest")) $("mictest").onclick = openMicSetup;
    if (micReady && $("start")) $("start").onclick = () => start({ micOnly: true });
    return;
  }

  const ctx = `
    <div class="ctx">
      <img src="${esc(meeting.iconUrl)}" alt="" width="30" height="30" />
      <div style="min-width:0"><div class="ctx__name" title="${esc(meeting.name)}">${esc(meeting.name)}</div><div class="ctx__id">${esc(meeting.sub)}</div></div>
    </div>`;

  if (capturing) {
    const _s = st.minutesCapture?.sources || {};
    const fr = (src) => {
      const m = (_s[src]?.status || "").match(/(\d[\d,]*)\s*frames/);
      return m ? "· " + esc(m[1]) + " frames" : "";
    };
    // Which sources this capture has. The offscreen publishes the live truth to _s; capTab/capMic
    // cover the brief startup grace (and a popup reopened mid-capture relies on _s alone).
    const tabLive = !!_s.tab, micLive = !!_s.mic;
    const showTab = tabLive || capTab;
    const showMic = micLive || capMic;
    // Mic-only captures (a blank/non-capturable tab) get a mic context instead of the meeting card.
    const recCtx = (showTab || (!showMic && meeting.platform))
      ? `<div class="ctx"><img src="${esc(meeting.iconUrl || "brand/icon-web.svg")}" alt="" width="30" height="30" /><div style="min-width:0"><div class="ctx__name" title="${esc(meeting.name || "Recording")}">${esc(meeting.name || "Recording")}</div><div class="ctx__id">${esc(meeting.sub || "")}</div></div></div>`
      : `<div class="ctx"><div style="width:30px;height:30px;display:grid;place-items:center;font-size:20px">🎙</div><div style="min-width:0"><div class="ctx__name">Mic recording</div><div class="ctx__id">Host mic only</div></div></div>`;
    view.innerHTML = `
      ${recCtx}
      <div class="rec"><div class="rec__label"><span class="dot"></span>RECORDING</div></div>
      <div class="srcchips">
        ${showTab ? `<div class="srcchip ${tabLive ? "on" : "pending"}"><span class="d tab"></span>Online stream ${tabLive ? fr("tab") : "· starting…"}</div>` : ""}
        ${showMic ? `<div class="srcchip ${micLive ? "on" : "pending"}"><span class="d mic"></span>Host mic ${micLive ? fr("mic") : "· starting…"}</div>` : ""}
      </div>
      <div class="meterwrap"><div class="meter on"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div>
      <button class="btn lg stop" id="stop">■ Stop</button>
      <div class="cap-note">Streaming to ${esc(hostOf((st.backendBase) || ""))}</div>`;
    $("stop").onclick = stop;
  } else {
    const micOn = !!st.micEnabled;
    const micReady = micOn && st.micGranted;
    view.innerHTML = `
      ${ctx}
      <div class="meterwrap"><div class="meter idle"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div>
      <label class="microw"><input type="checkbox" id="micchk" ${micOn ? "checked" : ""} /><span>Also capture my microphone (Host mic)</span></label>
      ${micOn
        ? (micReady
            ? `<div class="micnote">🎙 Mic ready — <a id="mictest">test / change</a></div>`
            : `<button class="btn sm" id="micsetup">Grant microphone access…</button>`)
        : ""}
      <button class="btn lg primary" id="start">● Start recording</button>
      <div class="cap-note">Captures this tab's audio${micReady ? " + your mic" : ""} — you still hear everything.</div>`;
    $("start").onclick = start;
    const chk = $("micchk");
    if (chk) chk.onchange = async () => {
      await chrome.storage.local.set({ micEnabled: chk.checked });
      if (chk.checked && !st.micGranted) openMicSetup();
      else init();
    };
    if ($("mictest")) $("mictest").onclick = openMicSetup;
    if ($("micsetup")) $("micsetup").onclick = openMicSetup;
  }
}

function openMicSetup() {
  chrome.tabs.create({ url: chrome.runtime.getURL("permission.html") });
}

async function start(opts = {}) {
  const micOnly = !!opts.micOnly; // no capturable tab -> capture just the mic ("Host mic")
  const st = await chrome.storage.local.get([
    "backendBase", "deviceToken", "micEnabled", "micGranted", "micDeviceId", "micAec",
  ]);
  const note = view.querySelector(".cap-note");
  if (note) note.textContent = "Authorizing…";

  // Which sources to capture: the tab's audio (unless mic-only) and/or the mic.
  const tabCapture = !micOnly && !!meeting.platform;
  const micCapture = micOnly || (st.micEnabled && st.micGranted);

  // The meeting these sources attach to. A tab capture uses the tab's meeting; a mic-only capture
  // mints a fresh "web" meeting ("Mic recording") since there's no call/page behind it.
  let platform, externalMeetingId, title;
  if (tabCapture) {
    platform = meeting.platform;
    // The new Teams web app exposes no meeting id in the URL — mint a per-capture id so the meeting
    // is still created + authorized. (Classic Teams + Meet keep their real id.)
    externalMeetingId = meeting.externalMeetingId || ("web-" + crypto.randomUUID().slice(0, 8));
    title = (meeting.platform === "web" && meeting.name) ? meeting.name : null; // name it after the tab
  } else {
    platform = "web";
    externalMeetingId = "mic-" + crypto.randomUUID().slice(0, 8);
    title = "Mic recording";
  }

  try {
    const body = { platform, external_meeting_id: externalMeetingId };
    if (title) body.title = title;
    const r = await fetch(originOf(st.backendBase) + "/api/capture/token", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + st.deviceToken },
      body: JSON.stringify(body),
    });
    if (r.status === 401) {
      await chrome.storage.local.remove(["deviceToken", "deviceEmail"]);
      init();
      return;
    }
    if (!r.ok) {
      let d; try { d = (await r.json()).detail; } catch { /* ignore */ }
      throw new Error(d || "Could not authorize this meeting");
    }
    const { token } = await r.json();
    const base = { backendUrl: wsIngest(st.backendBase), token, platform, externalMeetingId };

    if (tabCapture) {
      const res = await chrome.runtime.sendMessage({
        type: "start", tabId: activeTabId, config: { ...base, callId: crypto.randomUUID() },
      });
      if (!res?.ok) throw new Error(res?.error || "capture failed to start");
    }
    // Mic as a source — DISTINCT call_id, source=mic, same meeting token. Alongside a tab it must
    // never affect the tab (best-effort); when it's the ONLY source, a dispatch failure is fatal.
    if (micCapture) {
      const micRes = await chrome.runtime.sendMessage({
        type: "add-mic", deviceId: st.micDeviceId || null, aec: !!st.micAec,
        config: { ...base, callId: crypto.randomUUID() },
      }).catch((e) => ({ ok: false, error: String(e) }));
      if (!tabCapture && !micRes?.ok) throw new Error(micRes?.error || "could not start the microphone");
    }

    capTab = tabCapture;
    capMic = micCapture;
    // Hold the recording UI through the startup window: the offscreen doc takes a beat to exist,
    // so the authoritative hasDocument() reads false for ~1s. The grace bridges that; afterwards
    // hasDocument() rules (and reverts to idle only if the capture genuinely failed).
    enterGrace("rec");
    const full = await chrome.storage.local.get(["backendBase", "deviceEmail", "minutesCapture"]);
    full.__active = true;
    renderCapture(full);
  } catch (e) {
    if (note) { note.textContent = e.message; note.classList.add("err"); }
  }
}

async function stop() {
  await chrome.runtime.sendMessage({ type: "stop" });
  capTab = false;
  capMic = false;
  // Hold idle through teardown (finalize + closeDocument take a beat before hasDocument() flips).
  enterGrace("idle");
  init();
}

// "Is capture live" comes from the background's offscreen doc, which exists for exactly the
// capture's lifetime — robust against the offscreen's storage-write lag (the old false-revert bug).
// Right after the user starts/stops, a short grace holds the optimistic state so createDocument /
// teardown latency can't flip the button; after the grace, hasDocument() is authoritative.
let graceUntil = 0;
let graceState = "idle";
function enterGrace(state) { graceState = state; graceUntil = Date.now() + 6000; }

async function captureActive() {
  try { const r = await chrome.runtime.sendMessage({ type: "capture-state" }); return !!r?.active; }
  catch { return false; }
}
async function isRecording() {
  if (Date.now() < graceUntil) return graceState === "rec";
  return captureActive();
}

// Converge the popup to the real state; refresh the frame counter in place when unchanged (no flicker).
async function reconcile() {
  const st = await chrome.storage.local.get(["minutesCapture", "deviceToken", "backendBase"]);
  if (!st.deviceToken || !st.backendBase) return;
  const active = await isRecording();
  if (active !== lastCapturing) { lastCapturing = active; init(); return; }
  if (active) {
    const fr = view.querySelector(".rec__frames");
    const _s = st.minutesCapture?.sources || {};
    const m = ((_s.tab || _s.mic || {}).status || "").match(/(\d[\d,]*)\s*frames/);
    if (fr) fr.textContent = m ? "frames " + m[1] : "live";
  }
}

// The offscreen's per-second status write nudges the frame counter; the poll owns state transitions.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.minutesCapture) reconcile();
});
setInterval(reconcile, 1200);

init();
