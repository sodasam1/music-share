"""Backup and restore. Standard library only, no Flask. Same shape as riffbox's:

  <backup folder>/db/musicshare-YYYYMMDD-HHMMSS.db   one per run, checked with SQLite's integrity test; the newest `keep` are kept
  <backup folder>/audio/                             every original audio file, copied once, never deleted
Play copies (FLAC for ALAC) are not backed up: they are rebuilt from the originals.
restore() works only into an EMPTY library and never changes the backup it reads.
"""
import datetime as dt
import hashlib
import shutil
import sqlite3
from pathlib import Path


def backup(data_dir, dest, keep=14, now=None, db_file=None):
    """Returns (database_copy_path, new_audio_files_copied, problems). The database may live elsewhere than the audio (db_file)."""
    data_dir, dest = Path(data_dir), Path(dest)
    db_file = Path(db_file) if db_file else data_dir / "musicshare.db"
    if not db_file.is_file():
        raise ValueError(f"no database at {db_file}")
    (dest / "db").mkdir(parents=True, exist_ok=True)
    (dest / "audio").mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%d-%H%M%S")
    target = dest / "db" / f"musicshare-{stamp}.db"
    partial = target.with_name(target.name + ".partial")
    src, snap = sqlite3.connect(db_file), sqlite3.connect(partial)
    try:
        src.backup(snap)
        check = snap.execute("PRAGMA integrity_check").fetchone()[0]
        names = [r[0] for r in snap.execute("SELECT filename FROM tracks")]
    finally:
        snap.close()
        src.close()
    if check != "ok":
        partial.unlink(missing_ok=True)
        raise ValueError(f"database copy failed its integrity check: {check}")
    partial.replace(target)
    copied, problems = 0, []
    for name in names:
        source, copy = data_dir / "audio" / name, dest / "audio" / name
        if not source.is_file():
            problems.append(f"audio file missing in the data folder: {name}")
            continue
        if copy.is_file() and copy.stat().st_size == source.stat().st_size:
            continue
        tmp = copy.with_name(copy.name + ".partial")
        shutil.copy2(source, tmp)
        tmp.replace(copy)
        copied += 1
    for old in sorted((dest / "db").glob("musicshare-*.db"))[:-max(1, keep)]:
        old.unlink()
    for leftover in (dest / "audio").glob("*.partial"):
        leftover.unlink(missing_ok=True)
    return target, copied, problems


def status(dest):
    dest = Path(dest)
    copies = sorted((dest / "db").glob("musicshare-*.db")) if (dest / "db").is_dir() else []
    return {"copies": len(copies), "newest": copies[-1].name if copies else None,
            "audio_files": len(list((dest / "audio").glob("*"))) if (dest / "audio").is_dir() else 0}


def inspect(source):
    """What a restore would do: {"ok": bool, "tracks": n, "db": path, "error": text}."""
    source = Path(source)
    dbs = sorted((source / "db").glob("musicshare-*.db")) if (source / "db").is_dir() else []
    if not dbs:
        return {"ok": False, "error": "no db/musicshare-*.db found in that folder"}
    conn = sqlite3.connect(f"file:{dbs[-1]}?mode=ro&immutable=1", uri=True)
    try:
        n = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"not a usable database: {exc}"}
    finally:
        conn.close()
    return {"ok": True, "tracks": n, "db": str(dbs[-1])}


def restore(source, data_dir, db_file=None):
    """Rebuild the library from a backup folder into an empty data folder. Returns (tracks_restored, problems).
    Every audio file is checked against the checksum stored in the database; a mismatch or a missing file is reported, not hidden."""
    source, data_dir = Path(source), Path(data_dir)
    info = inspect(source)
    if not info["ok"]:
        raise ValueError(info["error"])
    live = Path(db_file) if db_file else data_dir / "musicshare.db"
    live.parent.mkdir(parents=True, exist_ok=True)
    if live.is_file():
        c = sqlite3.connect(live)
        try:
            if c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]:
                raise ValueError("the library is not empty: restore only works into an empty library")
        except sqlite3.OperationalError:
            pass
        finally:
            c.close()
    src = sqlite3.connect(f"file:{info['db']}?mode=ro&immutable=1", uri=True)
    src.row_factory = sqlite3.Row
    try:
        rows = src.execute("SELECT id, filename, sha256, title FROM tracks").fetchall()
        (data_dir / "audio").mkdir(parents=True, exist_ok=True)
        problems = []
        for r in rows:
            f = source / "audio" / r["filename"]
            if not f.is_file():
                problems.append(f"missing audio for '{r['title']}' ({r['filename']})")
                continue
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != r["sha256"]:
                problems.append(f"checksum mismatch for '{r['title']}' ({r['filename']}): copied anyway, listed here")
            target = data_dir / "audio" / r["filename"]
            if not target.exists():
                shutil.copy2(f, target)
        # the database last: a failure before this point leaves the library empty and the run can simply be repeated
        dst = sqlite3.connect(live)
        try:
            src.backup(dst)
            dst.execute("UPDATE tracks SET play_status = CASE WHEN play_status = 'none' THEN 'none' ELSE 'pending' END, play_filename = NULL")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    return len(rows), problems
