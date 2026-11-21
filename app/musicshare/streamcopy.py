"""The small copy friends stream. A lossless original (WAV, FLAC, AIFF, ALAC) is 5 to 10 times larger than it needs to be for listening on a phone;
an AAC copy at 160 kbps is what the public pages play. The original is never touched and is what Download gives. Standard library + ffmpeg only."""
import subprocess
from pathlib import Path

from kitaudio import media

LOSSY_EXT = (".mp3", ".ogg", ".aac")          # already small: streamed as they are when their bit rate is modest
KBPS = 160


def needs_copy(src, duration_s, size, ffmpeg_codec=None):
    """True when streaming the original would be wasteful: it is lossless, or lossy but much bigger than the copy would be."""
    ext = Path(src).suffix.lower()
    if ext in (".m4a", ".mp4"):
        lossless = media.mp4_audio_codec(src) == "alac"
    else:
        lossless = ext not in LOSSY_EXT
    if lossless:
        return True
    if not duration_s:
        return False
    return (size * 8 / duration_s) > KBPS * 1000 * 1.25        # a lossy file well above the copy's rate (e.g. 320 kbps MP3) is also worth shrinking


def make_copy(ffmpeg, src, dst, kbps=KBPS, timeout=1800):
    """Write an AAC-in-MP4 copy of `src` to `dst` (fast start, so playback begins before the whole file arrives). Returns (ok, error_text)."""
    fmt = media.INPUT_FORMAT.get(Path(src).suffix.lower())
    if fmt is None:
        return False, "unsupported file type"
    tmp = Path(str(dst) + ".part")
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-y",
           "-protocol_whitelist", "file",          # never fetch anything over the network
           "-f", fmt, "-i", str(src),
           "-map", "0:a:0", "-vn", "-map_metadata", "-1",
           "-ac", "2", "-ar", "44100", "-threads", "1",
           "-c:a", "aac", "-b:a", f"{int(kbps)}k",
           "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True, errors="replace")
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        return False, "conversion timed out"
    except OSError as exc:
        return False, f"could not run ffmpeg: {exc}"
    if done.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False, (done.stderr or "ffmpeg failed")[-500:]
    tmp.replace(dst)
    return True, ""
