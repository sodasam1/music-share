"""music-share: put audio drafts on the web and send a link. One owner (the site login), anonymous listeners (a link, no account).

Public (no login):    /            the index of everything you marked "listed"
                      /t/<token>   one track        /p/<token>   one playlist      /a/<token>, /pa/<token>/<id>   the audio
Owner (site login):   /studio      the page you manage everything from;  /api/...  its JSON
Every route is declared @public, @owner or @token; one with no declaration is owner-only (see siteauth).
"""
import gzip
import hashlib
import json
import mimetypes
import os
import re
import statistics
import subprocess
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from flask import Blueprint, Flask, Response, abort, g, jsonify, redirect, render_template, request, send_file
from kitaudio import media
from siteauth import SiteAuth

from . import backup as backupmod
from . import db, jobs
from .counts import Counts
from .config import settings

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'")
MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".flac": "audio/flac", ".ogg": "audio/ogg",
        ".aac": "audio/aac", ".aif": "audio/aiff", ".aiff": "audio/aiff", ".caf": "audio/x-caf"}
CTRL = re.compile(r"[\x00-\x1f\x7f]")
MAX_TITLE, MAX_NOTES = 200, 2000
# Link-preview robots (a chat app fetching the page when you paste a link) are not listeners: they are not counted as opens.
ROBOT = re.compile(r"bot|crawl|spider|preview|facebookexternalhit|slack|whatsapp|telegram|discord|skype|linkedin|embedly|curl|wget|python-requests|headless", re.I)
TEXT_TYPES = ("text/css", "text/javascript", "application/javascript", "image/svg+xml", "text/html")


class Invalid(Exception):
    pass


def clean(text, limit, field, required=False):
    if not isinstance(text, str):
        raise Invalid(f"{field} must be text")
    text = CTRL.sub(" ", text).strip() if field == "title" else text.replace("\r", "").strip()
    if required and not text:
        raise Invalid(f"{field} cannot be empty")
    if len(text) > limit:
        raise Invalid(f"{field} can be at most {limit} characters")
    return text


def create_app(overrides=None):
    cfg = settings(overrides)
    app = Flask(__name__, static_folder=None, template_folder=str(Path(__file__).parent / "templates"))
    base = "/" + cfg.get("MUSICSHARE_BASE_PATH", "").strip("/") if cfg.get("MUSICSHARE_BASE_PATH", "").strip("/") else ""
    data_dir = Path(cfg.get("MUSICSHARE_DATA_DIR", "/var/music-share"))
    static_dir = Path(cfg.get("MUSICSHARE_STATIC_DIR", str(Path(__file__).resolve().parents[2] / "static")))
    backup_dir = cfg.get("MUSICSHARE_BACKUP_DIR", "")
    restore_dir = cfg.get("MUSICSHARE_RESTORE_DIR", "/var/music-share-restore")
    ffmpeg = cfg["ffmpeg"] if "ffmpeg" in cfg else (os.environ.get("MUSICSHARE_FFMPEG") if "MUSICSHARE_FFMPEG" in os.environ else shutil.which("ffmpeg"))
    show_download = cfg.get("MUSICSHARE_SHOW_DOWNLOAD", "1") == "1"
    archive_kills_link = cfg.get("MUSICSHARE_ARCHIVE_DISABLES_LINK", "0") == "1"
    app.config.update(MAX_CONTENT_LENGTH=int(cfg.get("MUSICSHARE_MAX_UPLOAD_MB", "300")) * 1024 * 1024, TESTING=bool(cfg.get("TESTING")))
    (data_dir / "audio").mkdir(parents=True, exist_ok=True)
    (data_dir / "tmp").mkdir(parents=True, exist_ok=True)
    # Werkzeug parks a big upload in a temp file while it parses the form (here up to MUSICSHARE_MAX_UPLOAD_MB). Keep that on the data volume,
    # not in the system /tmp on the small boot disk.
    tempfile.tempdir = str(data_dir / "tmp")
    for stale in (data_dir / "tmp").glob("*.part"):          # half-written uploads left by a crash or a restart mid-upload
        stale.unlink(missing_ok=True)
    # The database may live on a different disk than the audio (MUSICSHARE_DB_DIR): a fast boot disk for the small, write-heavy database,
    # the big volume for the audio. Default: next to the audio, as before.
    db_dir = Path(cfg.get("MUSICSHARE_DB_DIR") or data_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "musicshare.db"
    db.init(db_path)
    testing = bool(cfg.get("JOBS_SYNC"))
    state = {"v": 0}                                              # bumped whenever something a visitor can see may have changed
    pages = {}                                                    # cached public pages: (path, gzip?) -> (version, stamp, etag, body)

    def bump():
        state["v"] += 1

    worker = jobs.Worker(data_dir, db_path, ffmpeg, inline=testing, on_change=bump)
    counts = Counts(db_path, inline=testing)
    if not testing:
        worker.backfill()

        def maintenance():
            """Every few seconds write the batched counts; every minute move the write-ahead log into the database. Never on a visitor's time."""
            tick = 0
            while True:
                time.sleep(5)
                tick += 1
                try:
                    counts.flush()
                    if tick % 12 == 0:
                        db.checkpoint(db_path)
                except Exception:
                    app.logger.exception("maintenance failed")
        threading.Thread(target=maintenance, daemon=True, name="musicshare-maintenance").start()

    auth = SiteAuth(app, name="musicshare", key_file=cfg.get("MUSICSHARE_SITE_KEY_FILE", "/etc/site-auth/keys/musicshare.key"),
                    cookie_path=base or "/", login_url=cfg.get("MUSICSHARE_SITE_LOGIN_URL", "/login"), clock=cfg.get("clock") or time.time,
                    secure=cfg.get("MUSICSHARE_INSECURE_COOKIES") != "1", api_prefixes=(base + "/api/",))
    bp = Blueprint("music", __name__)

    def conn():
        return db.thread_conn(db_path)

    def timed(name):
        """with timed("db"): ... adds to the Server-Timing header, so a slow page shows where the time went."""
        class T:
            def __enter__(self):
                self.t = time.perf_counter()

            def __exit__(self, *a):
                g.setdefault("parts", {})
                g.parts[name] = g.parts.get(name, 0) + (time.perf_counter() - self.t) * 1000
        return T()

    @app.before_request
    def start_timer():
        g.t0 = time.perf_counter()

    @app.after_request
    def timing_and_gzip(resp):
        """Server-Timing: how long this server spent on the request (browsers show it under Network > Timing), so a slow page can be
        told apart as "slow server/disk" or "slow network". Also gzip for the JSON and HTML answers (the track list carries waveforms)."""
        t0 = g.get("t0")
        if t0 is not None:
            parts = "".join(f"{k};dur={v:.1f}, " for k, v in g.get("parts", {}).items())
            resp.headers["Server-Timing"] = f"{parts}app;dur={(time.perf_counter() - t0) * 1000:.1f}"
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and resp.status_code < 400:
            bump()                                                  # a visitor-visible change may have happened: cached pages are stale
        if (resp.status_code == 200 and not resp.direct_passthrough and resp.mimetype in ("application/json", "text/html")
                and "Content-Encoding" not in resp.headers and "gzip" in request.headers.get("Accept-Encoding", "")):
            body = resp.get_data()
            if len(body) > 1000:
                resp.set_data(gzip.compress(body, 5))
                resp.headers["Content-Encoding"] = "gzip"
                resp.vary.add("Accept-Encoding")
        return resp

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        rel = request.path[len(base):] if base and request.path.startswith(base) else request.path
        if rel.startswith(("/t/", "/p/", "/a/", "/pa/")):               # share links are for whoever you send them to, not for search engines
            resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        if resp.mimetype in ("application/json", "text/html"):
            resp.headers.setdefault("Cache-Control", "private, no-cache")
        return resp

    def err(message, status=400):
        return jsonify(error=message), status

    # ---- helpers ------------------------------------------------------------------------------------------
    def link(kind, token):
        return f"{base}/{kind}/{token}"

    def track_dict(r):
        """A track as the Studio sees it (only ever sent to the owner)."""
        return {"id": r["id"], "title": r["title"], "notes": r["notes"], "visibility": r["visibility"], "created_at": r["created_at"],
                "archived": r["archived_at"] is not None, "duration_s": r["duration_s"], "peaks": json.loads(r["peaks"]) if r["peaks"] else [],
                "analysis_status": r["analysis_status"], "size": r["size"], "orig_filename": r["orig_filename"],
                "token": r["token"], "link": link("t", r["token"]), "audio": f"{base}/api/audio/{r['id']}",
                "open_count": r["open_count"], "first_opened_at": r["first_opened_at"], "last_opened_at": r["last_opened_at"],
                "play_count": r["play_count"], "last_played_at": r["last_played_at"]}

    def playlist_dict(r, with_items=True):
        d = {"id": r["id"], "title": r["title"], "notes": r["notes"], "visibility": r["visibility"], "created_at": r["created_at"],
             "archived": r["archived_at"] is not None, "token": r["token"], "link": link("p", r["token"]), "open_count": r["open_count"],
             "first_opened_at": r["first_opened_at"], "last_opened_at": r["last_opened_at"]}
        if with_items:
            d["track_ids"] = [x[0] for x in conn().execute("SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position", (r["id"],))]
        return d

    def serve_audio(row):
        play = row["play_filename"] if row["play_status"] == "ready" else None
        name, mime = (play, "audio/flac") if play and (data_dir / "audio" / play).is_file() else (row["filename"], row["mime"])
        path = data_dir / "audio" / name
        if not path.is_file():
            abort(404)
        resp = send_file(path, mimetype=mime, conditional=True, max_age=0, download_name=row["orig_filename"],
                         as_attachment=request.args.get("download") == "1" and (show_download or auth.is_owner()))
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Cache-Control"] = "private, max-age=600"
        return resp

    def track_by_token(token, for_public=True):
        row = conn().execute("SELECT * FROM tracks WHERE token = ?", (token,)).fetchone()
        if row is None or (for_public and archive_kills_link and row["archived_at"]):
            abort(404)
        return row

    def is_robot():
        return bool(ROBOT.search(request.headers.get("User-Agent", "")))

    def count_open(table, token):
        """Count a person opening a page. Not the owner, not a HEAD request, not a link-preview robot. Costs no disk access: see counts.py."""
        if auth.is_owner() or request.method != "GET" or is_robot():
            return
        counts.opened(table, token)

    recent_plays = {}                      # (client, track) -> when: one press of play can fetch the audio more than once

    def count_play(kind, value):
        """Count a listener starting a track: the audio is requested from its first byte. Seeking asks for later bytes and is not counted."""
        rng = request.headers.get("Range", "")
        if auth.is_owner() or request.method != "GET" or is_robot() or (rng and not rng.startswith("bytes=0-")):
            return
        key, now = (request.remote_addr or "", request.headers.get("X-Forwarded-For", "").split(",")[-1].strip(), kind, value), time.time()
        if now - recent_plays.get(key, 0) < 600:
            return
        if len(recent_plays) > 2000:
            recent_plays.clear()
        recent_plays[key] = now
        counts.played(kind, value)

    def fit(peaks, n):
        """At most n bars: the loudest of each group, so the shape stays and the page gets smaller."""
        if len(peaks) <= n:
            return peaks
        return [max(peaks[i * len(peaks) // n:(i + 1) * len(peaks) // n]) for i in range(n)]

    def duration_text(sec):
        sec = int(round(sec or 0))
        return f"{sec // 60}:{sec % 60:02d}" if sec else ""

    def pub(r, bars=200):
        peaks = fit(json.loads(r["peaks"]), bars) if r["peaks"] else []
        return {"title": r["title"], "notes": r["notes"], "peaks": ",".join(map(str, peaks)),
                "duration": r["duration_s"] or 0, "length": duration_text(r["duration_s"]), "audio": link("a", r["token"]), "page": link("t", r["token"]),
                "created": r["created_at"][:10], "download": f"{link('a', r['token'])}?download=1" if show_download else None}

    # ---- public pages -----------------------------------------------------------------------------------------
    @bp.get("/health")
    @auth.public
    def health():
        return jsonify(ok=True)

    # Static files: each is read once, compressed once, and kept in memory. Pages refer to them as /static/x.css?v=<hash of the content>,
    # which may be cached for a year (the address changes when the file does); a bare address is revalidated with its ETag.
    assets = {}
    # A page needs several small CSS and JS files; each extra file is another connection and TLS handshake on a one-CPU server. These "bundles" are made
    # by joining the real files in memory (nothing to build, nothing extra to keep in step), so a page loads one stylesheet and one script.
    BUNDLES = {"bundle-public.css": ["minimal/minimal.css", "listen.css"], "bundle-studio.css": ["minimal/minimal.css", "listen.css", "musicshare.css"],
               "bundle-public.js": ["wave/wave.js", "public.js"], "bundle-studio.js": ["minimal/theme-menu.js", "wave/wave.js", "studio.js"]}

    def load_asset(name):
        parts = BUNDLES.get(name)
        if parts:
            files = [static_dir / x for x in parts]
            if not all(f.is_file() for f in files):
                return None
            stamp = tuple((f.stat().st_mtime_ns, f.stat().st_size) for f in files)
            path = None
        else:
            path = (static_dir / name).resolve()
            if name.startswith(".") or "/." in name or static_dir.resolve() not in path.parents or not path.is_file():
                return None
            st = path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        hit = assets.get(name)
        if hit is None or hit["stamp"] != stamp:
            raw = b"\n".join(f.read_bytes() for f in files) if parts else path.read_bytes()
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            hit = {"stamp": stamp, "raw": raw, "mime": mime, "etag": '"' + hashlib.sha1(raw).hexdigest()[:16] + '"',
                   "gz": gzip.compress(raw, 9) if mime in TEXT_TYPES and len(raw) > 600 else None}
            assets[name] = hit
        return hit

    def asset(name):
        a = load_asset(name)
        return f"{base}/static/{name}" + (f"?v={a['etag'][1:9]}" if a else "")

    app.jinja_env.globals["asset"] = asset

    @bp.get("/static/<path:name>")
    @auth.public
    def static_files(name):
        a = load_asset(name)
        if a is None:
            abort(404)
        cache = "public, max-age=31536000, immutable" if request.args.get("v") else "no-cache"
        if request.headers.get("If-None-Match") == a["etag"]:
            return Response(status=304, headers={"ETag": a["etag"], "Cache-Control": cache})
        use_gz = a["gz"] is not None and "gzip" in request.headers.get("Accept-Encoding", "")
        resp = Response(a["gz"] if use_gz else a["raw"], mimetype=a["mime"])
        resp.headers["ETag"] = a["etag"]
        resp.headers["Cache-Control"] = cache
        resp.vary.add("Accept-Encoding")
        if use_gz:
            resp.headers["Content-Encoding"] = "gzip"
        return resp

    def cached(render):
        """Visitors (not the owner) get a page kept in memory until something changes or five minutes pass: no database and no template work for
        a repeat view, and a 304 (a few bytes) when their browser already has it."""
        if auth.is_owner():
            return Response(render(), mimetype="text/html")
        gz_ok = "gzip" in request.headers.get("Accept-Encoding", "")
        key, now = (request.path, gz_ok), time.time()
        hit = pages.get(key)
        if not (hit and hit[0] == state["v"] and now - hit[1] < 300):
            version = state["v"]
            html = render().encode()
            zipped = gz_ok and len(html) > 1000
            hit = (version, now, '"' + hashlib.sha1(html).hexdigest()[:16] + '"', gzip.compress(html, 6) if zipped else html, zipped)
            if len(pages) > 500:
                pages.clear()
            pages[key] = hit
        if request.headers.get("If-None-Match") == hit[2]:
            return Response(status=304, headers={"ETag": hit[2], "Cache-Control": "private, no-cache"})
        resp = Response(hit[3], mimetype="text/html")
        resp.headers["ETag"] = hit[2]
        if hit[4]:
            resp.headers["Content-Encoding"] = "gzip"
        resp.vary.add("Accept-Encoding")
        return resp

    @bp.get("/")
    @auth.public
    def index():
        def render():
            with timed("db"):
                tracks = conn().execute("SELECT * FROM tracks WHERE visibility = 'listed' AND archived_at IS NULL ORDER BY created_at DESC, id DESC").fetchall()
                lists = conn().execute("SELECT * FROM playlists WHERE visibility = 'listed' AND archived_at IS NULL ORDER BY created_at DESC, id DESC").fetchall()
            with timed("render"):
                # `owner` only decides whether a quiet link to the Studio is shown; the page is otherwise the same for everyone
                return render_template("index.html", base=base, owner=auth.is_owner(), tracks=[pub(t, 100) for t in tracks],
                                       playlists=[{"title": p["title"], "notes": p["notes"], "page": link("p", p["token"])} for p in lists])
        return cached(render)

    @bp.get("/t/<token>")
    @auth.public
    def track_page(token):
        def render():
            row = track_by_token(token)
            return render_template("track.html", base=base, t=pub(row), noindex=True)
        resp = cached(render)
        if resp.status_code == 200:
            count_open("tracks", token)
        return resp

    @bp.get("/a/<token>")
    @auth.public
    def track_audio(token):
        row = track_by_token(token)
        count_play("token", token)
        return serve_audio(row)

    def playlist_by_token(token):
        row = conn().execute("SELECT * FROM playlists WHERE token = ?", (token,)).fetchone()
        if row is None:
            abort(404)
        return row

    @bp.get("/p/<token>")
    @auth.public
    def playlist_page(token):
        def render():
            pl = playlist_by_token(token)
            rows = conn().execute("SELECT t.* FROM playlist_items i JOIN tracks t ON t.id = i.track_id WHERE i.playlist_id = ? ORDER BY i.position", (pl["id"],)).fetchall()
            items = []
            for r in rows:
                d = pub(r, 100)
                d["audio"] = f"{base}/pa/{pl['token']}/{r['id']}"
                d["download"] = f"{d['audio']}?download=1" if show_download else None        # never the track's own link: a playlist link must not reveal it
                d["page"] = None
                items.append(d)
            return render_template("playlist.html", base=base, p={"title": pl["title"], "notes": pl["notes"]}, items=items, noindex=True)
        resp = cached(render)
        if resp.status_code == 200:
            count_open("playlists", token)
        return resp

    @bp.get("/pa/<token>/<int:track_id>")
    @auth.public
    def playlist_audio(token, track_id):
        pl = playlist_by_token(token)
        row = conn().execute("SELECT t.* FROM playlist_items i JOIN tracks t ON t.id = i.track_id WHERE i.playlist_id = ? AND t.id = ?", (pl["id"], track_id)).fetchone()
        if row is None:
            abort(404)
        count_play("id", track_id)
        return serve_audio(row)

    # ---- the owner's page ---------------------------------------------------------------------------------------
    @bp.get("/studio")
    @auth.owner
    def studio():
        return render_template("studio.html", base=base)

    @bp.get("/api/session")
    @auth.owner
    def session_status():
        return jsonify(ok=True, base=base)

    # ---- tracks ---------------------------------------------------------------------------------------------------
    @bp.get("/api/tracks")
    @auth.owner
    def list_tracks():
        counts.flush()                                              # the Studio shows up-to-date "played" and "opened" numbers
        archived = request.args.get("archived") == "1"
        rows = conn().execute(f"SELECT * FROM tracks WHERE archived_at IS {'NOT ' if archived else ''}NULL ORDER BY created_at DESC, id DESC").fetchall()
        return jsonify([track_dict(r) for r in rows])

    @bp.post("/api/tracks")
    @auth.owner
    def upload():
        files = request.files.getlist("files")
        if not files:
            return err("send one or more audio files in the 'files' field")
        visibility = request.form.get("visibility", "unlisted")
        if visibility not in ("listed", "unlisted"):
            return err("visibility must be listed or unlisted")
        made, problems = [], []
        for f in files:
            orig = CTRL.sub("", os.path.basename((f.filename or "").replace("\\", "/")))[:255] or "untitled"
            tmp = data_dir / "tmp" / (db.new_token() + ".part")
            f.save(tmp)
            try:
                with open(tmp, "rb") as fh:
                    head = fh.read(16)
                ext = Path(orig).suffix.lower()
                if ext not in media.INPUT_FORMAT:
                    ext = media.sniff_audio_ext(head) or ""
                if ext not in media.INPUT_FORMAT:
                    problems.append(f"{orig}: not a supported audio file")
                    continue
                digest = hashlib.sha256()
                with open(tmp, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
                stored = db.new_token().replace("-", "x").replace("_", "y") + ext
                shutil.move(str(tmp), data_dir / "audio" / stored)
                title = clean(re.sub(r"[_]+", " ", Path(orig).stem) or "Untitled", MAX_TITLE, "title", True)
                cur = conn().execute(
                    "INSERT INTO tracks (token, title, visibility, created_at, filename, orig_filename, mime, size, sha256) VALUES (?,?,?,?,?,?,?,?,?)",
                    (db.new_token(), title[:MAX_TITLE], visibility, db.now_iso(), stored, orig, MIME[ext], (data_dir / "audio" / stored).stat().st_size, digest.hexdigest()))
                conn().commit()
                worker.submit(cur.lastrowid)
                made.append(cur.lastrowid)
            finally:
                tmp.unlink(missing_ok=True)
        rows = [conn().execute("SELECT * FROM tracks WHERE id = ?", (i,)).fetchone() for i in made]
        return jsonify(tracks=[track_dict(r) for r in rows], problems=problems), (201 if made else 415)

    def live_track(tid):
        row = conn().execute("SELECT * FROM tracks WHERE id = ?", (tid,)).fetchone()
        if row is None:
            abort(404)
        return row

    @bp.patch("/api/tracks/<int:tid>")
    @auth.owner
    def edit_track(tid):
        live_track(tid)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return err("send a JSON object")
        try:
            sets, vals = [], []
            if "title" in body:
                sets.append("title = ?"); vals.append(clean(body["title"], MAX_TITLE, "title", True))
            if "notes" in body:
                sets.append("notes = ?"); vals.append(clean(body["notes"], MAX_NOTES, "notes"))
            if "visibility" in body:
                if body["visibility"] not in ("listed", "unlisted"):
                    raise Invalid("visibility must be listed or unlisted")
                sets.append("visibility = ?"); vals.append(body["visibility"])
        except Invalid as exc:
            return err(str(exc))
        if sets:
            conn().execute(f"UPDATE tracks SET {', '.join(sets)} WHERE id = ?", (*vals, tid))
            conn().commit()
        return jsonify(track_dict(live_track(tid)))

    @bp.post("/api/tracks/<int:tid>/archive")
    @auth.owner
    def archive_track(tid):
        live_track(tid)
        conn().execute("UPDATE tracks SET archived_at = ? WHERE id = ? AND archived_at IS NULL", (db.now_iso(), tid))
        conn().commit()
        return jsonify(track_dict(live_track(tid)))

    @bp.post("/api/tracks/<int:tid>/unarchive")
    @auth.owner
    def unarchive_track(tid):
        live_track(tid)
        conn().execute("UPDATE tracks SET archived_at = NULL WHERE id = ?", (tid,))
        conn().commit()
        return jsonify(track_dict(live_track(tid)))

    @bp.post("/api/tracks/<int:tid>/new-link")
    @auth.owner
    def new_track_link(tid):
        """Revoke the old link: the track gets a new secret, so every copy of the old link stops working."""
        live_track(tid)
        conn().execute("UPDATE tracks SET token = ? WHERE id = ?", (db.new_token(), tid))
        conn().commit()
        return jsonify(track_dict(live_track(tid)))

    @bp.delete("/api/tracks/<int:tid>")
    @auth.owner
    def delete_track(tid):
        """Delete forever: removes the audio. Needs the exact title typed back, so it cannot happen by accident."""
        row = live_track(tid)
        body = request.get_json(silent=True) or {}
        if body.get("confirm") != row["title"]:
            return err("to delete forever, send the exact title as 'confirm'", 409)
        for name in (row["filename"], row["play_filename"]):
            if name:
                (data_dir / "audio" / name).unlink(missing_ok=True)
        conn().execute("DELETE FROM tracks WHERE id = ?", (tid,))
        conn().commit()
        return jsonify(ok=True)

    @bp.get("/api/audio/<int:tid>")
    @auth.owner
    def owner_audio(tid):
        return serve_audio(live_track(tid))

    # ---- playlists --------------------------------------------------------------------------------------------------
    def live_playlist(pid):
        row = conn().execute("SELECT * FROM playlists WHERE id = ?", (pid,)).fetchone()
        if row is None:
            abort(404)
        return row

    @bp.get("/api/playlists")
    @auth.owner
    def list_playlists():
        counts.flush()
        archived = request.args.get("archived") == "1"
        rows = conn().execute(f"SELECT * FROM playlists WHERE archived_at IS {'NOT ' if archived else ''}NULL ORDER BY created_at DESC, id DESC").fetchall()
        return jsonify([playlist_dict(r) for r in rows])

    @bp.post("/api/playlists")
    @auth.owner
    def create_playlist():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return err("send a JSON object")
        try:
            title = clean(body.get("title", ""), MAX_TITLE, "title", True)
            notes = clean(body.get("notes", ""), MAX_NOTES, "notes")
        except Invalid as exc:
            return err(str(exc))
        vis = body.get("visibility", "unlisted")
        if vis not in ("listed", "unlisted"):
            return err("visibility must be listed or unlisted")
        cur = conn().execute("INSERT INTO playlists (token, title, notes, visibility, created_at) VALUES (?,?,?,?,?)", (db.new_token(), title, notes, vis, db.now_iso()))
        conn().commit()
        return jsonify(playlist_dict(live_playlist(cur.lastrowid))), 201

    @bp.patch("/api/playlists/<int:pid>")
    @auth.owner
    def edit_playlist(pid):
        live_playlist(pid)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return err("send a JSON object")
        try:
            sets, vals = [], []
            if "title" in body:
                sets.append("title = ?"); vals.append(clean(body["title"], MAX_TITLE, "title", True))
            if "notes" in body:
                sets.append("notes = ?"); vals.append(clean(body["notes"], MAX_NOTES, "notes"))
            if "visibility" in body:
                if body["visibility"] not in ("listed", "unlisted"):
                    raise Invalid("visibility must be listed or unlisted")
                sets.append("visibility = ?"); vals.append(body["visibility"])
        except Invalid as exc:
            return err(str(exc))
        if sets:
            conn().execute(f"UPDATE playlists SET {', '.join(sets)} WHERE id = ?", (*vals, pid))
            conn().commit()
        return jsonify(playlist_dict(live_playlist(pid)))

    @bp.put("/api/playlists/<int:pid>/items")
    @auth.owner
    def set_items(pid):
        live_playlist(pid)
        body = request.get_json(silent=True)
        ids = body.get("track_ids") if isinstance(body, dict) else None
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids) or len(set(ids)) != len(ids):
            return err("track_ids must be a list of distinct track ids")
        for i in ids:
            if conn().execute("SELECT 1 FROM tracks WHERE id = ?", (i,)).fetchone() is None:
                return err(f"no track {i}", 404)
        conn().execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
        conn().executemany("INSERT INTO playlist_items (playlist_id, track_id, position) VALUES (?,?,?)", [(pid, t, n) for n, t in enumerate(ids)])
        conn().commit()
        return jsonify(playlist_dict(live_playlist(pid)))

    @bp.post("/api/playlists/<int:pid>/archive")
    @auth.owner
    def archive_playlist(pid):
        live_playlist(pid)
        conn().execute("UPDATE playlists SET archived_at = ? WHERE id = ? AND archived_at IS NULL", (db.now_iso(), pid))
        conn().commit()
        return jsonify(playlist_dict(live_playlist(pid)))

    @bp.post("/api/playlists/<int:pid>/unarchive")
    @auth.owner
    def unarchive_playlist(pid):
        live_playlist(pid)
        conn().execute("UPDATE playlists SET archived_at = NULL WHERE id = ?", (pid,))
        conn().commit()
        return jsonify(playlist_dict(live_playlist(pid)))

    @bp.post("/api/playlists/<int:pid>/new-link")
    @auth.owner
    def new_playlist_link(pid):
        live_playlist(pid)
        conn().execute("UPDATE playlists SET token = ? WHERE id = ?", (db.new_token(), pid))
        conn().commit()
        return jsonify(playlist_dict(live_playlist(pid)))

    @bp.delete("/api/playlists/<int:pid>")
    @auth.owner
    def delete_playlist(pid):
        row = live_playlist(pid)
        body = request.get_json(silent=True) or {}
        if body.get("confirm") != row["title"]:
            return err("to delete forever, send the exact title as 'confirm'", 409)
        conn().execute("DELETE FROM playlists WHERE id = ?", (pid,))          # the tracks themselves stay
        conn().commit()
        return jsonify(ok=True)

    # ---- backup and restore ---------------------------------------------------------------------------------------------
    @bp.get("/api/backup")
    @auth.owner
    def backup_status():
        return jsonify(enabled=bool(backup_dir), **(backupmod.status(backup_dir) if backup_dir else {}),
                       restore=backupmod.inspect(restore_dir) if Path(restore_dir).is_dir() else {"ok": False, "error": "no restore folder"})

    def commit_ms(directory):
        """How long a small durable write takes in `directory` (write 4 KB and wait until the disk has it): the delay behind every save."""
        out = []
        try:
            for _ in range(5):
                p = Path(directory) / (".speed-" + db.new_token())
                t = time.perf_counter()
                fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
                try:
                    os.write(fd, b"x" * 4096)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                out.append((time.perf_counter() - t) * 1000)
                p.unlink()
        except OSError:
            return None
        return round(statistics.median(out), 1)

    @bp.get("/api/diagnostics")
    @auth.owner
    def diagnostics():
        """A speed check you can run from the Studio: is it the disk, the CPU, or neither? Writes and deletes a few 4 KB test files; changes nothing else."""
        data_ms = commit_ms(data_dir / "tmp")
        boot_ms = commit_ms(cfg.get("MUSICSHARE_BOOT_TMP", "/var/tmp"))
        t = time.perf_counter()
        c = db.connect(db_path)                                       # a fresh connection: the cold path a first visit takes
        try:
            n = c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            c.execute("SELECT * FROM tracks ORDER BY created_at DESC LIMIT 50").fetchall()
        finally:
            c.close()
        read_ms = round((time.perf_counter() - t) * 1000, 1)
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            load = None
        try:
            swap = subprocess.run(["swapctl", "-s"], capture_output=True, text=True, timeout=2).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            swap = None
        cpus = os.cpu_count() or 1
        advice = []
        if data_ms is not None and data_ms > 150:
            advice.append(f"Saving to the data volume takes {data_ms} ms" + (f" (the boot disk: {boot_ms} ms)" if boot_ms is not None else "") +
                          ". That is the wait you feel when you change a track. Set MUSICSHARE_DB_DIR to a folder on the boot disk to move just the small database there; the audio stays on the volume.")
        if load and load[0] > cpus * 1.5:
            advice.append(f"The server is busy (load {load[0]} on {cpus} CPU). Something is using the processor: a track being measured, or another service.")
        if worker.pending():
            advice.append(f"{worker.pending()} track(s) are still being measured in the background; that uses the CPU and the disk until it finishes.")
        if read_ms > 300:
            advice.append(f"A cold read of the database took {read_ms} ms: the disk is slow to read as well.")
        if not advice:
            advice.append("Nothing looks slow on the server. If pages still load slowly, the time is going on the network or in relayd; open the browser's developer tools, Network tab, and compare the Server-Timing line with the total time.")
        return jsonify(commit_data_volume_ms=data_ms, commit_boot_disk_ms=boot_ms, database_read_ms=read_ms, tracks=n, load=load, cpus=cpus, swap=swap,
                       jobs_pending=worker.pending(), counts_pending=counts.pending(), database_dir=str(db_dir), advice=advice)

    @bp.post("/api/backup")
    @auth.owner
    def backup_now():
        if not backup_dir:
            return err("no backup folder is set (MUSICSHARE_BACKUP_DIR)", 409)
        try:
            target, copied, problems = backupmod.backup(data_dir, backup_dir, db_file=db_path)
        except (ValueError, OSError, sqlite3.Error) as exc:
            return err(str(exc), 500)
        return jsonify(ok=True, copy=target.name, audio_copied=copied, problems=problems)

    @bp.post("/api/restore")
    @auth.owner
    def restore_now():
        try:
            n, problems = backupmod.restore(restore_dir, data_dir, db_file=db_path)
        except (ValueError, OSError, sqlite3.Error) as exc:
            return err(str(exc), 409)
        db.init(db_path)                                   # a backup made by an older version gets the new columns
        bump()
        worker.backfill()
        return jsonify(ok=True, tracks=n, problems=problems)

    app.register_blueprint(bp, url_prefix=base or None)
    if base:
        app.add_url_rule(base, "base_redirect", auth.public(lambda: redirect(base + "/")))
    app.extensions["musicshare_worker"] = worker
    return app
