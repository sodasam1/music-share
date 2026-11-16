"""Background work after an upload: measure the waveform, and make a FLAC play copy of Apple Lossless (ALAC) files, which Chrome and
Firefox cannot play. One worker thread, one job at a time (the server has one CPU)."""
import json
import logging
import queue
import threading
from pathlib import Path

from kitaudio import media

from . import db

log = logging.getLogger("musicshare")


def process(data_dir, ffmpeg, track_id):
    conn = db.connect(Path(data_dir) / "musicshare.db")
    try:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if row is None:
            return
        src = Path(data_dir) / "audio" / row["filename"]
        if not ffmpeg:
            conn.execute("UPDATE tracks SET analysis_status = 'failed', peaks = '[]' WHERE id = ?", (track_id,))
            conn.commit()
            return
        if row["analysis_status"] == "pending":
            ok, duration, peaks, err = media.analyze_audio(ffmpeg, src)
            if ok:
                conn.execute("UPDATE tracks SET analysis_status = 'ready', duration_s = ?, peaks = ? WHERE id = ?", (duration, json.dumps(peaks), track_id))
            else:
                log.warning("analysis failed for track %s: %s", track_id, err)
                conn.execute("UPDATE tracks SET analysis_status = 'failed', peaks = '[]' WHERE id = ?", (track_id,))
            conn.commit()
        if not row["play_filename"] and src.suffix.lower() in (".m4a", ".mp4") and media.mp4_audio_codec(src) == "alac":
            play = row["filename"].rsplit(".", 1)[0] + ".play.flac"
            done, err = media.convert_to_flac(ffmpeg, src, Path(data_dir) / "audio" / play)
            conn.execute("UPDATE tracks SET play_status = ?, play_filename = ? WHERE id = ?", ("ready" if done else "failed", play if done else None, track_id))
            if not done:
                log.warning("FLAC copy failed for track %s: %s", track_id, err)
            conn.commit()
    finally:
        conn.close()


class Worker:
    def __init__(self, data_dir, ffmpeg, inline=False):
        self.data_dir, self.ffmpeg, self.inline = data_dir, ffmpeg, inline
        self._q = queue.Queue()
        self._thread = None

    def submit(self, track_id):
        if self.inline:                                   # tests: run now, deterministic
            process(self.data_dir, self.ffmpeg, track_id)
            return
        self._q.put(track_id)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="musicshare-jobs")
            self._thread.start()

    def _run(self):
        while True:
            try:
                tid = self._q.get(timeout=30)
            except queue.Empty:
                return
            try:
                process(self.data_dir, self.ffmpeg, tid)
            except Exception:                              # one bad file must not stop the worker
                log.exception("job for track %s failed", tid)

    def backfill(self):
        """After a restart or a restore: queue every track that still has no waveform, and every ALAC play copy that is missing."""
        conn = db.connect(Path(self.data_dir) / "musicshare.db")
        try:
            for (tid,) in conn.execute("SELECT id FROM tracks WHERE analysis_status = 'pending' OR (play_status != 'none' AND play_filename IS NULL)").fetchall():
                self.submit(tid)
        finally:
            conn.close()
