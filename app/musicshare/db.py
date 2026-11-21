"""SQLite: one file, plain tables. Standard library only."""
import datetime as dt
import secrets
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
  id INTEGER PRIMARY KEY,
  token TEXT NOT NULL UNIQUE,                      -- the secret in the share link (128 random bits)
  title TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  visibility TEXT NOT NULL DEFAULT 'unlisted' CHECK (visibility IN ('listed', 'unlisted')),
  created_at TEXT NOT NULL,
  archived_at TEXT,                                -- hidden from lists; the link keeps working unless the setting says otherwise
  filename TEXT NOT NULL UNIQUE,                   -- the stored original (random name), never modified
  orig_filename TEXT NOT NULL,
  mime TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  duration_s REAL,
  peaks TEXT,                                      -- JSON list of 0-255, or '[]' = could not measure
  analysis_status TEXT NOT NULL DEFAULT 'pending', -- pending | ready | failed
  play_filename TEXT,                              -- a FLAC copy for browsers that cannot play the original (ALAC)
  play_status TEXT NOT NULL DEFAULT 'none',        -- none | pending | ready | failed
  stream_filename TEXT,                            -- a small AAC copy that friends stream (the original stays for Download)
  stream_status TEXT NOT NULL DEFAULT 'pending',   -- pending | ready | failed | none (the original is already small enough)
  open_count INTEGER NOT NULL DEFAULT 0,           -- how many times a person (not a link-preview robot) opened the track page; only you see this
  first_opened_at TEXT,
  last_opened_at TEXT,
  play_count INTEGER NOT NULL DEFAULT 0,           -- how many times someone pressed play (the audio was fetched from the start)
  last_played_at TEXT
);
CREATE TABLE IF NOT EXISTS playlists (
  id INTEGER PRIMARY KEY,
  token TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  visibility TEXT NOT NULL DEFAULT 'unlisted' CHECK (visibility IN ('listed', 'unlisted')),
  created_at TEXT NOT NULL,
  archived_at TEXT,
  open_count INTEGER NOT NULL DEFAULT 0,
  first_opened_at TEXT,
  last_opened_at TEXT
);
CREATE TABLE IF NOT EXISTS playlist_items (
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  PRIMARY KEY (playlist_id, track_id)
);
CREATE INDEX IF NOT EXISTS tracks_created ON tracks (created_at);
"""
VERSION = 3


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_token():
    return secrets.token_urlsafe(16)


def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")       # safe with WAL, and fewer disk syncs on the slow volume
    return conn


def init(path):
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > VERSION:
        raise RuntimeError(f"{path} was made by a newer version of this app (schema {have} > {VERSION})")
    conn.executescript(SCHEMA)
    have_cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    if "play_count" not in have_cols:                 # a version-1 database: add the two columns, keep everything else
        conn.execute("ALTER TABLE tracks ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE tracks ADD COLUMN last_played_at TEXT")
    if "stream_status" not in have_cols:               # a version-2 database: tracks already there get a stream copy made in the background
        conn.execute("ALTER TABLE tracks ADD COLUMN stream_filename TEXT")
        conn.execute("ALTER TABLE tracks ADD COLUMN stream_status TEXT NOT NULL DEFAULT 'pending'")
    conn.execute(f"PRAGMA user_version = {VERSION}")
    conn.commit()
    conn.close()


_local = threading.local()


def thread_conn(path):
    """One long-lived connection per worker thread (opening a database file on every request costs reads on a slow disk; a kept-open connection
    also keeps SQLite's page cache). No transaction is ever left open between requests, so each request sees the latest data."""
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    c = conns.get(str(path))
    if c is None:
        c = conns[str(path)] = connect(path)
        c.execute("PRAGMA wal_autocheckpoint = 0")       # request threads never pay for a checkpoint (see checkpoint(), run in the background)
        c.execute("PRAGMA temp_store = MEMORY")
    return c


def checkpoint(path):
    """Move the write-ahead log into the main file, without blocking readers or writers. Run from a background thread."""
    c = connect(path)
    try:
        c.execute("PRAGMA wal_checkpoint(PASSIVE)")
    finally:
        c.close()
