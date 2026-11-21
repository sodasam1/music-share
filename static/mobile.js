"use strict";
// Lock-screen and headset controls (the Media Session API), so a track keeps its title, its play/pause and its position on the phone's lock screen
// and in the car. Does nothing where the browser does not have it. Used by public.js and studio.js.
(function () {
  const ms = navigator.mediaSession;
  if (!ms || typeof MediaMetadata === "undefined") { window.lockScreen = { set() {}, bind() {} }; return; }
  const safe = (fn) => { try { fn(); } catch (e) { /* an action this browser does not know: ignore it */ } };
  window.lockScreen = {
    set(title) { safe(() => { ms.metadata = new MediaMetadata({ title, artist: "Music" }); }); },
    bind(audio, more) {
      const act = (name, fn) => safe(() => ms.setActionHandler(name, fn));
      act("play", () => audio.play());
      act("pause", () => audio.pause());
      act("seekbackward", (d) => { audio.currentTime = Math.max(0, audio.currentTime - ((d && d.seekOffset) || 10)); });
      act("seekforward", (d) => { audio.currentTime = Math.min(audio.duration || 1e9, audio.currentTime + ((d && d.seekOffset) || 10)); });
      act("seekto", (d) => { if (d && d.seekTime != null) audio.currentTime = d.seekTime; });
      act("previoustrack", more && more.prev ? more.prev : null);
      act("nexttrack", more && more.next ? more.next : null);
      const position = () => safe(() => { if (isFinite(audio.duration) && audio.duration > 0) ms.setPositionState({ duration: audio.duration, position: Math.min(audio.currentTime, audio.duration), playbackRate: audio.playbackRate || 1 }); });
      for (const ev of ["loadedmetadata", "durationchange", "seeked", "ratechange"]) audio.addEventListener(ev, position);
      audio.addEventListener("play", () => { ms.playbackState = "playing"; });
      audio.addEventListener("pause", () => { ms.playbackState = "paused"; });
    },
  };
})();
