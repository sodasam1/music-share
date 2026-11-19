import io
from pathlib import Path
import json
import os
import re
import shutil

import pytest

from conftest import upload, wav_bytes

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make(owner, name="Demo one.wav", visibility="unlisted", seed=0):
    r = upload(owner, name, visibility=visibility, seed=seed)
    assert r.status_code == 201, r.data
    return r.json["tracks"][0]


# ---- who can do what -------------------------------------------------------------------------------------------
def test_owner_api_needs_the_owner(guest):
    for method, url in (("get", "/music-share/api/tracks"), ("get", "/music-share/api/playlists"), ("get", "/music-share/api/backup"), ("post", "/music-share/api/backup"),
                        ("post", "/music-share/api/tracks"), ("get", "/music-share/api/audio/1"), ("delete", "/music-share/api/tracks/1"), ("post", "/music-share/api/restore")):
        assert getattr(guest, method)(url).status_code == 401, url
    assert guest.get("/music-share/studio").status_code == 401
    assert guest.get("/music-share/studio", headers={"Accept": "text/html"}).status_code == 303


def test_every_route_is_declared(app):
    from siteauth import undeclared_routes
    assert undeclared_routes(app) == []


def test_a_cookie_for_another_app_does_not_open_music_share(app, site):
    from conftest import owner_cookie
    c = app.test_client()
    c.set_cookie("site_musicshare", owner_cookie(site, "riffbox"))
    assert c.get("/music-share/api/tracks").status_code == 401


def test_public_pages_need_no_login(guest, owner):
    t = make(owner, visibility="listed")
    assert guest.get("/music-share/").status_code == 200
    assert guest.get(f"/music-share/t/{t['token']}").status_code == 200
    assert guest.get("/music-share/health").json == {"ok": True}
    assert guest.get("/music-share/static/musicshare.css").status_code == 200
    assert guest.get("/music-share/static/minimal/minimal.css").status_code == 200


def test_static_route_does_not_serve_dotfiles_or_traversal(guest):
    for p in ("/music-share/static/.x", "/music-share/static/../studio.html/../../conftest.py", "/music-share/static/%2e%2e/%2e%2e/tests/conftest.py"):
        assert guest.get(p).status_code in (404, 308)


# ---- listed vs unlisted ---------------------------------------------------------------------------------------------
def test_listed_shows_on_the_public_page_unlisted_only_by_link(owner, guest):
    listed = make(owner, "Listed song.wav", "listed", seed=1)
    hidden = make(owner, "Hidden song.wav", "unlisted", seed=2)
    page = guest.get("/music-share/").get_data(as_text=True)
    assert "Listed song" in page and "Hidden song" not in page
    assert hidden["token"] not in page
    assert "Hidden song" in guest.get(f"/music-share/t/{hidden['token']}").get_data(as_text=True)
    assert guest.get(f"/music-share/t/{listed['token']}").status_code == 200


def test_unknown_token_is_404_and_tokens_are_unguessable(owner, guest):
    t = make(owner)
    assert guest.get("/music-share/t/nonsense").status_code == 404
    assert guest.get("/music-share/a/nonsense").status_code == 404
    assert len(t["token"]) >= 21 and re.fullmatch(r"[A-Za-z0-9_-]+", t["token"])             # 128 random bits
    assert make(owner, seed=5)["token"] != t["token"]


def test_share_pages_are_noindex(owner, guest):
    t = make(owner)
    r = guest.get(f"/music-share/t/{t['token']}")
    assert "noindex" in r.headers["X-Robots-Tag"] and b'name="robots"' in r.data
    assert "noindex" in guest.get(f"/music-share/a/{t['token']}").headers["X-Robots-Tag"]
    assert "X-Robots-Tag" not in guest.get("/music-share/").headers                                  # the public index may be found


def test_changing_visibility(owner, guest):
    t = make(owner, "Maybe.wav", "unlisted")
    assert "Maybe" not in guest.get("/music-share/").get_data(as_text=True)
    assert owner.patch(f"/music-share/api/tracks/{t['id']}", json={"visibility": "listed"}).json["visibility"] == "listed"
    assert "Maybe" in guest.get("/music-share/").get_data(as_text=True)
    assert owner.patch(f"/music-share/api/tracks/{t['id']}", json={"visibility": "secret"}).status_code == 400


# ---- archive, new link, delete ----------------------------------------------------------------------------------------
def test_archive_hides_from_lists_but_the_link_keeps_working(owner, guest):
    t = make(owner, "Old demo.wav", "listed")
    assert owner.post(f"/music-share/api/tracks/{t['id']}/archive").json["archived"] is True
    assert "Old demo" not in guest.get("/music-share/").get_data(as_text=True)
    assert guest.get(f"/music-share/t/{t['token']}").status_code == 200
    assert owner.get("/music-share/api/tracks").json == [] and len(owner.get("/music-share/api/tracks?archived=1").json) == 1
    owner.post(f"/music-share/api/tracks/{t['id']}/unarchive")
    assert "Old demo" in guest.get("/music-share/").get_data(as_text=True)


def test_archive_can_also_kill_the_link(tmp_path, site):
    from conftest import Wall, owner_cookie
    from musicshare import create_app
    app = create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "d"), "MUSICSHARE_BASE_PATH": "/music-share",
                      "MUSICSHARE_STATIC_DIR": str(__import__("conftest").ROOT / "static"), "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"),
                      "MUSICSHARE_INSECURE_COOKIES": "1", "MUSICSHARE_ARCHIVE_DISABLES_LINK": "1", "clock": Wall()})
    o = app.test_client(); o.set_cookie("site_musicshare", owner_cookie(site))
    t = upload(o).json["tracks"][0]
    assert app.test_client().get(f"/music-share/t/{t['token']}").status_code == 200
    o.post(f"/music-share/api/tracks/{t['id']}/archive")
    assert app.test_client().get(f"/music-share/t/{t['token']}").status_code == 404


def test_new_link_revokes_the_old_one(owner, guest):
    t = make(owner)
    new = owner.post(f"/music-share/api/tracks/{t['id']}/new-link").json
    assert new["token"] != t["token"]
    assert guest.get(f"/music-share/t/{t['token']}").status_code == 404 and guest.get(f"/music-share/a/{t['token']}").status_code == 404
    assert guest.get(f"/music-share/t/{new['token']}").status_code == 200


def test_delete_forever_needs_the_exact_title_and_removes_the_audio(owner, guest, app, tmp_path):
    t = make(owner, "Delete me.wav")
    files = list((tmp_path / "data" / "audio").iterdir())
    assert len(files) == 1
    assert owner.delete(f"/music-share/api/tracks/{t['id']}", json={"confirm": "wrong"}).status_code == 409
    assert owner.delete(f"/music-share/api/tracks/{t['id']}").status_code == 409
    assert files[0].exists()
    assert owner.delete(f"/music-share/api/tracks/{t['id']}", json={"confirm": "Delete me"}).status_code == 200
    assert not files[0].exists() and guest.get(f"/music-share/t/{t['token']}").status_code == 404


# ---- upload ---------------------------------------------------------------------------------------------------------------
def test_upload_many_and_title_defaults_from_filename(owner):
    r = owner.post("/music-share/api/tracks", data={"files": [(io.BytesIO(wav_bytes(seed=1)), "first_idea_v2.wav"), (io.BytesIO(wav_bytes(seed=2)), "second.wav")]},
                   content_type="multipart/form-data")
    assert r.status_code == 201 and [t["title"] for t in r.json["tracks"]] == ["first idea v2", "second"]


def test_upload_rejects_non_audio_and_sniffs_missing_extensions(owner):
    bad = upload(owner, "notes.txt", data=b"hello world, not audio at all")
    assert bad.status_code == 415 and bad.json["problems"]
    ok = upload(owner, "no_extension", data=wav_bytes())
    assert ok.status_code == 201 and ok.json["tracks"][0]["orig_filename"] == "no_extension"


def test_upload_path_tricks_in_the_file_name_cannot_escape(owner, tmp_path):
    r = upload(owner, "../../etc/passwd.wav", data=wav_bytes())
    assert r.status_code == 201 and r.json["tracks"][0]["orig_filename"] == "passwd.wav"
    assert not (tmp_path / "etc").exists()
    assert all(p.parent == tmp_path / "data" / "audio" for p in (tmp_path / "data" / "audio").iterdir())


@needs_ffmpeg
def test_peaks_and_duration_are_measured(owner):
    t = make(owner)
    row = owner.get("/music-share/api/tracks").json[0]
    assert row["analysis_status"] == "ready" and row["peaks"] and abs(row["duration_s"] - 1.0) < 0.2


def test_audio_supports_range_requests(owner, guest):
    t = make(owner)
    full = guest.get(f"/music-share/a/{t['token']}")
    assert full.status_code == 200 and full.headers["Accept-Ranges"] == "bytes" and full.data[:4] == b"RIFF"
    part = guest.get(f"/music-share/a/{t['token']}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.data) == 100 and part.headers["Content-Range"].startswith("bytes 0-99/")


def test_download_link_is_offered_on_the_track_page_only(owner, guest):
    t = make(owner, visibility="listed")
    assert "?download=1" in guest.get(f"/music-share/t/{t['token']}").get_data(as_text=True)
    assert "?download=1" not in guest.get("/music-share/").get_data(as_text=True)
    assert "attachment" in guest.get(f"/music-share/a/{t['token']}?download=1").headers["Content-Disposition"]


# ---- text is text ---------------------------------------------------------------------------------------------------------------
def test_titles_and_notes_are_escaped_on_public_pages(owner, guest):
    t = make(owner, visibility="listed")
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    owner.patch(f"/music-share/api/tracks/{t['id']}", json={"title": evil, "notes": evil})
    for url in ("/music-share/", f"/music-share/t/{t['token']}"):
        body = guest.get(url).get_data(as_text=True)
        assert "<script>alert" not in body and "<img src=x" not in body and "&lt;script&gt;" in body


def test_title_and_notes_limits_and_control_characters(owner):
    t = make(owner)
    assert owner.patch(f"/music-share/api/tracks/{t['id']}", json={"title": "x" * 201}).status_code == 400
    assert owner.patch(f"/music-share/api/tracks/{t['id']}", json={"title": "   "}).status_code == 400
    assert owner.patch(f"/music-share/api/tracks/{t['id']}", json={"notes": "y" * 2001}).status_code == 400
    assert owner.patch(f"/music-share/api/tracks/{t['id']}", json={"title": "a\x00b\nc"}).json["title"] == "a b c"


def test_strict_headers_everywhere(guest, owner):
    t = make(owner)
    for url in ("/music-share/", f"/music-share/t/{t['token']}", "/music-share/health"):
        r = guest.get(url)
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "frame-ancestors 'none'" in csp
        assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert b"<script>" not in guest.get(f"/music-share/t/{t['token']}").data.replace(b"<script src", b"")      # no inline script


# ---- opens counter ------------------------------------------------------------------------------------------------------------------
def test_opens_are_counted_for_guests_not_for_the_owner(owner, guest):
    t = make(owner)
    owner.get(f"/music-share/t/{t['token']}")
    assert owner.get("/music-share/api/tracks").json[0]["open_count"] == 0
    guest.get(f"/music-share/t/{t['token']}"); guest.get(f"/music-share/t/{t['token']}")
    row = owner.get("/music-share/api/tracks").json[0]
    assert row["open_count"] == 2 and row["first_opened_at"] and row["last_opened_at"]


# ---- playlists ------------------------------------------------------------------------------------------------------------------------
def test_playlist_order_links_and_audio_access(owner, guest):
    a, b, c = (make(owner, n, seed=i) for i, n in enumerate(("Aaa.wav", "Bbb.wav", "Ccc.wav")))
    pl = owner.post("/music-share/api/playlists", json={"title": "For Dana"}).json
    assert owner.put(f"/music-share/api/playlists/{pl['id']}/items", json={"track_ids": [c["id"], a["id"]]}).json["track_ids"] == [c["id"], a["id"]]
    page = guest.get(f"/music-share/p/{pl['token']}").get_data(as_text=True)
    assert page.index("Ccc") < page.index("Aaa") and "Bbb" not in page and "For Dana" in page
    assert guest.get(f"/music-share/pa/{pl['token']}/{c['id']}").status_code == 200
    assert guest.get(f"/music-share/pa/{pl['token']}/{b['id']}").status_code == 404            # not in this playlist: no audio through it
    assert c["token"] not in page                                                          # the playlist link does not reveal track links
    assert "noindex" in guest.get(f"/music-share/p/{pl['token']}").headers["X-Robots-Tag"]


def test_playlist_visibility_archive_new_link_delete(owner, guest):
    t = make(owner, "Song.wav")
    pl = owner.post("/music-share/api/playlists", json={"title": "Mix", "visibility": "listed"}).json
    owner.put(f"/music-share/api/playlists/{pl['id']}/items", json={"track_ids": [t["id"]]})
    assert "Mix" in guest.get("/music-share/").get_data(as_text=True)
    owner.post(f"/music-share/api/playlists/{pl['id']}/archive")
    assert "Mix" not in guest.get("/music-share/").get_data(as_text=True)
    assert guest.get(f"/music-share/p/{pl['token']}").status_code == 200
    new = owner.post(f"/music-share/api/playlists/{pl['id']}/new-link").json
    assert guest.get(f"/music-share/p/{pl['token']}").status_code == 404 and guest.get(f"/music-share/p/{new['token']}").status_code == 200
    assert owner.delete(f"/music-share/api/playlists/{pl['id']}", json={"confirm": "nope"}).status_code == 409
    assert owner.delete(f"/music-share/api/playlists/{pl['id']}", json={"confirm": "Mix"}).status_code == 200
    assert len(owner.get("/music-share/api/tracks").json) == 1                                   # the track itself stays


def test_playlist_items_are_validated(owner):
    t = make(owner)
    pl = owner.post("/music-share/api/playlists", json={"title": "x"}).json
    for bad in ({"track_ids": [t["id"], t["id"]]}, {"track_ids": ["1"]}, {"track_ids": 5}, {}):
        assert owner.put(f"/music-share/api/playlists/{pl['id']}/items", json=bad).status_code == 400
    assert owner.put(f"/music-share/api/playlists/{pl['id']}/items", json={"track_ids": [999]}).status_code == 404


def test_deleting_a_track_removes_it_from_playlists(owner, guest):
    t = make(owner, "Gone.wav")
    pl = owner.post("/music-share/api/playlists", json={"title": "x"}).json
    owner.put(f"/music-share/api/playlists/{pl['id']}/items", json={"track_ids": [t["id"]]})
    owner.delete(f"/music-share/api/tracks/{t['id']}", json={"confirm": "Gone"})
    assert owner.get("/music-share/api/playlists").json[0]["track_ids"] == []


# ---- backup and restore -------------------------------------------------------------------------------------------------------------------
def test_backup_then_restore_into_an_empty_library_reproduces_everything(owner, tmp_path, site):
    import hashlib
    from conftest import Wall, owner_cookie
    from musicshare import create_app
    t1 = make(owner, "One.wav", "listed", seed=1)
    t2 = make(owner, "Two.wav", seed=2)
    pl = owner.post("/music-share/api/playlists", json={"title": "Mix"}).json
    owner.put(f"/music-share/api/playlists/{pl['id']}/items", json={"track_ids": [t2["id"], t1["id"]]})
    r = owner.post("/music-share/api/backup")
    assert r.status_code == 200 and r.json["audio_copied"] == 2 and r.json["problems"] == []
    again = owner.post("/music-share/api/backup").json
    assert again["audio_copied"] == 0                                             # audio is copied once
    import shutil as sh
    sh.copytree(tmp_path / "backup", tmp_path / "restore")
    app2 = create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "new"), "MUSICSHARE_BASE_PATH": "/music-share",
                       "MUSICSHARE_STATIC_DIR": str(__import__("conftest").ROOT / "static"), "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"),
                       "MUSICSHARE_INSECURE_COOKIES": "1", "MUSICSHARE_BACKUP_DIR": str(tmp_path / "backup2"),
                       "MUSICSHARE_RESTORE_DIR": str(tmp_path / "restore"), "clock": Wall()})
    o2 = app2.test_client(); o2.set_cookie("site_musicshare", owner_cookie(site))
    assert o2.get("/music-share/api/backup").json["restore"]["tracks"] == 2
    done = o2.post("/music-share/api/restore").json
    assert done["ok"] and done["tracks"] == 2 and done["problems"] == []
    assert [t["title"] for t in o2.get("/music-share/api/tracks").json] == ["Two", "One"]
    g2 = app2.test_client()
    assert g2.get(f"/music-share/t/{t1['token']}").status_code == 200                      # the same links work on the rebuilt server
    for t in (t1, t2):
        original = (tmp_path / "data" / "audio").glob("*").__next__().parent
    assert o2.post("/music-share/api/restore").status_code == 409                          # not into a library that has tracks
    assert o2.get("/music-share/api/playlists").json[0]["track_ids"] == [t2["id"], t1["id"]]
    for row in o2.get("/music-share/api/tracks").json:
        data = o2.get(row["audio"]).data
        assert hashlib.sha256(data).hexdigest() in {hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path / "data" / "audio").iterdir()}


def test_restore_reports_a_damaged_file_instead_of_hiding_it(owner, tmp_path):
    from musicshare import backup
    make(owner, "Solo.wav")
    owner.post("/music-share/api/backup")
    f = next((tmp_path / "backup" / "audio").iterdir())
    f.write_bytes(b"corrupted")
    n, problems = backup.restore(tmp_path / "backup", tmp_path / "fresh")
    assert n == 1 and len(problems) == 1 and "checksum mismatch" in problems[0]


def test_backup_without_a_folder_is_refused_politely(tmp_path, site):
    from conftest import Wall, owner_cookie
    from musicshare import create_app
    app = create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "d"), "MUSICSHARE_BASE_PATH": "/music-share",
                      "MUSICSHARE_STATIC_DIR": str(__import__("conftest").ROOT / "static"), "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"),
                      "MUSICSHARE_INSECURE_COOKIES": "1", "clock": Wall()})
    o = app.test_client(); o.set_cookie("site_musicshare", owner_cookie(site))
    assert o.post("/music-share/api/backup").status_code == 409 and o.get("/music-share/api/backup").json["enabled"] is False


def test_big_uploads_are_parked_on_the_data_volume_not_the_boot_disks_tmp(owner, tmp_path):
    import tempfile
    assert Path(tempfile.gettempdir()) == tmp_path / "data" / "tmp"             # Werkzeug's spill-over file for a large upload goes here
    big = wav_bytes(seconds=40.0, rate=16000)                                   # about 1.2 MB: above the 500 KB where Werkzeug leaves memory
    assert len(big) > 1_000_000
    assert upload(owner, data=big).status_code in (200, 201)                    # a large upload still works with the spill-over folder moved
    assert not list((tmp_path / "data" / "tmp").glob("*.part"))                 # and the upload left no half-written file behind


# ---- the quiet link to the Studio -----------------------------------------------------------------------------------------------
def test_studio_link_is_shown_only_to_the_owner(owner, guest):
    assert 'href="/music-share/studio"' in owner.get("/music-share/").get_data(as_text=True)
    page = guest.get("/music-share/").get_data(as_text=True)
    assert "studio" not in page.lower()                                   # strangers see no hint that an owner side exists


def test_owner_empty_state_points_at_the_studio(owner, guest):
    assert "Upload tracks" in owner.get("/music-share/").get_data(as_text=True)
    assert "Upload tracks" not in guest.get("/music-share/").get_data(as_text=True)


# ---- speed: timing header and compression ------------------------------------------------------------------------------------------
def test_server_timing_header_on_answers(owner, guest):
    for client, url in ((owner, "/music-share/api/tracks"), (guest, "/music-share/"), (guest, "/music-share/health")):
        h = client.get(url).headers.get("Server-Timing", "")
        assert "app;dur=" in h and float(h.rsplit("app;dur=", 1)[1]) >= 0


def test_big_json_and_html_are_gzipped_when_asked_and_plain_otherwise(owner, guest):
    import gzip as gz
    for i in range(8):
        make(owner, f"Track number {i}.wav", seed=i)
    plain = owner.get("/music-share/api/tracks")
    zipped = owner.get("/music-share/api/tracks", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in plain.headers
    assert zipped.headers["Content-Encoding"] == "gzip" and "Accept-Encoding" in zipped.headers["Vary"]
    assert json.loads(gz.decompress(zipped.data)) == plain.json
    assert "Content-Encoding" not in guest.get("/music-share/health", headers={"Accept-Encoding": "gzip"}).headers      # small answers are left alone


def test_audio_is_never_compressed(owner, guest):
    t = make(owner)
    r = guest.get(f"/music-share/a/{t['token']}", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
    assert r.status_code == 206 and "Content-Encoding" not in r.headers


def test_studio_page_carries_its_menu_and_placeholders_in_the_html(owner):
    """The menu and grey placeholders must be in the page itself, so they show before any script or data request finishes."""
    html = owner.get("/music-share/studio").get_data(as_text=True)
    for name in ("Tracks", "Archive", "Playlists", "Backup"):
        assert f">{name}</button>" in html
    assert html.count('class="skeleton"') == 3 and 'id="tabs"' in html


# ---- listeners are counted, robots are not ------------------------------------------------------------------------------------------
ROBOT_UA = {"User-Agent": "Mozilla/5.0 (compatible; Slackbot-LinkExpanding 1.0)"}
PERSON = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15 Safari/604.1"}


def stats(owner):
    row = owner.get("/music-share/api/tracks").json[0]
    return row["open_count"], row["play_count"]


def test_link_preview_robots_and_head_requests_are_not_counted_as_opens(owner, guest):
    t = make(owner)
    guest.get(f"/music-share/t/{t['token']}", headers=ROBOT_UA)
    guest.head(f"/music-share/t/{t['token']}", headers=PERSON)
    guest.get(f"/music-share/t/{t['token']}", headers={"User-Agent": "WhatsApp/2.23"})
    assert stats(owner) == (0, 0)
    guest.get(f"/music-share/t/{t['token']}", headers=PERSON)
    assert stats(owner) == (1, 0)


def test_a_play_is_counted_when_the_audio_starts_from_the_beginning_once_per_listener(owner, guest, app):
    t = make(owner)
    guest.get(f"/music-share/a/{t['token']}", headers={**PERSON, "Range": "bytes=0-"})
    guest.get(f"/music-share/a/{t['token']}", headers={**PERSON, "Range": "bytes=0-"})            # the browser asking again: the same play
    assert stats(owner)[1] == 1
    guest.get(f"/music-share/a/{t['token']}", headers={**PERSON, "Range": "bytes=5000-"})          # seeking is not a new play
    assert stats(owner)[1] == 1
    other = app.test_client()
    other.get(f"/music-share/a/{t['token']}", headers={**PERSON, "X-Forwarded-For": "8.8.8.8"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert stats(owner)[1] == 2                                                                      # a different listener
    guest.get(f"/music-share/a/{t['token']}", headers={**ROBOT_UA})
    owner.get(f"/music-share/a/{t['token']}")
    assert stats(owner)[1] == 2                                                                      # robots and the owner never count
    row = owner.get("/music-share/api/tracks").json[0]
    assert row["last_played_at"]


def test_plays_through_a_playlist_count_for_the_track(owner, guest):
    t = make(owner)
    pl = owner.post("/music-share/api/playlists", json={"title": "x"}).json
    owner.put(f"/music-share/api/playlists/{pl['id']}/items", json={"track_ids": [t["id"]]})
    guest.get(f"/music-share/pa/{pl['token']}/{t['id']}", headers={**PERSON, "Range": "bytes=0-"})
    assert stats(owner)[1] == 1


def test_owner_can_always_download_the_original_even_when_the_link_download_is_off(tmp_path, site):
    from conftest import Wall, owner_cookie
    from musicshare import create_app
    app = create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "d"), "MUSICSHARE_BASE_PATH": "/music-share",
                      "MUSICSHARE_STATIC_DIR": str(__import__("conftest").ROOT / "static"), "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"),
                      "MUSICSHARE_INSECURE_COOKIES": "1", "MUSICSHARE_SHOW_DOWNLOAD": "0", "clock": Wall()})
    o = app.test_client(); o.set_cookie("site_musicshare", owner_cookie(site))
    t = upload(o).json["tracks"][0]
    assert "attachment" in o.get(f"/music-share/api/audio/{t['id']}?download=1").headers["Content-Disposition"]
    g = app.test_client()
    assert "attachment" not in g.get(f"/music-share/a/{t['token']}?download=1").headers.get("Content-Disposition", "")
    assert "Download" not in g.get(f"/music-share/t/{t['token']}").get_data(as_text=True)


# ---- speed: assets are cached for a year by content, compressed once --------------------------------------------------------------------
def test_pages_refer_to_versioned_assets_that_cache_for_a_year(guest):
    import re
    page = guest.get("/music-share/").get_data(as_text=True)
    urls = re.findall(r'(?:href|src)="(/music-share/static/[^"]+\?v=[0-9a-f]{8})"', page)
    assert len(urls) >= 3
    r = guest.get(urls[0])
    assert r.status_code == 200 and "immutable" in r.headers["Cache-Control"] and "max-age=31536000" in r.headers["Cache-Control"]
    assert guest.get("/music-share/static/listen.css").headers["Cache-Control"] == "no-cache"             # a bare address is always revalidated


def test_static_text_files_are_gzipped_and_support_304(guest):
    import gzip as gz
    plain = guest.get("/music-share/static/listen.css")
    zipped = guest.get("/music-share/static/listen.css", headers={"Accept-Encoding": "gzip"})
    assert zipped.headers["Content-Encoding"] == "gzip" and gz.decompress(zipped.data) == plain.data and len(zipped.data) < len(plain.data) * 0.6
    again = guest.get("/music-share/static/listen.css", headers={"If-None-Match": plain.headers["ETag"]})
    assert again.status_code == 304 and not again.data


def test_static_route_still_refuses_traversal_and_dotfiles(guest):
    for p in ("/music-share/static/../app/musicshare/__init__.py", "/music-share/static/.x", "/music-share/static/%2e%2e/wsgi.py", "/music-share/static/nothing.css"):
        assert guest.get(p).status_code in (404, 308)


# ---- an older database is upgraded, an older backup is too -------------------------------------------------------------------------------------
def test_a_version_1_database_gets_the_new_columns_and_keeps_its_rows(tmp_path):
    import sqlite3
    from musicshare import db
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript(db.SCHEMA.replace("  play_count INTEGER NOT NULL DEFAULT 0,           -- how many times someone pressed play (the audio was fetched from the start)\n  last_played_at TEXT\n", "  x INTEGER\n").replace("  last_opened_at TEXT,\n  x INTEGER", "  last_opened_at TEXT"))
    c.execute("INSERT INTO tracks (token, title, created_at, filename, orig_filename, mime, size, sha256) VALUES ('t','Old','2026-01-01T00:00:00Z','f.wav','f.wav','audio/wav',1,'x')")
    c.execute("PRAGMA user_version = 1"); c.commit(); c.close()
    db.init(path)
    c = db.connect(path)
    row = c.execute("SELECT title, play_count, last_played_at FROM tracks").fetchone()
    assert tuple(row) == ("Old", 0, None) and c.execute("PRAGMA user_version").fetchone()[0] == db.VERSION
    db.init(path)                                                                                          # and running it again is harmless


def test_public_pages_ship_smaller_peak_lists_than_the_studio(owner, guest):
    t = make(owner, visibility="listed")
    studio_len = len(owner.get("/music-share/api/tracks").json[0]["peaks"])
    page = guest.get("/music-share/").get_data(as_text=True)
    import re
    bars = len(re.search(r'data-peaks="([^"]*)"', page).group(1).split(","))
    assert bars <= 100 and (studio_len == 0 or bars <= studio_len)


def test_pages_carry_a_favicon_and_link_preview_text_but_no_social_parts(owner, guest):
    t = make(owner, "Preview me.wav")
    owner.patch(f"/music-share/api/tracks/{t['id']}", json={"notes": "rough mix, drums too loud"})
    page = guest.get(f"/music-share/t/{t['token']}").get_data(as_text=True)
    assert 'rel="icon"' in page and 'property="og:title" content="Preview me"' in page and "rough mix, drums too loud" in page
    for social in ("like", "follow", "share on", "repost", "comment", "subscribe"):
        assert social not in page.lower().replace("link", "").replace("nofollow", "")


# ---- the public pages do not wait on the disk ---------------------------------------------------------------------------------------------------------
def test_a_repeat_visit_to_a_public_page_touches_no_database(owner, guest, monkeypatch):
    from musicshare import db
    t = make(owner, "Cached.wav", "listed")
    first = guest.get("/music-share/")
    assert first.status_code == 200 and b"Cached" in first.data
    track_first = guest.get(f"/music-share/t/{t['token']}", headers=ROBOT_UA)
    def boom(*a, **k):
        raise AssertionError("the database was used for a repeat view")
    monkeypatch.setattr(db, "thread_conn", boom)
    monkeypatch.setattr(db, "connect", boom)
    assert guest.get("/music-share/").data == first.data
    assert guest.get(f"/music-share/t/{t['token']}", headers=ROBOT_UA).data == track_first.data      # (a robot's view is not counted, so even the tally stays off the disk)


def test_cached_pages_are_refreshed_the_moment_the_owner_changes_something(owner, guest):
    t = make(owner, "Before.wav", "listed")
    assert b"Before" in guest.get("/music-share/").data
    owner.patch(f"/music-share/api/tracks/{t['id']}", json={"title": "After rename"})
    page = guest.get("/music-share/").data
    assert b"After rename" in page and b"Before" not in page
    owner.patch(f"/music-share/api/tracks/{t['id']}", json={"visibility": "unlisted"})
    assert b"After rename" not in guest.get("/music-share/").data                          # taken off the public page at once
    new = owner.post(f"/music-share/api/tracks/{t['id']}/new-link").json
    assert guest.get(f"/music-share/t/{t['token']}").status_code == 404 and guest.get(f"/music-share/t/{new['token']}").status_code == 200


def test_the_owner_never_gets_a_stale_page(owner):
    make(owner, "Mine.wav", "listed")
    owner.get("/music-share/")
    make(owner, "Second.wav", "listed", seed=3)
    assert b"Second" in owner.get("/music-share/").data


def test_pages_answer_304_when_the_browser_already_has_them_and_are_gzipped_once(guest, owner):
    import gzip as gz
    for i in range(6):
        make(owner, f"Many {i}.wav", "listed", seed=i)
    plain = guest.get("/music-share/")
    zipped = guest.get("/music-share/", headers={"Accept-Encoding": "gzip"})
    assert zipped.headers["Content-Encoding"] == "gzip" and gz.decompress(zipped.data) == plain.data
    again = guest.get("/music-share/", headers={"Accept-Encoding": "gzip", "If-None-Match": zipped.headers["ETag"]})
    assert again.status_code == 304 and not again.data


def test_a_404_for_an_unknown_token_is_never_cached(guest):
    assert guest.get("/music-share/t/nothing").status_code == 404
    assert guest.get("/music-share/t/nothing").status_code == 404


def test_counts_are_buffered_and_flushed_before_the_studio_shows_them(owner, guest, app):
    t = make(owner)
    assert guest.get(f"/music-share/t/{t['token']}", headers=PERSON).status_code == 200
    assert stats(owner)[0] == 1


def test_the_counter_tally_batches_and_does_not_lose_counts(tmp_path):
    from musicshare import db
    from musicshare.counts import Counts
    path = tmp_path / "c.db"
    db.init(path)
    c = db.connect(path)
    c.execute("INSERT INTO tracks (token, title, created_at, filename, orig_filename, mime, size, sha256) VALUES ('tk','T','2026-01-01T00:00:00Z','f','f','audio/wav',1,'x')")
    c.commit(); c.close()
    counts = Counts(path, inline=False)
    for _ in range(5):
        counts.opened("tracks", "tk")
    counts.played("token", "tk"); counts.played("id", 1)
    assert counts.pending() == 3
    r = db.connect(path).execute("SELECT open_count, play_count FROM tracks").fetchone()
    assert tuple(r) == (0, 0)                                        # nothing written yet: a view costs no disk access
    counts.flush()
    r = db.connect(path).execute("SELECT open_count, play_count, first_opened_at IS NOT NULL FROM tracks").fetchone()
    assert tuple(r) == (5, 2, 1) and counts.pending() == 0
    counts.flush()                                                   # nothing to do, nothing breaks


def test_one_connection_per_thread_sees_other_connections_writes(tmp_path):
    from musicshare import db
    path = tmp_path / "t.db"
    db.init(path)
    a = db.thread_conn(path)
    assert db.thread_conn(path) is a
    other = db.connect(path)
    other.execute("INSERT INTO tracks (token, title, created_at, filename, orig_filename, mime, size, sha256) VALUES ('x','Seen','2026-01-01T00:00:00Z','f','f','audio/wav',1,'x')")
    other.commit(); other.close()
    assert a.execute("SELECT title FROM tracks").fetchone()[0] == "Seen"


def test_bundles_join_the_real_files_and_cache_for_a_year(guest):
    import re
    page = guest.get("/music-share/").get_data(as_text=True)
    assert len(re.findall(r'(?:href|src)="/music-share/static/', page)) == 3                          # theme.js, one stylesheet, one script
    css = guest.get(re.search(r'href="(/music-share/static/bundle-public\.css\?v=[0-9a-f]{8})"', page).group(1))
    js = guest.get(re.search(r'src="(/music-share/static/bundle-public\.js\?v=[0-9a-f]{8})"', page).group(1))
    assert css.status_code == 200 and "immutable" in css.headers["Cache-Control"] and b".play-btn" in css.data and b"--accent" in css.data
    assert js.status_code == 200 and b"window.Wave" in js.data and b"player-bar" not in js.data or b"bar-play" in js.data
    assert guest.get("/music-share/static/bundle-nothing.js").status_code == 404


def test_speed_check_reports_disks_load_and_advice(owner, guest):
    r = owner.get("/music-share/api/diagnostics")
    d = r.json
    assert r.status_code == 200 and d["commit_data_volume_ms"] is not None and d["database_read_ms"] >= 0 and d["advice"] and "load" in d
    assert guest.get("/music-share/api/diagnostics").status_code == 401


def test_database_can_live_on_another_disk_and_backup_and_restore_still_work(tmp_path, site):
    import shutil
    from conftest import Wall, owner_cookie
    from musicshare import create_app
    cfg = {"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_BASE_PATH": "/music-share", "MUSICSHARE_STATIC_DIR": str(__import__("conftest").ROOT / "static"),
           "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"), "MUSICSHARE_INSECURE_COOKIES": "1", "clock": Wall()}
    app = create_app({**cfg, "MUSICSHARE_DATA_DIR": str(tmp_path / "volume"), "MUSICSHARE_DB_DIR": str(tmp_path / "boot"),
                      "MUSICSHARE_BACKUP_DIR": str(tmp_path / "bk"), "MUSICSHARE_RESTORE_DIR": str(tmp_path / "rs")})
    o = app.test_client(); o.set_cookie("site_musicshare", owner_cookie(site))
    t = upload(o).json["tracks"][0]
    assert (tmp_path / "boot" / "musicshare.db").is_file() and not (tmp_path / "volume" / "musicshare.db").exists()
    assert o.post("/music-share/api/backup").json["audio_copied"] == 1
    shutil.copytree(tmp_path / "bk", tmp_path / "rs")
    app2 = create_app({**cfg, "MUSICSHARE_DATA_DIR": str(tmp_path / "v2"), "MUSICSHARE_DB_DIR": str(tmp_path / "b2"), "MUSICSHARE_RESTORE_DIR": str(tmp_path / "rs")})
    o2 = app2.test_client(); o2.set_cookie("site_musicshare", owner_cookie(site))
    assert o2.post("/music-share/api/restore").json["tracks"] == 1
    assert app2.test_client().get(f"/music-share/t/{t['token']}").status_code == 200 and (tmp_path / "b2" / "musicshare.db").is_file()


def test_pages_are_set_up_for_phones(owner, guest):
    for client, url in ((guest, "/music-share/"), (owner, "/music-share/studio")):
        html = client.get(url).get_data(as_text=True)
        assert "viewport-fit=cover" in html and 'name="theme-color"' in html
    css = guest.get(re.search(r'href="([^"]*bundle-public\.css[^"]*)"', guest.get("/music-share/").get_data(as_text=True)).group(1)).get_data(as_text=True)
    assert "pointer: coarse" in css and "safe-area-inset-bottom" in css and "font-size: 16px" in css
    js = guest.get(re.search(r'src="([^"]*bundle-public\.js[^"]*)"', guest.get("/music-share/").get_data(as_text=True)).group(1)).get_data(as_text=True)
    assert "mediaSession" in js and 'pan-y' in js
