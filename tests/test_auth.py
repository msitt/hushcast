"""Web UI auth: password hashing, session tokens, and the setup/login flow."""
import time

import pytest
from fastapi.testclient import TestClient

from hushcast import auth

# ---------- pure unit tests ----------


def test_password_hash_roundtrip():
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password("correct horse battery", "garbage")
    assert not auth.verify_password("correct horse battery", "")


def test_two_hashes_of_same_password_differ():
    assert auth.hash_password("pw") != auth.hash_password("pw")  # random salt


def test_token_roundtrip(monkeypatch):
    monkeypatch.setattr(auth, "_signing_key", b"k" * 32)
    token = auth.issue_token()
    assert auth.verify_token(token)
    # tampered signature / expiry
    expires, _, sig = token.partition(".")
    assert not auth.verify_token(f"{expires}.{'0' * len(sig)}")
    assert not auth.verify_token(f"{int(expires) + 1}.{sig}")
    assert not auth.verify_token("not-a-token")
    assert not auth.verify_token("")
    # expired
    assert not auth.verify_token(auth._sign(int(time.time()) - 10))
    # different key -> invalid
    monkeypatch.setattr(auth, "_signing_key", b"x" * 32)
    assert not auth.verify_token(token)


def test_verify_token_without_credentials(monkeypatch):
    monkeypatch.setattr(auth, "_signing_key", None)
    assert not auth.verify_token("123.abc")


def test_is_protected():
    assert auth.is_protected("/api")
    assert auth.is_protected("/api/settings")
    assert auth.is_protected("/api/auth/change")
    assert not auth.is_protected("/api/auth/status")
    assert not auth.is_protected("/api/auth/login")
    assert not auth.is_protected("/api/auth/setup")
    assert not auth.is_protected("/api/auth/logout")
    assert not auth.is_protected("/p/tok/slug/feed.xml")
    assert not auth.is_protected("/healthz")
    assert not auth.is_protected("/")


# ---------- app-level tests ----------


@pytest.fixture
def make_client(tmp_path_factory, monkeypatch):
    """Real app with fresh config/data dirs. Call with auth env value or None."""

    def _make(auth_env: str | None = None) -> TestClient:
        monkeypatch.setenv("HUSHCAST_DATA_DIR", str(tmp_path_factory.mktemp("data")))
        monkeypatch.setenv("HUSHCAST_CONFIG_DIR", str(tmp_path_factory.mktemp("config")))
        if auth_env is not None:
            monkeypatch.setenv("HUSHCAST_AUTH", auth_env)
        else:
            monkeypatch.delenv("HUSHCAST_AUTH", raising=False)
        monkeypatch.setattr(auth, "FAILED_LOGIN_DELAY_S", 0)

        from hushcast import settings_store
        from hushcast.config import get_config

        get_config.cache_clear()
        settings_store.invalidate_cache()
        from hushcast.main import app

        return TestClient(app)

    yield _make
    from hushcast.config import get_config

    get_config.cache_clear()


def test_disabled_mode(make_client):
    with make_client("disabled") as client:
        status = client.get("/api/auth/status").json()
        assert status == {"mode": "disabled", "authenticated": True, "username": None}
        assert client.get("/api/settings").status_code == 200
        assert client.post("/api/auth/setup", json={"username": "a", "password": "x" * 8}).status_code == 409
        assert client.post("/api/auth/login", json={"username": "a", "password": "x" * 8}).status_code == 409


def test_setup_mode_locks_api_but_not_public_paths(make_client):
    with make_client() as client:
        assert client.get("/api/auth/status").json()["mode"] == "setup"
        assert client.get("/api/settings").status_code == 401
        assert client.get("/api/system/status").status_code == 401
        assert client.post("/api/auth/change", json={"current_password": "x"}).status_code == 401
        assert client.get("/healthz").status_code == 200
        # /p/* stays token-gated, never session-gated: 404 (unknown token), not 401
        assert client.get("/p/deadbeef/some-slug/feed.xml").status_code == 404
        # login is refused before credentials exist
        assert client.post("/api/auth/login", json={"username": "a", "password": "x" * 8}).status_code == 409


def test_setup_validation(make_client):
    with make_client() as client:
        assert client.post("/api/auth/setup", json={"username": "me", "password": "short"}).status_code == 400
        assert client.post("/api/auth/setup", json={"username": "  ", "password": "x" * 8}).status_code == 400


def test_setup_login_change_flow(make_client):
    with make_client() as client:
        # setup creates credentials and logs in
        r = client.post("/api/auth/setup", json={"username": "me", "password": "hunter2hunter2"})
        assert r.status_code == 200
        assert r.json() == {"mode": "login", "authenticated": True, "username": "me"}
        assert auth.COOKIE_NAME in client.cookies
        assert client.get("/api/settings").status_code == 200

        # setup is one-time
        r = client.post("/api/auth/setup", json={"username": "x", "password": "y" * 8})
        assert r.status_code == 409

        # logout drops the session
        client.post("/api/auth/logout")
        assert client.get("/api/settings").status_code == 401
        assert client.get("/api/auth/status").json()["authenticated"] is False

        # wrong username or password both 401
        assert client.post("/api/auth/login", json={"username": "me", "password": "wrongwrong"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "you", "password": "hunter2hunter2"}).status_code == 401
        assert auth.COOKIE_NAME not in client.cookies

        # correct login
        r = client.post("/api/auth/login", json={"username": "me", "password": "hunter2hunter2"})
        assert r.status_code == 200
        assert r.json()["username"] == "me"
        assert client.get("/api/settings").status_code == 200

        # change requires the current password
        r = client.post("/api/auth/change", json={"current_password": "nope-nope", "username": "me2"})
        assert r.status_code == 403

        # change username + password, old session cookie is replaced and works
        old_cookie = client.cookies[auth.COOKIE_NAME]
        r = client.post(
            "/api/auth/change",
            json={"current_password": "hunter2hunter2", "username": "me2", "new_password": "swordfish123"},
        )
        assert r.status_code == 200
        assert r.json()["username"] == "me2"
        assert client.get("/api/settings").status_code == 200

        # the pre-change cookie no longer verifies (signing key rotated)
        assert not auth.verify_token(old_cookie)

        # old password is dead, new one works
        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={"username": "me2", "password": "hunter2hunter2"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "me2", "password": "swordfish123"}).status_code == 200


def test_credentials_unreachable_via_settings_api(make_client):
    with make_client() as client:
        client.post("/api/auth/setup", json={"username": "me", "password": "hunter2hunter2"})
        r = client.put("/api/settings", json={"auth_username": "evil"})
        assert r.status_code == 400
        assert "auth_username" not in client.get("/api/settings").json()


def test_credentials_survive_restart(make_client, monkeypatch):
    with make_client() as client:
        client.post("/api/auth/setup", json={"username": "me", "password": "hunter2hunter2"})
        cookie = client.cookies[auth.COOKIE_NAME]
        config_dir = client.get("/api/system/info").json()["config_dir"]

    # same config dir, new app lifecycle (auth.refresh reloads from the DB)
    monkeypatch.setenv("HUSHCAST_CONFIG_DIR", config_dir)
    from hushcast import settings_store
    from hushcast.config import get_config

    get_config.cache_clear()
    settings_store.invalidate_cache()
    from hushcast.main import app

    with TestClient(app) as client2:
        assert client2.get("/api/auth/status").json()["mode"] == "login"
        client2.cookies.set(auth.COOKIE_NAME, cookie)
        assert client2.get("/api/settings").status_code == 200
