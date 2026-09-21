from __future__ import annotations

import json
import socket
import uuid

from .protocol import list_descriptors


class HostClient:
    """Development/fallback client for the private CADia host descriptor."""

    def __init__(self, target_hint: str | None = None, target_info: dict | None = None):
        self.target = None
        self.target_hint = target_hint
        self._bound_target = None
        # The in-app controller already owns the exact live host descriptor.  Bind it
        # directly instead of rediscovering our own process through a descriptor file.
        # External/stdin MCP clients still use the normal descriptor discovery path.
        if target_info is not None:
            info = dict(target_info)
            actual = str(info.get("target_id") or info.get("id") or "")
            if target_hint and actual and actual != str(target_hint):
                raise ValueError(f"Target descriptor mismatch: expected {target_hint!r}, got {actual!r}")
            self._bound_target = info
            self.target = dict(info)
            if not self.target_hint and actual:
                self.target_hint = actual
        elif target_hint:
            self.select(target_hint)

    @staticmethod
    def public(x):
        return {k: v for k, v in x.items() if k not in ("auth_token", "_path")}

    def targets(self):
        ds = list_descriptors()
        if self._bound_target is not None:
            bound_id = str(self._bound_target.get("target_id") or self._bound_target.get("id") or "")
            if not any(str(x.get("target_id") or x.get("id") or "") == bound_id for x in ds):
                ds.append(dict(self._bound_target))
        return [self.public(x) for x in ds]

    def select(self, target=None):
        if target is not None:
            self.target_hint = str(target)
        wanted = self.target_hint
        if self._bound_target is not None:
            candidates = {
                str(self._bound_target.get("target_id") or ""),
                str(self._bound_target.get("id") or ""),
                str(self._bound_target.get("process_id") or ""),
                str(self._bound_target.get("pid") or ""),
            }
            if not wanted or str(wanted) in candidates:
                self.target = dict(self._bound_target)
                return self.target
        ds = list_descriptors()
        if not ds:
            self.target = None
            return None
        if wanted:
            self.target = next((x for x in ds if str(x.get("target_id")) == wanted or str(x.get("id")) == wanted or str(x.get("process_id")) == wanted or str(x.get("pid")) == wanted), None)
        else:
            self.target = ds[-1]
        return self.target

    def call(self, command, params=None, read_only=False):
        if self.target is None:
            self.select()
        if self.target is None:
            raise RuntimeError(f"NO_TARGET: CADia target {self.target_hint!r} is not live" if self.target_hint else "NO_TARGET: start CADia UI first")
        req = {"id": str(uuid.uuid4()), "command": command, "params": params or {}, "timeout_ms": 30000, "auth_token": self.target["auth_token"], "read_only": bool(read_only)}
        try:
            with socket.create_connection((self.target["host"], int(self.target["port"])), timeout=45) as sock:
                f = sock.makefile("rwb")
                f.write((json.dumps(req, separators=(",", ":")) + "\n").encode())
                f.flush()
                line = f.readline()
        except OSError as exc:
            self.target = None
            raise RuntimeError("NO_TARGET: pinned CADia target is no longer reachable") from exc
        if not line:
            raise RuntimeError("Host closed connection")
        res = json.loads(line)
        if not res.get("ok"):
            raise RuntimeError(f"{res.get('error', {}).get('code')}: {res.get('error', {}).get('message')}")
        return res.get("data")
