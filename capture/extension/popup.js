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
let lastSig = ""; // per-source signature (which sources + live/starting/error) — re-render when it changes
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
    "micEnabled", "micGranted", "micDeviceId", "micDeviceLabel", "micAec",
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

// dual-source markup helpers (mirror the designer's screens-dual-ext.jsx)
function micSwitch(on) {
  return `<label class="fs-switch ${on ? "is-on" : ""}" id="micchk"><span class="fs-switch__track"><span class="fs-switch__thumb"></span></span></label>`;
}
// A per-source recording chip. kind: "online"|"mic"; state: "live"|"starting"|"error".
function srcChipHtml(kind, name, meta, state) {
  if (state === "error") {
    return `<div class="src-chip src-chip--${kind} src-chip--error"><span class="src-chip__edge"></span>
      <div class="src-chip__body"><div class="src-chip__name">${name}</div>
        <div class="src-chip__err"><span style="font-weight:700">✕</span><span>${esc(meta || "Transcription stopped.")} <button class="src-chip__fix" data-fix>check your plan / top up</button></span></div></div></div>`;
  }
  const right = state === "starting"
    ? `<span class="src-chip__starting"><span class="src-chip__spin"></span>starting…</span>`
    : `<span class="src-chip__live"><i></i>live</span>`;
  return `<div class="src-chip src-chip--${kind} ${state === "starting" ? "src-chip--starting" : ""}"><span class="src-chip__edge"></span>
    <div class="src-chip__body"><div class="src-chip__name">${name}</div>${meta ? `<div class="src-chip__meta">${esc(meta)}</div>` : ""}</div>
    ${right}</div>`;
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
    const devLabel = st.micDeviceLabel ? esc(st.micDeviceLabel) : "default microphone";
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
      <div class="mic-row">
        <div class="mic-row__head">
          <span class="mic-row__label"><span class="src-mark src-mark--mic"></span>Capture my microphone</span>
          ${micSwitch(micOn)}
        </div>
        ${micReady
          ? `<div class="mic-row__action" style="border-top:none;padding-top:0"><span class="mic-ready"><span class="d"></span>Mic ready · ${devLabel}</span><button class="mic-ready__change" id="mictest">Test / change</button></div>`
          : (micOn
              ? `<div class="mic-row__action"><button class="btn sm" id="micsetup" style="flex:1"><span class="src-mark src-mark--mic" style="margin-right:2px"></span>Grant microphone access</button></div>`
              : `<div class="mic-row__sub">Records your own voice as a separate <b>Host mic</b> track. Off by default.</div>`)}
      </div>
      ${micReady
        ? `<button class="btn lg primary" id="start" style="margin-top:auto">● Start recording (mic only)</button>
           <div class="cap-note">Records your microphone only — nothing from this tab.</div>`
        : `<button class="btn lg" id="start" disabled style="background:var(--surface2);color:var(--muted);margin-top:auto">● Start recording</button>`}`;
    wireMicToggle(st);
    if ($("micsetup")) $("micsetup").onclick = openMicSetup;
    if ($("mictest")) $("mictest").onclick = openMicSetup;
    if (micReady && $("start")) $("start").onclick = () => start({ micOnly: true });
    return;
  }

  const ctx = `
    <div class="ctx">
      <img src="${esc(meeting.iconUrl)}" alt="" width="30" height="30" />
      <div style="flex:1;min-width:0"><div class="ctx__name" title="${esc(meeting.name)}">${esc(meeting.name)}</div><div class="ctx__id">${esc(meeting.sub)}</div></div>
      <span class="src-mark src-mark--online" title="Online stream"></span>
    </div>`;

  if (capturing) {
    const _s = st.minutesCapture?.sources || {};
    const tabObj = _s.tab, micObj = _s.mic;
    const tabLive = !!tabObj?.capturing, micLive = !!micObj?.capturing;
    // A source is shown if it's published (live OR an error chip) or this start requested it (grace).
    const showTab = !!tabObj || capTab;
    const showMic = !!micObj || capMic;
    // Mic-only captures (a blank/non-capturable tab) get a mic context instead of the meeting card.
    const devLabel = st.micDeviceLabel ? esc(st.micDeviceLabel) : "";
    const recCtx = (showTab || (!showMic && meeting.platform))
      ? `<div class="ctx"><img src="${esc(meeting.iconUrl || "brand/icon-web.svg")}" alt="" width="30" height="30" /><div style="flex:1;min-width:0"><div class="ctx__name" title="${esc(meeting.name || "Recording")}">${esc(meeting.name || "Recording")}</div><div class="ctx__id">${esc(meeting.sub || "")}</div></div><span class="src-mark src-mark--online" title="Online stream"></span></div>`
      : `<div class="ctx"><div style="width:30px;height:30px;display:grid;place-items:center;font-size:20px">🎙</div><div style="flex:1;min-width:0"><div class="ctx__name">Mic recording</div><div class="ctx__id">${devLabel || "Host mic only"}</div></div><span class="src-mark src-mark--mic" title="Host mic"></span></div>`;
    const chip = (kind, name, obj, prefix) => {
      const state = obj?.error ? "error" : (obj?.capturing ? "live" : "starting");
      const frames = `${(obj?.frames || 0).toLocaleString()} frames`;
      const meta = state === "live" ? (prefix ? `${prefix} · ${frames}` : frames)
        : state === "error" ? (obj.status || "Transcription stopped.")
          : "";
      return srcChipHtml(kind, name, meta, state);
    };
    const liveCount = (tabLive ? 1 : 0) + (micLive ? 1 : 0);
    const totalShown = (showTab ? 1 : 0) + (showMic ? 1 : 0);
    const anyErr = !!tabObj?.error || !!micObj?.error;
    const header = anyErr ? `RECORDING · ${liveCount} of ${totalShown}`
      : totalShown > 1 ? `RECORDING · ${totalShown} sources`
        : "RECORDING";
    const note = anyErr
      ? "Tab + mic open two Soniox connections. If your plan caps concurrency, the second is rejected — the other source keeps recording."
      : totalShown > 1 ? "Two separate transcripts — merged later."
        : `Streaming to ${esc(hostOf((st.backendBase) || ""))}`;
    view.innerHTML = `
      ${recCtx}
      <div class="rec"><div class="rec__label"><span class="dot"></span>${header}</div></div>
      <div class="srcchips">
        ${showTab ? chip("online", "Online stream", tabObj) : ""}
        ${showMic ? chip("mic", "Host mic", micObj, devLabel) : ""}
      </div>
      <button class="btn lg stop" id="stop" style="margin-top:auto">■ ${totalShown > 1 ? "Stop both" : "Stop"}</button>
      <div class="cap-note">${note}</div>`;
    $("stop").onclick = stop;
    view.querySelectorAll("[data-fix]").forEach((b) => (b.onclick = () => chrome.tabs.create({ url: "https://console.soniox.com/" })));
  } else {
    const micOn = !!st.micEnabled;
    const micReady = micOn && st.micGranted;
    const devLabel = st.micDeviceLabel ? esc(st.micDeviceLabel) : "default microphone";
    view.innerHTML = `
      ${ctx}
      <div class="mic-row">
        <div class="mic-row__head">
          <span class="mic-row__label"><span class="src-mark src-mark--mic"></span>Also capture my microphone</span>
          ${micSwitch(micOn)}
        </div>
        ${micReady
          ? `<div class="mic-row__action" style="border-top:none;padding-top:0"><span class="mic-ready"><span class="d"></span>Mic ready · ${devLabel}</span><button class="mic-ready__change" id="mictest">Test / change</button></div>`
          : `<div class="mic-row__sub">Adds a separate <b>Host mic</b> track for your own voice — no second bot account. Off by default.</div>
             ${micOn ? `<div class="mic-row__action"><button class="btn sm" id="micsetup" style="flex:1"><span class="src-mark src-mark--mic" style="margin-right:2px"></span>Grant microphone access</button></div>` : ""}`}
      </div>
      <button class="btn lg primary" id="start" style="margin-top:auto">● Start recording${micReady ? " · 2 sources" : ""}</button>
      <div class="cap-note">${micReady ? "Online stream + Host mic, kept separate." : "Captures this tab's audio — you still hear everything."}</div>`;
    $("start").onclick = start;
    wireMicToggle(st);
    if ($("mictest")) $("mictest").onclick = openMicSetup;
    if ($("micsetup")) $("micsetup").onclick = openMicSetup;
  }
}

// Wire the fs-switch mic toggle (#micchk is a label, not a checkbox — flip on click).
function wireMicToggle(st) {
  const sw = $("micchk");
  if (!sw) return;
  sw.onclick = async () => {
    const on = !sw.classList.contains("is-on"); // the state we're switching TO
    await chrome.storage.local.set({ micEnabled: on });
    if (on && !st.micGranted) openMicSetup(); // first enable -> grant + test on the setup page
    else init();
  };
}

function openMicSetup() {
  // Mic grant + device/level/echo/silence all live in the single settings page now. Opening it in a
  // tab (open_in_tab) survives Chrome's mic-permission prompt stealing focus.
  chrome.runtime.openOptionsPage();
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
    const full = await chrome.storage.local.get(["backendBase", "deviceEmail", "minutesCapture", "micDeviceLabel"]);
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

// Converge the popup to the real state. Re-render on a liveness transition OR a per-source change
// (a source going live / starting / erroring); otherwise refresh frame counts in place (no flicker).
async function reconcile() {
  const st = await chrome.storage.local.get(["minutesCapture", "deviceToken", "backendBase"]);
  if (!st.deviceToken || !st.backendBase) return;
  const active = await isRecording();
  const _s = st.minutesCapture?.sources || {};
  const sig = ["tab", "mic"].map((k) => {
    const o = _s[k];
    return !o ? "-" : o.error ? "e" : o.capturing ? "L" : "s";
  }).join("");
  if (active !== lastCapturing || (active && sig !== lastSig)) {
    lastCapturing = active;
    lastSig = sig;
    init();
    return;
  }
  if (active) {
    for (const k of ["tab", "mic"]) {
      const o = _s[k];
      const el = view.querySelector(`.src-chip--${k === "tab" ? "online" : "mic"} .src-chip__meta`);
      if (el && o?.capturing) el.textContent = `${(o.frames || 0).toLocaleString()} frames`;
    }
  }
}

// Three nudges to converge the popup, belt-and-suspenders: the offscreen's direct status push
// (most reliable), the storage write it also makes, and a slow poll as a backstop.
chrome.runtime.onMessage.addListener((msg) => { if (msg?.type === "capture-status") reconcile(); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.minutesCapture) reconcile();
});
setInterval(reconcile, 1200);

init();
