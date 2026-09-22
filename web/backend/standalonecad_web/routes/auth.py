from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import shutil
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import CurrentUser, clear_session_cookie, hash_password, normalize_email, set_session_cookie, verify_password
from ..cad_runtime import runtime_manager
from ..database import get_db
from ..config import settings
from ..models import AIProviderConnection, McpAccessToken, OAuthIdentity, Project, User
from ..providers.app_server import app_server_manager
from ..schemas import LoginRequest, RegisterRequest, UserOut


router = APIRouter(prefix="/auth", tags=["auth"])
GOOGLE_STATE_COOKIE = "nexis_google_oauth_state"
GOOGLE_PKCE_COOKIE = "nexis_google_oauth_pkce"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _is_guest_user(user: User) -> bool:
    email = str(user.email or "").casefold()
    return user.password_hash == "guest$disabled" and email.startswith("guest-") and email.endswith("@cadia.local")


def _guest_storage_roots(user_id: str) -> tuple:
    return (
        settings.data_root / "projects" / user_id,
        settings.data_root / "codex-users" / user_id,
    )


def _purge_guest_user(user: User, db: Session) -> None:
    """Permanently remove data owned by one temporary guest account."""
    if not _is_guest_user(user):
        raise ValueError("Refusing to purge a non-guest user.")

    user_id = str(user.id)
    project_ids = list(db.scalars(select(Project.id).where(Project.owner_id == user_id)))

    # Stop in-memory/process state first so no worker can recreate files after cleanup.
    for project_id in project_ids:
        runtime_manager.drop(user_id, str(project_id))
    app_server_manager.drop(user_id, logout=True)

    # Remove all durable CAD and per-user Codex state for this guest.
    for root in _guest_storage_roots(user_id):
        if root.exists():
            shutil.rmtree(root)

    # Do not rely solely on database-specific FK cascade behavior for credentials/tokens.
    db.execute(delete(McpAccessToken).where(McpAccessToken.owner_id == user_id))
    db.execute(delete(AIProviderConnection).where(AIProviderConnection.user_id == user_id))
    db.execute(delete(OAuthIdentity).where(OAuthIdentity.user_id == user_id))
    db.delete(user)
    db.commit()


def _google_ready() -> None:
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret and settings.google_oauth_redirect_uri):
        raise HTTPException(status_code=503, detail="Google sign-in is not configured on this server yet.")


def _google_json(request: UrlRequest, timeout: int = 15) -> dict:
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise HTTPException(status_code=502, detail=f"Google authorization server error: {detail or exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail="Could not reach the Google authorization server.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Unexpected Google authorization response format.")
    return payload


@router.get("/google/login")
def google_login():
    _google_ready()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    query = urlencode({
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })
    response = RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}", status_code=302)
    response.set_cookie(
        GOOGLE_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/auth/google",
    )
    response.set_cookie(
        GOOGLE_PKCE_COOKIE,
        verifier,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/auth/google",
    )
    return response


@router.get("/google/callback")
def google_callback(request: Request, db: Session = Depends(get_db)):
    _google_ready()
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    oauth_error = request.query_params.get("error", "")
    expected = request.cookies.get(GOOGLE_STATE_COOKIE, "")
    verifier = request.cookies.get(GOOGLE_PKCE_COOKIE, "")
    if oauth_error:
        response = RedirectResponse("/login?" + urlencode({"oauth_error": oauth_error}), status_code=302)
        response.delete_cookie(GOOGLE_STATE_COOKIE, path="/api/auth/google")
        response.delete_cookie(GOOGLE_PKCE_COOKIE, path="/api/auth/google")
        return response
    if not code or not state or not expected or not verifier or not hmac.compare_digest(state, expected):
        raise HTTPException(status_code=400, detail="Google sign-in state validation failed. Please sign in again.")

    token_body = urlencode({
        "code": code,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode("utf-8")
    token = _google_json(UrlRequest(
        GOOGLE_TOKEN_URL,
        data=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    ))
    access_token = str(token.get("access_token") or "")
    if not access_token:
        raise HTTPException(status_code=502, detail="Could not receive a Google access token.")
    profile = _google_json(UrlRequest(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    ))
    subject = str(profile.get("sub") or "").strip()
    email = normalize_email(str(profile.get("email") or ""))
    if not subject or profile.get("email_verified") is not True:
        raise HTTPException(status_code=403, detail="A verified Google email account is required.")

    identity = db.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == "google", OAuthIdentity.subject == subject))
    if identity is not None:
        user = db.get(User, identity.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Could not find the linked CADia account.")
        identity.email_snapshot = email
    else:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            display_name = str(profile.get("name") or email.split("@", 1)[0]).strip()[:80] or "Google User"
            user = User(email=email, display_name=display_name, password_hash="oauth$google$disabled")
            db.add(user)
            db.flush()
        existing_provider = db.scalar(select(OAuthIdentity).where(OAuthIdentity.user_id == user.id, OAuthIdentity.provider == "google"))
        if existing_provider is not None and existing_provider.subject != subject:
            raise HTTPException(status_code=409, detail="Another Google account is already linked to this CADia account.")
        if existing_provider is None:
            db.add(OAuthIdentity(user_id=user.id, provider="google", subject=subject, email_snapshot=email))
    db.commit()

    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(GOOGLE_STATE_COOKIE, path="/api/auth/google")
    response.delete_cookie(GOOGLE_PKCE_COOKIE, path="/api/auth/google")
    set_session_cookie(response, user.id)
    return response


@router.post("/guest", status_code=201)
def guest_launch(response: Response, db: Session = Depends(get_db)):
    """Create an isolated temporary guest workspace.

    Guests can open the product without signing up first. The guest account is
    still a real CADia user so Codex/App Server state remains isolated by
    user_id exactly like normal accounts.
    """
    token = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].lower()
    user = User(
        email=f"guest-{token}@cadia.local",
        display_name=f"Guest {token[:4].upper()}",
        password_hash="guest$disabled",
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        name="Starter Workspace",
        description="Start here: try the sample prompts for gears, mounting plates, lead screws, and assemblies.",
    )
    db.add(project)
    db.commit()
    db.refresh(user)
    db.refresh(project)
    set_session_cookie(response, user.id)
    return {"user": UserOut.model_validate(user), "project": {"id": project.id, "name": project.name, "description": project.description, "created_at": project.created_at, "updated_at": project.updated_at}}


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    email = normalize_email(payload.email)
    display_name = payload.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="Please enter a display name.")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="This email is already registered.")
    user = User(email=email, display_name=display_name, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This email is already registered.") from exc
    db.refresh(user)
    set_session_cookie(response, user.id)
    return user


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    email = normalize_email(payload.email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    set_session_cookie(response, user.id)
    return user


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.post("/logout", status_code=204)
def logout(response: Response, user: CurrentUser, db: Session = Depends(get_db)):
    if _is_guest_user(user):
        _purge_guest_user(user, db)
    clear_session_cookie(response)
    response.status_code = 204
    return None
