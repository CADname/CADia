from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import secrets
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db_session
from ..models import McpAccessToken


class CopilotSDKError(RuntimeError):
    pass


def _obj_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):
        try:
            data = value.model_dump()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    raw = getattr(value, "__dict__", None)
    return dict(raw) if isinstance(raw, dict) else {}


class CopilotSDKProvider:
    """GitHub Copilot transport wired to CADia through the project MCP gateway.

    Important architectural boundary:
      * This class does NOT generate CADia plan JSON.
      * It does NOT reimplement any CAD command, planner, verifier, recovery, or
        topology/selection algorithm.
      * For modeling turns, Copilot receives the CADia project MCP
        server and calls the same 118 tools exposed by ProjectMcpGateway.
      * Those MCP calls are dispatched by the project runtime executor.

    This mirrors the ipt-mcp pattern: AI client -> MCP -> CAD tool surface.
    """

    MCP_SERVER_NAME = "cadia"

    def __init__(self, user_token: str, user_id: str, default_model: str | None = None) -> None:
        self.user_token = user_token.strip()
        self.user_id = user_id
        self.default_model = (default_model or "auto").strip() or "auto"
        self.base_directory = settings.data_root / "copilot-users" / user_id
        self.base_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._cancelled = threading.Event()
        self._turn_lock = threading.RLock()

    def _client(self):
        try:
            from copilot import CopilotClient
        except Exception as exc:
            raise CopilotSDKError("GitHub Copilot SDK is not installed. Rebuild the Docker image.") from exc
        if not self.user_token:
            raise CopilotSDKError("GitHub Copilot connection is required.")
        return CopilotClient(
            github_token=self.user_token,
            use_logged_in_user=False,
            base_directory=str(self.base_directory),
            mode="empty",
        )

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    async def _list_models_async(self) -> list[dict[str, Any]]:
        client = self._client()
        await client.start()
        try:
            raw_models = await client.list_models()
        finally:
            await client.stop()
        rows: list[dict[str, Any]] = []
        for item in raw_models or []:
            data = _obj_dict(item)
            mid = str(
                data.get("id")
                or data.get("model")
                or data.get("model_id")
                or getattr(item, "id", "")
                or getattr(item, "model", "")
                or ""
            ).strip()
            if not mid:
                continue
            display = str(
                data.get("display_name")
                or data.get("displayName")
                or data.get("name")
                or getattr(item, "display_name", "")
                or mid
            )
            efforts = data.get("supported_reasoning_efforts") or data.get("supportedReasoningEfforts") or []
            rows.append({
                "id": mid,
                "model": mid,
                "displayName": display,
                "isDefault": mid == self.default_model,
                "supportedReasoningEfforts": efforts if isinstance(efforts, list) else [],
            })
        if not rows:
            rows = [{"id": "auto", "model": "auto", "displayName": "Auto", "isDefault": True}]
        elif not any(row.get("id") == "auto" for row in rows):
            rows.insert(0, {"id": "auto", "model": "auto", "displayName": "Auto", "isDefault": self.default_model == "auto"})
        return rows

    def list_models(self) -> list[dict[str, Any]]:
        with self._turn_lock:
            return self._run(self._list_models_async())

    @staticmethod
    def _bridge_path() -> Path:
        path = Path(__file__).resolve().parents[4] / "tools" / "cadia_web_mcp_bridge.py"
        if not path.is_file():
            raise CopilotSDKError(f"Could not find the CADia MCP bridge: {path}")
        return path

    @staticmethod
    def _tool_names() -> list[str]:
        # Lazy import keeps the Copilot SDK isolated from the Codex/web-agent import graph.
        from ..web_agent import desktop_catalog

        names = [str(row.get("name") or "").strip() for row in desktop_catalog()]
        names = [name for name in names if name]
        if len(names) != 118:
            raise CopilotSDKError(f"CADia MCP tool surface mismatch: expected 118, got {len(names)}")
        return names

    def _issue_ephemeral_mcp_token(self, project_id: str) -> tuple[str, str]:
        raw = "scad_mcp_" + secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        row = McpAccessToken(
            project_id=project_id,
            owner_id=self.user_id,
            token_hash=digest,
            label="CADia internal Copilot MCP",
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        with db_session() as db:
            db.add(row)
            db.flush()
            token_id = row.id
        return raw, token_id

    @staticmethod
    def _revoke_ephemeral_mcp_token(token_id: str) -> None:
        try:
            with db_session() as db:
                row = db.get(McpAccessToken, token_id)
                if row is not None:
                    row.revoked_at = datetime.now(timezone.utc)
        except Exception:
            # Token expiry is short; cleanup failure must not mask the model result.
            pass

    @staticmethod
    def _response_text(response: Any) -> str:
        if response is None:
            return ""
        data = getattr(response, "data", None)
        if data is not None:
            text = getattr(data, "content", None)
            if isinstance(text, str):
                return text.strip()
            if isinstance(data, dict) and isinstance(data.get("content"), str):
                return str(data["content"]).strip()
        text = getattr(response, "content", None)
        return text.strip() if isinstance(text, str) else ""

    async def _run_mcp_turn_async(
        self,
        project_id: str,
        prompt: str,
        *,
        model: str | None,
        effort: str | None,
    ) -> str:
        try:
            from copilot.session import PermissionHandler
        except Exception as exc:
            raise CopilotSDKError("Could not import the GitHub Copilot SDK session module.") from exc

        tool_names = self._tool_names()
        available_tools = [f"mcp:{self.MCP_SERVER_NAME}-{name}" for name in tool_names]
        bridge = self._bridge_path()
        mcp_token, token_id = self._issue_ephemeral_mcp_token(project_id)

        # The bridge uses the project-scoped MCP gateway; CAD implementation is not
        # duplicated in the provider transport.
        mcp_servers = {
            self.MCP_SERVER_NAME: {
                "type": "local",
                "command": sys.executable,
                "args": [
                    str(bridge),
                    "--server", "http://127.0.0.1:8000",
                    "--project", project_id,
                    "--timeout", "120",
                ],
                "env": {"CADIA_MCP_TOKEN": mcp_token},
                "cwd": str(Path(__file__).resolve().parents[4]),
                "tools": ["*"],
                "timeout": 120000,
            }
        }

        system_message = {
            "content": (
                "You are the CADia CAD agent. Operate the active CADia project only through the "
                "CADia MCP tools provided in this session. Do not invent tool names, argument keys, "
                "runtime-reference syntaxes, or CAD state. Inspect the live project with MCP query "
                "tools whenever needed. If the user refers to 'selected', 'this', 'here', a selected "
                "face, or a selected edge, call cad_get_selection and use the exact references returned "
                "by that tool. For topology-specific work, use cad_get_topology when needed. Execute the "
                "requested modeling work with MCP tools and use tool error results to correct subsequent "
                "calls. Never claim that a CAD change succeeded unless the corresponding MCP tool call "
                "succeeded. Do not use shell/file/code tools; only the CADia MCP tool surface is available."
            )
        }

        client = self._client()
        await client.start()
        try:
            kwargs: dict[str, Any] = {
                "model": (model or self.default_model or "auto"),
                "session_id": f"cadia-mcp-{self.user_id}-{uuid.uuid4().hex}",
                "enable_session_store": False,
                "github_token": self.user_token,
                "on_permission_request": PermissionHandler.approve_all,
                "mcp_servers": mcp_servers,
                "available_tools": available_tools,
                "system_message": system_message,
                "working_directory": str(self.base_directory),
            }
            if effort:
                kwargs["reasoning_effort"] = effort
            session = await client.create_session(**kwargs)
            try:
                response = await session.send_and_wait(prompt, timeout=300.0)
                if self._cancelled.is_set():
                    raise CopilotSDKError("The task was canceled by the user.")
                text = self._response_text(response)
                return text or "Modeling task completed."
            finally:
                try:
                    await session.disconnect()
                except Exception:
                    pass
        except CopilotSDKError:
            raise
        except Exception as exc:
            raise CopilotSDKError(f"GitHub Copilot MCP error: {exc}") from exc
        finally:
            try:
                await client.stop()
            except Exception:
                pass
            self._revoke_ephemeral_mcp_token(token_id)

    def run_mcp_turn(
        self,
        project_id: str,
        prompt: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        on_event=None,
    ) -> str:
        with self._turn_lock:
            self._cancelled.clear()
            if on_event:
                on_event("progress", {"message": "Connecting GitHub Copilot MCP session…", "percent": 8})
            result = self._run(self._run_mcp_turn_async(project_id, prompt, model=model, effort=effort))
            if on_event:
                on_event("progress", {"message": "CADia MCP task completed", "percent": 95})
            return result

    def complete_json(self, *args, **kwargs):
        # Copilot must use the MCP path above; JSON-plan transport is not accepted.
        raise CopilotSDKError("GitHub Copilot only runs through the CADia MCP path.")

    def cancel(self) -> None:
        self._cancelled.set()
