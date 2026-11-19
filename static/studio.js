"use strict";
// The owner's page. Everything user-written is put on the page with textContent (never innerHTML), so a title cannot run script.
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const audio = new Audio();
  audio.preload = "none";
  let tab = "tracks", tracks = [], playlists = [], playing = null, backup = null;
  const waves = new Map();

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const k in attrs || {}) {
      if (k === "class") el.className = attrs[k];
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== false && attrs[k] != null) el.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    }
    for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }
  const notice = (msg) => { const n = $("#notice"); n.textContent = msg || ""; n.hidden = !msg; };
  const fmt = (s) => (s ? Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0") : "");
  const full = (path) => location.origin + path;

  async function api(path, opts) {
    const r = await fetch("api/" + path, { credentials: "same-origin", ...opts });
    if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname); throw new Error("signed out"); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || "error " + r.status);
    return data;
  }
  const send = (path, method, body) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  async function run(fn) { try { notice(""); await fn(); } catch (e) { if (e.message !== "signed out") notice(e.message); } }

  // Show grey placeholders where the rows will be, so the page has its shape before the data arrives.
  function showSkeleton() {
    const main = $("#main"); main.textContent = ""; waves.clear();
    main.setAttribute("aria-busy", "true");
    for (let i = 0; i < 3; i++) main.append(h("div", { class: "skeleton", "aria-hidden": "true" }, h("div", { class: "sk-line" }), h("div", { class: "sk-wave" })));
  }
  // Fetch only what the current tab shows (all of it at once), then draw. `fresh` = first load or a tab change: show placeholders meanwhile.
  // After an action (edit, archive...) the old rows stay on screen until the new ones are ready, so nothing flashes.
  async function load(fresh) {
    const want = tab;
    if (fresh) showSkeleton();
    if (tab === "tracks" || tab === "archive") tracks = await api("tracks" + (tab === "archive" ? "?archived=1" : ""));
    else if (tab === "playlists") { [playlists, allTracks] = await Promise.all([api("playlists"), api("tracks")]); tracks = allTracks; }
    else backup = await api("backup");
    if (want !== tab) return;                  // you switched tabs while this was loading: the newer load draws
    render();
  }

  // The tab buttons are in studio.html, so they are on screen before this script runs. Here they only get their clicks.
  function markTabs() { for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-pressed", String(b.dataset.tab === tab)); }
  for (const b of document.querySelectorAll("#tabs button")) b.addEventListener("click", () => { tab = b.dataset.tab; markTabs(); run(() => load(true)); });

  function play(t, at) {
    if (playing !== t.id) { playing = t.id; audio.src = t.audio; if (at) audio.addEventListener("loadedmetadata", () => { audio.currentTime = at; }, { once: true }); }
    else if (at != null) audio.currentTime = at;
    audio.play().catch(() => {});
  }
  audio.addEventListener("timeupdate", () => { const w = waves.get(playing); if (w) w.set({ time: audio.currentTime, duration: audio.duration || 0 }); });

  // A waveform is drawn only when its row is near the screen (a long list does not pay for rows far below).
  const seen = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { seen.unobserve(e.target); const f = e.target._draw; if (f) { e.target._draw = null; f(); } }
  }, { rootMargin: "400px" }) : null;
  function lazyWave(canvas, draw) { if (!seen) { draw(); return; } canvas._draw = draw; seen.observe(canvas); }

  function trackRow(t) {
    const canvas = h("canvas", { class: "wave" });
    const title = h("span", { class: "title" }, t.title);
    const state = t.analysis_status === "pending" ? "measuring waveform…" : t.analysis_status === "failed" ? "waveform unavailable" : "";
    const opened = t.open_count ? "opened " + t.open_count + "x, last " + (t.last_opened_at || "").slice(0, 10) : "not opened yet";
    const row = h("article", { class: "item" },
      h("div", { class: "row" },
        h("button", { class: "play", type: "button", "aria-label": "Play " + t.title, onclick: () => play(t) }, "▶"),
        h("div", { class: "grow" }, title, h("div", { class: "muted small" }, [t.created_at.slice(0, 10), fmt(t.duration_s), t.visibility, t.archived ? "archived" : "", opened, state].filter(Boolean).join(" · ")))),
      canvas,
      h("div", { class: "actions" },
        h("button", { type: "button", onclick: () => run(async () => { await navigator.clipboard.writeText(full(t.link)); notice("Link copied."); }) }, "Copy link"),
        h("a", { class: "btn", href: t.link, target: "_blank", rel: "noopener" }, "Open"),
        h("button", { type: "button", onclick: () => edit(t, row) }, "Edit"),
        t.archived
          ? h("button", { type: "button", onclick: () => run(async () => { await send(`tracks/${t.id}/unarchive`, "POST"); await load(); }) }, "Unarchive")
          : h("button", { type: "button", onclick: () => run(async () => { await send(`tracks/${t.id}/archive`, "POST"); await load(); }) }, "Archive"),
        h("button", { type: "button", onclick: () => run(async () => { if (!confirm("Make a new link? The old link will stop working.")) return; await send(`tracks/${t.id}/new-link`, "POST"); await load(); }) }, "New link"),
        h("button", { type: "button", onclick: () => run(async () => { const typed = prompt("This deletes the audio forever. Type the exact title to confirm:\n\n" + t.title); if (typed == null) return; await send(`tracks/${t.id}`, "DELETE", { confirm: typed }); await load(); }) }, "Delete forever")));
    if (t.notes) row.insertBefore(h("p", { class: "muted notes" }, t.notes), $(".actions", row));
    lazyWave(canvas, () => waves.set(t.id, window.Wave.attach(canvas, { peaks: t.peaks, duration: t.duration_s || 0, onSeek: (s) => play(t, s) })));
    return row;
  }

  function edit(t, row) {
    const title = h("input", { type: "text", value: t.title, maxlength: "200", "aria-label": "Title" });
    const notes = h("textarea", { rows: "3", maxlength: "2000", "aria-label": "Notes" }, t.notes);
    const vis = h("select", { "aria-label": "Visibility" }, h("option", { value: "unlisted" }, "Unlisted: only people with the link"), h("option", { value: "listed" }, "Listed: also on the public page"));
    vis.value = t.visibility;
    const form = h("div", { class: "edit" }, title, notes, vis, h("div", { class: "actions" },
      h("button", { class: "primary", type: "button", onclick: () => run(async () => { await send(`tracks/${t.id}`, "PATCH", { title: title.value, notes: notes.value, visibility: vis.value }); await load(); }) }, "Save"),
      h("button", { type: "button", onclick: () => run(load) }, "Cancel")));
    row.insertBefore(form, $(".actions", row));
  }

  function uploadBox() {
    const input = h("input", { type: "file", multiple: true, accept: "audio/*,.wav,.mp3,.m4a,.flac,.ogg,.aac,.aif,.aiff,.caf", hidden: true });
    const vis = h("select", { "aria-label": "Visibility of new uploads" }, h("option", { value: "unlisted" }, "Unlisted"), h("option", { value: "listed" }, "Listed"));
    async function go(files) {
      if (!files.length) return;
      notice("Uploading " + files.length + " file(s)…");
      const fd = new FormData();
      for (const f of files) fd.append("files", f);
      fd.append("visibility", vis.value);
      await run(async () => {
        const r = await fetch("api/tracks", { method: "POST", body: fd, credentials: "same-origin" });
        const data = await r.json().catch(() => ({}));
        if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname); return; }
        notice((data.problems || []).join(" ") || (r.ok ? "Uploaded." : data.error || "Upload failed"));
        await load();
        setTimeout(() => run(load), 4000);                                  // waveforms are measured in the background
      });
    }
    const drop = h("div", { class: "drop", tabindex: "0", role: "button", onclick: () => input.click(), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") input.click(); } },
      "Drop audio files here, or click to choose. New uploads are: ", vis);
    vis.addEventListener("click", (e) => e.stopPropagation());
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); go(Array.from(e.dataTransfer.files)); });
    input.addEventListener("change", () => go(Array.from(input.files)));
    return h("div", null, drop, input);
  }

  function playlistCard(p) {
    const byId = new Map(tracks.concat(allTracks).map((t) => [t.id, t]));
    let ids = p.track_ids.slice();
    const list = h("ul", { class: "pl-items" });
    const add = h("select", { "aria-label": "Add a track" });
    function paint() {
      list.textContent = ""; add.textContent = "";
      add.append(h("option", { value: "" }, "Add a track…"));
      for (const t of allTracks) if (!ids.includes(t.id)) add.append(h("option", { value: String(t.id) }, t.title));
      ids.forEach((id, i) => {
        const t = byId.get(id);
        list.append(h("li", null, h("span", null, t ? t.title : "(missing track " + id + ")"),
          h("button", { type: "button", "aria-label": "Move up", disabled: i === 0, onclick: () => { [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]]; paint(); } }, "↑"),
          h("button", { type: "button", "aria-label": "Move down", disabled: i === ids.length - 1, onclick: () => { [ids[i + 1], ids[i]] = [ids[i], ids[i + 1]]; paint(); } }, "↓"),
          h("button", { type: "button", "aria-label": "Remove", onclick: () => { ids.splice(i, 1); paint(); } }, "×")));
      });
    }
    add.addEventListener("change", () => { if (add.value) { ids.push(Number(add.value)); paint(); } });
    paint();
    const opened = p.open_count ? "opened " + p.open_count + "x, last " + (p.last_opened_at || "").slice(0, 10) : "not opened yet";
    return h("article", { class: "item" },
      h("div", { class: "row" }, h("div", { class: "grow" }, h("span", { class: "title" }, p.title), h("div", { class: "muted small" }, [p.visibility, p.archived ? "archived" : "", opened].filter(Boolean).join(" · ")))),
      p.notes ? h("p", { class: "muted notes" }, p.notes) : null, list, add,
      h("div", { class: "actions" },
        h("button", { class: "primary", type: "button", onclick: () => run(async () => { await send(`playlists/${p.id}/items`, "PUT", { track_ids: ids }); notice("Saved."); await load(); }) }, "Save order"),
        h("button", { type: "button", onclick: () => run(async () => { await navigator.clipboard.writeText(full(p.link)); notice("Link copied."); }) }, "Copy link"),
        h("a", { class: "btn", href: p.link, target: "_blank", rel: "noopener" }, "Open"),
        h("button", { type: "button", onclick: () => run(async () => { const title = prompt("Title", p.title); if (title == null) return; const notes = prompt("Notes (optional)", p.notes); await send(`playlists/${p.id}`, "PATCH", { title, notes: notes == null ? p.notes : notes }); await load(); }) }, "Rename"),
        h("button", { type: "button", onclick: () => run(async () => { await send(`playlists/${p.id}`, "PATCH", { visibility: p.visibility === "listed" ? "unlisted" : "listed" }); await load(); }) }, p.visibility === "listed" ? "Make unlisted" : "Make listed"),
        p.archived
          ? h("button", { type: "button", onclick: () => run(async () => { await send(`playlists/${p.id}/unarchive`, "POST"); await load(); }) }, "Unarchive")
          : h("button", { type: "button", onclick: () => run(async () => { await send(`playlists/${p.id}/archive`, "POST"); await load(); }) }, "Archive"),
        h("button", { type: "button", onclick: () => run(async () => { if (!confirm("Make a new link? The old link will stop working.")) return; await send(`playlists/${p.id}/new-link`, "POST"); await load(); }) }, "New link"),
        h("button", { type: "button", onclick: () => run(async () => { const typed = prompt("This deletes the playlist (not its tracks). Type the exact title to confirm:\n\n" + p.title); if (typed == null) return; await send(`playlists/${p.id}`, "DELETE", { confirm: typed }); await load(); }) }, "Delete playlist")));
  }
  let allTracks = [];

  function backupPanel() {
    if (!backup || !backup.enabled) return h("p", { class: "muted" }, "No backup folder is set on the server (MUSICSHARE_BACKUP_DIR).");
    const rest = backup.restore || {};
    return h("div", null,
      h("p", null, backup.copies + " database copy(ies), newest: " + (backup.newest || "none") + "; " + backup.audio_files + " audio file(s) backed up."),
      h("button", { class: "primary", type: "button", onclick: () => run(async () => { const r = await send("backup", "POST"); notice("Backed up " + r.copy + ", " + r.audio_copied + " new audio file(s)." + (r.problems.length ? " Problems: " + r.problems.join("; ") : "")); await load(); }) }, "Back up now"),
      rest.ok ? h("div", null, h("h2", null, "Restore"), h("p", null, "Found a backup with " + rest.tracks + " track(s) in the restore folder. Restoring works only into an empty library."),
        h("button", { type: "button", onclick: () => run(async () => { const r = await send("restore", "POST"); notice("Restored " + r.tracks + " track(s)." + (r.problems.length ? " Problems: " + r.problems.join("; ") : "")); tab = "tracks"; await load(); }) }, "Restore from this folder")) : null);
  }

  function render() {
    markTabs();
    const main = $("#main"); main.textContent = ""; waves.clear(); main.removeAttribute("aria-busy");
    if (tab === "tracks" || tab === "archive") {
      if (tab === "tracks") main.append(uploadBox());
      if (!tracks.length) main.append(h("p", { class: "muted" }, tab === "tracks" ? "No tracks yet. Drop a file above." : "Nothing archived."));
      for (const t of tracks) main.append(trackRow(t));
    } else if (tab === "playlists") {
      main.append(h("button", { class: "primary", type: "button", onclick: () => run(async () => { const title = prompt("Playlist title"); if (!title) return; await send("playlists", "POST", { title }); await load(); }) }, "New playlist"));
      if (!playlists.length) main.append(h("p", { class: "muted" }, "No playlists yet."));
      for (const p of playlists) main.append(playlistCard(p));
    } else main.append(backupPanel());
  }

  run(() => load(true));
})();
