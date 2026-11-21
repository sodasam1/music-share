"""Streaming copies, lossy/lossless playback, preview pictures, replacing audio, and choosing several tracks at once."""
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from conftest import ROOT, Wall, owner_cookie, upload, wav_bytes
from musicshare import create_app, db, streamcopy

B = "/music-share"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make(owner, name="Track.wav", visibility="unlisted", seed=0, seconds=2.0):
    r = upload(owner, name, data=wav_bytes(seconds, rate=44100, seed=seed), visibility=visibility)
    assert r.status_code == 201, r.data
    return r.json["tracks"][0]


def row(owner, tid):
    return next(t for t in owner.get(f"{B}/api/tracks").json if t["id"] == tid)


# ---- the small streaming copy -------------------------------------------------------------------------------------------
@needs_ffmpeg
def test_friends_stream_a_small_copy_and_download_gives_the_original(owner, guest):
    t = make(owner, seconds=4)
    assert row(owner, t["id"])["stream_status"] == "ready"
    original = guest.get(f"{B}/a/{t['token']}?download=1")
    small = guest.get(f"{B}/a/{t['token']}")
    assert original.status_code == 200 and original.data[:4] == b"RIFF" and "attachment" in original.headers["Content-Disposition"]
    assert small.status_code == 200 and small.mimetype == "audio/mp4" and small.data[4:8] == b"ftyp"
    assert len(small.data) < len(original.data) / 3
    part = guest.get(f"{B}/a/{t['token']}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.data) == 100


@needs_ffmpeg
def test_lossless_switch_serves_the_original_and_lossy_is_the_default(owner, guest):
    t = make(owner)
    default = guest.get(f"{B}/a/{t['token']}")
    lossless = guest.get(f"{B}/a/{t['token']}?q=lossless")
    assert default.mimetype == "audio/mp4" and lossless.mimetype == "audio/wav" and lossless.data[:4] == b"RIFF"
    assert lossless.headers.get("Content-Disposition", "inline").startswith("inline")             # played, not downloaded
    studio = owner.get(f"{B}/api/audio/{t['id']}")
    assert studio.mimetype == "audio/mp4" and owner.get(f"{B}/api/audio/{t['id']}?q=lossless").mimetype == "audio/wav"


@needs_ffmpeg
def test_a_track_always_plays_even_when_its_copy_is_missing(owner, guest, tmp_path):
    t = make(owner)
    for f in (tmp_path / "data" / "audio").glob("*.stream.m4a"):
        f.unlink()                                                   # e.g. deleted by hand, or not made yet
    r = guest.get(f"{B}/a/{t['token']}")
    assert r.status_code == 200 and r.data[:4] == b"RIFF"            # the next best file: the original


def test_when_is_a_copy_needed():
    assert streamcopy.needs_copy("a.wav", 200, 30_000_000)
    assert streamcopy.needs_copy("a.flac", 200, 20_000_000)
    assert not streamcopy.needs_copy("a.mp3", 200, 200 * 96_000 // 8)          # a 96 kbps MP3 is already small
    assert streamcopy.needs_copy("a.mp3", 200, 200 * 320_000 // 8)             # a 320 kbps MP3 is worth shrinking
    assert not streamcopy.needs_copy("a.ogg", None, 5_000_000)                 # unknown length: leave it


def test_an_old_database_gets_the_new_columns(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript(db.SCHEMA.replace("  stream_filename TEXT,", "").replace("  stream_status TEXT NOT NULL DEFAULT 'pending',", "").replace("  stream_filename TEXT,  ", ""))
    c.execute("INSERT INTO tracks (token,title,created_at,filename,orig_filename,mime,size,sha256) VALUES ('t','T','2026-01-01T00:00:00Z','f.wav','f.wav','audio/wav',1,'x')")
    c.execute("PRAGMA user_version = 2"); c.commit(); c.close()
    db.init(p)
    c = sqlite3.connect(p)
    cols = {r[1] for r in c.execute("PRAGMA table_info(tracks)")}
    assert {"stream_filename", "stream_status"} <= cols
    assert c.execute("SELECT stream_status FROM tracks").fetchone()[0] == "pending"
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.VERSION


# ---- preview pictures ----------------------------------------------------------------------------------------------------
def test_preview_picture_is_a_small_png_reachable_with_the_link_only(owner, guest):
    t = make(owner)
    r = guest.get(f"{B}/t/{t['token']}/preview.png")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data[:8] == b"\x89PNG\r\n\x1a\n" and len(r.data) < 20_000
    assert "Cache-Control" in r.headers and r.headers["X-Robots-Tag"].startswith("noindex")
    again = guest.get(f"{B}/t/{t['token']}/preview.png", headers={"If-None-Match": r.headers["ETag"]})
    assert again.status_code == 304
    assert guest.get(f"{B}/t/not-a-token/preview.png").status_code == 404
    assert b"Track" not in r.data                                          # no text is drawn into the picture


def test_pages_name_the_preview_picture_and_use_the_public_address_when_set(owner, guest, tmp_path, site):
    t = make(owner)
    page = guest.get(f"{B}/t/{t['token']}").get_data(as_text=True)
    assert f'property="og:image" content="{B}/t/{t["token"]}/preview.png"' in page and "summary_large_image" in page
    app = create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "d2"), "MUSICSHARE_BASE_PATH": B, "MUSICSHARE_STATIC_DIR": str(ROOT / "static"),
                      "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"), "MUSICSHARE_INSECURE_COOKIES": "1",
                      "MUSICSHARE_PUBLIC_URL": "https://example.test/", "clock": Wall()})
    o = app.test_client(); o.set_cookie("site_musicshare", owner_cookie(site))
    t2 = make(o)
    page = app.test_client().get(f"{B}/t/{t2['token']}").get_data(as_text=True)
    assert f'content="https://example.test{B}/t/{t2["token"]}/preview.png"' in page


def test_playlist_preview(owner, guest):
    t = make(owner)
    pl = owner.post(f"{B}/api/playlists", json={"title": "Set"}).json
    owner.put(f"{B}/api/playlists/{pl['id']}/items", json={"track_ids": [t["id"]]})
    assert guest.get(f"{pl['link']}/preview.png").data[:4] == b"\x89PNG"
    assert guest.get(f"{B}/p/nope/preview.png").status_code == 404


# ---- replacing the audio behind a link --------------------------------------------------------------------------------------
def put_audio(client, tid, data, name="new.wav"):
    import io
    return client.put(f"{B}/api/tracks/{tid}/audio", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")


@needs_ffmpeg
def test_replacing_keeps_the_link_title_and_counts_and_moves_the_old_file(owner, guest, tmp_path):
    t = make(owner, "Demo.wav", "listed", seed=1)
    guest.get(f"{B}/a/{t['token']}")                                      # one play
    before = row(owner, t["id"])
    old_audio = [p for p in (tmp_path / "data" / "audio").iterdir()]
    r = put_audio(owner, t["id"], wav_bytes(3.0, rate=44100, seed=9))
    assert r.status_code == 200 and r.json["token"] == t["token"] and r.json["title"] == "Demo"
    after = row(owner, t["id"])
    assert after["token"] == before["token"] and after["title"] == before["title"] and after["visibility"] == "listed"
    assert after["size"] != before["size"] and after["analysis_status"] == "ready" and after["stream_status"] == "ready"
    assert guest.get(f"{B}/a/{t['token']}?download=1").data == wav_bytes(3.0, rate=44100, seed=9)
    kept = list((tmp_path / "data" / "replaced").iterdir())
    assert len(kept) == 1 and kept[0].read_bytes() == wav_bytes(2.0, rate=44100, seed=1)      # the old original is kept, not deleted
    names = {p.name for p in (tmp_path / "data" / "audio").iterdir()}
    assert not any(p.name in names for p in old_audio)                      # the old files (and their copies) are gone from audio/
    assert after["play_count"] == before["play_count"]


def test_replacing_refuses_bad_files_and_strangers(owner, guest):
    t = make(owner)
    assert put_audio(owner, t["id"], b"this is not audio", "x.txt").status_code == 415
    assert owner.put(f"{B}/api/tracks/{t['id']}/audio").status_code == 400
    assert put_audio(guest, t["id"], wav_bytes()).status_code == 401
    assert put_audio(owner, 9999, wav_bytes()).status_code == 404
    assert owner.put(f"{B}/api/tracks/{t['id']}/audio", data={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert guest.get(f"{B}/a/{t['token']}?download=1").data == wav_bytes(2.0, rate=44100, seed=0)


# ---- choosing several tracks -------------------------------------------------------------------------------------------------
def bulk(client, **body):
    return client.post(f"{B}/api/tracks/bulk", json=body)


def test_bulk_list_unlist_archive_unarchive(owner, guest):
    ts = [make(owner, f"T{i}.wav", seed=i) for i in range(3)]
    ids = [t["id"] for t in ts]
    assert bulk(owner, action="list", ids=ids[:2]).json["changed"] == 2
    vis = {t["id"]: t["visibility"] for t in owner.get(f"{B}/api/tracks").json}
    assert vis[ids[0]] == vis[ids[1]] == "listed" and vis[ids[2]] == "unlisted"
    assert "T0" in guest.get(f"{B}/").get_data(as_text=True) and "T2" not in guest.get(f"{B}/").get_data(as_text=True)      # one request, and the public page follows
    assert bulk(owner, action="unlist", ids=ids).status_code == 200
    assert "T0" not in guest.get(f"{B}/").get_data(as_text=True)
    assert bulk(owner, action="archive", ids=ids[:2]).status_code == 200
    assert [t["id"] for t in owner.get(f"{B}/api/tracks").json] == [ids[2]]
    assert len(owner.get(f"{B}/api/tracks?archived=1").json) == 2
    assert bulk(owner, action="unarchive", ids=ids[:2]).status_code == 200 and len(owner.get(f"{B}/api/tracks").json) == 3


def test_bulk_add_to_playlist_in_order_without_duplicates(owner):
    ts = [make(owner, f"T{i}.wav", seed=i) for i in range(3)]
    ids = [t["id"] for t in ts]
    pl = owner.post(f"{B}/api/playlists", json={"title": "Set"}).json
    assert bulk(owner, action="add_to_playlist", ids=ids[:2], playlist_id=pl["id"]).status_code == 200
    assert bulk(owner, action="add_to_playlist", ids=ids, playlist_id=pl["id"]).status_code == 200
    got = next(p for p in owner.get(f"{B}/api/playlists").json if p["id"] == pl["id"])["track_ids"]
    assert got == ids[:2] + [ids[2]] and len(got) == len(set(got))


def test_bulk_is_strict_about_what_it_accepts(owner, guest):
    t = make(owner)
    for body in ({}, {"action": "delete", "ids": [t["id"]]}, {"action": "list"}, {"action": "list", "ids": []}, {"action": "list", "ids": ["1"]},
                 {"action": "list", "ids": [True]}, {"action": "list", "ids": list(range(501))}, {"action": "list", "ids": "all"}):
        assert bulk(owner, **body).status_code == 400, body
    assert bulk(owner, action="list", ids=[99999]).status_code == 404
    assert bulk(owner, action="add_to_playlist", ids=[t["id"]], playlist_id=999).status_code == 404
    assert bulk(owner, action="add_to_playlist", ids=[t["id"]]).status_code == 404
    assert bulk(guest, action="list", ids=[t["id"]]).status_code == 401
    assert owner.post(f"{B}/api/tracks/bulk", json={"action": "list", "ids": [t["id"]]}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert owner.get(f"{B}/api/tracks").json[0]["visibility"] == "unlisted"          # nothing above changed anything
    assert len(owner.get(f"{B}/api/tracks").json) == 1                              # and there is no bulk delete


def test_the_pages_carry_the_playback_switch_and_the_new_scripts(owner, guest):
    for client, url in ((guest, f"{B}/"), (owner, f"{B}/studio")):
        html = client.get(url).get_data(as_text=True)
        assert 'id="quality"' in html
        js = client.get(re.search(r'src="([^"]*bundle-[a-z]+\.js[^"]*)"', html).group(1)).get_data(as_text=True)
        assert "musicshare.quality" in js and "q=lossless" in js
