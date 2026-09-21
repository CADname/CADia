from __future__ import annotations

import json
import socket

from standalonecad.bridge.host import CadHost
from standalonecad.core.engine import CadEngine
from standalonecad.mcp.server import McpServer


def _wire(host, command, params=None, read_only=False, token=None):
    req = {
        "id": "11111111-1111-1111-1111-111111111111",
        "command": command,
        "params": params or {},
        "timeout_ms": 30000,
        "auth_token": token if token is not None else host.token,
        "read_only": read_only,
    }
    with socket.create_connection(("127.0.0.1", host.port), timeout=2) as sock:
        f = sock.makefile("rwb")
        f.write((json.dumps(req) + "\n").encode())
        f.flush()
        return json.loads(f.readline())


def test_standalone_wire_contract_and_auth():
    engine = CadEngine()
    host = CadHost(engine)
    host.start()
    try:
        d = host.info
        assert d["target_id"].startswith("standalonecad-")
        assert d["host_app"] in {"CADia", "StandaloneCAD"}
        assert d["autodesk_inventor_attached"] is False
        assert d["transport"] == "tcp"
        assert d["contract"] == "standalonecad-local-ipt-mcp-58-tool-compat"

        r = _wire(host, "health")
        assert set(r) >= {"id", "ok", "data", "error", "meta"}
        assert r["ok"] is True
        assert r["data"]["host_app"] in {"CADia", "StandaloneCAD"}
        assert r["meta"]["target_id"] == host.info["target_id"]

        ro = _wire(host, "new_part", {}, read_only=True)
        assert ro["ok"] is False and ro["error"]["code"] == "READ_ONLY"

        bad = _wire(host, "health", token="wrong")
        assert bad["ok"] is False and bad["error"]["code"] == "UNAUTHORIZED"

        sc = _wire(host, "send_code", {"code": "return 1;"})
        assert sc["ok"] is False and sc["error"]["code"] == "SEND_CODE_DISABLED"
    finally:
        host.close()


def test_exact_58_default_surface_and_59_code_surface():
    default = McpServer("inventor")
    code = McpServer("inventor", enable_code=True)
    assert len(default.tools) == 58
    assert len(code.tools) == 59
    assert "inventor_send_code" not in default.tool_names
    assert "inventor_send_code" in code.tool_names



def test_embedded_target_pin_cannot_switch_away():
    a = CadHost(CadEngine())
    a.start()
    try:
        s = McpServer("inventor", target_id=a.info["target_id"])
        listed = s.dispatch({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"inventor_list_available_targets","arguments":{}}})["result"]["structuredContent"]["targets"]
        assert [x["target_id"] for x in listed] == [a.info["target_id"]]
        out = s.dispatch({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"inventor_switch_target","arguments":{"target":"standalonecad-other"}}})["result"]
        assert out["isError"] is True
        assert "TARGET_PINNED" in out["structuredContent"]["error"]["message"]
    finally:
        a.close()
