// Service worker: orchestrates tab-audio capture via an offscreen document.
// MV3 tab capture flow: the service worker mints a media-stream id for the target tab, then an
// offscreen document redeems it with getUserMedia (a service worker has no DOM/getUserMedia).

const OFFSCREEN_URL = "offscreen.html";

// On (re)load, clear a stale "capturing" flag unless an offscreen capture is genuinely still alive.
(async () => {
  try {
    if (!(await chrome.offscreen.hasDocument?.())) {
      await chrome.storage.local.set({ minutesCapture: { capturing: false, status: "" } });
    }
  } catch {}
})();

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument?.();
  if (has) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ["USER_MEDIA"],
    justification: "Capture meeting tab audio for transcription.",
  });
}

async function startCapture(tabId, config) {
  // Reset any prior capture first, else getMediaStreamId throws "Cannot capture a tab with an
  // active stream" when the offscreen doc is still holding the previous tab stream.
  try {
    if (await chrome.offscreen.hasDocument?.()) await chrome.offscreen.closeDocument();
  } catch {}
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await ensureOffscreen();
  await chrome.runtime.sendMessage({ target: "offscreen", type: "start", streamId, config });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "start") {
        // From the popup: explicit tabId.
        await startCapture(msg.tabId, msg.config);
        sendResponse({ ok: true });
      } else if (msg.type === "start-here") {
        // From the content script (bot-join driver): capture the sender's own tab.
        await startCapture(sender.tab?.id, msg.config);
        sendResponse({ ok: true });
      } else if (msg.type === "stop") {
        await chrome.runtime.sendMessage({ target: "offscreen", type: "stop" });
        sendResponse({ ok: true });
      }
    } catch (err) {
      sendResponse({ ok: false, error: String(err) });
    }
  })();
  return true; // async sendResponse
});
