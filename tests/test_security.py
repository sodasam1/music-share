"""Attack tests for music-share: what a stranger, or a friend holding a link, must NOT be able to do."""
import io
import re
import shutil

import pytest

from conftest import upload, wav_bytes

B = "/music-share"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
SQLI = ["'; DROP TABLE tracks;--", "\" OR \"1\"=\"1", "x' UNION SELECT token,token,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1 FROM tracks--", "1; PRAGMA writable_schema=1", "%' OR 1=1 --", "\\"]


def make(owner, name="Track.wav", visibility="unlisted", seed=0):
    r = upload(owner, name, visibility=visibility, seed=seed)
    assert r.status_code == 201, r.data
    return r.json["tracks"][0]


def tables_intact(owner):
    return owner.get(f"{B}/api/tracks").status_code == 200 and owner.get(f"{B}/api/playlists").status_code == 200


@pytest.mark.parametrize("payload", SQLI)
def test_sql_injection_text_is_stored_as_text_everywhere(owner, guest, payload):
    t = make(owner, "Inject.wav", "listed")
    assert owner.patch(f"{B}/api/tracks/{t['id']}", json={"title": payload[:200], "notes": payload}).status_code in (200, 400)
    pl = owner.post(f"{B}/api/playlists", json={"title": payload[:200] or "x", "notes": payload}).json
    owner.delete(f"{B}/api/playlists/{pl['id']}", json={"confirm": payload})
    assert tables_intact(owner)
    assert len(owner.get(f"{B}/api/tracks").json) == 1
    for url in (f"{B}/t/{payload}", f"{B}/p/{payload}", f"{B}/a/{payload}", f"{B}/pa/{payload}/1", f"{B}/static/{payload}"):
        assert guest.get(url).status_code in (404, 308, 400), url
    assert owner.get(f"{B}/api/tracks", query_string={"archived": payload}).status_code == 200
    assert tables_intact(owner)


def test_unlisted_tracks_and_tokens_are_never_enumerable(owner, guest):
    hidden = make(owner, "Secret demo.wav", "unlisted")
    make(owner, "Public demo.wav", "listed", seed=2)
    page = guest.get(f"{B}/").get_data(as_text=True)
    assert "Secret demo" not in page and hidden["token"] not in page and "Public demo" in page
    for url in (f"{B}/api/tracks", f"{B}/api/playlists", f"{B}/api/backup", f"{B}/sitemap.xml", f"{B}/robots.txt", f"{B}/t/", f"{B}/a/"):
        assert guest.get(url).status_code in (401, 404, 308), url


def test_extra_fields_in_an_edit_cannot_change_things_they_should_not(owner):
    t = make(owner)
    r = owner.patch(f"{B}/api/tracks/{t['id']}", json={"title": "ok", "id": 999, "token": "AAAAAAAAAAAAAAAAAAAAAA", "filename": "../../etc/passwd", "sha256": "x",
                                                        "play_count": 9999, "archived_at": "2020-01-01", "visibility": "unlisted", "mime": "text/html"})
    assert r.status_code == 200
    after = owner.get(f"{B}/api/tracks").json[0]
    assert after["id"] == t["id"] and after["token"] == t["token"] and after["play_count"] == 0 and not after["archived"] and after["title"] == "ok"


def test_the_host_header_is_never_reflected(owner, guest):
    t = make(owner, visibility="listed")
    for url in (f"{B}/", f"{B}/t/{t['token']}"):
        r = guest.get(url, headers={"Host": "evil.example", "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http"})
        assert b"evil.example" not in r.data and "evil.example" not in str(r.headers)


def test_the_cached_public_page_never_shows_the_owner_view_to_a_stranger_or_the_reverse(owner, guest):
    make(owner, visibility="listed")
    assert 'href="/music-share/studio"' in owner.get(f"{B}/").get_data(as_text=True)
    assert "studio" not in guest.get(f"{B}/").get_data(as_text=True).lower()
    assert "studio" not in guest.get(f"{B}/").get_data(as_text=True).lower()          # and again, now from the cache
    assert 'href="/music-share/studio"' in owner.get(f"{B}/").get_data(as_text=True)


def test_a_forged_or_other_apps_cookie_does_not_make_a_stranger_the_owner_on_the_public_page(guest):
    guest.set_cookie("site_musicshare", "A" * 120, path="/")
    assert "studio" not in guest.get(f"{B}/").get_data(as_text=True).lower()
    assert guest.get(f"{B}/api/tracks").status_code == 401


def test_a_file_name_cannot_inject_headers_or_break_the_download(owner, guest):
    for name in ('evil"; x=1.wav', "line\r\nX-Injected: 1.wav", "a\x00b.wav", "../../etc/passwd.wav", "ünï©ode ✓.wav", "C:\\windows\\system32\\x.wav", "a" * 300 + ".wav"):
        r = upload(owner, name)
        assert r.status_code in (201, 400), (name, r.status_code)          # refusing a hostile name outright is as good as sanitising it
        if r.status_code == 400:
            continue
        t = r.json["tracks"][0]
        d = guest.get(f"{B}/a/{t['token']}?download=1")
        assert d.status_code == 200 and "X-Injected" not in d.headers and "x=1" not in d.headers.get("Content-Disposition", "").replace('\\"', "").split("filename")[0]
        assert d.headers["Content-Disposition"].count("\n") == 0 and "\r" not in d.headers["Content-Disposition"]


@needs_ffmpeg
def test_a_playlist_or_script_disguised_as_audio_cannot_read_server_files(owner, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT")
    bait = f"#EXTM3U\n#EXTINF:1,x\nfile://{secret}\nconcat:{secret}\n".encode()
    for name in ("trick.wav", "trick.mp3", "trick.m4a", "trick.flac", "trick.ogg", "trick.aac"):
        r = upload(owner, name, data=bait)
        assert r.status_code == 201
    rows = owner.get(f"{B}/api/tracks").json
    assert rows and all(t["analysis_status"] == "failed" for t in rows)               # ffmpeg refused them: the input format is forced from the extension
    for t in rows:
        assert "TOP-SECRET" not in str(t) and t["peaks"] == []


def test_oversize_uploads_are_refused(tmp_path, site):
    from conftest import ROOT, Wall, owner_cookie
    from musicshare import create_app
    app = create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "d"), "MUSICSHARE_BASE_PATH": "/music-share", "MUSICSHARE_MAX_UPLOAD_MB": "1",
                      "MUSICSHARE_STATIC_DIR": str(ROOT / "static"), "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"), "MUSICSHARE_INSECURE_COOKIES": "1", "clock": Wall()})
    o = app.test_client(); o.set_cookie("site_musicshare", owner_cookie(site))
    assert upload(o, "big.wav", data=b"RIFF" + b"\0" * (2 * 1024 * 1024)).status_code == 413


def test_public_routes_accept_only_reading(owner, guest):
    t = make(owner, visibility="listed")
    for url in (f"{B}/", f"{B}/t/{t['token']}", f"{B}/a/{t['token']}", f"{B}/health"):
        for m in ("post", "put", "delete", "patch"):
            assert getattr(guest, m)(url).status_code in (401, 403, 405), (m, url)
    r = guest.get(f"{B}/t/{t['token']}", headers={"X-HTTP-Method-Override": "DELETE"})
    assert r.status_code == 200 and len(owner.get(f"{B}/api/tracks").json) == 1


def test_no_cross_origin_reading_is_allowed(owner, guest):
    t = make(owner, visibility="listed")
    for client, url in ((guest, f"{B}/"), (owner, f"{B}/api/tracks"), (guest, f"{B}/a/{t['token']}")):
        r = client.get(url, headers={"Origin": "https://evil.example"})
        assert "Access-Control-Allow-Origin" not in r.headers and "Access-Control-Allow-Credentials" not in r.headers


def test_cross_site_writes_are_refused_even_with_the_owner_cookie(owner):
    t = make(owner)
    for hdr in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}):
        assert owner.patch(f"{B}/api/tracks/{t['id']}", json={"title": "pwned"}, headers=hdr).status_code == 403
        assert owner.delete(f"{B}/api/tracks/{t['id']}", json={"confirm": t["title"]}, headers=hdr).status_code == 403
    assert owner.get(f"{B}/api/tracks").json[0]["title"] == t["title"]


def test_every_answer_carries_the_security_headers(owner, guest):
    t = make(owner, visibility="listed")
    for client, url in ((guest, f"{B}/"), (guest, f"{B}/t/{t['token']}"), (guest, f"{B}/health"), (guest, f"{B}/static/listen.css"), (owner, f"{B}/studio"), (owner, f"{B}/api/tracks"),
                        (guest, f"{B}/nothing-here")):
        r = client.get(url)
        assert r.headers["X-Content-Type-Options"] == "nosniff", url
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and "'unsafe-inline'" not in r.headers["Content-Security-Policy"], url
        assert r.headers["Referrer-Policy"] == "same-origin", url


def test_errors_do_not_reveal_internals(guest, owner):
    for r in (guest.get(f"{B}/nothing-here"), guest.get(f"{B}/t/x"), owner.get(f"{B}/api/tracks/99999"), owner.patch(f"{B}/api/tracks/99999", json={})):
        body = r.get_data(as_text=True)
        assert "Traceback" not in body and "/home/" not in body and "site-packages" not in body and "sqlite" not in body.lower()


def test_secret_files_in_the_static_folder_cannot_be_fetched(guest):
    for p in ("app/wsgi.py", "../app/wsgi.py", "../../etc/passwd", "..%2f..%2fetc%2fpasswd", "%2e%2e/%2e%2e/etc/passwd", ".git/config", "studio.html", "templates/studio.html", "musicshare.db", "../data/musicshare.db"):
        r = guest.get(f"{B}/static/{p}")
        assert r.status_code in (404, 308) and b"root:" not in r.data and b"import " not in r.data, p


def test_tokens_are_long_random_and_unique(owner):
    tokens = {make(owner, f"T{i}.wav", seed=i)["token"] for i in range(40)}
    assert len(tokens) == 40 and all(len(t) >= 21 and re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in tokens)
