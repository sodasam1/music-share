"""SQLite: one file, plain tables. Standard library only."""
import datetime as dt
import secrets
import sqlite3

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
  open_count INTEGER NOT NULL DEFAULT 0,           -- how many times someone else opened the track page (owner only sees this)
  first_opened_at TEXT,
  last_opened_at TEXT
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
VERSION = 1


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_token():
    return secrets.token_urlsafe(16)


def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(path):
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > VERSION:
        raise RuntimeError(f"{path} was made by a newer version of this app (schema {have} > {VERSION})")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {VERSION}")
    conn.commit()
    conn.close()
