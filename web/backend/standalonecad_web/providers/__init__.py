from __future__ import annotations

from ..ai_credentials import provider_ai, selected_ai
from ..database import SessionLocal
from .anthropic_api import AnthropicAPIProvider
from .app_server import AppServerClient, AppServerManager, app_server_manager
from .base import AIProvider
from .copilot_sdk import CopilotSDKProvider
from .gemini_api import GeminiAPIProvider
from .openai_api import OpenAIAPIProvider


def selected_provider_name(user_id: str) -> str:
    with SessionLocal() as db:
        return selected_ai(db, user_id).provider


def provider_for_user(user_id: str, provider_name: str | None = None) -> AIProvider:
    with SessionLocal() as db:
        selected = selected_ai(db, user_id) if provider_name is None else provider_ai(db, user_id, provider_name)
    if selected.provider == "codex":
        return app_server_manager.get(user_id)
    if selected.provider == "copilot":
        if not selected.api_key:
            raise RuntimeError("GitHub Copilot connection is required.")
        return CopilotSDKProvider(selected.api_key, user_id=user_id, default_model=selected.model or "auto")
    if selected.provider == "openai":
        return OpenAIAPIProvider(api_key=selected.api_key, default_model=selected.model or None)
    if selected.provider == "anthropic":
        return AnthropicAPIProvider(api_key=selected.api_key, default_model=selected.model or None)
    if selected.provider == "gemini":
        return GeminiAPIProvider(api_key=selected.api_key, default_model=selected.model or None)
    raise RuntimeError(f"Unsupported AI provider: {selected.provider}")


__all__ = [
    "AIProvider",
    "AppServerClient",
    "AppServerManager",
    "CopilotSDKProvider",
    "app_server_manager",
    "provider_for_user",
    "selected_provider_name",
]
