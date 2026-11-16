"use strict";
// Plays the tracks on the public pages: one shared audio element, click a play button or the waveform. In a playlist the next track follows.
(function () {
  const items = Array.from(document.querySelectorAll(".item"));
  if (!items.length) return;
  const audio = new Audio();
  audio.preload = "none";
  const playlist = !!document.querySelector("[data-playlist]");
  let current = null;
  const fmt = (s) => (isFinite(s) ? Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0") : "");

  function setButtons() {
    for (const it of items) {
      const on = it === current && !audio.paused;
      it.classList.toggle("playing", it === current);
      it.querySelector(".play").textContent = on ? "❚❚" : "▶";
      it.querySelector(".play").setAttribute("aria-label", (on ? "Pause " : "Play ") + it.querySelector(".title").textContent);
    }
  }
  function start(it, at) {
    if (current !== it) {
      current = it;
      audio.src = it.dataset.audio;
      audio.addEventListener("loadedmetadata", function once() { audio.removeEventListener("loadedmetadata", once); if (at) audio.currentTime = at; });
    } else if (at != null) audio.currentTime = at;
    audio.play().catch(() => { /* the browser refused (no gesture yet): the next click works */ });
    setButtons();
  }
  for (const it of items) {
    const canvas = it.querySelector("canvas.wave");
    it._wave = window.Wave.attach(canvas, {
      peaks: canvas.dataset.peaks ? canvas.dataset.peaks.split(",").map(Number) : [],
      duration: Number(canvas.dataset.duration) || 0,
      label: "Waveform of " + it.querySelector(".title").textContent + ": click to play from there",
      onSeek: (t) => start(it, t),
    });
    it.querySelector(".play").addEventListener("click", () => {
      if (current === it && !audio.paused) { audio.pause(); setButtons(); } else start(it);
    });
  }
  audio.addEventListener("timeupdate", () => {
    if (!current) return;
    const d = audio.duration || Number(current.querySelector("canvas").dataset.duration) || 0;
    current._wave.set({ time: audio.currentTime, duration: d });
    current.querySelector(".time").textContent = fmt(audio.currentTime) + " / " + fmt(d);
  });
  audio.addEventListener("pause", setButtons);
  audio.addEventListener("play", setButtons);
  audio.addEventListener("ended", () => {
    const i = items.indexOf(current);
    if (playlist && i >= 0 && i + 1 < items.length) start(items[i + 1]); else setButtons();
  });
})();
