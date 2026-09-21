from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 320 or not EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=422, detail="Please enter a valid email address.")
    return email


def validate_password(password: str) -> None:
    if len(password) < 8 or len(password) > 256:
        raise HTTPException(status_code=422, detail="Password must be 8 to 256 characters.")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, n, r, p, salt64, digest64 = encoded.split("$", 5)
        if kind != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt64.encode())
        expected = base64.urlsafe_b64decode(digest64.encode())
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode())


def issue_session(user_id: str) -> str:
    payload = {"sub": user_id, "iat": int(time.time()), "exp": int(time.time()) + settings.session_ttl_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def decode_session(token: str) -> str:
    try:
        body, signature = token.split(".", 1)
        expected = _b64(hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        payload = json.loads(_unb64(body))
        if int(payload["exp"]) < int(time.time()):
            raise ValueError("expired")
        return str(payload["sub"])
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign-in is required.") from exc


def set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        settings.session_cookie,
        issue_session(user_id),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.session_cookie, path="/", secure=settings.cookie_secure, samesite="lax")


def _token_from_request(request: Request, authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.cookies.get(settings.session_cookie)


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    token = _token_from_request(request, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    user = db.get(User, decode_session(token))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    return user


def websocket_user_id(request_cookies: dict[str, str], authorization: str | None = None) -> str:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    token = token or request_cookies.get(settings.session_cookie)
    if not token:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    return decode_session(token)


CurrentUser = Annotated[User, Depends(get_current_user)]
