# music-share

Put audio drafts on the web and send someone a link. One owner (you), anonymous listeners (a link, no account, no comments, no versions).
Built on the shared kit from the `personal-site` repo (installer, one site-wide login, the Minimal look, waveforms). Tech Independence
rules: your audio stays as plain files, metadata in one SQLite file, Flask + waitress + ffmpeg only, uploaded by hand with FreeFileSync.

## What it does
- **Studio** (`/music-share/studio`, owner only): drag in audio files; each gets a waveform, a title (from the file name), notes, and a secret link.
  Per track: **Listed** (also on the public page) or **Unlisted** (only people with the link). Copy link, Open, Edit, Archive, New link
  (revokes the old one), Delete forever (type the exact title). Playlists: ordered, own link, own listed/unlisted, archive.
  See how often a link was opened (your own visits are not counted).
- **Public** (no login): `/music-share/` lists what you marked Listed (when you are signed in it also shows a small "Studio" link at the bottom; signed out, bookmark `/music-share/studio`, which sends you to the login first) (tracks and playlists). `/music-share/t/<token>` plays one track,
  `/music-share/p/<token>` one playlist. Share pages are `noindex`. A playlist link never reveals its tracks' own links.
- **Backup / restore**: "Back up now" (database copy + every original, copied once); restore into an **empty** library from
  `/mnt/data/apps/music-share/restore`. Same shape as riffbox's.

## Decisions to know
- Links are 128 random bits. Anyone holding a link can play (and, if allowed, download) that item. No cookie, no account.
- **Archive** hides an item from lists and the Studio's main view; its link keeps working (set `MUSICSHARE_ARCHIVE_DISABLES_LINK=1` to turn the link off too). "New link" is the way to revoke.
- Originals are never modified. Apple Lossless (ALAC) `.m4a` gets a FLAC play copy because Chrome and Firefox cannot play ALAC.
- A "Download" link appears on single-track and playlist pages (`MUSICSHARE_SHOW_DOWNLOAD=0` removes it).
- Not built: comments, versions (that is the Version Control app), export to a zip/folder (backup/restore only).

## Settings (`/etc/musicshare.conf`, written by the installer)
`MUSICSHARE_DATA_DIR`, `MUSICSHARE_BASE_PATH` (`/music-share`), `MUSICSHARE_STATIC_DIR`, `MUSICSHARE_BACKUP_DIR`, `MUSICSHARE_RESTORE_DIR`,
`MUSICSHARE_MAX_UPLOAD_MB` (300), `MUSICSHARE_SHOW_DOWNLOAD` (1), `MUSICSHARE_ARCHIVE_DISABLES_LINK` (0), `MUSICSHARE_SITE_KEY_FILE`.

## Install (after the site login: personal-site `RESTORE.html` B2)
Data lives on the separate volume mounted at `/mnt/data` (folders `music-share/{data,backup,restore}`); the installer refuses to run until that volume is mounted and holds `/mnt/data/.volume-ok`. Program files stay on the boot disk.
1. Upload this folder to `~/staging/music-share` on the server (FreeFileSync, as for riffbox; no `tests/` needed).
2. `doas sh ~/staging/music-share/deploy/install.sh --check` (changes nothing), then `doas sh ~/staging/music-share/deploy/install.sh`. It installs the packages, makes the
   `_musicshare` user and folders, writes the service script and `/etc/musicshare.conf`, registers with the site login (own key), and asks `y` before adding
   `/music-share` routes to `/etc/relayd.conf`.
3. Everyday updates: upload again, `doas sh ~/staging/music-share/deploy/install.sh --app-files-only`.
4. Pull `/mnt/data/apps/music-share/backup` to your computer with FreeFileSync (like riffbox's backups).

## Tests (offline)
```
python3 -m venv /tmp/v && /tmp/v/bin/pip install flask==3.1.3 werkzeug==3.1.9 itsdangerous==2.2.0 waitress==3.0.2 pytest
/tmp/v/bin/python -m pytest -q tests                              # 30 tests (needs ffmpeg for the waveform ones)
VENV_PY=/tmp/v/bin/python sh tests/installer/test-install.sh      # the installer, in test mode, on a copy of your relayd.conf
MS_PY=/tmp/v/bin/python node tests/e2e/smoke.cjs                  # real browser (Playwright): studio, public pages, no console/CSP errors
```
The kit under `app/siteauth`, `app/kitaudio`, `static/minimal`, `static/wave`, `deploy/appkit.sh` is vendored from personal-site (`kit.use` lists the parts;
`sh kit/sync-into.sh` there refreshes it, `sh kit/check-vendored.sh` there checks for drift).

## Why the Studio shows its menu at once (and how to see where time goes)
The menu and grey placeholder rows are part of `studio.html`, so they appear as soon as that one small file arrives. The page then makes **one** request for what the current tab needs
(it used to make four in a row before drawing anything), and audio is never loaded until you press play. Waveforms are drawn only for rows near the screen.
To see whether a slow page is the server or the network: open the browser's developer tools, Network tab, click a request, Timing. The **Server-Timing: app** line is the time this
server spent on it. Program files (`/usr/local/music-share/static`) are on the boot disk; only the database and audio are on the data volume.
`MS_PY=... node tests/e2e/first-paint.cjs` times the Studio with an artificial API delay (before: menu at 1367 ms with a 300 ms delay; after: 67 ms).

## Look and feel (branch `music-share-soundcloud-style-polish-and-speed`)
Round play buttons, chunky waveforms with the played part in your accent colour, the times at the ends, a bar at the bottom that follows what is playing (pause, seek, space bar), a bigger single-track page, and the same
header, logo and favicon as Version Control. `static/listen.css` is that shared look: **the same file lives in both repos (music-share and version-control); keep them identical.** There are no social parts and there will not be:
no likes, follows, comments, counts shown to listeners, discovery or sign-ups. The only numbers (opened, played) are for you, in the Studio.
- **Played vs opened.** "Opened" counts people opening a page; chat apps that fetch the page to build a link preview (and HEAD requests) are ignored. "Played" counts a listener starting a track (the audio is requested from its first byte), once per listener per ten minutes; seeking, you, and robots do not count.
- Link previews: pages carry a title and your notes as the preview text, nothing else (no image).
- Studio: upload progress bar; per-track "played N times, last <date>".
- Speed: each static file is read once, gzipped once and kept in memory; pages ask for `/static/x.css?v=<hash>` which browsers keep for a year (the address changes when the file does). Waveform data on list pages is halved (100 bars).
- Upgrade: the database is upgraded in place on start (two new columns); restoring an older backup upgrades it too.

## Speed

See `docs/SPEED.md`: running the Speed check in the Studio and moving the database to the boot disk.
