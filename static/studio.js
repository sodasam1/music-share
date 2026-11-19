"use strict";
// The owner's page. Everything user-written is put on the page with textContent (never innerHTML), so a title cannot run script.
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const audio = new Audio();
  audio.preload = "none";
  let tab = "tracks", tracks = [], playlists = [], allTracks = [], backup = null, playing = null;
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
  const notice = (m) => { const n = $("#notice"); n.textContent = m || ""; n.hidden = !m; };
  const fmt = (s) => (s ? Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0") : "");
  const full = (path) => location.origin + path;
  const day = (iso) => (iso || "").slice(0, 10);
  const login = () => { location.href = "/login?next=" + encodeURIComponent(location.pathname); };

  async function api(path, opts) {
    const r = await fetch("api/" + path, { credentials: "same-origin", ...opts });
    if (r.status === 401) { login(); throw new Error("signed out"); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || "error " + r.status);
    return data;
  }
  const send = (path, method, body) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  async function run(fn) { try { notice(""); await fn(); } catch (e) { if (e.message !== "signed out") notice(e.message); } }
  // a button that does something, then reloads the list
  const act = (label, fn, cls) => h("button", { type: "button", class: cls || false, onclick: () => run(async () => { await fn(); }) }, label);
  const copyLink = (path) => async () => { await navigator.clipboard.writeText(full(path)); notice("Link copied."); };
  const confirmed = (text, fn) => async () => { if (confirm(text)) await fn(); };
  const typed = (text, title, fn) => async () => { const t = prompt(text + "\n\n" + title); if (t != null) await fn(t); };

  // ---- loading: only what the tab needs, all at once; placeholders first -------------------------------------------
  function showSkeleton() {
    const main = $("#main"); main.textContent = ""; waves.clear(); main.setAttribute("aria-busy", "true");
    for (let i = 0; i < 3; i++) main.append(h("div", { class: "skeleton", "aria-hidden": "true" }, h("div", { class: "sk-line" }), h("div", { class: "sk-wave" })));
  }
  async function load(fresh) {
    const want = tab;
    if (fresh) showSkeleton();
    if (tab === "tracks" || tab === "archive") tracks = await api("tracks" + (tab === "archive" ? "?archived=1" : ""));
    else if (tab === "playlists") { [playlists, allTracks] = await Promise.all([api("playlists"), api("tracks")]); tracks = allTracks; }
    else backup = await api("backup");
    if (want === tab) render();
  }
  const reload = () => load(false);
  function markTabs() { for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-pressed", String(b.dataset.tab === tab)); }
  for (const b of document.querySelectorAll("#tabs button")) b.addEventListener("click", () => { tab = b.dataset.tab; markTabs(); run(() => load(true)); });

  // ---- playing in the Studio ---------------------------------------------------------------------------------------
  function paintPlay() { for (const b of document.querySelectorAll(".play[data-id]")) b.dataset.state = Number(b.dataset.id) === playing && !audio.paused ? "pause" : "play"; }
  function play(t, at) {
    if (playing !== t.id) { playing = t.id; audio.src = t.audio; if (at) audio.addEventListener("loadedmetadata", () => { audio.currentTime = at; }, { once: true }); }
    else if (at != null) audio.currentTime = at;
    audio.play().catch(() => {});
  }
  audio.addEventListener("timeupdate", () => { const w = waves.get(playing); if (w) w.set({ time: audio.currentTime, duration: audio.duration || 0 }); });
  audio.addEventListener("play", paintPlay); audio.addEventListener("pause", paintPlay);

  // a waveform is drawn only when its row is near the screen
  const seen = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { seen.unobserve(e.target); const f = e.target._draw; if (f) { e.target._draw = null; f(); } }
  }, { rootMargin: "400px" }) : null;
  const lazyWave = (canvas, draw) => { if (!seen) draw(); else { canvas._draw = draw; seen.observe(canvas); } };
  const fit = (p, n) => (p.length <= n ? p : Array.from({ length: n }, (_, i) => Math.max(...p.slice(Math.floor(i * p.length / n), Math.floor((i + 1) * p.length / n)))));

  // ---- one track ----------------------------------------------------------------------------------------------------
  function trackRow(t) {
    const canvas = h("canvas", { class: "wave" });
    const facts = [day(t.created_at), fmt(t.duration_s), t.visibility, t.archived ? "archived" : "",
      t.play_count ? "played " + t.play_count + "x, last " + day(t.last_played_at) : "not played yet", t.open_count ? "opened " + t.open_count + "x" : "",
      t.analysis_status === "pending" ? "measuring waveform…" : t.analysis_status === "failed" ? "waveform unavailable" : ""].filter(Boolean).join(" · ");
    const row = h("article", { class: "item track" },
      h("div", { class: "track-head" },
        h("button", { class: "play play-btn", type: "button", "data-id": String(t.id), "data-state": "play", "aria-label": "Play " + t.title, onclick: () => (playing === t.id && !audio.paused ? audio.pause() : play(t)) }),
        h("div", { class: "track-text" }, h("span", { class: "title" }, t.title), h("div", { class: "track-meta" }, facts))),
      h("div", { class: "wave-box" }, canvas),
      t.notes ? h("p", { class: "muted notes" }, t.notes) : null,
      h("div", { class: "actions" },
        act("Copy link", copyLink(t.link)),
        h("a", { class: "btn", href: t.link, target: "_blank", rel: "noopener" }, "Open"),
        h("button", { type: "button", onclick: () => edit(t, row) }, "Edit"),
        act(t.archived ? "Unarchive" : "Archive", async () => { await send(`tracks/${t.id}/${t.archived ? "unarchive" : "archive"}`, "POST"); await reload(); }),
        act("New link", confirmed("Make a new link? The old link will stop working.", async () => { await send(`tracks/${t.id}/new-link`, "POST"); await reload(); })),
        act("Delete forever", typed("This deletes the audio forever. Type the exact title to confirm:", t.title, async (v) => { await send(`tracks/${t.id}`, "DELETE", { confirm: v }); await reload(); }))));
    lazyWave(canvas, () => {
      const n = Math.max(24, Math.floor(canvas.clientWidth / 4));
      waves.set(t.id, window.Wave.attach(canvas, { peaks: fit(t.peaks, n), duration: t.duration_s || 0, onSeek: (s) => play(t, s) }));
    });
    return row;
  }

  function edit(t, row) {
    const title = h("input", { type: "text", value: t.title, maxlength: "200", "aria-label": "Title" });
    const notes = h("textarea", { rows: "3", maxlength: "2000", "aria-label": "Notes" }, t.notes);
    const vis = h("select", { "aria-label": "Visibility" }, h("option", { value: "unlisted" }, "Unlisted: only people with the link"), h("option", { value: "listed" }, "Listed: also on the public page"));
    vis.value = t.visibility;
    row.insertBefore(h("div", { class: "edit" }, title, notes, vis, h("div", { class: "actions" },
      act("Save", async () => { await send(`tracks/${t.id}`, "PATCH", { title: title.value, notes: notes.value, visibility: vis.value }); await reload(); }, "primary"),
      act("Cancel", reload))), $(".actions", row));
  }

  // ---- uploading, with a progress bar -----------------------------------------------------------------------------
  function uploadBox() {
    const input = h("input", { type: "file", multiple: true, accept: "audio/*,.wav,.mp3,.m4a,.flac,.ogg,.aac,.aif,.aiff,.caf", hidden: true });
    const vis = h("select", { "aria-label": "Visibility of new uploads" }, h("option", { value: "unlisted" }, "Unlisted"), h("option", { value: "listed" }, "Listed"));
    const bar = h("progress", { max: "100", value: "0", hidden: true, "aria-label": "Upload progress" });
    function go(files) {
      if (!files.length) return;
      const fd = new FormData();
      for (const f of files) fd.append("files", f);
      fd.append("visibility", vis.value);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "api/tracks"); xhr.withCredentials = true;
      bar.hidden = false; bar.value = 0; notice("Uploading " + files.length + " file(s)…");
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) { bar.value = (e.loaded / e.total) * 100; notice("Uploading… " + Math.round((e.loaded / e.total) * 100) + "%"); } };
      xhr.onerror = () => { bar.hidden = true; notice("The upload did not go through. Check your connection and try again."); };
      xhr.onload = () => {
        bar.hidden = true;
        if (xhr.status === 401) return login();
        let data = {}; try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
        notice((data.problems || []).join(" ") || (xhr.status < 300 ? "Uploaded. Waveforms are measured in the background." : data.error || "Upload failed (" + xhr.status + ")"));
        run(reload); setTimeout(() => run(reload), 4000);
      };
      xhr.send(fd);
    }
    const drop = h("div", { class: "drop", tabindex: "0", role: "button", onclick: () => input.click(), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") input.click(); } },
      "Drop audio files here, or click to choose. New uploads are ", vis);
    vis.addEventListener("click", (e) => e.stopPropagation());
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); go(Array.from(e.dataTransfer.files)); });
    input.addEventListener("change", () => go(Array.from(input.files)));
    return h("div", null, drop, input, bar);
  }

  // ---- playlists ----------------------------------------------------------------------------------------------------------
  function playlistCard(p) {
    const byId = new Map(allTracks.map((t) => [t.id, t]));
    const ids = p.track_ids.slice();
    const list = h("ul", { class: "pl-items" }), add = h("select", { "aria-label": "Add a track" });
    const move = (i, d) => { [ids[i + d], ids[i]] = [ids[i], ids[i + d]]; paint(); };
    function paint() {
      list.textContent = ""; add.textContent = "";
      add.append(h("option", { value: "" }, "Add a track…"));
      for (const t of allTracks) if (!ids.includes(t.id)) add.append(h("option", { value: String(t.id) }, t.title));
      ids.forEach((id, i) => list.append(h("li", null, h("span", null, byId.has(id) ? byId.get(id).title : "(missing track " + id + ")"),
        h("button", { type: "button", "aria-label": "Move up", disabled: i === 0, onclick: () => move(i, -1) }, "↑"),
        h("button", { type: "button", "aria-label": "Move down", disabled: i === ids.length - 1, onclick: () => move(i, 1) }, "↓"),
        h("button", { type: "button", "aria-label": "Remove", onclick: () => { ids.splice(i, 1); paint(); } }, "×"))));
    }
    add.addEventListener("change", () => { if (add.value) { ids.push(Number(add.value)); paint(); } });
    paint();
    const facts = [p.visibility, p.archived ? "archived" : "", p.open_count ? "opened " + p.open_count + "x, last " + day(p.last_opened_at) : "not opened yet"].filter(Boolean).join(" · ");
    return h("article", { class: "item track" },
      h("div", { class: "track-text" }, h("span", { class: "title" }, p.title), h("div", { class: "track-meta" }, facts)),
      p.notes ? h("p", { class: "muted notes" }, p.notes) : null, list, add,
      h("div", { class: "actions" },
        act("Save order", async () => { await send(`playlists/${p.id}/items`, "PUT", { track_ids: ids }); notice("Saved."); await reload(); }, "primary"),
        act("Copy link", copyLink(p.link)),
        h("a", { class: "btn", href: p.link, target: "_blank", rel: "noopener" }, "Open"),
        act("Rename", async () => { const title = prompt("Title", p.title); if (title == null) return; const notes = prompt("Notes (optional)", p.notes); await send(`playlists/${p.id}`, "PATCH", { title, notes: notes == null ? p.notes : notes }); await reload(); }),
        act(p.visibility === "listed" ? "Make unlisted" : "Make listed", async () => { await send(`playlists/${p.id}`, "PATCH", { visibility: p.visibility === "listed" ? "unlisted" : "listed" }); await reload(); }),
        act(p.archived ? "Unarchive" : "Archive", async () => { await send(`playlists/${p.id}/${p.archived ? "unarchive" : "archive"}`, "POST"); await reload(); }),
        act("New link", confirmed("Make a new link? The old link will stop working.", async () => { await send(`playlists/${p.id}/new-link`, "POST"); await reload(); })),
        act("Delete playlist", typed("This deletes the playlist (not its tracks). Type the exact title to confirm:", p.title, async (v) => { await send(`playlists/${p.id}`, "DELETE", { confirm: v }); await reload(); }))));
  }

  function backupPanel() {
    if (!backup || !backup.enabled) return h("p", { class: "muted" }, "No backup folder is set on the server (MUSICSHARE_BACKUP_DIR).");
    const rest = backup.restore || {}, problems = (r) => (r.problems.length ? " Problems: " + r.problems.join("; ") : "");
    return h("div", null,
      h("p", null, backup.copies + " database copy(ies), newest: " + (backup.newest || "none") + "; " + backup.audio_files + " audio file(s) backed up."),
      act("Back up now", async () => { const r = await send("backup", "POST"); notice("Backed up " + r.copy + ", " + r.audio_copied + " new audio file(s)." + problems(r)); await reload(); }, "primary"),
      rest.ok ? h("div", null, h("h2", { class: "section" }, "Restore"), h("p", null, "Found a backup with " + rest.tracks + " track(s) in the restore folder. Restoring works only into an empty library."),
        act("Restore from this folder", async () => { const r = await send("restore", "POST"); notice("Restored " + r.tracks + " track(s)." + problems(r)); tab = "tracks"; await load(true); })) : null);
  }

  function render() {
    markTabs();
    const main = $("#main"); main.textContent = ""; waves.clear(); main.removeAttribute("aria-busy");
    if (tab === "tracks" || tab === "archive") {
      if (tab === "tracks") main.append(uploadBox());
      if (!tracks.length) main.append(h("p", { class: "empty" }, tab === "tracks" ? "No tracks yet. Drop a file above." : "Nothing archived."));
      for (const t of tracks) main.append(trackRow(t));
    } else if (tab === "playlists") {
      main.append(act("New playlist", async () => { const title = prompt("Playlist title"); if (!title) return; await send("playlists", "POST", { title }); await reload(); }, "primary"));
      if (!playlists.length) main.append(h("p", { class: "empty" }, "No playlists yet."));
      for (const p of playlists) main.append(playlistCard(p));
    } else main.append(backupPanel());
    paintPlay();
  }

  run(() => load(true));
})();
