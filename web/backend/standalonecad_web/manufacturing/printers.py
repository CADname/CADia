from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class PrinterError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrinterTarget:
    kind: str
    base_url: str
    api_key: str = ""
    allow_execution: bool = False


def _valid_base_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username and not parsed.password
    except Exception:
        return False


def configured_printers() -> dict[str, PrinterTarget]:
    allow_execution = os.getenv("CADIA_ALLOW_MACHINE_EXECUTION", "false").lower() in {"1", "true", "yes", "on"}
    targets: dict[str, PrinterTarget] = {}
    octo = os.getenv("CADIA_OCTOPRINT_URL", "").strip().rstrip("/")
    if octo and _valid_base_url(octo):
        targets["octoprint"] = PrinterTarget("octoprint", octo, os.getenv("CADIA_OCTOPRINT_API_KEY", "").strip(), allow_execution)
    moon = os.getenv("CADIA_MOONRAKER_URL", "").strip().rstrip("/")
    if moon and _valid_base_url(moon):
        targets["moonraker"] = PrinterTarget("moonraker", moon, os.getenv("CADIA_MOONRAKER_API_KEY", "").strip(), allow_execution)
    return targets


def _multipart(file_path: Path, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = "----cadia-" + secrets.token_hex(12)
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode(), b"\r\n",
        ]
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks += [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        file_path.read_bytes(), b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), boundary


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str]):
        super().__init__()
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if (parsed.scheme, parsed.netloc) != self.origin:
            raise PrinterError("The printer API redirected to a different origin; the request was blocked.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None, timeout: int = 20):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PrinterError("Invalid printer URL.")
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    opener = urllib.request.build_opener(_SameOriginRedirect((parsed.scheme, parsed.netloc)))
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise PrinterError("Printer API response exceeded the allowed size.")
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except Exception:
                payload = raw.decode("utf-8", "replace")
            return response.status, payload
    except PrinterError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PrinterError(str(exc)) from exc


def upload_gcode(target: PrinterTarget, file_path: Path, *, start_print: bool = False) -> dict:
    if start_print and not target.allow_execution:
        raise PrinterError("Starting a physical print requires CADIA_ALLOW_MACHINE_EXECUTION=true.")
    if not file_path.is_file() or file_path.stat().st_size <= 0:
        raise PrinterError("No G-code file is available for upload.")
    if target.kind == "octoprint":
        if not target.api_key:
            raise PrinterError("OctoPrint API key is not configured.")
        body, boundary = _multipart(file_path, {"select": "true" if start_print else "false", "print": "true" if start_print else "false"})
        status, payload = _request(
            f"{target.base_url}/api/files/local", method="POST",
            headers={"X-Api-Key": target.api_key, "Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))}, body=body,
        )
        return {"kind": target.kind, "status": status, "response": payload, "started": start_print}
    if target.kind == "moonraker":
        checksum = hashlib.sha256(file_path.read_bytes()).hexdigest()
        body, boundary = _multipart(file_path, {"root": "gcodes", "checksum": checksum, "print": "true" if start_print else "false"})
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))}
        if target.api_key:
            headers["X-Api-Key"] = target.api_key
        status, payload = _request(f"{target.base_url}/server/files/upload", method="POST", headers=headers, body=body)
        return {"kind": target.kind, "status": status, "response": payload, "started": start_print}
    raise PrinterError("Unsupported printer target.")
