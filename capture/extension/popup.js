// Popup: configure the backend + token, auto-detect the meeting from the active tab, start/stop.
const $ = (id) => document.getElementById(id);

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
  } catch {}
  return { platform: "meet", externalMeetingId: "" };
}

let activeTabId = null;

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTabId = tab?.id ?? null;
  const meeting = extractMeeting(tab?.url || "");
  $("platform").value = meeting.platform;
  $("meetingId").value = meeting.externalMeetingId;
  const saved = await chrome.storage.local.get(["backendUrl", "token"]);
  $("backendUrl").value = saved.backendUrl || "ws://localhost:8000/ingest";
  $("token").value = saved.token || "";
}

$("start").onclick = async () => {
  const config = {
    backendUrl: $("backendUrl").value.trim(),
    token: $("token").value.trim(),
    platform: $("platform").value.trim(),
    externalMeetingId: $("meetingId").value.trim(),
    callId: crypto.randomUUID(),
  };
  await chrome.storage.local.set({ backendUrl: config.backendUrl, token: config.token });
  const res = await chrome.runtime.sendMessage({ type: "start", tabId: activeTabId, config });
  $("status").textContent = res?.ok ? "starting…" : "error: " + (res?.error || "unknown");
};

$("stop").onclick = async () => {
  await chrome.runtime.sendMessage({ type: "stop" });
  $("status").textContent = "stopping…";
};

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target === "popup" && msg.type === "status") $("status").textContent = msg.status;
});

init();
