from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import secrets
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from standalonecad.recovery import execute_with_failure_recovery

from ..ai_credentials import PROVIDER_LABELS, SUPPORTED_PROVIDERS, provider_ai, provider_connection, remove_provider, select_provider, selected_ai
from ..auth import CurrentUser
from ..cad_runtime import runtime_manager
from ..database import db_session, get_db
from ..config import settings
from ..models import AIProviderConnection, ChatMessage
from ..providers import provider_for_user, selected_provider_name
from ..providers.anthropic_api import AnthropicAPIProvider
from ..providers.copilot_sdk import CopilotSDKProvider
from ..providers.app_server import AppServerError, app_server_manager
from ..providers.gemini_api import GeminiAPIProvider
from ..providers.openai_api import OpenAIAPIProvider
from ..schemas import AIProviderConnectRequest, AIProviderSelectRequest, PromptRequest
from ..state import planning_state
from ..web_agent import APICompatibleAgent, DesktopParityCodexAgent
from .projects import owned_project


router = APIRouter(prefix="/ai", tags=["ai"])
ai_turn_slots = threading.BoundedSemaphore(settings.max_concurrent_ai_turns)
cancel_lock = threading.RLock()
cancel_events: dict[tuple[str, str], threading.Event] = {}
active_agents: dict[tuple[str, str], Any] = {}


GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _github_ready() -> None:
    if not (settings.github_oauth_client_id and settings.github_oauth_client_secret and settings.github_oauth_redirect_uri):
        raise HTTPException(status_code=503, detail="GitHub Copilot OAuth is not configured on this server yet.")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))


def _issue_github_state(user_id: str) -> str:
    body = _b64(json.dumps({"sub": user_id, "exp": int(time.time()) + 600, "nonce": secrets.token_urlsafe(18)}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(settings.session_secret.encode(), ("github-copilot|" + body).encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _verify_github_state(value: str, user_id: str) -> None:
    try:
        body, sig = value.split(".", 1)
        expected = _b64(hmac.new(settings.session_secret.encode(), ("github-copilot|" + body).encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise ValueError("signature")
        payload = json.loads(_unb64(body))
        if str(payload.get("sub")) != user_id or int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("expired")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="GitHub OAuth state validation failed. Please connect again.") from exc


def _github_json(request: UrlRequest, timeout: int = 20) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise HTTPException(status_code=502, detail=f"GitHub authorization server error ({exc.code}): {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail="Could not reach the GitHub authorization server.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Unexpected GitHub authorization response format.")
    return payload


def _github_profile(token: str) -> dict[str, Any]:
    return _github_json(UrlRequest(GITHUB_USER_URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "CADia"}))


def _codex_connected(user_id: str) -> bool:
    try:
        return bool(_client(user_id).account().get("account"))
    except Exception:
        return False


def _stored_connected(db: Session, user_id: str, provider: str) -> bool:
    row = provider_connection(db, user_id, provider)
    return bool(row and row.encrypted_api_key)


def _client(user_id: str):
    try:
        return app_server_manager.get(user_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not start Codex App Server: {exc}") from exc


def _provider_payload(provider: str, *, connected: bool, model: str | None = None, account: dict | None = None) -> dict[str, Any]:
    return {
        "provider": provider,
        "providerLabel": PROVIDER_LABELS.get(provider, provider),
        "connected": connected,
        "model": model,
        "supportsReasoning": provider in {"codex", "copilot", "openai"},
        "account": account,
        "requiresOpenaiAuth": provider == "codex" and not connected,
    }


def _api_provider(provider: str, api_key: str, model: str | None = None):
    if provider == "openai":
        return OpenAIAPIProvider(api_key=api_key, default_model=model or None)
    if provider == "anthropic":
        return AnthropicAPIProvider(api_key=api_key, default_model=model or None)
    if provider == "gemini":
        return GeminiAPIProvider(api_key=api_key, default_model=model or None)
    raise ValueError(f"Not an API provider: {provider}")


def _model_rows(provider, preferred: str = "") -> list[dict[str, Any]]:
    rows = provider.list_models()
    seen: set[str] = set()
    cleaned = []
    for row in rows:
        mid = str(row.get("model") or row.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        cleaned.append({
            "id": mid,
            "model": mid,
            "displayName": row.get("displayName") or mid,
            "isDefault": bool(preferred and mid == preferred) or bool(row.get("isDefault")),
            "supportedReasoningEfforts": ([{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}, {"reasoningEffort": "high"}] if isinstance(provider, OpenAIAPIProvider) else []),
            "defaultReasoningEffort": "medium" if isinstance(provider, OpenAIAPIProvider) else "",
        })
    if preferred and preferred not in seen:
        cleaned.insert(0, {
            "id": preferred,
            "model": preferred,
            "displayName": preferred,
            "isDefault": True,
            "supportedReasoningEfforts": ([{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}, {"reasoningEffort": "high"}] if isinstance(provider, OpenAIAPIProvider) else []),
            "defaultReasoningEffort": "medium" if isinstance(provider, OpenAIAPIProvider) else "",
        })
    # Keep the browser selector usable even if a provider account exposes a very large catalog.
    return cleaned[:200]


@router.get("/diagnostics")
def diagnostics(user: CurrentUser, db: Session = Depends(get_db)):
    selected = selected_ai(db, user.id)
    if selected.provider == "copilot":
        provider = CopilotSDKProvider(selected.api_key, user_id=user.id, default_model=selected.model or "auto")
        try:
            rows = _model_rows(provider, selected.model)
            return {"provider": "copilot", "providerLabel": PROVIDER_LABELS["copilot"], "models": len(rows), "ok": True}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if selected.provider != "codex":
        provider = _api_provider(selected.provider, selected.api_key, selected.model)
        try:
            rows = _model_rows(provider, selected.model)
            return {"provider": selected.provider, "providerLabel": PROVIDER_LABELS[selected.provider], "models": len(rows), "ok": True}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    client = _client(user.id)
    try:
        executable = client._resolve_executable()
        import subprocess
        version = subprocess.run([executable, "--version"], text=True, capture_output=True, timeout=10)
        app_help = subprocess.run([executable, "app-server", "--help"], text=True, capture_output=True, timeout=10)
        return {
            "provider": "codex",
            "executable": executable,
            "version": (version.stdout or version.stderr).strip(),
            "version_ok": version.returncode == 0,
            "app_server_ok": app_help.returncode == 0,
            "running": client.running,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/providers")
def providers(user: CurrentUser, db: Session = Depends(get_db)):
    selected = selected_ai(db, user.id)
    stored = {row.provider: row for row in db.scalars(select(AIProviderConnection).where(AIProviderConnection.user_id == user.id))}
    codex_connected = _codex_connected(user.id)
    result = []
    for provider in SUPPORTED_PROVIDERS:
        if provider == "codex":
            connected = codex_connected
        else:
            connected = bool(stored.get(provider) and stored[provider].encrypted_api_key)
        result.append({
            "id": provider,
            "label": PROVIDER_LABELS[provider],
            "configured": connected,
            "connected": connected,
            "selected": provider == selected.provider,
            "model": stored.get(provider).model if stored.get(provider) else None,
        })
    return {"selected": selected.provider, "autoFallback": settings.ai_auto_fallback, "providers": result}


@router.post("/provider/select")
def choose_provider(payload: AIProviderSelectRequest, user: CurrentUser, db: Session = Depends(get_db)):
    provider_name = payload.provider.strip().lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail="Unsupported AI provider.")
    if provider_name == "codex":
        if not _codex_connected(user.id):
            raise HTTPException(status_code=401, detail="Connect ChatGPT / Codex first.")
    elif not _stored_connected(db, user.id, provider_name):
        raise HTTPException(status_code=401, detail=f"Connect {PROVIDER_LABELS[provider_name]} first.")
    current = provider_ai(db, user.id, provider_name)
    select_provider(db, user.id, provider_name, model=current.model or None)
    db.commit()
    return _provider_payload(provider_name, connected=True, model=current.model or None, account={"type": "oauth" if provider_name == "copilot" else "stored", "planType": PROVIDER_LABELS[provider_name]})


@router.get("/copilot/login")
def copilot_login(user: CurrentUser):
    _github_ready()
    state = _issue_github_state(user.id)
    query = urlencode({
        "client_id": settings.github_oauth_client_id,
        "redirect_uri": settings.github_oauth_redirect_uri,
        "state": state,
    })
    return {"authorizationUrl": f"{GITHUB_AUTHORIZE_URL}?{query}"}


@router.get("/copilot/callback")
def copilot_callback(request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    _github_ready()
    error = request.query_params.get("error", "")
    if error:
        return HTMLResponse(f"<!doctype html><meta charset='utf-8'><title>CADia</title><p>GitHub connection failed: {html.escape(error)}</p><script>window.opener&&window.opener.postMessage({{type:'cadia:copilot-oauth',ok:false}},location.origin);window.close();</script>", headers={"Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'"})
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    if not code or not state:
        raise HTTPException(status_code=400, detail="GitHub OAuth code/state is missing.")
    _verify_github_state(state, user.id)
    body = urlencode({
        "client_id": settings.github_oauth_client_id,
        "client_secret": settings.github_oauth_client_secret,
        "code": code,
        "redirect_uri": settings.github_oauth_redirect_uri,
    }).encode("utf-8")
    token_data = _github_json(UrlRequest(GITHUB_TOKEN_URL, data=body, headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "CADia"}, method="POST"))
    token = str(token_data.get("access_token") or "").strip()
    if not token:
        raise HTTPException(status_code=502, detail=str(token_data.get("error_description") or token_data.get("error") or "Could not receive a GitHub access token."))
    profile = _github_profile(token)
    login = str(profile.get("login") or "GitHub user")
    # Validate that this user token can reach Copilot before persisting it.
    provider = CopilotSDKProvider(token, user_id=user.id, default_model="auto")
    try:
        models = provider.list_models()
    except Exception as exc:
        raise HTTPException(status_code=403, detail=f"Could not verify GitHub Copilot access: {exc}") from exc
    chosen = "auto" if any(str(row.get("id")) == "auto" for row in models) else str(models[0].get("id") or models[0].get("model") or "auto")
    select_provider(db, user.id, "copilot", api_key=token, model=chosen)
    db.commit()
    html = f"""<!doctype html><meta charset='utf-8'><title>CADia</title><style>body{{font-family:system-ui;background:#0f1319;color:#e8eef7;display:grid;place-items:center;height:100vh;margin:0}}div{{text-align:center}}small{{color:#91a0b5}}</style><div><h2>GitHub Copilot connected</h2><small>{html.escape(login)} · This window will close automatically.</small></div><script>window.opener&&window.opener.postMessage({{type:'cadia:copilot-oauth',ok:true}},location.origin);setTimeout(()=>window.close(),350);</script>"""
    return HTMLResponse(html, headers={"Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'", "Referrer-Policy": "no-referrer"})


@router.post("/provider/connect")
def connect_provider(payload: AIProviderConnectRequest, user: CurrentUser, db: Session = Depends(get_db)):
    provider_name = payload.provider.strip().lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail="Unsupported AI provider.")
    if provider_name == "codex":
        select_provider(db, user.id, "codex")
        db.commit()
        try:
            account = _client(user.id).account()
            connected = bool(account.get("account"))
            return _provider_payload("codex", connected=connected, account=account.get("account"))
        except Exception:
            return _provider_payload("codex", connected=False)
    if provider_name == "copilot":
        current = provider_ai(db, user.id, "copilot")
        if not current.api_key:
            raise HTTPException(status_code=401, detail="Connect Copilot with GitHub OAuth first.")
        select_provider(db, user.id, "copilot", model=current.model or "auto")
        db.commit()
        profile = _github_profile(current.api_key)
        return _provider_payload("copilot", connected=True, model=current.model or "auto", account={"type": "oauth", "email": profile.get("login"), "planType": "GitHub Copilot"})

    api_key = (payload.api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=422, detail="Enter an API key.")
    candidate = _api_provider(provider_name, api_key, payload.model)
    try:
        models = _model_rows(candidate, payload.model or "")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"API connection check failed: {exc}") from exc
    if not models and not payload.model:
        raise HTTPException(status_code=422, detail="Could not find available models. Enter a model ID manually.")
    chosen = (payload.model or "").strip() or str((models[0].get("model") if models else "") or "")
    select_provider(db, user.id, provider_name, api_key=api_key, model=chosen)
    db.commit()
    return _provider_payload(
        provider_name,
        connected=True,
        model=chosen,
        account={"type": "api", "email": None, "planType": PROVIDER_LABELS[provider_name]},
    )


@router.post("/provider/disconnect")
def disconnect_provider(user: CurrentUser, db: Session = Depends(get_db)):
    selected = selected_ai(db, user.id)
    if selected.provider == "codex":
        try:
            _client(user.id).logout()
        except Exception:
            pass
        return _provider_payload("codex", connected=False)
    remove_provider(db, user.id, selected.provider)
    db.commit()
    return _provider_payload("codex", connected=False)


@router.get("/account")
def account(user: CurrentUser, db: Session = Depends(get_db)):
    selected = selected_ai(db, user.id)
    if selected.provider == "codex":
        try:
            raw = _client(user.id).account()
            account_value = raw.get("account")
            return _provider_payload("codex", connected=bool(account_value), account=account_value)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if selected.provider == "copilot":
        if not selected.api_key:
            return _provider_payload("copilot", connected=False, model=selected.model)
        try:
            profile = _github_profile(selected.api_key)
            return _provider_payload("copilot", connected=True, model=selected.model or "auto", account={"type": "oauth", "email": profile.get("login"), "planType": "GitHub Copilot"})
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not selected.api_key:
        return _provider_payload(selected.provider, connected=False, model=selected.model)
    return _provider_payload(
        selected.provider,
        connected=True,
        model=selected.model,
        account={"type": "api", "email": None, "planType": PROVIDER_LABELS[selected.provider]},
    )


@router.post("/login/device")
def login_device(user: CurrentUser, db: Session = Depends(get_db)):
    select_provider(db, user.id, "codex")
    db.commit()
    try:
        return _client(user.id).start_device_login()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/login/{login_id}/cancel")
def cancel_login(login_id: str, user: CurrentUser):
    try:
        return _client(user.id).cancel_login(login_id)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/login/{login_id}/status")
def login_status(login_id: str, user: CurrentUser):
    return _client(user.id).login_status(login_id)


@router.post("/logout")
def codex_logout(user: CurrentUser):
    try:
        return _client(user.id).logout()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/models")
def models(user: CurrentUser, db: Session = Depends(get_db)):
    selected = selected_ai(db, user.id)
    if selected.provider == "codex":
        try:
            return {"models": _client(user.id).models()}
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if selected.provider == "copilot":
        if not selected.api_key:
            raise HTTPException(status_code=401, detail="GitHub Copilot connection is required.")
        try:
            return {"models": _model_rows(CopilotSDKProvider(selected.api_key, user_id=user.id, default_model=selected.model or "auto"), selected.model or "auto")}
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not selected.api_key:
        raise HTTPException(status_code=401, detail="AI API connection is required.")
    try:
        return {"models": _model_rows(_api_provider(selected.provider, selected.api_key, selected.model), selected.model)}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/rate-limits")
def rate_limits(user: CurrentUser, db: Session = Depends(get_db)):
    selected = selected_ai(db, user.id)
    if selected.provider != "codex":
        return {"rateLimits": {}}
    try:
        return _client(user.id).rate_limits()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _execute_prompt_body(user_id: str, project_id: str, payload: PromptRequest, on_event=None):
    runtime = runtime_manager.get(user_id, project_id)
    provider_name = selected_provider_name(user_id)

    # Copilot follows the ipt-mcp architecture directly:
    # Copilot SDK -> CADia project MCP -> existing 118 tools -> existing executor/core.
    # It does not use the JSON-plan adapter and does not modify the Codex path below.
    if provider_name == "copilot":
        with db_session() as db:
            cfg = provider_ai(db, user_id, "copilot")
        if not cfg.api_key:
            raise RuntimeError("GitHub Copilot connection is required.")
        agent = CopilotSDKProvider(cfg.api_key, user_id=user_id, default_model=cfg.model or "auto")
        prompt = payload.prompt.strip()
        key = (user_id, project_id)
        with cancel_lock:
            active_agents[key] = agent
        try:
            message = agent.run_mcp_turn(
                project_id,
                prompt,
                model=payload.model or cfg.model or "auto",
                effort=payload.effort,
                on_event=on_event,
            )
            with db_session() as db:
                db.add(ChatMessage(project_id=project_id, role="user", content=prompt))
                db.add(ChatMessage(project_id=project_id, role="assistant", content=message))
            return {"message": message, "state": runtime.state()}
        finally:
            with cancel_lock:
                if active_agents.get(key) is agent:
                    active_agents.pop(key, None)

    # IMPORTANT: this is the original CADia/StandaloneCAD planner path.
    # In particular, the ChatGPT/Codex branch below is intentionally preserved
    # with the same DesktopParityCodexAgent, prompt/state, executor, verifier and
    # failure-recovery flow as before Copilot was added.
    if provider_name == "codex":
        # ChatGPT/Codex preserves the exact desktop-compatible CodexAgent path.
        client = app_server_manager.get(user_id)
        model = payload.model or "gpt-5.6-sol"
        reasoning = payload.effort or "medium"
        agent = DesktopParityCodexAgent(
            client=client,
            target_id=f"web:{project_id}",
            project_root=runtime.root,
            model=model,
            reasoning=reasoning,
        )
    else:
        # Other API providers retain the pre-Copilot API-compatible path.
        provider = provider_for_user(user_id)
        agent = APICompatibleAgent(provider, model=payload.model, reasoning=payload.effort)

    prompt = payload.prompt.strip()
    with runtime.engine.lock:
        selection = dict(runtime.engine.selection)
    agent_prompt = prompt
    if selection:
        agent_prompt += f"\n\nSelected CAD context: {selection}. If the request says this/selected/here, use this selection."

    key = (user_id, project_id)
    with cancel_lock:
        active_agents[key] = agent
    try:
        with runtime.operation_lock:
            # Deliberately mirrors desktop Qt send_prompt(): same agent.plan(),
            # same selection text, same planning state, same recovery function,
            # same progress span and same max_ai_repairs=2.
            plan = agent.plan(agent_prompt, planning_state(runtime.engine), on_event=on_event)
            executed = execute_with_failure_recovery(
                executor=runtime.executor,
                agent=agent,
                user_prompt=agent_prompt,
                state_provider=lambda: planning_state(runtime.engine),
                plan=plan,
                on_event=on_event,
                initial_progress_span=(25, 70),
                max_ai_repairs=2,
            )
            if not executed.ok:
                raise RuntimeError("Modeling failed: " + str(executed.error or "unknown error"))
            if on_event:
                on_event("progress", {"message": "Saving project…", "percent": 99})
            runtime.persist()

        with db_session() as db:
            db.add(ChatMessage(project_id=project_id, role="user", content=prompt))
            db.add(ChatMessage(project_id=project_id, role="assistant", content=executed.message))
        return {"message": executed.message, "state": runtime.state()}
    finally:
        with cancel_lock:
            if active_agents.get(key) is agent:
                active_agents.pop(key, None)


def execute_prompt_sync(user_id: str, project_id: str, payload: PromptRequest, on_event=None):
    key = (user_id, project_id)
    event = threading.Event()
    with cancel_lock:
        if key in cancel_events:
            raise RuntimeError("A modeling task is already running in this project.")
        cancel_events[key] = event
    acquired = False
    try:
        acquired = ai_turn_slots.acquire(timeout=10)
        if not acquired:
            raise RuntimeError("The server has reached its concurrent modeling limit. Please try again shortly.")

        def guarded_event(kind, value):
            if event.is_set():
                raise RuntimeError("The modeling task was canceled by the user.")
            if on_event:
                on_event(kind, value)

        if event.is_set():
            raise RuntimeError("The modeling task was canceled by the user.")
        return _execute_prompt_body(user_id, project_id, payload, guarded_event)
    finally:
        if acquired:
            ai_turn_slots.release()
        with cancel_lock:
            if cancel_events.get(key) is event:
                cancel_events.pop(key, None)


def cancel_prompt(user_id: str, project_id: str) -> None:
    key = (user_id, project_id)
    with cancel_lock:
        event = cancel_events.get(key)
        agent = active_agents.get(key)
        if event:
            event.set()
    if agent is not None:
        try:
            agent.cancel()
        except Exception:
            pass


@router.post("/projects/{project_id}/prompt")
async def prompt(project_id: str, payload: PromptRequest, user: CurrentUser, db: Session = Depends(get_db)):
    owned_project(db, user.id, project_id)
    try:
        return await asyncio.to_thread(execute_prompt_sync, user.id, project_id, payload)
    except AppServerError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
