"""User accounts: web login (cookie session + rotated refresh), the extension device login, the
self-service settings (password, provider keys, translation defaults), and the capture-token mint
the extension uses to open the ingest WebSocket (Chunk 2).

Accounts are created out-of-band by an admin (``python -m app.admin``); there is no public signup.
Provider API keys are encrypted at rest (app/crypto.py) and never returned in any response.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app import crypto
from app.auth.dependencies import REFRESH_COOKIE, SESSION_COOKIE, require_device, require_user
from app.auth.passwords import WeakPassword, hash_password, validate_password, verify_password
from app.auth.sessions import hash_token, issue_access_token, new_opaque_token
from app.auth.tokens import issue_capability_token
from app.config import DEV_ENVS
from app.db import repo
from app.db.models import TokenKind, User
from app.logging import get_logger

router = APIRouter(tags=["accounts"])
log = get_logger("accounts")


# --- request bodies ---


class LoginBody(BaseModel):
    email: str
    password: str
    client: Literal["web", "device"] = "web"


class PasswordChangeBody(BaseModel):
    current_password: str
    new_password: str


class KeysBody(BaseModel):
    soniox_key: str | None = None
    anthropic_key: str | None = None


class SettingsBody(BaseModel):
    default_translation_on: bool | None = None
    default_output_language: str | None = None
    default_model: str | None = None


class CaptureTokenBody(BaseModel):
    platform: Literal["meet", "teams", "web"]
    external_meeting_id: str
    title: str | None = None  # optional display name (e.g. the browser tab title for a web capture)


# --- helpers ---


@cache
def _dummy_hash() -> str:
    # Verify against this when the email is unknown, so login timing doesn't leak existence.
    return hash_password("timing-equalizer")


def _user_public(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "is_admin": user.is_admin,
        "default_translation_on": user.default_translation_on,
        "default_output_language": user.default_output_language,
        "default_model": user.default_model,
        "keys_set": {
            "soniox": bool(user.soniox_key_enc),
            "anthropic": bool(user.anthropic_key_enc),
        },
    }


def _set_web_cookies(response: Response, access: str, refresh: str, settings) -> None:
    secure = settings.app_env not in DEV_ENVS
    response.set_cookie(
        SESSION_COOKIE, access, max_age=settings.session_ttl_s,
        httponly=True, secure=secure, samesite="lax", path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh, max_age=settings.refresh_ttl_s,
        httponly=True, secure=secure, samesite="lax", path="/",
    )


def _clear_web_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


# --- auth flow ---


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    settings = request.app.state.settings
    async with request.app.state.session_factory() as db:
        user = await repo.get_user_by_email(db, body.email)
        ok = verify_password(user.password_hash if user else _dummy_hash(), body.password)
        if user is None or not user.is_active or not ok:
            raise HTTPException(status_code=401, detail="invalid email or password")
        if body.client == "device":
            raw = new_opaque_token()
            expires = datetime.now(UTC) + timedelta(seconds=settings.device_token_ttl_s)
            await repo.create_auth_token(
                db, user_id=user.id, kind=TokenKind.device,
                token_hash=hash_token(raw), expires_at=expires,
            )
            await db.commit()
            return {"device_token": raw, "expires_at": expires.isoformat(), "email": user.email}
        # web: short access JWT + rotated, DB-backed refresh token, both in HttpOnly cookies
        access = issue_access_token(
            user_id=str(user.id), is_admin=user.is_admin, token_version=user.token_version,
            secret=settings.auth_secret, algorithm=settings.auth_algorithm,
            ttl_s=settings.session_ttl_s,
        )
        raw_refresh = new_opaque_token()
        expires = datetime.now(UTC) + timedelta(seconds=settings.refresh_ttl_s)
        await repo.create_auth_token(
            db, user_id=user.id, kind=TokenKind.web_refresh,
            token_hash=hash_token(raw_refresh), expires_at=expires,
        )
        await db.commit()
        public = _user_public(user)
    _set_web_cookies(response, access, raw_refresh, settings)
    return {"user": public}


@router.post("/auth/refresh")
async def refresh(request: Request, response: Response) -> dict:
    settings = request.app.state.settings
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="not authenticated")
    token_hash = hash_token(raw)
    async with request.app.state.session_factory() as db:
        tok = await repo.get_active_auth_token(
            db, token_hash=token_hash, kind=TokenKind.web_refresh
        )
        if tok is None:
            # Reuse detection: replay of a known-but-revoked refresh token -> evict the user's
            # whole web-session family (RFC 9700 rotation hardening).
            stale = await repo.get_auth_token_any(db, token_hash=token_hash)
            if stale is not None and stale.kind == TokenKind.web_refresh:
                await repo.revoke_user_tokens(
                    db, user_id=stale.user_id, kind=TokenKind.web_refresh
                )
                await db.commit()
                log.warning("auth.refresh_reuse", user_id=str(stale.user_id))
            raise HTTPException(status_code=401, detail="invalid session")
        user = await repo.get_user_by_id(db, tok.user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="no such user")
        await repo.revoke_auth_token(db, token_hash=token_hash)  # rotate
        new_refresh = new_opaque_token()
        expires = datetime.now(UTC) + timedelta(seconds=settings.refresh_ttl_s)
        await repo.create_auth_token(
            db, user_id=user.id, kind=TokenKind.web_refresh,
            token_hash=hash_token(new_refresh), expires_at=expires,
        )
        access = issue_access_token(
            user_id=str(user.id), is_admin=user.is_admin, token_version=user.token_version,
            secret=settings.auth_secret, algorithm=settings.auth_algorithm,
            ttl_s=settings.session_ttl_s,
        )
        await db.commit()
        public = _user_public(user)
    _set_web_cookies(response, access, new_refresh, settings)
    return {"user": public}


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        async with request.app.state.session_factory() as db:
            await repo.revoke_auth_token(db, token_hash=hash_token(raw))
            await db.commit()
    _clear_web_cookies(response)
    return {"ok": True}


# --- self-service account ---


@router.get("/me")
async def me(user: User = Depends(require_user)) -> dict:
    return _user_public(user)


@router.put("/me/password")
async def change_password(
    body: PasswordChangeBody,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
) -> dict:
    settings = request.app.state.settings
    try:
        validate_password(body.new_password)
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    async with request.app.state.session_factory() as db:
        current = await repo.get_user_by_id(db, user.id)
        if current is None or not verify_password(current.password_hash, body.current_password):
            raise HTTPException(status_code=400, detail="current password is incorrect")
        current.password_hash = hash_password(body.new_password)
        current.token_version += 1  # invalidate all outstanding access JWTs
        await repo.revoke_user_tokens(db, user_id=user.id)  # revoke all refresh/device tokens
        # Keep THIS caller signed in: fresh access JWT (new version) + fresh refresh token.
        access = issue_access_token(
            user_id=str(current.id), is_admin=current.is_admin,
            token_version=current.token_version,
            secret=settings.auth_secret, algorithm=settings.auth_algorithm,
            ttl_s=settings.session_ttl_s,
        )
        new_refresh = new_opaque_token()
        expires = datetime.now(UTC) + timedelta(seconds=settings.refresh_ttl_s)
        await repo.create_auth_token(
            db, user_id=current.id, kind=TokenKind.web_refresh,
            token_hash=hash_token(new_refresh), expires_at=expires,
        )
        await db.commit()
    _set_web_cookies(response, access, new_refresh, settings)
    return {"ok": True}


@router.put("/me/keys")
async def set_keys(
    body: KeysBody, request: Request, user: User = Depends(require_user)
) -> dict:
    settings = request.app.state.settings
    fields = body.model_fields_set
    async with request.app.state.session_factory() as db:
        current = await repo.get_user_by_id(db, user.id)
        if current is None:
            raise HTTPException(status_code=401, detail="no such user")
        if "soniox_key" in fields:
            current.soniox_key_enc = (
                crypto.encrypt(body.soniox_key, secret=settings.secret_key, aad=str(current.id))
                if body.soniox_key else None
            )
        if "anthropic_key" in fields:
            current.anthropic_key_enc = (
                crypto.encrypt(body.anthropic_key, secret=settings.secret_key, aad=str(current.id))
                if body.anthropic_key else None
            )
        await db.commit()
        keys_set = {
            "soniox": bool(current.soniox_key_enc),
            "anthropic": bool(current.anthropic_key_enc),
        }
    return {"keys_set": keys_set}


@router.put("/me/settings")
async def update_settings(
    body: SettingsBody, request: Request, user: User = Depends(require_user)
) -> dict:
    fields = body.model_fields_set
    async with request.app.state.session_factory() as db:
        current = await repo.get_user_by_id(db, user.id)
        if current is None:
            raise HTTPException(status_code=401, detail="no such user")
        if "default_translation_on" in fields:
            current.default_translation_on = bool(body.default_translation_on)
        if "default_output_language" in fields:
            current.default_output_language = body.default_output_language
        if "default_model" in fields:
            current.default_model = body.default_model
        await db.commit()
        public = _user_public(current)
    return public


@router.delete("/me")
async def delete_me(
    request: Request, response: Response, user: User = Depends(require_user)
) -> dict:
    async with request.app.state.session_factory() as db:
        await repo.delete_user(db, user.id)  # cascade removes the user's auth tokens
        await db.commit()
    _clear_web_cookies(response)
    return {"deleted": True}


# --- capture token for the extension (device-authenticated) ---


@router.post("/capture/token")
async def capture_token(
    body: CaptureTokenBody, request: Request, user: User = Depends(require_device)
) -> dict:
    """Mint a short capability token for the ingest WS, scoped to one meeting."""
    settings = request.app.state.settings
    async with request.app.state.session_factory() as db:
        meeting = await repo.upsert_meeting(
            db,
            platform=body.platform,
            external_meeting_id=body.external_meeting_id,
            owner_id=user.id,  # claim the meeting for this user (owner-scoping)
        )
        # Seed translation config from the owner's defaults (once; never clobbers an explicit
        # per-meeting choice) so the capture session starts translating per the user's preference.
        # Only the meeting's owner may seed — a non-owner reconnecting to someone else's meeting
        # (owner_id sticks via coalesce) must not stamp its own defaults onto that meeting.
        if meeting.owner_id == user.id:
            await repo.seed_meeting_translation(
                db,
                meeting_id=meeting.id,
                enabled=user.default_translation_on,
                output_language=user.default_output_language,
                model=user.default_model,
            )
            # Seed a display name (e.g. the browser tab title) once, if the meeting is unnamed.
            if body.title and body.title.strip() and meeting.title is None:
                await repo.set_meeting_title(
                    db, meeting_id=meeting.id, title=body.title.strip()[:512]
                )
        await db.commit()
    scope = f"{body.platform}:{body.external_meeting_id}"
    token = issue_capability_token(
        principal=str(user.id),
        secret=settings.auth_secret,
        algorithm=settings.auth_algorithm,
        ttl_s=settings.auth_token_ttl_s,
        meetings=[scope],
    )
    return {"token": token, "scope": scope, "expires_in": settings.auth_token_ttl_s}
