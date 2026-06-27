/* minutes — animated live multilingual transcript
   Streams source lines, then lands translations a beat later.
   Persian (fa) renders right-to-left. Loops; respects reduced-motion. */
(function () {
  const LINES = [
    {
      who: "Anna Vogel", t: "09:32",
      lang: "DE", dir: "ltr",
      src: "Sollen wir mit den Quartalszahlen anfangen?",
      tr: { lang: "EN", dir: "ltr", text: "Shall we start with the quarterly figures?" },
    },
    {
      who: "Darius Ahmadi", t: "09:32",
      lang: "FA", dir: "rtl",
      src: "بله، من خلاصه را از قبل آماده کرده‌ام.",
      tr: { lang: "EN", dir: "ltr", text: "Yes, I've already prepared the summary." },
    },
    {
      who: "James Okoro", t: "09:33",
      lang: "EN", dir: "ltr",
      src: "Great — can you walk us through the revenue?",
      tr: { lang: "DE", dir: "ltr", text: "Super — kannst du uns durch den Umsatz führen?" },
    },
    {
      who: "Anna Vogel", t: "09:33",
      lang: "DE", dir: "ltr",
      src: "Der Umsatz ist um 18 % gestiegen.",
      tr: { lang: "EN", dir: "ltr", text: "Revenue grew by 18%." },
    },
    {
      who: "Darius Ahmadi", t: "09:34",
      lang: "FA", dir: "rtl",
      src: "هزینه‌ها هم تحت کنترل باقی ماند.",
      tr: { lang: "EN", dir: "ltr", text: "Costs also stayed under control." },
    },
  ];

  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function buildLine(d) {
    const row = el("div", "tline");
    row.dataset.lang = d.lang;

    const meta = el("div", "tline__meta");
    meta.appendChild(el("span", "tline__who", d.who));
    meta.appendChild(el("span", "tline__time fs-mono", d.t));
    row.appendChild(meta);

    const src = el("div", "tline__src");
    src.dir = d.dir;
    const tag = el("span", "tline__lang fs-mono", d.lang);
    const txt = el("span", "tline__text", d.src);
    if (d.dir === "rtl") { src.appendChild(txt); src.appendChild(tag); }
    else { src.appendChild(tag); src.appendChild(txt); }
    row.appendChild(src);

    const tr = el("div", "tline__tr");
    tr.dir = d.tr.dir;
    const ti = el("span", "tline__tricon fs-mono", "→ " + d.tr.lang);
    const tt = el("span", "tline__text", d.tr.text);
    tr.appendChild(ti);
    tr.appendChild(tt);
    row.appendChild(tr);

    return row;
  }

  function init() {
    const stream = document.getElementById("transcript-stream");
    if (!stream) return;
    const interim = document.getElementById("transcript-interim");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function renderStatic() {
      stream.innerHTML = "";
      LINES.forEach((d) => {
        const row = buildLine(d);
        row.classList.add("is-in");
        row.querySelector(".tline__tr").classList.add("is-in");
        stream.appendChild(row);
      });
      if (interim) interim.style.opacity = "0";
    }

    if (reduce || window.__minutesTranscriptMotion === false) {
      renderStatic();
      return;
    }

    let timers = [];
    function clear() { timers.forEach(clearTimeout); timers = []; }
    function at(ms, fn) { timers.push(setTimeout(fn, ms)); }

    function run() {
      clear();
      stream.innerHTML = "";
      if (interim) interim.style.opacity = "0";

      let clock = 300;
      const SRC_GAP = 1150;
      const TR_DELAY = 520;

      LINES.forEach((d) => {
        const row = buildLine(d);
        stream.appendChild(row);
        const appearAt = clock;
        at(appearAt, () => {
          row.classList.add("is-in");
          if (interim) {
            interim.style.opacity = "1";
            const ip = interim.querySelector(".tinterim__text");
            if (ip) ip.textContent = "transcribing…";
          }
          stream.scrollTo({ top: stream.scrollHeight, behavior: "smooth" });
        });
        at(appearAt + TR_DELAY, () => {
          row.querySelector(".tline__tr").classList.add("is-in");
          stream.scrollTo({ top: stream.scrollHeight, behavior: "smooth" });
        });
        clock += SRC_GAP;
      });

      at(clock + 1400, () => {
        if (interim) {
          interim.style.opacity = "1";
          const ip = interim.querySelector(".tinterim__text");
          if (ip) ip.textContent = "listening…";
        }
      });
      at(clock + 3200, run);
    }

    window.__restartTranscript = function () {
      clear();
      if (window.__minutesTranscriptMotion === false) renderStatic();
      else run();
    };

    run();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
