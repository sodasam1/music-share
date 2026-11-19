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
    for method, url in (("get", "/music/api/tracks"), ("get", "/music/api/playlists"), ("get", "/music/api/backup"), ("post", "/music/api/backup"),
                        ("post", "/music/api/tracks"), ("get", "/music/api/audio/1"), ("delete", "/music/api/tracks/1"), ("post", "/music/api/restore")):
        assert getattr(guest, method)(url).status_code == 401, url
    assert guest.get("/music/studio").status_code == 401
    assert guest.get("/music/studio", headers={"Accept": "text/html"}).status_code == 303


def test_every_route_is_declared(app):
    from siteauth import undeclared_routes
    assert undeclared_routes(app) == []


def test_a_cookie_for_another_app_does_not_open_music_share(app, site):
    from conftest import owner_cookie
    c = app.test_client()
    c.set_cookie("site_musicshare", owner_cookie(site, "riffbox"))
    assert c.get("/music/api/tracks").status_code == 401


def test_public_pages_need_no_login(guest, owner):
    t = make(owner, visibility="listed")
    assert guest.get("/music/").status_code == 200
    assert guest.get(f"/music/t/{t['token']}").status_code == 200
    assert guest.get("/music/health").json == {"ok": True}
    assert guest.get("/music/static/musicshare.css").status_code == 200
    assert guest.get("/music/static/minimal/minimal.css").status_code == 200


def test_static_route_does_not_serve_dotfiles_or_traversal(guest):
    for p in ("/music/static/.x", "/music/static/../studio.html/../../conftest.py", "/music/static/%2e%2e/%2e%2e/tests/conftest.py"):
        assert guest.get(p).status_code in (404, 308)


# ---- listed vs unlisted ---------------------------------------------------------------------------------------------
def test_listed_shows_on_the_public_page_unlisted_only_by_link(owner, guest):
    listed = make(owner, "Listed song.wav", "listed", seed=1)
    hidden = make(owner, "Hidden song.wav", "unlisted", seed=2)
    page = guest.get("/music/").get_data(as_text=True)
    assert "Listed song" in page and "Hidden song" not in page
    assert hidden["token"] not in page
    assert "Hidden song" in guest.get(f"/music/t/{hidden['token']}").get_data(as_text=True)
    assert guest.get(f"/music/t/{listed['token']}").status_code == 200


def test_unknown_token_is_404_and_tokens_are_unguessable(owner, guest):
    t = make(owner)
    assert guest.get("/music/t/nonsense").status_code == 404
    assert guest.get("/music/a/nonsense").status_code == 404
    assert len(t["token"]) >= 21 and re.fullmatch(r"[A-Za-z0-9_-]+", t["token"])             # 128 random bits
    assert make(owner, seed=5)["token"] != t["token"]


def test_share_pages_are_noindex(owner, guest):
    t = make(owner)
    r = guest.get(f"/music/t/{t['token']}")
    assert "noindex" in r.headers["X-Robots-Tag"] and b'name="robots"' in r.data
    assert "noindex" in guest.get(f"/music/a/{t['token']}").headers["X-Robots-Tag"]
    assert "X-Robots-Tag" not in guest.get("/music/").headers                                  # the public index may be found


def test_changing_visibility(owner, guest):
    t = make(owner, "Maybe.wav", "unlisted")
    assert "Maybe" not in guest.get("/music/").get_data(as_text=True)
    assert owner.patch(f"/music/api/tracks/{t['id']}", json={"visibility": "listed"}).json["visibility"] == "listed"
    assert "Maybe" in guest.get("/music/").get_data(as_text=True)
    assert owner.patch(f"/music/api/tracks/{t['id']}", json={"visibility": "secret"}).status_code == 400


# ---- archive, new link, delete ----------------------------------------------------------------------------------------
def test_archive_hides_from_lists_but_the_link_keeps_working(owner, guest):
    t = make(owner, "Old demo.wav", "listed")
    assert owner.post(f"/music/api/tracks/{t['id']}/archive").json["archived"] is True
    assert "Old demo" not in guest.get("/music/").get_data(as_text=True)
    assert guest.get(f"/music/t/{t['token']}").status_code == 200
    assert owner.get("/music/api/tracks").json == [] and len(owner.get("/music/api/tracks?archived=1").json) == 1
    owner.post(f"/music/api/tracks/{t['id']}/unarchive")
    assert "Old demo" in guest.get("/music/").get_data(as_text=True)


def test_archive_can_also_kill_the_link(tmp_path, site):
    from conftest import Wall, owner_cookie
    from musicshare import create_app
    app = create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "d"), "MUSICSHARE_BASE_PATH": "/music",
                      "MUSICSHARE_STATIC_DIR": str(__import__("conftest").ROOT / "static"), "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"),
                      "MUSICSHARE_INSECURE_COOKIES": "1", "MUSICSHARE_ARCHIVE_DISABLES_LINK": "1", "clock": Wall()})
    o = app.test_client(); o.set_cookie("site_musicshare", owner_cookie(site))
    t = upload(o).json["tracks"][0]
    assert app.test_client().get(f"/music/t/{t['token']}").status_code == 200
    o.post(f"/music/api/tracks/{t['id']}/archive")
    assert app.test_client().get(f"/music/t/{t['token']}").status_code == 404


def test_new_link_revokes_the_old_one(owner, guest):
    t = make(owner)
    new = owner.post(f"/music/api/tracks/{t['id']}/new-link").json
    assert new["token"] != t["token"]
    assert guest.get(f"/music/t/{t['token']}").status_code == 404 and guest.get(f"/music/a/{t['token']}").status_code == 404
    assert guest.get(f"/music/t/{new['token']}").status_code == 200


def test_delete_forever_needs_the_exact_title_and_removes_the_audio(owner, guest, app, tmp_path):
    t = make(owner, "Delete me.wav")
    files = list((tmp_path / "data" / "audio").iterdir())
    assert len(files) == 1
    assert owner.delete(f"/music/api/tracks/{t['id']}", json={"confirm": "wrong"}).status_code == 409
    assert owner.delete(f"/music/api/tracks/{t['id']}").status_code == 409
    assert files[0].exists()
    assert owner.delete(f"/music/api/tracks/{t['id']}", json={"confirm": "Delete me"}).status_code == 200
    assert not files[0].exists() and guest.get(f"/music/t/{t['token']}").status_code == 404


# ---- upload ---------------------------------------------------------------------------------------------------------------
def test_upload_many_and_title_defaults_from_filename(owner):
    r = owner.post("/music/api/tracks", data={"files": [(io.BytesIO(wav_bytes(seed=1)), "first_idea_v2.wav"), (io.BytesIO(wav_bytes(seed=2)), "second.wav")]},
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
    row = owner.get("/music/api/tracks").json[0]
    assert row["analysis_status"] == "ready" and row["peaks"] and abs(row["duration_s"] - 1.0) < 0.2


def test_audio_supports_range_requests(owner, guest):
    t = make(owner)
    full = guest.get(f"/music/a/{t['token']}")
    assert full.status_code == 200 and full.headers["Accept-Ranges"] == "bytes" and full.data[:4] == b"RIFF"
    part = guest.get(f"/music/a/{t['token']}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.data) == 100 and part.headers["Content-Range"].startswith("bytes 0-99/")


def test_download_link_is_offered_on_the_track_page_only(owner, guest):
    t = make(owner, visibility="listed")
    assert "?download=1" in guest.get(f"/music/t/{t['token']}").get_data(as_text=True)
    assert "?download=1" not in guest.get("/music/").get_data(as_text=True)
    assert "attachment" in guest.get(f"/music/a/{t['token']}?download=1").headers["Content-Disposition"]


# ---- text is text ---------------------------------------------------------------------------------------------------------------
def test_titles_and_notes_are_escaped_on_public_pages(owner, guest):
    t = make(owner, visibility="listed")
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    owner.patch(f"/music/api/tracks/{t['id']}", json={"title": evil, "notes": evil})
    for url in ("/music/", f"/music/t/{t['token']}"):
        body = guest.get(url).get_data(as_text=True)
        assert "<script>alert" not in body and "<img src=x" not in body and "&lt;script&gt;" in body


def test_title_and_notes_limits_and_control_characters(owner):
    t = make(owner)
    assert owner.patch(f"/music/api/tracks/{t['id']}", json={"title": "x" * 201}).status_code == 400
    assert owner.patch(f"/music/api/tracks/{t['id']}", json={"title": "   "}).status_code == 400
    assert owner.patch(f"/music/api/tracks/{t['id']}", json={"notes": "y" * 2001}).status_code == 400
    assert owner.patch(f"/music/api/tracks/{t['id']}", json={"title": "a\x00b\nc"}).json["title"] == "a b c"


def test_strict_headers_everywhere(guest, owner):
    t = make(owner)
    for url in ("/music/", f"/music/t/{t['token']}", "/music/health"):
        r = guest.get(url)
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "frame-ancestors 'none'" in csp
        assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert b"<script>" not in guest.get(f"/music/t/{t['token']}").data.replace(b"<script src", b"")      # no inline script


# ---- opens counter ------------------------------------------------------------------------------------------------------------------
def test_opens_are_counted_for_guests_not_for_the_owner(owner, guest):
    t = make(owner)
    owner.get(f"/music/t/{t['token']}")
    assert owner.get("/music/api/tracks").json[0]["open_count"] == 0
    guest.get(f"/music/t/{t['token']}"); guest.get(f"/music/t/{t['token']}")
    row = owner.get("/music/api/tracks").json[0]
    assert row["open_count"] == 2 and row["first_opened_at"] and row["last_opened_at"]


# ---- playlists ------------------------------------------------------------------------------------------------------------------------
def test_playlist_order_links_and_audio_access(owner, guest):
    a, b, c = (make(owner, n, seed=i) for i, n in enumerate(("Aaa.wav", "Bbb.wav", "Ccc.wav")))
    pl = owner.post("/music/api/playlists", json={"title": "For Dana"}).json
    assert owner.put(f"/music/api/playlists/{pl['id']}/items", json={"track_ids": [c["id"], a["id"]]}).json["track_ids"] == [c["id"], a["id"]]
    page = guest.get(f"/music/p/{pl['token']}").get_data(as_text=True)
    assert page.index("Ccc") < page.index("Aaa") and "Bbb" not in page and "For Dana" in page
    assert guest.get(f"/music/pa/{pl['token']}/{c['id']}").status_code == 200
    assert guest.get(f"/music/pa/{pl['token']}/{b['id']}").status_code == 404            # not in this playlist: no audio through it
    assert c["token"] not in page                                                          # the playlist link does not reveal track links
    assert "noindex" in guest.get(f"/music/p/{pl['token']}").headers["X-Robots-Tag"]


def test_playlist_visibility_archive_new_link_delete(owner, guest):
    t = make(owner, "Song.wav")
    pl = owner.post("/music/api/playlists", json={"title": "Mix", "visibility": "listed"}).json
    owner.put(f"/music/api/playlists/{pl['id']}/items", json={"track_ids": [t["id"]]})
    assert "Mix" in guest.get("/music/").get_data(as_text=True)
    owner.post(f"/music/api/playlists/{pl['id']}/archive")
    assert "Mix" not in guest.get("/music/").get_data(as_text=True)
    assert guest.get(f"/music/p/{pl['token']}").status_code == 200
    new = owner.post(f"/music/api/playlists/{pl['id']}/new-link").json
    assert guest.get(f"/music/p/{pl['token']}").status_code == 404 and guest.get(f"/music/p/{new['token']}").status_code == 200
    assert owner.delete(f"/music/api/playlists/{pl['id']}", json={"confirm": "nope"}).status_code == 409
    assert owner.delete(f"/music/api/playlists/{pl['id']}", json={"confirm": "Mix"}).status_code == 200
    assert len(owner.get("/music/api/tracks").json) == 1                                   # the track itself stays


def test_playlist_items_are_validated(owner):
    t = make(owner)
    pl = owner.post("/music/api/playlists", json={"title": "x"}).json
    for bad in ({"track_ids": [t["id"], t["id"]]}, {"track_ids": ["1"]}, {"track_ids": 5}, {}):
        assert owner.put(f"/music/api/playlists/{pl['id']}/items", json=bad).status_code == 400
    assert owner.put(f"/music/api/playlists/{pl['id']}/items", json={"track_ids": [999]}).status_code == 404


def test_deleting_a_track_removes_it_from_playlists(owner, guest):
    t = make(owner, "Gone.wav")
    pl = owner.post("/music/api/playlists", json={"title": "x"}).json
    owner.put(f"/music/api/playlists/{pl['id']}/items", json={"track_ids": [t["id"]]})
    owner.delete(f"/music/api/tracks/{t['id']}", json={"confirm": "Gone"})
    assert owner.get("/music/api/playlists").json[0]["track_ids"] == []


# ---- backup and restore -------------------------------------------------------------------------------------------------------------------
def test_backup_then_restore_into_an_empty_library_reproduces_everything(owner, tmp_path, site):
    import hashlib
    from conftest import Wall, owner_cookie
    from musicshare import create_app
    t1 = make(owner, "One.wav", "listed", seed=1)
    t2 = make(owner, "Two.wav", seed=2)
    pl = owner.post("/music/api/playlists", json={"title": "Mix"}).json
    owner.put(f"/music/api/playlists/{pl['id']}/items", json={"track_ids": [t2["id"], t1["id"]]})
    r = owner.post("/music/api/backup")
    assert r.status_code == 200 and r.json["audio_copied"] == 2 and r.json["problems"] == []
    again = owner.post("/music/api/backup").json
    assert again["audio_copied"] == 0                                             # audio is copied once
    import shutil as sh
    sh.copytree(tmp_path / "backup", tmp_path / "restore")
    app2 = create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "new"), "MUSICSHARE_BASE_PATH": "/music",
                       "MUSICSHARE_STATIC_DIR": str(__import__("conftest").ROOT / "static"), "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"),
                       "MUSICSHARE_INSECURE_COOKIES": "1", "MUSICSHARE_BACKUP_DIR": str(tmp_path / "backup2"),
                       "MUSICSHARE_RESTORE_DIR": str(tmp_path / "restore"), "clock": Wall()})
    o2 = app2.test_client(); o2.set_cookie("site_musicshare", owner_cookie(site))
    assert o2.get("/music/api/backup").json["restore"]["tracks"] == 2
    done = o2.post("/music/api/restore").json
    assert done["ok"] and done["tracks"] == 2 and done["problems"] == []
    assert [t["title"] for t in o2.get("/music/api/tracks").json] == ["Two", "One"]
    g2 = app2.test_client()
    assert g2.get(f"/music/t/{t1['token']}").status_code == 200                      # the same links work on the rebuilt server
    for t in (t1, t2):
        original = (tmp_path / "data" / "audio").glob("*").__next__().parent
    assert o2.post("/music/api/restore").status_code == 409                          # not into a library that has tracks
    assert o2.get("/music/api/playlists").json[0]["track_ids"] == [t2["id"], t1["id"]]
    for row in o2.get("/music/api/tracks").json:
        data = o2.get(row["audio"]).data
        assert hashlib.sha256(data).hexdigest() in {hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path / "data" / "audio").iterdir()}


def test_restore_reports_a_damaged_file_instead_of_hiding_it(owner, tmp_path):
    from musicshare import backup
    make(owner, "Solo.wav")
    owner.post("/music/api/backup")
    f = next((tmp_path / "backup" / "audio").iterdir())
    f.write_bytes(b"corrupted")
    n, problems = backup.restore(tmp_path / "backup", tmp_path / "fresh")
    assert n == 1 and len(problems) == 1 and "checksum mismatch" in problems[0]


def test_backup_without_a_folder_is_refused_politely(tmp_path, site):
    from conftest import Wall, owner_cookie
    from musicshare import create_app
    app = create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "d"), "MUSICSHARE_BASE_PATH": "/music",
                      "MUSICSHARE_STATIC_DIR": str(__import__("conftest").ROOT / "static"), "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"),
                      "MUSICSHARE_INSECURE_COOKIES": "1", "clock": Wall()})
    o = app.test_client(); o.set_cookie("site_musicshare", owner_cookie(site))
    assert o.post("/music/api/backup").status_code == 409 and o.get("/music/api/backup").json["enabled"] is False


def test_big_uploads_are_parked_on_the_data_volume_not_the_boot_disks_tmp(owner, tmp_path):
    import tempfile
    assert Path(tempfile.gettempdir()) == tmp_path / "data" / "tmp"             # Werkzeug's spill-over file for a large upload goes here
    big = wav_bytes(seconds=40.0, rate=16000)                                   # about 1.2 MB: above the 500 KB where Werkzeug leaves memory
    assert len(big) > 1_000_000
    assert upload(owner, data=big).status_code in (200, 201)                    # a large upload still works with the spill-over folder moved
    assert not list((tmp_path / "data" / "tmp").glob("*.part"))                 # and the upload left no half-written file behind


# ---- the quiet link to the Studio -----------------------------------------------------------------------------------------------
def test_studio_link_is_shown_only_to_the_owner(owner, guest):
    assert 'href="/music/studio"' in owner.get("/music/").get_data(as_text=True)
    page = guest.get("/music/").get_data(as_text=True)
    assert "studio" not in page.lower()                                   # strangers see no hint that an owner side exists


def test_owner_empty_state_points_at_the_studio(owner, guest):
    assert "in the Studio" in owner.get("/music/").get_data(as_text=True)
    assert "in the Studio" not in guest.get("/music/").get_data(as_text=True)
