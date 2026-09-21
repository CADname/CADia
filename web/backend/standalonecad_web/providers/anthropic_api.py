from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

from standalonecad.codex_agent import _extract_json_object


class AnthropicAPIError(RuntimeError):
    pass


class AnthropicAPIProvider:
    def __init__(self, api_key: str, default_model: str | None = None, base_url: str = "https://api.anthropic.com") -> None:
        self.api_key = api_key.strip()
        self.default_model = (default_model or "").strip()
        self.base_url = base_url.rstrip("/")
        self._cancelled = threading.Event()
        self._turn_lock = threading.RLock()

    def _request(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 600) -> dict[str, Any]:
        if not self.api_key:
            raise AnthropicAPIError("Anthropic API key is missing.")
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
                detail = ((parsed.get("error") or {}).get("message") or body)
            except Exception:
                detail = body
            raise AnthropicAPIError(f"Anthropic API error ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise AnthropicAPIError(f"Anthropic API connection error: {exc.reason}") from exc

    def list_models(self) -> list[dict[str, Any]]:
        data = self._request("/v1/models", timeout=30)
        result = []
        for row in data.get("data", []) or []:
            mid = str(row.get("id") or "").strip()
            if mid:
                result.append({"id": mid, "model": mid, "displayName": row.get("display_name") or mid, "isDefault": mid == self.default_model})
        return result

    def complete_json(self, prompt: str, *, output_schema: dict[str, Any], model: str | None = None, effort: str | None = None, on_event=None) -> dict[str, Any]:
        del effort
        with self._turn_lock:
            self._cancelled.clear()
            selected_model = (model or self.default_model).strip()
            if not selected_model:
                raise AnthropicAPIError("Select a Claude model.")
            if on_event:
                on_event("progress", {"message": "Requesting CAD plan from Claude…", "percent": 8})
            strict_prompt = prompt + "\n\nReturn ONLY one JSON object matching this JSON Schema exactly:\n" + json.dumps(output_schema, ensure_ascii=False)
            response = self._request("/v1/messages", method="POST", payload={
                "model": selected_model,
                "max_tokens": 12000,
                "messages": [{"role": "user", "content": strict_prompt}],
            })
            if self._cancelled.is_set():
                raise AnthropicAPIError("The task was canceled by the user.")
            text = "".join(str(part.get("text") or "") for part in response.get("content", []) or [] if part.get("type") == "text")
            if not text:
                raise AnthropicAPIError("Claude did not return CAD plan JSON.")
            if on_event:
                on_event("progress", {"message": "Validating CAD plan…", "percent": 20})
            return _extract_json_object(text)

    def cancel(self) -> None:
        self._cancelled.set()
