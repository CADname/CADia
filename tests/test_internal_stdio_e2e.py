from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from standalonecad.bridge.host import CadHost
from standalonecad.core.engine import CadEngine


def _request(proc, payload):
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, "MCP server closed stdout"
    return json.loads(line)


def _call(proc, i, name, args=None):
    r = _request(proc, {"jsonrpc":"2.0","id":i,"method":"tools/call","params":{"name":name,"arguments":args or {}}})
    assert "error" not in r, r
    out = r["result"]
    assert not out.get("isError"), out
    return out["structuredContent"]


def test_internal_stdio_mcp_to_live_occt_host():
    engine = CadEngine()
    host = CadHost(engine)
    host.start()
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    proc = subprocess.Popen(
        [sys.executable, "-m", "standalonecad.mcp.server", "--target", host.info["target_id"]],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        init = _request(proc, {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}})
        assert init["result"]["serverInfo"]["name"] in {"CADia-MCP", "StandaloneCAD-MCP"}
        tools = _request(proc, {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})["result"]["tools"]
        assert len(tools) == 58
        _call(proc, 3, "inventor_new_part")
        sk = _call(proc, 4, "inventor_create_sketch", {"plane":"XY"})["sketch_name"]
        _call(proc, 5, "inventor_draw_rectangle", {"x1":-10,"y1":-5,"x2":10,"y2":5})
        _call(proc, 6, "inventor_close_sketch", {"sketchName":sk})
        _call(proc, 7, "inventor_extrude", {"sketchName":sk,"distance":3,"operation":"join","direction":"positive"})
        mass = _call(proc, 8, "inventor_get_mass_properties")
        assert abs(mass["volume_mm3"] - 600.0) < 1e-6
        assert abs(engine.doc.shape.Volume() - 600.0) < 1e-6
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        host.close()
