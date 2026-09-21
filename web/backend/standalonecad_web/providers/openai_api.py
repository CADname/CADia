from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any, Callable

from standalonecad.codex_agent import _extract_json_object

from ..config import settings


class OpenAIAPIError(RuntimeError):
    pass


class OpenAIAPIProvider:
    """Optional operator-paid fallback. It is never the default provider."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.openai_api_key
        self.base_url = (base_url or settings.openai_api_base).rstrip("/")
        self.default_model = default_model or settings.openai_api_model
        self._transport = transport
        self._cancelled = threading.Event()
        self._turn_lock = threading.RLock()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._transport:
            return self._transport(payload)
        if not self.api_key:
            raise OpenAIAPIError("Operator OpenAI API key is not configured.")
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                detail = (json.loads(body).get("error") or {}).get("message") or body
            except Exception:
                detail = body
            raise OpenAIAPIError(f"OpenAI API error ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise OpenAIAPIError(f"OpenAI API connection error: {exc.reason}") from exc

    @staticmethod
    def _output_text(response: dict[str, Any]) -> str:
        direct = response.get("output_text")
        if isinstance(direct, str) and direct:
            return direct
        parts: list[str] = []
        for item in response.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "".join(parts)

    def list_models(self) -> list[dict[str, Any]]:
        if not self.api_key:
            raise OpenAIAPIError("OpenAI API key is missing.")
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                detail = (json.loads(body).get("error") or {}).get("message") or body
            except Exception:
                detail = body
            raise OpenAIAPIError(f"OpenAI API error ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise OpenAIAPIError(f"OpenAI API connection error: {exc.reason}") from exc
        rows = []
        for item in payload.get("data", []) or []:
            mid = str(item.get("id") or "").strip()
            if mid:
                rows.append({"id": mid, "model": mid, "displayName": mid, "isDefault": mid == self.default_model})
        return rows

    def complete_json(
        self,
        prompt: str,
        *,
        output_schema: dict[str, Any],
        model: str | None = None,
        effort: str | None = None,
        on_event=None,
    ) -> dict[str, Any]:
        with self._turn_lock:
            return self._complete_json(
                prompt,
                output_schema=output_schema,
                model=model,
                effort=effort,
                on_event=on_event,
            )

    def _complete_json(
        self,
        prompt: str,
        *,
        output_schema: dict[str, Any],
        model: str | None = None,
        effort: str | None = None,
        on_event=None,
    ) -> dict[str, Any]:
        self._cancelled.clear()
        selected_model = model or self.default_model
        if not selected_model:
            raise OpenAIAPIError("Operator API fallback model is not configured.")
        if on_event:
            on_event("progress", {"message": "Requesting CAD plan from OpenAI API…", "percent": 8})
        payload: dict[str, Any] = {
            "model": selected_model,
            "input": [{"role": "user", "content": prompt}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "cad_tool_plan",
                    "strict": True,
                    "schema": output_schema,
                }
            },
            "store": False,
        }
        if effort:
            payload["reasoning"] = {"effort": effort}
        response = self._post(payload)
        if self._cancelled.is_set():
            raise OpenAIAPIError("The task was canceled by the user.")
        text = self._output_text(response)
        if not text:
            error = response.get("error") or {}
            raise OpenAIAPIError(str(error.get("message") or "OpenAI API did not return CAD plan JSON."))
        if on_event:
            on_event("progress", {"message": "Validating CAD plan…", "percent": 20})
        return _extract_json_object(text)

    def cancel(self) -> None:
        # The non-streaming HTTP fallback cannot abort urllib portably, but the
        # result is discarded before it can mutate CAD state.
        self._cancelled.set()


class OpenAIAPIManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, OpenAIAPIProvider] = {}

    def get(self, user_id: str) -> OpenAIAPIProvider:
        with self._lock:
            provider = self._items.get(user_id)
            if provider is None:
                provider = OpenAIAPIProvider()
                self._items[user_id] = provider
            return provider


openai_api_manager = OpenAIAPIManager()
