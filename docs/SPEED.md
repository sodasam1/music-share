# Speed: the Speed check, and moving the database to the boot disk

Why this exists: on the owner's server a durable write on the data volume took **1285.7 ms** and on the boot disk **1.9 ms** (measured 2026-11-19). Anything that waits for the disk to confirm a write feels slow on the volume. The audio does not need fast writes; the small database does.
(The same text also lives in the version-control repo, `docs/DEPLOY.md` sections 9 and 10; this copy is the one to keep next to the code.)

## 1. Run the Speed check (after the new code is on the server)
The check only has the new "A database save on ..." lines once this version is **deployed**; merging on GitHub does not change the server.

**Deploy the code (about 3 minutes):**
1. On your computer, download the ZIP of `main` from GitHub (branch selector, Code, Download ZIP), unpack it, rename the folder to `music-share`, and put it at `server-mirror\home\me\staging\music-share` (replace the old contents).
2. Run `Deploy.ffs_gui`, press **Compare**, read the preview (only music-share files should change), **Synchronize**.
3. On the server:
```
doas sh ~/staging/music-share/deploy/install.sh --check
doas sh ~/staging/music-share/deploy/install.sh --app-files-only
```
`--check` prints what it would do and changes nothing. `--app-files-only` copies only the program files and restarts music-share (a few seconds); it touches no system file.

**Run the check:** `https://sanjaysubramanian.com/music-share/studio` -> **Backup** tab -> scroll to **Speed check** -> **Run speed check**.
Run it three times: idle, right after an upload (while the track is being measured), and when the public page felt slow.

**Read it:**
| Line | Meaning |
|---|---|
| Saving to the data volume / boot disk | raw create + write + wait-for-disk (what you measured before) |
| A database save on the data volume / boot disk | a real SQLite save with this app's settings. First number: the wait for a normal save. Second: the background step that reaches the disk |
| Reading the database (cold) | over 300 ms means reads are slow too |
| Server load / Swap | CPU and memory pressure |

Decision: if "A database save on the data volume" is slow (over about 150 ms either number) and the boot disk's is fast, move the database (section 2). If a normal save is already fast (a few ms) on the volume, the move still helps the background step but is less urgent.

## 2. Move only the database to the boot disk
Needs: the app installed and working, a copy of your latest backup pulled to your computer (T-03), and 5 minutes. The audio stays on the volume. The old database file stays where it is as a copy until you delete it yourself.
```
doas rcctl stop musicshare
doas mkdir /var/music-share-db
doas cp -p /mnt/data/apps/music-share/data/musicshare.db* /var/music-share-db/
doas chown -R _musicshare:_musicshare /var/music-share-db
doas chmod 700 /var/music-share-db
printf 'MUSICSHARE_DB_DIR=/var/music-share-db\n' | doas tee -a /etc/musicshare.conf
doas rcctl start musicshare
```
Line by line:
1. stop the app so no write is in flight;
2. make the folder on the boot disk (`/var` is on it);
3. **copy** (never move) the database and its `-wal` and `-shm` side files, keeping owner and mode (the `*` also matches those; if no side files exist `cp` just copies the one file);
4. give the app's own user the folder;
5. keep it private to that user;
6. add the one setting to the app's config (`tee -a` appends; do it once, and check with `doas grep DB_DIR /etc/musicshare.conf` that there is exactly one line);
7. start.

Check: `curl -s http://127.0.0.1:8101/music-share/health`; open the Studio, your tracks are all there; press **Run speed check** (it also prints the database folder in the raw answer); switch a track public/unlisted and see how fast it answers.
Then press **Back up now** and pull the backup: the backup and restore read the database from its new place.

**Way back:** `doas rcctl stop musicshare`, delete the `MUSICSHARE_DB_DIR` line from `/etc/musicshare.conf` (`doas vi`), `doas rcctl start musicshare`. It uses the old copy again, which is stale by whatever you changed since; copy the newer files back first if that matters.
**Trade-off:** the database now lives on the boot disk, so a rebuilt server loses it unless you restore from a backup. That is why the pull job (T-03) matters. Delete the old copy on the volume only when you are sure: `doas rm /mnt/data/apps/music-share/data/musicshare.db*` (your call, nothing here does it).
**Unverified (no OpenBSD here):** the commands are standard but I have not run them on your server. Everything before `rcctl start` is reversible.

## 3. What "long-lived cache headers" are, and why you might care (explained, not built)
Every file a browser downloads comes with **headers** that say how long it may keep a copy. A **long-lived cache header** tells the browser "this file will not change; keep it for a year, do not even ask me again" (`Cache-Control: public, max-age=31536000, immutable`). The browser then plays it from its own disk with **no request at all**.
- **Why it matters here:** audio is the heavy part of the site. Without it, a friend who plays a track, comes back tomorrow, or scrubs around re-downloads (or at least re-asks the server about) the same bytes over the HDD volume and your VPS's bandwidth. With it, repeat plays are instant and free, and the server does nothing.
- **Why it is safe only for content that never changes under the same address.** The pages already do this for the CSS and JS: their address contains a fingerprint (`?v=<hash>`), so a changed file gets a new address and the year-long copy is never wrong. Audio addresses are `/a/<token>`: the same address can later hold **different** audio (after "Replace audio…") or a different quality (lossy/lossless), so a year-long cache could make a friend hear the old take. That is why audio is cached for only 10 minutes now (`private, max-age=600`).
- **What would make long caching safe:** put a fingerprint in the audio address (for example `/a/<token>?v=<hash of the file>`), as the CSS already has; then the header can be a year. It also must stay `private` (not `public`) for unlisted tracks so shared caches never keep them, and revoking a link (New link) already changes the address.
- **Cost and risk:** a small change in the page and routes, plus tests; the main risk is getting the fingerprint wrong and serving a stale take. The gain is largest for friends who replay tracks. It is not built; say if you want it.

