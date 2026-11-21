"use strict";
// The "Playback: lossy / lossless" switch in the page header. Lossy (the default) plays the small AAC copy: far less data, and what streaming services
// do. Lossless plays the original file. The choice is remembered in this browser. Used by public.js and studio.js.
(function () {
  const KEY = "musicshare.quality";
  const store = { get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }, set(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* storage unavailable */ } } };
  let mode = store.get() === "lossless" ? "lossless" : "lossy";
  const listeners = [];
  const btn = () => document.getElementById("quality");
  function paint() {
    const b = btn(); if (!b) return;
    b.hidden = false;
    b.textContent = "Playback: " + mode;
    b.setAttribute("aria-pressed", String(mode === "lossless"));
    b.title = mode === "lossless" ? "Playing the original files. Press for the smaller lossy copies (less data)." : "Playing a small lossy copy (less data). Press to play the original files, lossless.";
  }
  window.quality = {
    mode: () => mode,
    url(src) { return mode === "lossless" ? src + (src.includes("?") ? "&" : "?") + "q=lossless" : src; },
    onChange(fn) { listeners.push(fn); },
  };
  function init() {
    const b = btn(); if (!b) return;
    paint();
    b.addEventListener("click", () => { mode = mode === "lossless" ? "lossy" : "lossless"; store.set(mode); paint(); for (const f of listeners) f(mode); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
