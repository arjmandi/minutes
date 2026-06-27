// Injected on meet.google.com / teams.microsoft.com. Lets the bot-join driver start/stop capture
// for THIS tab via window.postMessage (the driver runs Playwright in the page context; the
// extension reads the sender tab id in the background), enabling unattended capture.
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || data.source !== "minutes-driver") return;
  if (data.type === "start") {
    chrome.runtime.sendMessage({ type: "start-here", config: data.config });
  } else if (data.type === "stop") {
    chrome.runtime.sendMessage({ type: "stop" });
  }
});
