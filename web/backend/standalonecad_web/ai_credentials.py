from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import settings
from .models import AIProviderConnection

SUPPORTED_PROVIDERS = ("codex", "copilot", "openai", "anthropic", "gemini")
PROVIDER_LABELS = {
    "codex": "ChatGPT / Codex",
    "copilot": "GitHub Copilot",
    "openai": "OpenAI API",
    "anthropic": "Claude API",
    "gemini": "Gemini API",
}


def _fernet() -> Fernet:
    raw = hashlib.sha256((settings.session_secret + "|standalonecad-ai-credentials-v1").encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Could not decrypt the stored AI API key. Check whether the server secret changed.") from exc


@dataclass(frozen=True)
class SelectedAI:
    provider: str
    api_key: str = ""
    model: str = ""


def selected_connection(db: Session, user_id: str) -> AIProviderConnection | None:
    return db.scalar(
        select(AIProviderConnection)
        .where(AIProviderConnection.user_id == user_id, AIProviderConnection.selected.is_(True))
        .order_by(AIProviderConnection.updated_at.desc())
    )


def selected_ai(db: Session, user_id: str) -> SelectedAI:
    row = selected_connection(db, user_id)
    if row is None:
        return SelectedAI(provider="codex")
    return SelectedAI(provider=row.provider, api_key=decrypt_secret(row.encrypted_api_key), model=row.model or "")




def provider_connection(db: Session, user_id: str, provider: str) -> AIProviderConnection | None:
    return db.scalar(
        select(AIProviderConnection).where(
            AIProviderConnection.user_id == user_id,
            AIProviderConnection.provider == provider.strip().lower(),
        )
    )


def provider_ai(db: Session, user_id: str, provider: str) -> SelectedAI:
    provider = provider.strip().lower()
    if provider == "codex":
        return SelectedAI(provider="codex")
    row = provider_connection(db, user_id, provider)
    if row is None:
        return SelectedAI(provider=provider)
    return SelectedAI(provider=provider, api_key=decrypt_secret(row.encrypted_api_key), model=row.model or "")


def select_provider(
    db: Session,
    user_id: str,
    provider: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> AIProviderConnection:
    provider = provider.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported AI provider: {provider}")
    rows = list(db.scalars(select(AIProviderConnection).where(AIProviderConnection.user_id == user_id)))
    row = next((item for item in rows if item.provider == provider), None)
    if row is None:
        row = AIProviderConnection(user_id=user_id, provider=provider)
        db.add(row)
    for item in rows:
        item.selected = False
    row.selected = True
    if api_key is not None:
        row.encrypted_api_key = encrypt_secret(api_key.strip()) if api_key.strip() else None
    if model is not None:
        row.model = model.strip() or None
    db.flush()
    return row


def remove_provider(db: Session, user_id: str, provider: str) -> None:
    provider = provider.strip().lower()
    row = db.scalar(select(AIProviderConnection).where(AIProviderConnection.user_id == user_id, AIProviderConnection.provider == provider))
    if row is not None:
        db.delete(row)
        db.flush()
    # Fall back to Codex without requiring a database row.
    db.execute(update(AIProviderConnection).where(AIProviderConnection.user_id == user_id).values(selected=False))
