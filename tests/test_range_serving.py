"""Verify FileResponse serves byte ranges (podcast clients stream/seek with Range)."""
import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    path = tmp_path_factory.mktemp("audio") / "ep.mp3"
    path.write_bytes(bytes(range(256)) * 40)  # 10240 bytes

    app = FastAPI()

    @app.get("/audio.mp3")
    @app.head("/audio.mp3")
    async def audio():
        return FileResponse(path, media_type="audio/mpeg")

    return TestClient(app)


def test_full_get(client):
    r = client.get("/audio.mp3")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert int(r.headers["content-length"]) == 10240


def test_head(client):
    r = client.head("/audio.mp3")
    assert r.status_code == 200
    assert int(r.headers["content-length"]) == 10240
    assert r.content == b""


def test_range_206(client):
    r = client.get("/audio.mp3", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 100-199/10240"
    assert len(r.content) == 100


def test_open_ended_range(client):
    r = client.get("/audio.mp3", headers={"Range": "bytes=10200-"})
    assert r.status_code == 206
    assert len(r.content) == 40


def test_unsatisfiable_range(client):
    r = client.get("/audio.mp3", headers={"Range": "bytes=999999-"})
    assert r.status_code == 416
