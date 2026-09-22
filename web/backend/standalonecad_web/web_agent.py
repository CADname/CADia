from __future__ import annotations

# CADia resilient LLM routing

import json
import os
import shlex
from pathlib import Path
from typing import Any

from standalonecad.codex_agent import CodexAgent, PLANNER_PREFIX, REPAIR_PREFIX, _validate_planner_plan, _planner_cancelled, _retry_planner_prompt, _legacy_direct_first_enabled
from standalonecad.mcp.tools import make_tools
from standalonecad.planning import direct_plan

from .providers.app_server import AppServerClient
from .providers.base import AIProvider


# The app-server path is used for authentication/account/model discovery.
# CAD planning runs through CodexAgent with the user's isolated CODEX_HOME.


class DesktopParityCodexAgent(CodexAgent):
    """Run the exact desktop CodexAgent against a web user's isolated login.

    The shared CodexAgent provides:
      - tool catalog construction
      - resilient LLM-first planning with deterministic final fallback
      - PLANNER_PREFIX / REPAIR_PREFIX
      - JSON prompt serialization
      - codex exec command line
      - JSON extraction
      - plan()/repair() control flow
      - cancel() semantics

    The only web-specific adaptation is the executable wrapper below, which sets
    CODEX_HOME/HOME to the current web user's isolated authentication directory
    before executing the Codex CLI binary. No CAD/planning algorithm is
    reimplemented here.
    """

    def __init__(
        self,
        client: AppServerClient,
        target_id: str,
        project_root: Path,
        model: str = "gpt-5.6-sol",
        reasoning: str = "medium",
    ) -> None:
        self._web_client = client
        self._wrapper_path = client.codex_home / "bin" / "codex-desktop-parity"
        super().__init__(target_id=target_id, project_root=project_root, model=model, reasoning=reasoning)
        # Desktop CodexAgent uses an otherwise-empty agent workspace.  Keep the
        # same semantics while isolating concurrent web users from one another.
        self.workspace = client.workspace
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _ensure_user_codex_wrapper(client: AppServerClient) -> Path:
        executable = Path(client._resolve_executable()).resolve()
        user_home = (client.codex_home.parent / "home").resolve()
        user_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        bin_dir = client.codex_home / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        wrapper = bin_dir / "codex-desktop-parity"
        script = "\n".join(
            [
                "#!/bin/sh",
                f"export CODEX_HOME={shlex.quote(str(client.codex_home))}",
                f"export HOME={shlex.quote(str(user_home))}",
                f"exec {shlex.quote(str(executable))} \"$@\"",
                "",
            ]
        )
        if not wrapper.exists() or wrapper.read_text(encoding="utf-8", errors="ignore") != script:
            wrapper.write_text(script, encoding="utf-8")
            os.chmod(wrapper, 0o700)
        return wrapper

    @property
    def codex_exe(self) -> Path | None:
        # Resolve/install the wrapper only when Codex is actually needed. Free-form
        # planning is LLM-first; direct_plan is final compatibility fallback only.
        try:
            self._wrapper_path = self._ensure_user_codex_wrapper(self._web_client)
        except FileNotFoundError:
            return None
        return self._wrapper_path if self._wrapper_path.is_file() else None


# API fallback is intentionally separate.  It cannot be byte-for-byte equivalent
# to the desktop ChatGPT/Codex CLI execution transport, but retains the same
# prompt/tool-catalog/retry/fallback contract for deployments that explicitly choose
# operator-paid API mode.
PLAN_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "calls": {
            "type": "array",
            "minItems": 1,
            "maxItems": 80,
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                    "bind": {"type": "string"},
                },
                "required": ["tool", "arguments"],
                "additionalProperties": False,
            },
        },
        "note": {"type": "string"},
    },
    "required": ["calls", "note"],
    "additionalProperties": False,
}


def desktop_catalog() -> list[dict[str, Any]]:
    """Exact copy of CodexAgent._make_catalog(), kept explicit for audit tests."""
    base = make_tools("inventor")
    inventor_extended = make_tools("inventor", assembly_extensions=True)
    base_inventory = {x["name"] for x in base}
    assembly_extensions = [x for x in inventor_extended if x["name"] not in base_inventory]
    native_all = make_tools("cad", extensions=True)
    base_names = {x["name"].replace("inventor_", "cad_", 1) for x in base}
    native_extensions = [x for x in native_all if x["name"] not in base_names]
    rows = base + assembly_extensions + native_extensions
    return [
        {
            "name": r["name"],
            "description": r.get("description", ""),
            "inputSchema": r.get("inputSchema", {}),
        }
        for r in rows
    ]


class APICompatibleAgent:
    """Same planning contract over the optional operator-paid API transport."""

    def __init__(self, provider: AIProvider, model: str | None = None, reasoning: str | None = None):
        self.provider = provider
        self.model = model
        self.reasoning = reasoning
        self._catalog = desktop_catalog()

    def plan(self, user_prompt: str, current_state: dict[str, Any], on_event=None) -> dict[str, Any]:
        # API transport mirrors desktop routing policy: full request first, one planner
        # retry with structured feedback, deterministic direct_plan only after both fail.
        if _legacy_direct_first_enabled():
            local = direct_plan(user_prompt, current_state)
            if local is not None:
                if on_event:
                    on_event("progress", {"message": "Standard geometry plan completed", "percent": 20})
                return local

        base_prompt = PLANNER_PREFIX.format(
            tool_catalog=json.dumps(self._catalog, ensure_ascii=False, separators=(",", ":")),
            current_state=json.dumps(current_state, ensure_ascii=False, separators=(",", ":")),
            user_request=user_prompt.strip(),
        )
        last_exc: Exception | None = None
        prompt = base_prompt
        for attempt in (1, 2):
            try:
                planned = self.provider.complete_json(
                    prompt,
                    output_schema=PLAN_OUTPUT_SCHEMA,
                    model=self.model,
                    effort=self.reasoning,
                    on_event=on_event,
                )
                return _validate_planner_plan(planned, self._catalog)
            except Exception as exc:
                if _planner_cancelled(exc):
                    raise
                last_exc = exc
                if attempt == 1:
                    if on_event:
                        on_event("progress", {"message": "Planner response invalid; retrying with error feedback", "percent": 14})
                    prompt = _retry_planner_prompt(base_prompt, exc, attempt)

        local = direct_plan(user_prompt, current_state)
        if local is not None:
            if on_event:
                on_event("progress", {"message": "AI planner unavailable; using deterministic compatibility fallback", "percent": 20})
            return local
        assert last_exc is not None
        raise last_exc

    def repair(
        self,
        user_prompt: str,
        current_state: dict[str, Any],
        failed_plan: dict[str, Any],
        error: str,
        on_event=None,
        recovery_context: dict[str, Any] | None = None,
        progress_span: tuple[int, int] = (72, 78),
    ) -> dict[str, Any]:
        prompt = REPAIR_PREFIX.format(
            tool_catalog=json.dumps(self._catalog, ensure_ascii=False, separators=(",", ":")),
            current_state=json.dumps(current_state, ensure_ascii=False, separators=(",", ":")),
            user_request=user_prompt.strip(),
            failed_plan=json.dumps(failed_plan, ensure_ascii=False, separators=(",", ":")),
            error=str(error),
            recovery_context=json.dumps(recovery_context or {}, ensure_ascii=False, separators=(",", ":")),
        )
        return self.provider.complete_json(
            prompt,
            output_schema=PLAN_OUTPUT_SCHEMA,
            model=self.model,
            effort=self.reasoning,
            on_event=on_event,
        )

    def cancel(self) -> None:
        self.provider.cancel()


# Backward-compatible symbol for older tests/imports.  In app-server mode the
# route uses DesktopParityCodexAgent directly.
WebCodexAgent = APICompatibleAgent
