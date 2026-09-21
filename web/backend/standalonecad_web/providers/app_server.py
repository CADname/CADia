from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from standalonecad.codex_agent import _extract_json_object

from ..config import settings


class AppServerError(RuntimeError):
    pass


class AppServerClient:
    """One isolated Codex app-server stdio process for exactly one CADia user."""

    def __init__(self, user_id: str, codex_home: Path, workspace: Path, executable: str | None = None):
        self.user_id = str(user_id)
        self.codex_home = codex_home.resolve()
        self.workspace = workspace.resolve()
        self.executable = executable or settings.codex_bin
        self.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stderr: deque[str] = deque(maxlen=80)
        self._pending: dict[int, queue.Queue] = {}
        self._pending_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._turn_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._events: deque[tuple[int, dict[str, Any]]] = deque(maxlen=4000)
        self._event_seq = 0
        self._event_condition = threading.Condition()
        self._next_id = 1
        self._inflight = 0
        self._active_turn: tuple[str, str] | None = None
        self.last_access = time.monotonic()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _resolve_executable(self) -> str:
        path = shutil.which(self.executable)
        if path:
            return path
        candidate = Path(self.executable)
        if candidate.is_file():
            return str(candidate.resolve())
        raise FileNotFoundError(f"Could not find Codex CLI: {self.executable}")

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.running:
                self.last_access = time.monotonic()
                return
            self.stop()
            env = os.environ.copy()
            env["CODEX_HOME"] = str(self.codex_home)
            env["HOME"] = str(self.codex_home.parent / "home")
            Path(env["HOME"]).mkdir(parents=True, exist_ok=True, mode=0o700)
            process = subprocess.Popen(
                [self._resolve_executable(), "app-server"],
                cwd=str(self.workspace),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._process = process
            self._stderr.clear()
            with self._event_condition:
                self._events.clear()
                self._event_seq = 0
            self._reader = threading.Thread(target=self._read_stdout, args=(process,), daemon=True, name=f"codex-app-{self.user_id[:8]}")
            self._stderr_reader = threading.Thread(target=self._read_stderr, args=(process,), daemon=True, name=f"codex-app-err-{self.user_id[:8]}")
            self._reader.start()
            self._stderr_reader.start()
            try:
                self._request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "cadia_web",
                            "title": "CADia Web",
                            "version": "2026.09.15",
                        }
                    },
                    timeout=30,
                    ensure_started=False,
                )
                self._notify("initialized", {})
            except Exception:
                self.stop()
                raise
            self.last_access = time.monotonic()

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            self._stderr.append(line.rstrip())

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except Exception:
                    continue
                message_id = message.get("id")
                if message_id is not None and "method" not in message:
                    with self._pending_lock:
                        waiter = self._pending.get(int(message_id))
                    if waiter:
                        waiter.put(message)
                    continue
                if message_id is not None and message.get("method"):
                    self._reply_server_request(message)
                    continue
                with self._event_condition:
                    self._event_seq += 1
                    self._events.append((self._event_seq, message))
                    self._event_condition.notify_all()
        finally:
            if self._process is not process:
                return
            error = AppServerError(self._exit_message())
            with self._pending_lock:
                for waiter in self._pending.values():
                    waiter.put(error)
            with self._event_condition:
                self._event_condition.notify_all()

    def _reply_server_request(self, message: dict[str, Any]) -> None:
        # CAD planning is deliberately read-only and approvalPolicy=never. Unexpected
        # host requests fail closed instead of giving the model host capabilities.
        self._send_raw(
            {
                "id": message.get("id"),
                "error": {"code": -32601, "message": f"Unsupported server request: {message.get('method')}"},
            }
        )

    def _exit_message(self) -> str:
        process = self._process
        code = process.poll() if process else None
        tail = "\n".join(list(self._stderr)[-12:])
        base = f"Codex App Server exited (code={code})."
        return f"{base}\n{tail}".strip()

    def _send_raw(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise AppServerError(self._exit_message())
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            process.stdin.write(line)
            process.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        self._send_raw(payload)

    def _request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 60, ensure_started: bool = True) -> dict[str, Any]:
        if ensure_started:
            self.start()
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            waiter: queue.Queue = queue.Queue(maxsize=1)
            self._pending[request_id] = waiter
            self._inflight += 1
        payload: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            payload["params"] = params
        try:
            self._send_raw(payload)
            response = waiter.get(timeout=timeout)
            if isinstance(response, Exception):
                raise response
            if response.get("error"):
                error = response["error"]
                raise AppServerError(str(error.get("message") or error))
            self.last_access = time.monotonic()
            return response.get("result") or {}
        except queue.Empty as exc:
            raise TimeoutError(f"Codex App Server request timed out: {method}") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
                self._inflight = max(0, self._inflight - 1)

    def _events_after(self, seq: int) -> list[tuple[int, dict[str, Any]]]:
        with self._event_condition:
            return [(number, event) for number, event in self._events if number > seq]

    def account(self, refresh: bool = False) -> dict[str, Any]:
        return self._request("account/read", {"refreshToken": bool(refresh)}, timeout=30)

    def start_device_login(self) -> dict[str, Any]:
        return self._request("account/login/start", {"type": "chatgptDeviceCode"}, timeout=30)

    def cancel_login(self, login_id: str) -> dict[str, Any]:
        return self._request("account/login/cancel", {"loginId": login_id}, timeout=30)

    def login_status(self, login_id: str) -> dict[str, Any]:
        with self._event_condition:
            for _, event in reversed(self._events):
                if event.get("method") != "account/login/completed":
                    continue
                params = event.get("params") or {}
                if str(params.get("loginId") or "") == str(login_id):
                    return {
                        "pending": False,
                        "success": bool(params.get("success")),
                        "error": params.get("error"),
                    }
        return {"pending": True, "success": None, "error": None}

    def logout(self) -> dict[str, Any]:
        return self._request("account/logout", None, timeout=30)

    def rate_limits(self) -> dict[str, Any]:
        return self._request("account/rateLimits/read", None, timeout=30)

    def models(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            result = self._request("model/list", params, timeout=30)
            data.extend(x for x in result.get("data", []) if isinstance(x, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return data

    def choose_model(self, requested: str | None = None) -> tuple[str, dict[str, Any]]:
        models = self.models()
        if not models:
            raise AppServerError("No Codex models are available for the signed-in account.")
        by_id = {str(item.get("model") or item.get("id")): item for item in models}
        candidates = [requested] if requested else []
        candidates.extend(settings.preferred_models)
        for candidate in candidates:
            if candidate and candidate in by_id:
                return str(candidate), by_id[str(candidate)]
        default = next((item for item in models if item.get("isDefault")), models[0])
        return str(default.get("model") or default.get("id")), default

    @staticmethod
    def _effort(model_info: dict[str, Any], requested: str | None) -> str | None:
        supported = [str(x.get("reasoningEffort")) for x in model_info.get("supportedReasoningEfforts", []) if x.get("reasoningEffort")]
        if requested and (not supported or requested in supported):
            return requested
        default = model_info.get("defaultReasoningEffort")
        if default and (not supported or default in supported):
            return str(default)
        return supported[0] if supported else None

    def complete_json(
        self,
        prompt: str,
        *,
        output_schema: dict[str, Any],
        model: str | None = None,
        effort: str | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ) -> dict[str, Any]:
        with self._turn_lock:
            account = self.account()
            if not account.get("account"):
                raise AppServerError("ChatGPT/Codex sign-in is required.")
            selected_model, model_info = self.choose_model(model)
            selected_effort = self._effort(model_info, effort)
            if on_event:
                on_event("progress", {"message": f"{model_info.get('displayName') or selected_model} requesting…", "percent": 8})
            thread_result = self._request(
                "thread/start",
                {
                    "model": selected_model,
                    "cwd": str(self.workspace),
                    "approvalPolicy": "never",
                    "sandbox": "readOnly",
                    "serviceName": "cadia_web",
                },
                timeout=60,
            )
            thread_id = str((thread_result.get("thread") or {}).get("id") or "")
            if not thread_id:
                raise AppServerError("Codex thread/start did not return a thread id.")
            with self._event_condition:
                start_seq = self._event_seq
            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "cwd": str(self.workspace),
                "approvalPolicy": "never",
                "sandboxPolicy": {
                    "type": "readOnly",
                    "access": {"type": "restricted", "includePlatformDefaults": True, "readableRoots": []},
                },
                "model": selected_model,
                "outputSchema": output_schema,
            }
            if selected_effort:
                turn_params["effort"] = selected_effort
            turn_result = self._request("turn/start", turn_params, timeout=60)
            turn_id = str((turn_result.get("turn") or {}).get("id") or "")
            if not turn_id:
                raise AppServerError("Codex turn/start did not return a turn id.")
            self._active_turn = (thread_id, turn_id)
            final_text = ""
            delta_text = ""
            cursor = start_seq
            deadline = time.monotonic() + 600
            turn_finished = False
            try:
                while time.monotonic() < deadline:
                    events = self._events_after(cursor)
                    if not events:
                        with self._event_condition:
                            self._event_condition.wait(timeout=min(1.0, max(0.0, deadline - time.monotonic())))
                        continue
                    for seq, event in events:
                        cursor = max(cursor, seq)
                        method = event.get("method")
                        params = event.get("params") or {}
                        event_thread = params.get("threadId") or (params.get("turn") or {}).get("threadId")
                        if event_thread and str(event_thread) != thread_id:
                            continue
                        if method == "item/agentMessage/delta":
                            delta = params.get("delta")
                            if isinstance(delta, str):
                                delta_text += delta
                        elif method == "item/completed":
                            item = params.get("item") or {}
                            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                                final_text = item["text"]
                        elif method == "error":
                            error = params.get("error") or event.get("error") or {}
                            raise AppServerError(str(error.get("message") or error))
                        elif method == "turn/completed":
                            turn = params.get("turn") or {}
                            if str(turn.get("id") or turn_id) != turn_id:
                                continue
                            status = turn.get("status")
                            if status != "completed":
                                error = turn.get("error") or {}
                                raise AppServerError(str(error.get("message") or f"Codex turn {status}"))
                            text = final_text or delta_text
                            if not text:
                                for item in turn.get("items", []) or []:
                                    if item.get("type") == "agentMessage" and item.get("text"):
                                        text = str(item["text"])
                            if not text:
                                raise AppServerError("Codex did not return CAD plan JSON.")
                            if on_event:
                                on_event("progress", {"message": "Validating CAD plan…", "percent": 20})
                            turn_finished = True
                            return _extract_json_object(text)
                raise TimeoutError("Codex modeling plan generation exceeded 10 minutes.")
            finally:
                if not turn_finished and self.running:
                    try:
                        self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=10)
                    except Exception:
                        pass
                self._active_turn = None
                if self.running:
                    try:
                        self._request("thread/delete", {"threadId": thread_id}, timeout=10)
                    except Exception:
                        pass

    def cancel(self) -> None:
        active = self._active_turn
        if active and self.running:
            try:
                self._request("turn/interrupt", {"threadId": active[0], "turnId": active[1]}, timeout=10)
            except Exception:
                pass

    def stop(self) -> None:
        with self._lifecycle_lock:
            process, self._process = self._process, None
            self._active_turn = None
            if process is None:
                return
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            except Exception:
                pass


class AppServerManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, AppServerClient] = {}

    def get(self, user_id: str) -> AppServerClient:
        with self._lock:
            client = self._items.get(user_id)
            if client is None:
                if len(self._items) >= settings.max_codex_processes:
                    safe_cutoff = time.monotonic() - 30
                    candidates = [
                        (key, value)
                        for key, value in self._items.items()
                        if value._active_turn is None and value._inflight == 0 and value.last_access < safe_cutoff
                    ]
                    if not candidates:
                        raise AppServerError("Concurrent Codex user limit reached. Please try again shortly.")
                    victim_key, victim = min(candidates, key=lambda item: item[1].last_access)
                    self._items.pop(victim_key)
                    victim.stop()
                root = settings.data_root / "codex-users" / user_id
                client = AppServerClient(user_id, root / "codex-home", root / "agent-workspace")
                self._items[user_id] = client
            client.last_access = time.monotonic()
            return client

    def evict_idle(self) -> int:
        cutoff = time.monotonic() - settings.codex_process_idle_seconds
        with self._lock:
            stale = [key for key, client in self._items.items() if client.last_access < cutoff and client._active_turn is None]
            for key in stale:
                self._items.pop(key).stop()
        return len(stale)

    def close_all(self) -> None:
        with self._lock:
            values = list(self._items.values())
            self._items.clear()
        for client in values:
            client.stop()


app_server_manager = AppServerManager()
