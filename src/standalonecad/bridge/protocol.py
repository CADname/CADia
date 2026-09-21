from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

APP_DIR = Path.home() / ".standalonecad-mcp"
APP_DIR.mkdir(parents=True, exist_ok=True)


def new_token() -> str:
    """Match bimwright/ipt-mcp v0.1.0 AuthToken.Generate().

    Upstream generates 32 random bytes and serializes them as 64 lowercase hex chars.
    """
    return secrets.token_hex(32)


def descriptor_path(pid: int) -> Path:
    return APP_DIR / f"host-{pid}.json"


def write_descriptor(info: dict) -> Path:
    p = descriptor_path(int(info["process_id"]))
    p.write_text(json.dumps(info, indent=2), encoding="utf-8")
    return p


def list_descriptors() -> list[dict]:
    out: list[dict] = []
    for p in APP_DIR.glob("host-*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            pid = int(d.get("process_id", d.get("pid", 0)))
            try:
                os.kill(pid, 0)
            except Exception:
                p.unlink(missing_ok=True)
                continue
            d["_path"] = str(p)
            out.append(d)
        except Exception:
            pass
    return sorted(out, key=lambda x: x.get("process_id", 0))
