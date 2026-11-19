"use strict";
// Plays the tracks on the public pages: one shared audio element; press a round play button or click the waveform. A bar at the bottom
// follows what is playing. In a playlist the next track follows by itself. The audio is fetched only when you press play.
(function () {
  const items = Array.from(document.querySelectorAll(".item"));
  if (!items.length) return;
  const $ = (id) => document.getElementById(id);
  const bar = $("bar"), barPlay = $("bar-play"), barTitle = $("bar-title"), barFill = $("bar-fill"), barProg = $("bar-progress"), barTime = $("bar-time");
  const audio = new Audio();
  audio.preload = "none";
  const playlist = !!document.querySelector("[data-playlist]");
  let current = null;
  const fmt = (s) => (isFinite(s) && s >= 0 ? Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0") : "0:00");
  const titleOf = (it) => it.querySelector(".title").textContent;
  // fewer, chunkier bars on a narrow screen: the loudest of each group, so the shape stays
  const fit = (p, n) => (p.length <= n ? p : Array.from({ length: n }, (_, i) => Math.max(...p.slice(Math.floor(i * p.length / n), Math.floor((i + 1) * p.length / n)))));

  function paint() {
    const playing = !audio.paused;
    for (const it of items) {
      const on = it === current && playing, btn = it.querySelector(".play");
      it.classList.toggle("playing", it === current);
      btn.dataset.state = on ? "pause" : "play";
      btn.setAttribute("aria-label", (on ? "Pause " : "Play ") + titleOf(it));
    }
    barPlay.dataset.state = playing ? "pause" : "play";
    barPlay.setAttribute("aria-label", playing ? "Pause" : "Play");
  }
  function start(it, at) {
    if (current !== it) {
      current = it;
      audio.src = it.dataset.audio;
      barTitle.textContent = titleOf(it);
      bar.hidden = false;
      if (at) audio.addEventListener("loadedmetadata", () => { audio.currentTime = at; }, { once: true });
    } else if (at != null) audio.currentTime = at;
    audio.play().catch(() => { /* the browser refused (no tap yet): the next tap works */ });
    paint();
  }
  const toggle = (it) => { if (current === it && !audio.paused) audio.pause(); else start(it); };

  for (const it of items) {
    const canvas = it.querySelector("canvas.wave");
    const raw = canvas.dataset.peaks ? canvas.dataset.peaks.split(",").map(Number) : [];
    let bins = 0;
    const w = it._wave = window.Wave.attach(canvas, {
      peaks: raw, duration: Number(canvas.dataset.duration) || 0,
      label: "Waveform of " + titleOf(it) + ": click to play from there", onSeek: (t) => start(it, t),
    });
    const refit = () => { const n = Math.max(24, Math.floor(canvas.clientWidth / 4)); if (n !== bins && raw.length) { bins = n; w.set({ peaks: fit(raw, n) }); } };
    if (window.ResizeObserver) new ResizeObserver(refit).observe(canvas); else refit();
    it.querySelector(".play").addEventListener("click", () => toggle(it));
  }

  audio.addEventListener("timeupdate", () => {
    if (!current) return;
    const d = audio.duration || Number(current.querySelector("canvas").dataset.duration) || 0, t = audio.currentTime;
    current._wave.set({ time: t, duration: d });
    current.querySelector(".t-now").textContent = fmt(t);
    barFill.style.width = d ? Math.min(100, (t / d) * 100) + "%" : "0";
    barProg.setAttribute("aria-valuenow", String(d ? Math.round((t / d) * 100) : 0));
    barTime.textContent = fmt(t) + " / " + fmt(d);
  });
  audio.addEventListener("pause", paint);
  audio.addEventListener("play", paint);
  audio.addEventListener("ended", () => {
    const i = items.indexOf(current);
    if (playlist && i >= 0 && i + 1 < items.length) start(items[i + 1]); else paint();
  });
  barPlay.addEventListener("click", () => { if (current) toggle(current); else toggle(items[0]); });
  function seekBar(e) { const r = barProg.getBoundingClientRect(), d = audio.duration; if (d) audio.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * d; }
  barProg.addEventListener("pointerdown", (e) => { barProg.setPointerCapture(e.pointerId); seekBar(e); barProg.onpointermove = seekBar; });
  barProg.addEventListener("pointerup", () => { barProg.onpointermove = null; });
  barProg.addEventListener("keydown", (e) => { if (e.key === "ArrowRight") audio.currentTime += 5; else if (e.key === "ArrowLeft") audio.currentTime = Math.max(0, audio.currentTime - 5); });
  document.addEventListener("keydown", (e) => { if (e.key === " " && !/^(INPUT|TEXTAREA|SELECT|BUTTON|A)$/.test(e.target.tagName)) { e.preventDefault(); toggle(current || items[0]); } });
})();
