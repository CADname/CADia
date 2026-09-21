from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from standalonecad.codex_agent import _extract_json_object


class GeminiAPIError(RuntimeError):
    pass


class GeminiAPIProvider:
    def __init__(self, api_key: str, default_model: str | None = None, base_url: str = "https://generativelanguage.googleapis.com/v1beta") -> None:
        self.api_key = api_key.strip()
        self.default_model = (default_model or "").strip()
        self.base_url = base_url.rstrip("/")
        self._cancelled = threading.Event()
        self._turn_lock = threading.RLock()

    def _request(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 600) -> dict[str, Any]:
        if not self.api_key:
            raise GeminiAPIError("Gemini API key is missing.")
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method,
            headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
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
            raise GeminiAPIError(f"Gemini API error ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise GeminiAPIError(f"Gemini API connection error: {exc.reason}") from exc

    def list_models(self) -> list[dict[str, Any]]:
        data = self._request("/models", timeout=30)
        result = []
        for row in data.get("models", []) or []:
            methods = row.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            raw = str(row.get("name") or "")
            mid = raw.split("/", 1)[1] if raw.startswith("models/") else raw
            if mid:
                result.append({"id": mid, "model": mid, "displayName": row.get("displayName") or mid, "isDefault": mid == self.default_model})
        return result

    def complete_json(self, prompt: str, *, output_schema: dict[str, Any], model: str | None = None, effort: str | None = None, on_event=None) -> dict[str, Any]:
        del effort
        with self._turn_lock:
            self._cancelled.clear()
            selected_model = (model or self.default_model).strip()
            if selected_model.startswith("models/"):
                selected_model = selected_model.split("/", 1)[1]
            if not selected_model:
                raise GeminiAPIError("Select a Gemini model.")
            if on_event:
                on_event("progress", {"message": "Requesting CAD plan from Gemini…", "percent": 8})
            model_path = urllib.parse.quote(selected_model, safe="-._")
            strict_prompt = prompt + "\n\nReturn ONLY one JSON object matching this JSON Schema exactly:\n" + json.dumps(output_schema, ensure_ascii=False)
            response = self._request(f"/models/{model_path}:generateContent", method="POST", payload={
                "contents": [{"role": "user", "parts": [{"text": strict_prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            })
            if self._cancelled.is_set():
                raise GeminiAPIError("The task was canceled by the user.")
            parts = []
            for candidate in response.get("candidates", []) or []:
                for part in ((candidate.get("content") or {}).get("parts") or []):
                    if isinstance(part.get("text"), str):
                        parts.append(part["text"])
            text = "".join(parts)
            if not text:
                raise GeminiAPIError("Gemini did not return CAD plan JSON.")
            if on_event:
                on_event("progress", {"message": "Validating CAD plan…", "percent": 20})
            return _extract_json_object(text)

    def cancel(self) -> None:
        self._cancelled.set()
