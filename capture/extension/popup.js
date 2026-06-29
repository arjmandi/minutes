// Popup: sign in (device token) + capture. The server URL lives in the options page (gear); the
// popup only signs in against the saved server and exchanges the device token for a short,
// meeting-scoped capability token via POST /api/capture/token before streaming to /ingest.
const $ = (id) => document.getElementById(id);
const view = $("view");
const foot = $("foot");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

$("gear").onclick = () => chrome.runtime.openOptionsPage();

const PLATFORM = {
  meet: { label: "Google Meet", icon: "brand/icon-meet.svg" },
  teams: { label: "Microsoft Teams", icon: "brand/icon-teams.svg" },
};
const hostOf = (base) => { try { return new URL(base).host; } catch { return base; } };
const wsIngest = (base) => base.replace(/\/+$/, "").replace(/^http/, "ws") + "/ingest";

function extractMeeting(url) {
  try {
    const u = new URL(url);
    if (u.hostname === "meet.google.com") {
      const m = u.pathname.match(/([a-z]{3}-[a-z]{4}-[a-z]{3})/);
      return { platform: "meet", externalMeetingId: m ? m[1] : "" };
    }
    if (u.hostname === "teams.microsoft.com") {
      const m = decodeURIComponent(u.href).match(/(19:meeting_[^@]+@thread\.v2)/);
      return { platform: "teams", externalMeetingId: m ? m[1] : "" };
    }
  } catch { /* not a URL */ }
  return { platform: "", externalMeetingId: "" };
}

let activeTabId = null;
let meeting = { platform: "", externalMeetingId: "" };
let lastCapturing = null; // last rendered capture state (so the live listener only re-renders on transitions)

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
  meeting = extractMeeting(tab?.url || "");
  const st = await chrome.storage.local.get(["backendBase", "deviceToken", "deviceEmail", "minutesCapture"]);
  if (!st.backendBase) return renderNoServer();
  if (!st.deviceToken) return renderLogin(st);
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
  err.style.display = "none";
  const btn = $("signin");
  btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const r = await fetch(base.replace(/\/+$/, "") + "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("email").value.trim(), password: $("password").value, client: "device" }),
    });
    if (!r.ok) {
      let d; try { d = (await r.json()).detail; } catch { /* ignore */ }
      throw new Error(d || (r.status === 401 ? "Invalid email or password" : "Sign-in failed"));
    }
    const data = await r.json();
    await chrome.storage.local.set({ deviceToken: data.device_token, deviceEmail: data.email });
    init();
  } catch (e) {
    err.textContent = e.message; err.style.display = "block";
    btn.disabled = false; btn.textContent = "Sign in";
  }
}

async function signOut() {
  await chrome.runtime.sendMessage({ type: "stop" }).catch(() => {});
  await chrome.storage.local.remove(["deviceToken", "deviceEmail"]);
  init();
}

// ---------- signed-in: capture (idle / recording / no-meeting) ----------
function renderCapture(st) {
  showFoot(st.deviceEmail);
  const capturing = !!st.minutesCapture?.capturing;
  lastCapturing = capturing;
  const p = PLATFORM[meeting.platform];

  if (!p || !meeting.externalMeetingId) {
    view.innerHTML = `
      <div class="empty">
        <div style="display:flex;gap:8px;opacity:.5"><img src="brand/icon-meet.svg" width="30" height="30"/><img src="brand/icon-teams.svg" width="30" height="30"/></div>
        <div class="empty__title">No meeting on this tab</div>
        <div class="empty__sub">Open a Google Meet or Teams meeting to capture.</div>
      </div>
      <button class="btn lg" id="start" disabled style="background:var(--surface2);color:var(--muted)">● Start recording</button>`;
    return;
  }

  const ctx = `
    <div class="ctx">
      <img src="${p.icon}" alt="" />
      <div style="min-width:0"><div class="ctx__name">${esc(p.label)}</div><div class="ctx__id">${esc(meeting.externalMeetingId)}</div></div>
    </div>`;

  if (capturing) {
    const frames = (st.minutesCapture.status || "").match(/(\d[\d,]*)\s*frames/);
    view.innerHTML = `
      ${ctx}
      <div class="rec">
        <div class="rec__label"><span class="dot"></span>RECORDING</div>
        <div class="rec__frames">${frames ? "frames " + esc(frames[1]) : "live"}</div>
      </div>
      <div class="meterwrap"><div class="meter on"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div>
      <button class="btn lg stop" id="stop">■ Stop</button>
      <div class="cap-note">Streaming to ${esc(hostOf((st.backendBase) || ""))}</div>`;
    $("stop").onclick = stop;
  } else {
    view.innerHTML = `
      ${ctx}
      <div class="meterwrap"><div class="meter idle"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div>
      <button class="btn lg primary" id="start">● Start recording</button>
      <div class="cap-note">Captures this tab's audio — you still hear everything.</div>`;
    $("start").onclick = start;
  }
}

async function start() {
  const st = await chrome.storage.local.get(["backendBase", "deviceToken"]);
  const note = view.querySelector(".cap-note");
  if (note) note.textContent = "Authorizing…";
  try {
    const r = await fetch(st.backendBase.replace(/\/+$/, "") + "/api/capture/token", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + st.deviceToken },
      body: JSON.stringify({ platform: meeting.platform, external_meeting_id: meeting.externalMeetingId }),
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
    const res = await chrome.runtime.sendMessage({
      type: "start",
      tabId: activeTabId,
      config: {
        backendUrl: wsIngest(st.backendBase),
        token,
        platform: meeting.platform,
        externalMeetingId: meeting.externalMeetingId,
        callId: crypto.randomUUID(),
      },
    });
    if (!res?.ok) throw new Error(res?.error || "capture failed to start");
    setTimeout(init, 400);
  } catch (e) {
    if (note) { note.textContent = e.message; note.classList.add("err"); }
  }
}

async function stop() {
  await chrome.runtime.sendMessage({ type: "stop" });
  const note = view.querySelector(".cap-note");
  if (note) note.textContent = "Stopping…";
  setTimeout(init, 400);
}

// Live status pushed from the offscreen doc (~1/s while recording). Re-render only on a
// start/stop transition; otherwise just refresh the frame counter in place (no flicker).
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes.minutesCapture) return;
  const v = changes.minutesCapture.newValue;
  const cap = !!v?.capturing;
  if (cap !== lastCapturing) {
    lastCapturing = cap;
    init();
  } else if (cap) {
    const fr = view.querySelector(".rec__frames");
    const m = (v.status || "").match(/(\d[\d,]*)\s*frames/);
    if (fr) fr.textContent = m ? "frames " + m[1] : "live";
  }
});

init();
