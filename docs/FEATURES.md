# New in this release: streaming copies, lossy/lossless playback, preview pictures, replace audio, choose several, lazy waveforms

Written 2026-11-21. Upload and install steps: `docs/SPEED.md` section 1 (same procedure: upload the folder, `--check`, then `--app-files-only`). Read section 5 of this file before the first start.

## 1. Streaming copies (and what streaming services do)
- Every upload (and every existing track, after the update) gets a small **AAC copy** (about 160 kbps, `.stream.m4a` next to the original), made by ffmpeg in the background, one track at a time.
- **Friends hear the small copy by default; Download always gives the original, byte for byte.** A lossless 4-minute WAV is about 40 MB; the copy is about 5 MB (measured in tests: under a third of the original for a 4-second file, far less for music).
- Files that are already small (a 96 kbps MP3, say) are streamed as they are; a 320 kbps MP3 gets a copy. If a copy is missing or failed, the original plays: a track always plays.
- **Is this what Spotify and SoundCloud do?** Yes in kind, with differences (verified against their help pages 2026-11-21, see the links in the chat summary):
  - SoundCloud: uploads are transcoded for streaming, as AAC at 256, 160 and 96 kbps depending on the source (its API calls 160 kbps AAC the standard); the original is delivered only if downloads are enabled. This release's design is the same idea: 160 kbps AAC for listening, original behind Download.
  - Spotify: streams lossy tiers by default (about 24, 96, 160 and 320 kbps; "Automatic" picks by connection) and offers Lossless (up to 24-bit/44.1 kHz FLAC) as an opt-in setting. This release's switch is the same shape: lossy by default, lossless opt-in.
  - Differences: they make several bitrates and switch between them while you listen (adaptive streaming, HLS); this makes one. Spotify's lossy codec choice varies by platform (an official page for Ogg Vorbis was not found; do not rely on that detail).
- Setting: `MUSICSHARE_STREAM_KBPS=160` in `/etc/musicshare.conf` (64 to 320). Changing it affects tracks made afterwards; to remake existing copies delete the `*.stream.m4a` files and set `stream_status='pending'` (ask me for the one-line command when you want it).

## 2. Playback: lossy / lossless switch
A small **"Playback: lossy"** button in the header of the public pages and the Studio. Press it to play the original files (lossless); press again to go back. Lossy is the default; the choice is remembered per browser (key `musicshare.quality`). Switching while a track plays carries on from the same second. (Apple Lossless files play their FLAC copy in lossless mode, since Chrome and Firefox cannot play ALAC.)

## 3. Preview pictures for pasted links
Pasting a track or playlist link into a chat app now shows a blue card with the waveform (a 1200 x 630 PNG of about 1 to 2 KB, drawn by the app with no image library; no text in it). Chat apps want an **absolute address**, so add one line to `/etc/musicshare.conf`:
```
printf 'MUSICSHARE_PUBLIC_URL=https://sanjaysubramanian.com\n' | doas tee -a /etc/musicshare.conf
doas rcctl restart musicshare
```
Without it the page still names the picture by its relative path; some apps will not show it. Chat apps cache previews for a long time: test with a link they have not seen. Unverified here: how each chat app displays it.

## 4. Replace audio, choose several, lazy waveforms
- **Replace audio…** (a button on every Studio row): pick a new file; after you confirm, it goes behind the **same link**, title and notes. The old original is **moved, not deleted**, to `/mnt/data/apps/music-share/data/replaced/<id>-<date>-<name>`; delete that folder's files yourself when you are sure. The waveform and streaming copy are made again. Play counts are kept.
- **Select…** (Tracks and Archive tabs): tick tracks, then Make listed, Make unlisted, Archive or Add to playlist (or Unarchive). One request, one save. There is deliberately **no bulk delete**: Delete forever stays per track with the title typed back.
- **Share** (phones that have a share sheet): next to Copy link.
- **Lazy waveforms:** the public page draws a waveform only when its row is near the screen, so a long list opens quickly on a phone.

## 5. Before the first start (read this)
1. **Back up first:** Studio, Backup tab, *Back up now*, and pull it (T-03). The database moves to **schema 3** (two new columns) on first start. Older code refuses a newer database with a clear message, so to go back you would restore this backup into the older version.
2. **First start does extra work once:** every existing track gets a streaming copy made, one at a time, on the one CPU. Estimate (unverified): a few seconds per 4-minute track, so a library of 100 tracks takes several minutes during which the Studio says "making the streaming copy…" and the server is a little busier. Friends are never blocked: until a copy is ready the original plays.
3. **Disk:** each copy adds about 10 to 15 percent of the original's size (more for already-small originals that get a copy). The `replaced/` folder only grows when you use Replace audio.
4. **ffmpeg's AAC encoder** must be present (it is built in to ffmpeg; `ffmpeg -hide_banner -encoders | grep aac` should show `aac`). If it is not, copies show "failed" in the Studio and the originals play as before.
5. **Check after upgrading (5 minutes):** Studio: a row says "friends stream a ... copy (original ...)". Public page: Playback says lossy; in the browser's Network tab the audio request is `audio/mp4`; press the button and it becomes `audio/wav` (or your file's type). Paste a track link into a chat app (after setting `MUSICSHARE_PUBLIC_URL`). Select two tracks and make them unlisted, then listed again. Try Replace audio on a throwaway track.
