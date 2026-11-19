import io
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from musicshare import create_app  # noqa: E402
from siteauth import keys, make_token  # noqa: E402

NOW = 1_800_000_000.0


class Wall:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def wav_bytes(seconds=1.0, rate=8000, seed=0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(bytes(((i + seed) % 251) for i in range(int(seconds * rate) * 2)))
    return buf.getvalue()


@pytest.fixture
def site(tmp_path):
    d = tmp_path / "site-auth"
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "app" / "siteauth" / "keys.py"), *a, "--no-chown"], check=True, capture_output=True)   # noqa: E731
    run("init", "--dir", str(d))
    run("ensure-app", "--dir", str(d), "--app", "musicshare", "--path", "/music-share")
    run("ensure-app", "--dir", str(d), "--app", "riffbox", "--path", "/ideas")
    return d


@pytest.fixture
def app(tmp_path, site):
    return create_app({"TESTING": True, "JOBS_SYNC": True, "MUSICSHARE_DATA_DIR": str(tmp_path / "data"), "MUSICSHARE_BASE_PATH": "/music-share",
                       "MUSICSHARE_STATIC_DIR": str(ROOT / "static"), "MUSICSHARE_SITE_KEY_FILE": str(site / "keys" / "musicshare.key"),
                       "MUSICSHARE_INSECURE_COOKIES": "1", "MUSICSHARE_BACKUP_DIR": str(tmp_path / "backup"),
                       "MUSICSHARE_RESTORE_DIR": str(tmp_path / "restore"), "clock": Wall()})


def owner_cookie(site, app_name="musicshare"):
    master = keys.read_first_line(str(site / "master"))
    return make_token(keys.derive(master, app_name, keys.read_first_line(str(site / "generation"))), NOW)


@pytest.fixture
def owner(app, site):
    c = app.test_client()
    c.set_cookie("site_musicshare", owner_cookie(site))
    return c


@pytest.fixture
def guest(app):
    return app.test_client()


def upload(client, name="Demo one.wav", data=None, visibility="unlisted", seed=0):
    return client.post("/music-share/api/tracks", data={"files": (io.BytesIO(data or wav_bytes(seed=seed)), name), "visibility": visibility},
                       content_type="multipart/form-data")
