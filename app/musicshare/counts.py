"""Counting opens and plays without making a listener wait for the disk.

Every human view of a page and every play used to write to the database and wait for the commit. On a slow volume that is the wait you feel.
Now a view or a play is added to a small in-memory tally (no disk), and the tally is written to the database in one batch every few seconds
by a background thread (and just before the Studio shows you the numbers). A crash loses at most the last few seconds of counts. Standard library only.
"""
import threading

from . import db


class Counts:
    def __init__(self, db_path, inline=False):
        self.db_path, self.inline = db_path, inline
        self._lock = threading.Lock()
        self._opens = {}                       # (table, token) -> [count, first, last]
        self._plays = {}                       # ("token" | "id", value) -> [count, last]

    def pending(self):
        with self._lock:
            return len(self._opens) + len(self._plays)

    def opened(self, table, token):
        now = db.now_iso()
        with self._lock:
            e = self._opens.setdefault((table, token), [0, now, now])
            e[0] += 1
            e[2] = now
        if self.inline:
            self.flush()

    def played(self, kind, value):
        now = db.now_iso()
        with self._lock:
            e = self._plays.setdefault((kind, value), [0, now])
            e[0] += 1
            e[1] = now
        if self.inline:
            self.flush()

    def flush(self):
        with self._lock:
            opens, plays, self._opens, self._plays = self._opens, self._plays, {}, {}
        if not opens and not plays:
            return
        conn = db.connect(self.db_path)
        try:
            for (table, token), (n, first, last) in opens.items():
                conn.execute(f"UPDATE {table} SET open_count = open_count + ?, last_opened_at = ?, first_opened_at = COALESCE(first_opened_at, ?) WHERE token = ?", (n, last, first, token))
            for (kind, value), (n, last) in plays.items():
                col = "token" if kind == "token" else "id"
                conn.execute(f"UPDATE tracks SET play_count = play_count + ?, last_played_at = ? WHERE {col} = ?", (n, last, value))
            conn.commit()
        finally:
            conn.close()
