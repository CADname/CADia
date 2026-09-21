from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(x.strip() for x in os.getenv(name, default).split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    data_root: Path
    session_secret: str
    session_cookie: str
    session_ttl_seconds: int
    cookie_secure: bool
    cors_origins: tuple[str, ...]
    codex_bin: str
    codex_process_idle_seconds: int
    max_codex_processes: int
    max_concurrent_ai_turns: int
    engine_idle_seconds: int
    max_upload_bytes: int
    mesh_tolerance: float
    edge_tolerance: float
    preferred_models: tuple[str, ...]
    ai_provider: str
    openai_api_key: str
    openai_api_base: str
    openai_api_model: str
    google_oauth_client_id: str
    google_oauth_client_secret: str
    google_oauth_redirect_uri: str
    github_oauth_client_id: str
    github_oauth_client_secret: str
    github_oauth_redirect_uri: str
    ai_auto_fallback: bool
    allowed_hosts: tuple[str, ...]
    api_prefix: str

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("NEXIS_ENV", "development").strip().lower()
        data_root = Path(os.getenv("NEXIS_DATA_ROOT", "./.nexis-data")).expanduser().resolve()
        secret = os.getenv("NEXIS_SESSION_SECRET", "").strip()
        if not secret:
            if environment == "production":
                raise RuntimeError("NEXIS_SESSION_SECRET is required in production")
            secret = secrets.token_urlsafe(48)
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            database_url = f"sqlite:///{data_root / 'nexis-cad.db'}"
        ai_provider = os.getenv("NEXIS_AI_PROVIDER", "app_server").strip().lower()
        if ai_provider not in {"app_server", "openai_api"}:
            raise RuntimeError("NEXIS_AI_PROVIDER must be app_server or openai_api")
        openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        openai_api_model = os.getenv("OPENAI_API_MODEL", "").strip()
        if ai_provider == "openai_api" and not openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when NEXIS_AI_PROVIDER=openai_api")
        if ai_provider == "openai_api" and not openai_api_model:
            raise RuntimeError("OPENAI_API_MODEL is required when NEXIS_AI_PROVIDER=openai_api")
        return cls(
            environment=environment,
            database_url=database_url,
            data_root=data_root,
            session_secret=secret,
            session_cookie=os.getenv("NEXIS_SESSION_COOKIE", "nexis_cad_session"),
            session_ttl_seconds=int(os.getenv("NEXIS_SESSION_TTL_SECONDS", "1209600")),
            cookie_secure=_bool("NEXIS_COOKIE_SECURE", environment == "production"),
            cors_origins=_csv("NEXIS_CORS_ORIGINS", "http://localhost:5173"),
            codex_bin=os.getenv("CODEX_APP_SERVER_BIN", "codex"),
            codex_process_idle_seconds=int(os.getenv("CODEX_PROCESS_IDLE_SECONDS", "1800")),
            max_codex_processes=max(1, int(os.getenv("NEXIS_MAX_CODEX_PROCESSES", "24"))),
            max_concurrent_ai_turns=max(1, int(os.getenv("NEXIS_MAX_CONCURRENT_AI_TURNS", "4"))),
            engine_idle_seconds=int(os.getenv("CAD_ENGINE_IDLE_SECONDS", "1800")),
            max_upload_bytes=int(os.getenv("NEXIS_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
            mesh_tolerance=float(os.getenv("NEXIS_MESH_TOLERANCE", "0.08")),
            edge_tolerance=float(os.getenv("NEXIS_EDGE_TOLERANCE", "0.12")),
            preferred_models=_csv("NEXIS_PREFERRED_MODELS"),
            ai_provider=ai_provider,
            openai_api_key=openai_api_key,
            openai_api_base=os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/"),
            openai_api_model=openai_api_model,
            google_oauth_client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
            google_oauth_client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
            google_oauth_redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip(),
            github_oauth_client_id=os.getenv("GITHUB_OAUTH_CLIENT_ID", "").strip(),
            github_oauth_client_secret=os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "").strip(),
            github_oauth_redirect_uri=os.getenv("GITHUB_OAUTH_REDIRECT_URI", "https://app.cadia.co.kr/api/ai/copilot/callback").strip(),
            ai_auto_fallback=_bool("CADIA_AI_AUTO_FALLBACK", True),
            allowed_hosts=_csv("NEXIS_ALLOWED_HOSTS", "localhost,127.0.0.1"),
            api_prefix=os.getenv("NEXIS_API_PREFIX", "/api"),
        )


settings = Settings.from_env()
