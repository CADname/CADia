#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser(description="CADia Web project MCP stdio bridge")
    ap.add_argument("--server", required=True, help="CADia web origin, e.g. https://cad.example.com")
    ap.add_argument("--project", required=True, help="CADia project UUID")
    ap.add_argument("--token", default=None, help="Project MCP token; prefer CADIA_MCP_TOKEN env")
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()

    token = args.token or os.getenv("CADIA_MCP_TOKEN", "").strip() or os.getenv("STANDALONECAD_MCP_TOKEN", "").strip()
    if not token:
        print("CADIA_MCP_TOKEN is required (legacy STANDALONECAD_MCP_TOKEN is also accepted)", file=sys.stderr, flush=True)
        return 2
    url = args.server.rstrip("/") + "/api/mcp/projects/" + args.project

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=args.timeout) as response:
                    raw = response.read()
                    if response.status == 204 or not raw:
                        continue
                    out = json.loads(raw)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(detail)
                    message = parsed.get("detail") or detail
                except Exception:
                    message = detail or str(exc)
                out = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": {"code": -32001, "message": f"CADia web bridge HTTP {exc.code}: {message}"},
                }
            print(json.dumps(out, ensure_ascii=False, separators=(",", ":")), flush=True)
        except Exception as exc:
            try:
                rid = payload.get("id") if isinstance(payload, dict) else None
            except Exception:
                rid = None
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(exc)}}, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
