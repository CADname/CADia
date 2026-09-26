from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx


class Slant3DError(RuntimeError):
    pass


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


ALIASES: dict[str, tuple[str, ...]] = {
    "stl_base64": ("stl_base64", "base64", "file_base64", "file_content", "model_base64", "stl", "file", "data", "content"),
    "filename": ("filename", "file_name", "name"),
    "material": ("material", "material_name"),
    "filament_id": ("filament_id", "filamentId", "filamentid", "material_id", "materialId", "materialid"),
    "color": ("color", "colour", "filament_color", "material_color"),
    "quote_id": ("quote_id", "quoteid", "id"),
    "quantity": ("quantity", "qty", "count", "units"),
    "shipping_service": ("shipping_service", "shipping_option", "service", "service_id", "rate_id"),
    "email": ("email", "customer_email"),
    "name": ("name", "full_name", "recipient", "recipient_name"),
    "line1": ("line1", "address1", "address_line1", "street", "street1"),
    "line2": ("line2", "address2", "address_line2", "street2"),
    "city": ("city", "locality"),
    "state": ("state", "province", "region", "state_province"),
    "postal_code": ("postal_code", "postcode", "zip", "zip_code"),
    "country": ("country", "country_code", "countrycode"),
}


@dataclass(frozen=True)
class Slant3DConfig:
    enabled: bool
    demo_mode: bool
    endpoint: str
    timeout_seconds: float
    max_stl_bytes: int

    @classmethod
    def from_env(cls) -> "Slant3DConfig":
        return cls(
            enabled=_bool_env("CADIA_SLANT3D_ENABLED", True),
            demo_mode=_bool_env("CADIA_SLANT3D_DEMO_MODE", True),
            endpoint=os.getenv("CADIA_SLANT3D_MCP_URL", "https://www.slant3d.com/mcp").strip(),
            timeout_seconds=max(5.0, float(os.getenv("CADIA_SLANT3D_TIMEOUT_SECONDS", "45"))),
            max_stl_bytes=max(1024, int(os.getenv("CADIA_SLANT3D_MAX_STL_BYTES", str(20 * 1024 * 1024)))),
        )


def slant3d_capability() -> dict[str, Any]:
    cfg = Slant3DConfig.from_env()
    return {
        "provider": "Slant 3D",
        "enabled": cfg.enabled,
        "demo_mode": cfg.demo_mode,
        "mcp_url": cfg.endpoint,
        "checkout_enabled": cfg.enabled and not cfg.demo_mode,
        "build_volume_mm": {"x": 220.0, "y": 220.0, "z": 220.0},
        "transport": "MCP Streamable HTTP",
    }


def _parse_sse_or_json(response: httpx.Response) -> dict[str, Any]:
    text = response.text.strip()
    if not text:
        raise Slant3DError("Slant 3D MCP returned an empty response.")
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" in content_type or text.startswith("event:") or text.startswith("data:"):
        payloads: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                payloads.append(value)
        for value in reversed(payloads):
            if "result" in value or "error" in value:
                return value
        if payloads:
            return payloads[-1]
        raise Slant3DError("Slant 3D MCP returned an unreadable event-stream response.")
    try:
        value = response.json()
    except ValueError as exc:
        raise Slant3DError("Slant 3D MCP returned non-JSON data.") from exc
    if not isinstance(value, dict):
        raise Slant3DError("Slant 3D MCP returned an unexpected response shape.")
    return value


class StreamableMcpClient:
    def __init__(self, endpoint: str, *, timeout_seconds: float = 45.0) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._next_id = 1
        self._client = httpx.Client(timeout=self.timeout_seconds, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "CADia-Hackathon/1.0",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, payload: dict[str, Any], *, expect_response: bool = True) -> dict[str, Any] | None:
        try:
            response = self._client.post(self.endpoint, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise Slant3DError(f"Could not reach Slant 3D MCP: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text.strip()[:500]
            raise Slant3DError(f"Slant 3D MCP HTTP {response.status_code}: {detail or 'request failed'}")
        session_id = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id
        if not expect_response or response.status_code == 202 or not response.content:
            return None
        envelope = _parse_sse_or_json(response)
        if envelope.get("error"):
            error = envelope["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise Slant3DError(f"Slant 3D MCP error: {message}")
        return envelope

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        envelope = self._post({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        if not envelope:
            return None
        return envelope.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, expect_response=False)

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "CADia", "version": "1.0"},
            },
        )
        if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
            self.protocol_version = result["protocolVersion"]
        else:
            self.protocol_version = "2025-06-18"
        self.notify("notifications/initialized")

    def close(self) -> None:
        self._client.close()

    def tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list") or {}
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [item for item in tools if isinstance(item, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        return _unwrap_tool_result(result)


def _unwrap_tool_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if result.get("isError"):
        content = result.get("content")
        raise Slant3DError(f"Slant 3D tool failed: {_content_text(content) or 'unknown error'}")
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    content = result.get("content")
    text = _content_text(content)
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return result


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
            chunks.append(item["text"])
    return "\n".join(chunks).strip()


def _alias_value(property_name: str, values: dict[str, Any]) -> tuple[bool, Any]:
    pnorm = _norm(property_name)
    if property_name in values and values[property_name] not in (None, ""):
        return True, values[property_name]
    for canonical, aliases in ALIASES.items():
        if canonical not in values or values[canonical] in (None, ""):
            continue
        if pnorm in {_norm(alias) for alias in aliases}:
            return True, values[canonical]
    return False, None


def _object_from_schema(schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return {key: value for key, value in values.items() if value not in (None, "")}
    required = set(schema.get("required", [])) if isinstance(schema.get("required"), list) else set()
    output: dict[str, Any] = {}
    missing: list[str] = []
    for name, subschema in properties.items():
        if not isinstance(subschema, dict):
            subschema = {}
        found, value = _alias_value(name, values)
        stype = subschema.get("type")
        if not found and (stype == "object" or isinstance(subschema.get("properties"), dict)):
            nested = _object_from_schema(subschema, values)
            if nested:
                found, value = True, nested
        if not found:
            normalized = _norm(name)
            if normalized in {"encoding", "dataencoding", "fileencoding"}:
                found, value = True, "base64"
            elif normalized in {"format", "fileformat", "modeltype", "filetype"}:
                found, value = True, "stl"
            elif normalized in {"mimetype", "contenttype"}:
                found, value = True, "model/stl"
        if found:
            output[name] = value
        elif name in required:
            missing.append(name)
    if missing:
        raise Slant3DError("Slant 3D tool schema requires unsupported field(s): " + ", ".join(missing))
    return output


def call_slant_tool(tool_name: str, values: dict[str, Any]) -> Any:
    cfg = Slant3DConfig.from_env()
    if not cfg.enabled:
        raise Slant3DError("Slant 3D fulfillment is disabled by server configuration.")
    client = StreamableMcpClient(cfg.endpoint, timeout_seconds=cfg.timeout_seconds)
    try:
        client.initialize()
        tools = client.tools()
        tool = next((item for item in tools if item.get("name") == tool_name), None)
        if tool is None:
            available = ", ".join(sorted(str(item.get("name")) for item in tools if item.get("name")))
            raise Slant3DError(f"Slant 3D MCP does not expose {tool_name}. Available tools: {available or 'none'}")
        schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"}
        arguments = _object_from_schema(schema, values)
        return client.call_tool(tool_name, arguments)
    finally:
        client.close()


def list_materials() -> Any:
    return call_slant_tool("list_materials", {})
