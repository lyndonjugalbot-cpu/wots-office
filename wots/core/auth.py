"""Local dashboard sign-in (spec v2 §12, Phases 1-6).

`wots run` / `wots login-link` print a sign-in link carrying a signed, short-lived token. Opening
it sets a signed session cookie. Signatures use HMAC-SHA256 with a key derived from SECRETS_KEY
(or, if that isn't set, a random key kept in data/.session_key). Supabase Auth replaces this in
Phase 7.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from sqlalchemy import select

from .models import Membership, Organization, User

LINK_SECONDS = 10 * 60
SESSION_SECONDS = 12 * 60 * 60
COOKIE = "wots_session"


class AuthError(PermissionError):
    pass


def _key(rt) -> bytes:
    if os.environ.get("SECRETS_KEY"):
        return hashlib.sha256(b"wots-session:" + os.environ["SECRETS_KEY"].encode()).digest()
    path = rt.config.settings.data_path / ".session_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        path.chmod(0o600)
    return path.read_bytes()


def sign(rt, payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(_key(rt), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def verify(rt, token: str, purpose: str) -> dict:
    try:
        body, mac = token.rsplit(".", 1)
    except ValueError:
        raise AuthError("Malformed token") from None
    if not hmac.compare_digest(mac, hmac.new(_key(rt), body.encode(), hashlib.sha256).hexdigest()):
        raise AuthError("Invalid token")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if payload.get("purpose") != purpose or payload.get("exp", 0) < time.time():
        raise AuthError("This link or session has expired")
    return payload


def default_user_id(rt) -> str:
    """The internal office's owner (who `wots run` signs in by default)."""
    with rt.sessions() as s:
        uid = s.scalars(select(Membership.user_id).join(Organization, Organization.id == Membership.org_id).where(
            Organization.is_internal.is_(True), Membership.role == "owner").execution_options(cross_org=True)).first()
    if not uid:
        raise AuthError("No internal office owner found. Create an office with `wots orgs create`.")
    return uid


def login_link(rt, email: str | None, base_url: str) -> str:
    if email:
        with rt.sessions() as s:
            user = s.scalars(select(User).where(User.email == email.strip().lower())).first()
        if not user:
            raise AuthError(f"No user with email {email}")
        uid = user.id
    else:
        uid = default_user_id(rt)
    token = sign(rt, {"purpose": "login", "uid": uid, "exp": int(time.time()) + LINK_SECONDS,
                      "n": secrets.token_hex(4)})
    return f"{base_url}/auth/login?token={token}"


def file_token(rt, org_id: str, item_id: str) -> str:
    """Read access to one work item's files, for links and sandboxed previews (which send no cookie)."""
    return sign(rt, {"purpose": "files", "org": org_id, "item": item_id, "exp": int(time.time()) + SESSION_SECONDS})


def session_cookie(rt, user_id: str) -> str:
    return sign(rt, {"purpose": "session", "uid": user_id, "exp": int(time.time()) + SESSION_SECONDS})
