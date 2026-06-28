// Popup: sign in (device token), auto-detect the active meeting, then capture.
// On Start we exchange the stored device token for a short, meeting-scoped capability token via
// POST /api/capture/token (which also claims the meeting for this user + seeds its translation
// config), then hand that token + the derived ingest WS URL to the service worker.
const $ = (id) => document.getElementById(id);
const view = $("view");
const who = $("who");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const DEFAULT_BASE = "http://localhost:8000";

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

const wsIngest = (base) => base.replace(/\/$/, "").replace(/^http/, "ws") + "/ingest";

let activeTabId = null;
let meeting = { platform: "", externalMeetingId: "" };

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTabId = tab?.id ?? null;
  meeting = extractMeeting(tab?.url || "");
  const st = await chrome.storage.local.get(["backendBase", "deviceToken", "deviceEmail", "minutesCapture"]);
  if (st.deviceToken) {
    who.textContent = st.deviceEmail || "";
    renderCapture(st);
  } else {
    renderLogin(st);
  }
}

// ---------- signed-out: login ----------
function renderLogin(st) {
  who.textContent = "";
  view.innerHTML = `
    <label>Backend URL</label>
    <input id="base" placeholder="https://gettheminutes.com" />
    <label>Email</label>
    <input id="email" type="email" autocomplete="username" />
    <label>Password</label>
    <input id="password" type="password" autocomplete="current-password" />
    <div class="err" id="err" style="margin:8px 0 0;display:none"></div>
    <div style="margin-top:14px"><button class="primary" id="signin">Sign in</button></div>
    <div class="hint">Use your minutes account. No account? Ask your administrator.</div>`;
  $("base").value = st.backendBase || DEFAULT_BASE;
  $("password").addEventListener("keydown", (e) => { if (e.key === "Enter") $("signin").click(); });
  $("signin").onclick = signIn;
}

async function signIn() {
  const err = $("err");
  err.style.display = "none";
  const base = $("base").value.trim().replace(/\/$/, "");
  const btn = $("signin");
  btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const r = await fetch(base + "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("email").value.trim(), password: $("password").value, client: "device" }),
    });
    if (!r.ok) {
      let d; try { d = (await r.json()).detail; } catch { /* ignore */ }
      throw new Error(d || (r.status === 401 ? "Invalid email or password" : "Sign-in failed"));
    }
    const data = await r.json();
    await chrome.storage.local.set({ backendBase: base, deviceToken: data.device_token, deviceEmail: data.email });
    who.textContent = data.email || "";
    renderCapture(await chrome.storage.local.get(["minutesCapture"]));
  } catch (e) {
    err.textContent = e.message; err.style.display = "block";
  } finally {
    btn.disabled = false; btn.textContent = "Sign in";
  }
}

// ---------- signed-in: capture ----------
function renderCapture(st) {
  const cap = st?.minutesCapture;
  const capturing = !!cap?.capturing;
  const detected = meeting.externalMeetingId
    ? `${meeting.platform} · ${meeting.externalMeetingId}`
    : "No Meet/Teams meeting detected in this tab";
  view.innerHTML = `
    <div class="card">
      <div class="row between"><span style="font-weight:600">${capturing ? '<span class="dot"></span> Recording' : "Ready"}</span>
        <div class="meter ${capturing ? "on" : ""}"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div>
      <div class="meeting" style="margin-top:8px">${esc(detected)}</div>
    </div>
    <div style="margin-top:14px">
      ${capturing
        ? `<button class="stop" id="stop">Stop recording</button>`
        : `<button class="primary" id="start" ${meeting.externalMeetingId ? "" : "disabled"}>Start recording</button>`}
    </div>
    <div id="status"></div>
    <div class="row between" style="margin-top:14px">
      <button class="ghost" id="refresh">↻ Re-detect</button>
      <button class="danger" id="signout">Sign out</button>
    </div>`;
  $("status").textContent = cap?.status || ""; // status may carry backend/error text — never innerHTML
  if (capturing) $("stop").onclick = stop;
  else if ($("start")) $("start").onclick = start;
  $("refresh").onclick = init;
  $("signout").onclick = signOut;
}

async function signOut() {
  await chrome.runtime.sendMessage({ type: "stop" }).catch(() => {});
  await chrome.storage.local.remove(["deviceToken", "deviceEmail"]);
  init();
}

async function start() {
  const status = $("status");
  status.className = ""; status.textContent = "Authorizing…";
  const st = await chrome.storage.local.get(["backendBase", "deviceToken"]);
  try {
    const r = await fetch(st.backendBase.replace(/\/$/, "") + "/api/capture/token", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + st.deviceToken },
      body: JSON.stringify({ platform: meeting.platform, external_meeting_id: meeting.externalMeetingId }),
    });
    if (r.status === 401) {
      await chrome.storage.local.remove(["deviceToken", "deviceEmail"]);
      status.className = "err"; status.textContent = "Session expired — please sign in again.";
      setTimeout(init, 1200);
      return;
    }
    if (!r.ok) {
      let d; try { d = (await r.json()).detail; } catch { /* ignore */ }
      throw new Error(d || "Could not authorize this meeting");
    }
    const { token } = await r.json();
    const config = {
      backendUrl: wsIngest(st.backendBase),
      token,
      platform: meeting.platform,
      externalMeetingId: meeting.externalMeetingId,
      callId: crypto.randomUUID(),
    };
    const res = await chrome.runtime.sendMessage({ type: "start", tabId: activeTabId, config });
    if (!res?.ok) throw new Error(res?.error || "capture failed to start");
    status.textContent = "Recording started.";
    setTimeout(init, 400);
  } catch (e) {
    status.className = "err"; status.textContent = e.message;
  }
}

async function stop() {
  await chrome.runtime.sendMessage({ type: "stop" });
  $("status").textContent = "Stopping…";
  setTimeout(init, 400);
}

// Reflect capture status pushed from the offscreen doc while the popup is open.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target === "popup" && msg.type === "status" && $("status")) $("status").textContent = msg.status;
});

init();
