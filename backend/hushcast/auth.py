"""Web UI auth: credentials in the settings table, HMAC-signed session cookie.

Modes (reported by GET /api/auth/status):
- "disabled": HUSHCAST_AUTH=disabled, something in front (reverse proxy)
  handles auth.
- "setup": no credentials stored yet (fresh install / first upgrade to an
  auth-capable version). The API is locked, the UI shows a one-time
  create-your-login page.
- "login": credentials exist, the UI shows the password page.

Sessions are stateless: a signed expiry timestamp in an HttpOnly cookie. The
signing key mixes a per-install secret (config_dir/session_secret) with the
stored password hash, so changing the password invalidates every session
without a session table. Credential rows live in the settings table under keys
outside settings_store.DEFAULTS, which keeps them unreachable through the
settings API.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .api.deps import get_session
from .config import get_config
from .models import Setting

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "hushcast_session"
SESSION_TTL_S = 30 * 24 * 3600
MIN_PASSWORD_LEN = 8
MAX_FIELD_LEN = 200
FAILED_LOGIN_DELAY_S = 1.0

_USERNAME_KEY = "auth_username"
_PASSWORD_HASH_KEY = "auth_password_hash"

# Interactive-login scrypt cost (~16 MiB, tens of ms), parameters are stored
# with each hash so they can be raised later without breaking old hashes.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 16384, 8, 1

# /api paths reachable without a session (everything the login/setup screens
# need). /api/auth/change is deliberately NOT here: it requires a session.
PUBLIC_API_PATHS = {
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
    "/api/auth/logout",
}

# Cached credential state, loaded at startup and updated on credential writes.
# Single-process app, so a module-level cache is safe.
_username: str | None = None
_signing_key: bytes | None = None


# ---------- password hashing ----------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


# ---------- session tokens ----------


def _load_secret() -> bytes:
    """Per-install signing secret, generated once and kept on the config
    volume so sessions survive container recreation."""
    path = get_config().config_dir / "session_secret"
    if path.is_file():
        secret = path.read_text().strip()
        if secret:
            return secret.encode()
    secret = secrets.token_hex(32)
    path.write_text(secret)
    return secret.encode()


def _derive_key(password_hash: str) -> bytes:
    return hmac.new(_load_secret(), password_hash.encode(), hashlib.sha256).digest()


def _sign(expires_at: int) -> str:
    assert _signing_key is not None
    sig = hmac.new(_signing_key, str(expires_at).encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{sig}"


def issue_token() -> str:
    return _sign(int(time.time()) + SESSION_TTL_S)


def verify_token(token: str) -> bool:
    if _signing_key is None:
        return False
    expires_str, _, sig = token.partition(".")
    try:
        expires_at = int(expires_str)
    except ValueError:
        return False
    if expires_at < time.time():
        return False
    expected = hmac.new(_signing_key, expires_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


# ---------- credential storage ----------


async def refresh(session: AsyncSession) -> None:
    """(Re)load cached credential state from the DB. Call once at startup."""
    global _username, _signing_key
    username_row = await session.get(Setting, _USERNAME_KEY)
    hash_row = await session.get(Setting, _PASSWORD_HASH_KEY)
    if username_row is not None and hash_row is not None:
        _username = json.loads(username_row.value)
        _signing_key = _derive_key(json.loads(hash_row.value))
    else:
        _username = None
        _signing_key = None


async def _store_credentials(session: AsyncSession, username: str, password: str) -> None:
    global _username, _signing_key
    password_hash = hash_password(password)
    for key, value in ((_USERNAME_KEY, username), (_PASSWORD_HASH_KEY, password_hash)):
        row = await session.get(Setting, key)
        encoded = json.dumps(value)
        if row is None:
            session.add(Setting(key=key, value=encoded, is_secret=True))
        else:
            row.value = encoded
    await session.commit()
    _username = username
    _signing_key = _derive_key(password_hash)


async def _stored_password_hash(session: AsyncSession) -> str | None:
    row = await session.get(Setting, _PASSWORD_HASH_KEY)
    return json.loads(row.value) if row is not None else None


# ---------- request guard ----------


def mode() -> str:
    if get_config().auth_disabled:
        return "disabled"
    return "login" if _signing_key is not None else "setup"


def request_authenticated(request: Request) -> bool:
    if get_config().auth_disabled:
        return True
    token = request.cookies.get(COOKIE_NAME)
    return bool(token) and verify_token(token)


def is_protected(path: str) -> bool:
    return (path == "/api" or path.startswith("/api/")) and path not in PUBLIC_API_PATHS


# ---------- endpoints ----------


class SetupBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


class ChangeBody(BaseModel):
    current_password: str
    username: str | None = None
    new_password: str | None = None


def _set_session_cookie(response: Response) -> None:
    response.set_cookie(
        COOKIE_NAME,
        issue_token(),
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _status_payload(authenticated: bool) -> dict:
    m = mode()
    authed = m == "disabled" or (m == "login" and authenticated)
    return {
        "mode": m,
        "authenticated": authed,
        "username": _username if (authed and m == "login") else None,
    }


def _validate_username(username: str) -> str:
    username = username.strip()
    if not username or len(username) > MAX_FIELD_LEN:
        raise HTTPException(400, "username must be 1-200 characters")
    return username


def _validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password) > MAX_FIELD_LEN:
        raise HTTPException(400, "password too long")
    return password


@router.get("/status")
async def auth_status(request: Request) -> dict:
    return _status_payload(request_authenticated(request))


@router.post("/setup")
async def setup(
    body: SetupBody, response: Response, session: AsyncSession = Depends(get_session)
) -> dict:
    if mode() != "setup":
        raise HTTPException(409, "setup already completed")
    username = _validate_username(body.username)
    _validate_password(body.password)
    await _store_credentials(session, username, body.password)
    _set_session_cookie(response)
    log.info("auth: initial credentials created for %r", username)
    return _status_payload(True)


@router.post("/login")
async def login(
    body: LoginBody, request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> dict:
    if mode() != "login":
        raise HTTPException(409, "login is not available in this mode")
    stored_hash = await _stored_password_hash(session)
    username_ok = _username is not None and hmac.compare_digest(
        body.username.encode(), _username.encode()
    )
    if not (username_ok and stored_hash and verify_password(body.password, stored_hash)):
        log.warning("auth: failed login from %s", request.client.host if request.client else "?")
        await asyncio.sleep(FAILED_LOGIN_DELAY_S)
        raise HTTPException(401, "invalid username or password")
    _set_session_cookie(response)
    return _status_payload(True)


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/change")
async def change_credentials(
    body: ChangeBody, response: Response, session: AsyncSession = Depends(get_session)
) -> dict:
    # Reached only with a valid session (not in PUBLIC_API_PATHS).
    if mode() != "login":
        raise HTTPException(409, "credentials cannot be changed in this mode")
    stored_hash = await _stored_password_hash(session)
    if not (stored_hash and verify_password(body.current_password, stored_hash)):
        await asyncio.sleep(FAILED_LOGIN_DELAY_S)
        raise HTTPException(403, "current password is incorrect")
    username = _validate_username(body.username) if body.username is not None else _username
    password = (
        _validate_password(body.new_password)
        if body.new_password is not None
        else body.current_password
    )
    assert username is not None
    await _store_credentials(session, username, password)
    # The signing key changed with the password hash, killing every session
    # (including this one). Reissue so the caller stays logged in.
    _set_session_cookie(response)
    log.info("auth: credentials updated (username=%r)", username)
    return _status_payload(True)
