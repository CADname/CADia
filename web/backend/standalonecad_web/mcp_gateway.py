from __future__ import annotations

from typing import Any

from standalonecad import __version__

from .cad_runtime import CadRuntime
from .web_agent import desktop_catalog

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


class ProjectMcpGateway:
    """Project-scoped MCP JSON-RPC facade over the exact desktop tool surface.

    This layer does not implement CAD operations.  tools/call is delegated to the
    already-live PlanExecutor, which owns the same inventor/cad McpServer instances
    used by the desktop planner.  The web layer only pins access to one project.
    """

    def __init__(self, runtime: CadRuntime):
        self.runtime = runtime
        self.tools = desktop_catalog()
        self.tool_names = {row["name"] for row in self.tools}

    @staticmethod
    def ok(rid: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def err(rid: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            payload["data"] = data
        return {"jsonrpc": "2.0", "id": rid, "error": payload}

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any] | None:
        rid = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if method == "server/discover":
            return self.ok(rid, {
                "resultType": "complete",
                "supportedVersions": [MODERN, LEGACY],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "CADia-Web-MCP", "version": __version__},
                "instructions": "Project-scoped CADia MCP. Uses the same CAD tool surface and core as the desktop build.",
            })
        if method == "initialize":
            protocol = params.get("protocolVersion", LEGACY)
            return self.ok(rid, {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "CADia-Web-MCP", "version": __version__},
                "instructions": "This MCP session is pinned to one authenticated CADia web project.",
            })
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "ping":
            return self.ok(rid, {})
        if method == "tools/list":
            return self.ok(rid, {"resultType": "complete", "tools": self.tools})
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if name not in self.tool_names:
                return self.err(rid, -32602, f"Unknown tool: {name}")
            if not isinstance(arguments, dict):
                return self.err(rid, -32602, "tool arguments must be an object")
            try:
                with self.runtime.operation_lock:
                    result = self.runtime.executor._dispatch(name, arguments)
                    self.runtime.persist()
                return self.ok(rid, {
                    "resultType": "complete",
                    "content": [{"type": "text", "text": __import__("json").dumps(result, ensure_ascii=False, separators=(",", ":"))}],
                    "structuredContent": result,
                })
            except Exception as exc:
                structured = {"ok": False, "error": {"code": type(exc).__name__.upper(), "message": str(exc)}}
                return self.ok(rid, {
                    "resultType": "complete",
                    "content": [{"type": "text", "text": __import__("json").dumps(structured, ensure_ascii=False, separators=(",", ":"))}],
                    "structuredContent": structured,
                    "isError": True,
                })
        return self.err(rid, -32601, f"Method not found: {method}")
