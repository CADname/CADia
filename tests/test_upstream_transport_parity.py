from __future__ import annotations

import json
import socket
from pathlib import Path

from standalonecad.bridge.host import CadHost
from standalonecad.core.engine import CadEngine


def _open(host):
    s = socket.create_connection(("127.0.0.1", host.port), timeout=2)
    return s, s.makefile("rwb")


def _req(i: int, host, command="health", token=None):
    return {
        "id": f"00000000-0000-0000-0000-{i:012d}",
        "command": command,
        "params": {},
        "timeout_ms": 30000,
        "auth_token": host.token if token is None else token,
        "read_only": False,
    }


def test_auth_token_matches_upstream_256bit_lower_hex():
    host = CadHost(CadEngine())
    try:
        assert len(host.token) == 64
        assert host.token == host.token.lower()
        int(host.token, 16)
    finally:
        host.server.server_close()


def test_malformed_line_is_ignored_then_valid_request_works():
    host = CadHost(CadEngine())
    host.start()
    try:
        s, f = _open(host)
        with s, f:
            f.write(b"not-json\n")
            f.write((json.dumps(_req(1, host)) + "\n").encode())
            f.flush()
            r = json.loads(f.readline())
            assert r["ok"] is True
            assert r["id"].endswith("000000000001")
    finally:
        host.close()


def test_auth_failure_returns_unauthorized_and_drops_connection():
    host = CadHost(CadEngine())
    host.start()
    try:
        s, f = _open(host)
        with s, f:
            f.write((json.dumps(_req(1, host, token="bad")) + "\n").encode())
            f.flush()
            r = json.loads(f.readline())
            assert r["ok"] is False
            assert r["error"]["code"] == "UNAUTHORIZED"
            # Upstream closes the TCP session on auth failure.
            assert f.readline() == b""
    finally:
        host.close()


def test_upstream_rate_limit_20_requests_per_10s_and_drop():
    host = CadHost(CadEngine())
    host.start()
    try:
        s, f = _open(host)
        with s, f:
            for i in range(1, 21):
                f.write((json.dumps(_req(i, host)) + "\n").encode())
                f.flush()
                r = json.loads(f.readline())
                assert r["ok"] is True, (i, r)
            f.write((json.dumps(_req(21, host)) + "\n").encode())
            f.flush()
            r = json.loads(f.readline())
            assert r["ok"] is False
            assert r["error"]["code"] == "API_ERROR"
            assert "20 requests / 10 seconds" in r["error"]["message"]
            assert f.readline() == b""
    finally:
        host.close()


def test_bootstrap_has_no_external_upstream_runtime():
    root = Path(__file__).resolve().parents[1]
    checked_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in [root / "Dockerfile", root / "docker-compose.yml"]
        if p.exists()
    )
    assert "prepare_upstream_ipt_mcp.py" not in checked_text
    assert "check_upstream_server.py --setup" not in checked_text
    assert "check_upstream_server.py --structural-only" not in checked_text
    assert not (root / "src" / "standalonecad" / "original_gateway.py").exists()
    assert not (root / "src" / "standalonecad" / "bridge" / "ipt_compat.py").exists()
    assert not (root / "VERIFY_UPSTREAM_RUNTIME.bat").exists()
