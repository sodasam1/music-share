# music-share

Put audio drafts on the web and send someone a link. One owner (you), anonymous listeners (a link, no account, no comments, no versions).
Built on the shared kit from the `personal-site` repo (installer, one site-wide login, the Minimal look, waveforms). Tech Independence
rules: your audio stays as plain files, metadata in one SQLite file, Flask + waitress + ffmpeg only, uploaded by hand with FreeFileSync.

## What it does
- **Studio** (`/music/studio`, owner only): drag in audio files; each gets a waveform, a title (from the file name), notes, and a secret link.
  Per track: **Listed** (also on the public page) or **Unlisted** (only people with the link). Copy link, Open, Edit, Archive, New link
  (revokes the old one), Delete forever (type the exact title). Playlists: ordered, own link, own listed/unlisted, archive.
  See how often a link was opened (your own visits are not counted).
- **Public** (no login): `/music/` lists what you marked Listed (tracks and playlists). `/music/t/<token>` plays one track,
  `/music/p/<token>` one playlist. Share pages are `noindex`. A playlist link never reveals its tracks' own links.
- **Backup / restore**: "Back up now" (database copy + every original, copied once); restore into an **empty** library from
  `/mnt/data/apps/music-share/restore`. Same shape as riffbox's.

## Decisions to know
- Links are 128 random bits. Anyone holding a link can play (and, if allowed, download) that item. No cookie, no account.
- **Archive** hides an item from lists and the Studio's main view; its link keeps working (set `MUSICSHARE_ARCHIVE_DISABLES_LINK=1` to turn the link off too). "New link" is the way to revoke.
- Originals are never modified. Apple Lossless (ALAC) `.m4a` gets a FLAC play copy because Chrome and Firefox cannot play ALAC.
- A "Download" link appears on single-track and playlist pages (`MUSICSHARE_SHOW_DOWNLOAD=0` removes it).
- Not built: comments, versions (that is the Version Control app), export to a zip/folder (backup/restore only).

## Settings (`/etc/musicshare.conf`, written by the installer)
`MUSICSHARE_DATA_DIR`, `MUSICSHARE_BASE_PATH` (`/music`), `MUSICSHARE_STATIC_DIR`, `MUSICSHARE_BACKUP_DIR`, `MUSICSHARE_RESTORE_DIR`,
`MUSICSHARE_MAX_UPLOAD_MB` (300), `MUSICSHARE_SHOW_DOWNLOAD` (1), `MUSICSHARE_ARCHIVE_DISABLES_LINK` (0), `MUSICSHARE_SITE_KEY_FILE`.

## Install (after the site login: personal-site `RESTORE.html` B2)
Data lives on the separate volume mounted at `/mnt/data` (folders `music-share/{data,backup,restore}`); the installer refuses to run until that volume is mounted and holds `/mnt/data/.volume-ok`. Program files stay on the boot disk.
1. Upload this folder to `~/staging/music-share` on the server (FreeFileSync, as for riffbox; no `tests/` needed).
2. `doas sh ~/staging/music-share/deploy/install.sh --check` (changes nothing), then `doas sh ~/staging/music-share/deploy/install.sh`. It installs the packages, makes the
   `_musicshare` user and folders, writes the service script and `/etc/musicshare.conf`, registers with the site login (own key), and asks `y` before adding
   `/music` routes to `/etc/relayd.conf`.
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
