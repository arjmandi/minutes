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
  const capturing = !!st.minutesCapture?.capturing;
  lastCapturing = capturing;

  if (!meeting.platform) {
    view.innerHTML = `
      <div class="empty">
        <div style="display:flex;gap:8px;opacity:.5"><img src="brand/icon-meet.svg" width="30" height="30"/><img src="brand/icon-teams.svg" width="30" height="30"/></div>
        <div class="empty__title">Nothing to capture here</div>
        <div class="empty__sub">Open a Google Meet or Teams call — or any tab playing audio (a video, podcast, webinar…).</div>
      </div>
      <button class="btn lg" id="start" disabled style="background:var(--surface2);color:var(--muted)">● Start recording</button>`;
    return;
  }

  const ctx = `
    <div class="ctx">
      <img src="${esc(meeting.iconUrl)}" alt="" width="30" height="30" />
      <div style="min-width:0"><div class="ctx__name" title="${esc(meeting.name)}">${esc(meeting.name)}</div><div class="ctx__id">${esc(meeting.sub)}</div></div>
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
  // The new Teams web app exposes no meeting id in the URL — mint a per-capture id so the meeting
  // is still created + authorized. (Classic Teams + Meet keep their real id.)
  const externalMeetingId = meeting.externalMeetingId || ("web-" + crypto.randomUUID().slice(0, 8));
  try {
    const body = { platform: meeting.platform, external_meeting_id: externalMeetingId };
    if (meeting.platform === "web" && meeting.name) body.title = meeting.name; // name it after the tab
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
    const res = await chrome.runtime.sendMessage({
      type: "start",
      tabId: activeTabId,
      config: {
        backendUrl: wsIngest(st.backendBase),
        token,
        platform: meeting.platform,
        externalMeetingId,
        callId: crypto.randomUUID(),
      },
    });
    if (!res?.ok) throw new Error(res?.error || "capture failed to start");
    // Flip to the recording UI immediately — don't wait on the offscreen's first storage write
    // (it lags the WS handshake, and a single storage.onChanged can be missed while the popup is
    // mid-render). The poll below reconciles to the real state, incl. reverting if capture fails.
    lastCapturing = true;
    const full = await chrome.storage.local.get(["backendBase", "deviceEmail", "minutesCapture"]);
    renderCapture({
      backendBase: full.backendBase,
      deviceEmail: full.deviceEmail,
      minutesCapture: { capturing: true, status: full.minutesCapture?.status || "" },
    });
  } catch (e) {
    if (note) { note.textContent = e.message; note.classList.add("err"); }
  }
}

async function stop() {
  await chrome.runtime.sendMessage({ type: "stop" });
  // Render idle now (the offscreen writes capturing=false on stop); the poll reconciles.
  lastCapturing = false;
  init();
}

// Reconcile the popup to the real capture state: re-render on a start/stop transition, otherwise
// just refresh the frame counter in place (no flicker). Shared by the live storage event AND the
// poll, so the UI converges even when a single storage.onChanged is missed.
function applyCapture(v) {
  const cap = !!v?.capturing;
  if (cap !== lastCapturing) { lastCapturing = cap; init(); return; }
  if (cap) {
    const fr = view.querySelector(".rec__frames");
    const m = (v?.status || "").match(/(\d[\d,]*)\s*frames/);
    if (fr) fr.textContent = m ? "frames " + m[1] : "live";
  }
}

// Live status pushed from the offscreen doc (~1/s while recording).
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.minutesCapture) applyCapture(changes.minutesCapture.newValue);
});

// Fallback for a missed storage event: poll while the popup is open so it always converges to
// reality (and reverts an optimistic "recording" if the capture actually failed to connect).
setInterval(async () => {
  const st = await chrome.storage.local.get(["minutesCapture", "deviceToken", "backendBase"]);
  if (st.deviceToken && st.backendBase) applyCapture(st.minutesCapture);
}, 1200);

init();
