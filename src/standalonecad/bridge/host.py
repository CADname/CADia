from __future__ import annotations

import json
import os
import socketserver
import threading
import time
import hmac
import uuid
import re
from collections import deque
from datetime import datetime, timezone

from standalonecad import __version__

from .protocol import new_token, write_descriptor
EMULATED_INVENTOR_YEAR = 2024  # compatibility metadata only; no external Inventor/ipt-mcp runtime is launched.

# Commands a baked tool is allowed to dispatch in the upstream v0.1.0 implementation.
_BAKED_ALLOWED = {
    "health", "get_document_info", "list_open_documents", "list_parameters",
    "get_parameter", "get_iproperty", "get_mass_properties", "list_interfaces",
    "check_interference", "measure_min_distance", "get_assembly_bom", "list_constraints",
}


CANONICAL_ERROR_CODES = {
    "NO_TARGET", "TARGET_UNAVAILABLE", "NO_DOCUMENT", "WRONG_DOCUMENT_TYPE",
    "INVALID_ARGUMENT", "UNSUPPORTED_HOST", "API_ERROR", "TIMEOUT",
    "RESPONSE_TOO_LARGE", "READ_ONLY", "SEND_CODE_DISABLED", "UNAUTHORIZED",
}
MAX_RESPONSE_BYTES = 5_000_000


def _sanitize(message: str | None) -> str:
    """Behavioral port of upstream ErrorSanitizer + SecretMasker."""
    msg = (message or "").replace("\r", " ").replace("\n", " ").strip()
    msg = re.sub(r'[A-Za-z]:\\[^ ]+', '<path>', msg)
    msg = re.sub(r'("auth_token"\s*:\s*")[^"]+(")', r'\1***\2', msg)
    msg = re.sub(r'\b[A-Za-z0-9+/=]{24,}\b', '***', msg)
    return msg


def _error_code(exc: Exception) -> str:
    msg = str(exc).upper()
    if isinstance(exc, PermissionError):
        if "SEND_CODE_DISABLED" in msg:
            return "SEND_CODE_DISABLED"
        return "READ_ONLY" if "READ_ONLY" in msg else "UNAUTHORIZED"
    if "NO_DOCUMENT" in msg or "NO DOCUMENT" in msg or "NO BODY" in msg:
        return "NO_DOCUMENT"
    if "WRONG_DOCUMENT_TYPE" in msg or "WRONG DOCUMENT TYPE" in msg:
        return "WRONG_DOCUMENT_TYPE"
    if isinstance(exc, OverflowError):
        return "RESPONSE_TOO_LARGE"
    if isinstance(exc, (ValueError, KeyError, TypeError, FileNotFoundError, json.JSONDecodeError)):
        return "INVALID_ARGUMENT"
    return "API_ERROR"


def _guard_data(data):
    serialized = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    size = len(serialized.encode("utf-8"))
    if size > MAX_RESPONSE_BYTES:
        raise OverflowError(
            f"Response {size} bytes exceeds the configured limit of {MAX_RESPONSE_BYTES} bytes. "
            "Narrow the query (max_items/max_depth) and retry."
        )
    return data


def _json_object(value, default=None):
    if value is None:
        return {} if default is None else default
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("Expected a JSON object")
        return parsed
    raise ValueError("Expected a JSON object")


def _json_array(value):
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        parsed = json.loads(value or "[]")
        if not isinstance(parsed, list):
            raise ValueError("Expected a JSON array")
        return parsed
    raise ValueError("Expected a JSON array")


class _LoopbackTcpServer(socketserver.TCPServer):
    allow_reuse_address = True


class _Handler(socketserver.StreamRequestHandler):
    """Behavioral port of upstream v0.1.0 TcpTransportServer wire rules.

    The geometry dispatcher is CADia/OCCT, but the local TCP boundary keeps
    the upstream 1 MiB line bound, constant-time auth, 20 req/10 s rate limit,
    canonical error envelope, and auth-failure connection drop.
    """

    MAX_LINE_BYTES = 1024 * 1024
    RATE_MAX = 20
    RATE_WINDOW_S = 10.0

    @staticmethod
    def _wire_id(value) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except Exception:
            return str(uuid.UUID(int=0))

    def _write(self, obj: dict) -> bool:
        try:
            payload = json.dumps(obj, separators=(",", ":")) + "\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()
            return True
        except Exception:
            return False

    def _error(self, req_id, code: str, message: str, host, started: float | None = None) -> dict:
        if started is None:
            started = time.perf_counter()
        return {
            "id": self._wire_id(req_id),
            "ok": False,
            "data": None,
            "error": {"code": code, "message": message},
            "meta": host._meta({}, started),
        }

    def handle(self):
        host = self.server.cad_host
        stamps: deque[float] = deque()
        # Upstream sets a 120s receive timeout per connected client.
        try:
            self.connection.settimeout(120.0)
        except Exception:
            pass

        while True:
            # Bounded readline prevents a hostile client from forcing an unbounded allocation.
            try:
                raw = self.rfile.readline(self.MAX_LINE_BYTES + 2)
            except Exception:
                break
            if not raw:
                break
            if len(raw) > self.MAX_LINE_BYTES + 1 and not raw.endswith(b"\n"):
                self._write(self._error(None, "INVALID_ARGUMENT", "Request exceeded 1 MiB size limit.", host))
                break
            raw = raw.rstrip(b"\r\n")
            if len(raw) > self.MAX_LINE_BYTES:
                self._write(self._error(None, "INVALID_ARGUMENT", "Request exceeded 1 MiB size limit.", host))
                break
            if not raw.strip():
                continue

            try:
                req = json.loads(raw.decode("utf-8"))
                if not isinstance(req, dict):
                    continue
            except Exception:
                # Upstream ignores malformed JSON lines rather than reflecting parser details.
                continue

            started = time.perf_counter()
            req_id = self._wire_id(req.get("id"))
            candidate = str(req.get("auth_token") or "")
            expected = str(host.token or "")
            if not expected or not candidate or not hmac.compare_digest(expected, candidate):
                self._write(self._error(req_id, "UNAUTHORIZED", "Invalid or missing authorization token.", host, started))
                break

            now = time.monotonic()
            while stamps and (now - stamps[0]) > self.RATE_WINDOW_S:
                stamps.popleft()
            if len(stamps) >= self.RATE_MAX:
                self._write(self._error(req_id, "API_ERROR", "Rate limit: 20 requests / 10 seconds per connection.", host, started))
                break
            stamps.append(now)

            command = req.get("command")
            if not command:
                self._write(self._error(req_id, "INVALID_ARGUMENT", "command is required", host, started))
                continue
            params = req.get("params") or {}
            try:
                with host.engine.lock:
                    if command == "apply_bake":
                        result = host.apply_bake(params)
                    elif command == "run_baked_tool":
                        result = host.run_baked_tool(params)
                    else:
                        result = host.engine.execute(
                            command,
                            params,
                            read_only=bool(req.get("read_only", False)),
                        )
                _guard_data(result)
                host.refresh_descriptor()
                res = {
                    "id": req_id,
                    "ok": True,
                    "data": result,
                    "error": None,
                    "meta": host._meta(req, started),
                }
            except Exception as exc:
                res = {
                    "id": req_id,
                    "ok": False,
                    "data": None,
                    "error": {"code": _error_code(exc), "message": _sanitize(str(exc))},
                    "meta": host._meta(req, started),
                }
            if not self._write(res):
                break


class CadHost:
    """Local CADia execution host.

    This TCP host is used only by StandaloneCAD's own in-process compatibility layer.
    It does not advertise an Inventor target and cannot launch or attach to an external
    ipt-mcp executable.  No Autodesk process, SDK, COM object, or Inventor DLL is loaded.
    """

    def __init__(self, engine, emulated_year: int = EMULATED_INVENTOR_YEAR):
        self.engine = engine
        self.token = new_token()
        self.emulated_year = int(emulated_year)
        self.target_id = f"standalonecad-{os.getpid()}"
        self.server = _LoopbackTcpServer(("127.0.0.1", 0), _Handler)
        self.server.cad_host = self
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="CADia-Host")
        self.desc = None
        self.info: dict = {}
        self.refresh_descriptor()

    def descriptor(self):
        doc = self.engine.doc
        return {
            "target_id": self.target_id,
            "id": self.target_id,
            "process_id": os.getpid(),
            "pid": os.getpid(),
            "inventor_year": self.emulated_year,
            "host_app": "CADia",
            "host": "127.0.0.1",
            "port": self.port,
            "pipe_name": None,
            "transport": "tcp",
            "auth_token": self.token,
            "document_title": getattr(doc, "title", None),
            "document_path": getattr(doc, "path", None),
            "document_type": getattr(doc, "doc_type", None),
            "last_heartbeat_utc": datetime.now(timezone.utc).isoformat(),
            "version": __version__,
            "kernel": "Open CASCADE (CadQuery/OCP)",
            "mcp_server": "CADia-MCP internal compatibility server",
            "contract": "standalonecad-local-ipt-mcp-58-tool-compat",
            "autodesk_inventor_attached": False,
        }

    def _meta(self, req, started):
        # Keep the public response-meta shape while pinning it to the local StandaloneCAD target.
        return {
            "target_id": self.target_id,
            "inventor_year": self.emulated_year,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "read_only_enforced": bool(req.get("read_only", False)),
        }

    def apply_bake(self, p: dict):
        """Host side of the internal ToolBaker accept/apply compatibility contract.

        StandaloneCAD's own MCP compatibility server performs validation/persistence;
        this host validates dispatch records and mirrors the public wire semantics.
        """
        name = str(p.get("tool_name") or "")
        source = str(p.get("source") or "preset")
        handler = str(p.get("handler_tool") or "")
        fixed = p.get("fixed_args") or {}
        sequence = p.get("sequence") or []
        source_code = str(p.get("source_code") or "")
        schema = p.get("params_schema") if p.get("params_schema") is not None else {}
        if not name or source not in {"preset", "macro"} or not source_code:
            return {"success": False, "error_code": "INVALID_ARGUMENT", "message": "Invalid baked tool record."}
        if not isinstance(schema, (dict, str)):
            return {"success": False, "error_code": "INVALID_ARGUMENT", "message": "params_schema must be an object or JSON string."}
        try:
            if source == "preset":
                _json_object(fixed)
                if handler not in _BAKED_ALLOWED:
                    raise ValueError("Baked tool target is not allowed: " + handler)
            else:
                seq = _json_array(sequence)
                for step in seq:
                    cmd = step.get("cmd") if isinstance(step, dict) else str(step)
                    if cmd not in _BAKED_ALLOWED:
                        raise ValueError("Baked tool target is not allowed: " + str(cmd))
        except Exception as exc:
            return {"success": False, "error_code": "INVALID_ARGUMENT", "message": str(exc)}
        return {
            "success": True,
            "tool_name": name,
            "description": str(p.get("description") or name),
            "params_schema": schema if isinstance(schema, str) else json.dumps(schema, separators=(",", ":")),
            "source_code": source_code,
        }

    def run_baked_tool(self, p: dict):
        record = p.get("tool_record")
        if not isinstance(record, dict):
            raise ValueError("tool_record is required.")
        runtime = _json_object(p.get("params") or {})
        # JObject.FromObject(BakedToolRecord) normally uses PascalCase property names;
        # tolerate snake_case too for forward/backward compatibility.
        def rget(*names, default=None):
            for n in names:
                if n in record:
                    return record[n]
            return default
        source = str(rget("Source", "source", default=""))
        tool_name = str(rget("Name", "name", default=p.get("name") or ""))
        results = []

        def execute_one(command: str, base_params: dict):
            if command not in _BAKED_ALLOWED:
                raise ValueError("Baked tool target is not allowed: " + str(command))
            merged = dict(base_params)
            merged.update(runtime)
            return self.engine.execute(command, merged, read_only=True)

        if source == "preset":
            command = str(rget("HandlerTool", "handler_tool", default=""))
            fixed = _json_object(rget("FixedArgs", "fixed_args", default="{}"))
            return execute_one(command, fixed)
        if source != "macro":
            raise ValueError("Baked tool source must be preset or macro.")
        for step in _json_array(rget("Sequence", "sequence", default="[]")):
            if isinstance(step, dict):
                command = str(step.get("cmd") or "")
                params = _json_object(step.get("params") or {})
            else:
                command = str(step)
                params = {}
            results.append(execute_one(command, params))
        return {"ok": True, "tool_name": tool_name, "results": results}

    def refresh_descriptor(self):
        self.info = self.descriptor()
        self.desc = write_descriptor(self.info)
        return self.info

    def start(self):
        if not self.thread.is_alive():
            self.thread.start()
        self.refresh_descriptor()
        return self.info

    def close(self):
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()
            if self.desc:
                self.desc.unlink(missing_ok=True)
