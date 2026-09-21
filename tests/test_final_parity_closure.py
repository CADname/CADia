from __future__ import annotations

import json
from pathlib import Path

import pytest

from standalonecad.bridge.host import CadHost, _BAKED_ALLOWED
from standalonecad.core.engine import CadEngine
from standalonecad.mcp.bake import BakeStore, BAKED_ALLOWED
from standalonecad.mcp.server import McpServer


def _revolved(angle: float):
    e = CadEngine()
    e.execute("new_part", {})
    sk = e.execute("create_sketch", {"plane": "XY", "name": "Profile"})["sketch_name"]
    e.execute("draw_rectangle", {"sketch_name": sk, "x1": 5, "y1": -2, "x2": 10, "y2": 2})
    e.execute("close_sketch", {"sketch_name": sk})
    out = e.execute("revolve", {"sketch_name": sk, "axis_id": "YAxis", "angle_deg": angle, "operation": "join"})
    bb = e.doc.shape.BoundingBox()
    return e, out, (bb.xmin, bb.xmax, bb.ymin, bb.ymax, bb.zmin, bb.zmax)


def _saved_box(tmp_path: Path) -> Path:
    path = tmp_path / "box.scad.json"
    p = CadEngine()
    p.execute("new_part", {})
    p.execute("create_box", {"length_mm": 10, "width_mm": 10, "height_mm": 10, "replace": True})
    p.execute("save_document", {"path": str(path)})
    return path


def _tool_call(server: McpServer, short_name: str, args: dict | None = None):
    return server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "inventor_" + short_name, "arguments": args or {}},
    })["result"]


def test_revolve_matches_upstream_full_and_signed_extent_semantics():
    e0, r0, bb0 = _revolved(0)
    e360, _, bb360 = _revolved(360)
    en360, _, bbn360 = _revolved(-360)
    ep, rp, bbp = _revolved(90)
    en, rn, bbn = _revolved(-90)

    # Upstream RevolveHandler: 0 and +/-360 are AddFull.
    assert e0.doc.shape.Volume() == pytest.approx(e360.doc.shape.Volume(), rel=1e-10)
    assert e0.doc.shape.Volume() == pytest.approx(en360.doc.shape.Volume(), rel=1e-10)
    assert bb0 == pytest.approx(bb360, abs=1e-9)
    assert bb0 == pytest.approx(bbn360, abs=1e-9)

    # Negative partial angle is the same magnitude in the opposite extent direction,
    # not CadQuery's native 360-|angle| interpretation.
    assert ep.doc.shape.Volume() == pytest.approx(en.doc.shape.Volume(), rel=1e-10)
    assert rp["angle_deg"] == 90
    assert rn["angle_deg"] == -90
    assert bbp[4] < -1e-6 and bbp[5] <= 1e-8
    assert bbn[4] >= -1e-8 and bbn[5] > 1e-6


def test_flat_pattern_never_fakes_a_largest_planar_face(tmp_path):
    e = CadEngine()
    e.execute("create_box", {"length_mm": 30, "width_mm": 20, "height_mm": 2, "replace": True})
    out = tmp_path / "fake-flat.dxf"
    with pytest.raises(ValueError, match="WRONG_DOCUMENT_TYPE.*real sheet-metal flat pattern"):
        e.execute("export_dxf", {"output_path": str(out), "source": "flat_pattern"})
    assert not out.exists()


def test_flat_pattern_mcp_error_preserves_canonical_wrong_document_type(tmp_path, monkeypatch):
    engine = CadEngine()
    engine.execute("create_box", {"length_mm": 30, "width_mm": 20, "height_mm": 2, "replace": True})
    host = CadHost(engine)
    host.start()
    try:
        server = McpServer("inventor", target_info=host.info)
        res = _tool_call(server, "export_dxf", {"outputPath": str(tmp_path / "x.dxf"), "source": "flat_pattern"})
        assert res["isError"] is True
        assert res["structuredContent"]["error"]["code"] == "WRONG_DOCUMENT_TYPE"
    finally:
        host.close()


def test_assembly_bom_reports_constraint_graph_dof_not_count_estimate(tmp_path):
    part = _saved_box(tmp_path)
    a = CadEngine()
    a.execute("new_assembly", {})
    fixed = a.execute("place_occurrence", {"path": str(part), "grounded": True})["occurrence_name"]
    moving = a.execute("place_occurrence", {"path": str(part), "grounded": False, "position_mm": [20, 12, 7], "rotation_deg_xyz": [20, 30, 15]})["occurrence_name"]

    def row():
        return next(x for x in a.execute("get_assembly_bom", {})["occurrences"] if x["name"] == moving)

    assert (row()["dof_translation"], row()["dof_rotation"]) == (3, 3)
    a.execute("add_constraint", {"type": "mate", "a_occurrence": fixed, "a_ref": "XY Plane", "b_occurrence": moving, "b_ref": "XY Plane", "offset_mm": 0})
    assert (row()["dof_translation"], row()["dof_rotation"]) == (2, 1)

    # Use a clean assembly to exercise Insert's five independent equations.
    b = CadEngine(); b.execute("new_assembly", {})
    fixed2 = b.execute("place_occurrence", {"path": str(part), "grounded": True})["occurrence_name"]
    moving2 = b.execute("place_occurrence", {"path": str(part), "grounded": False, "position_mm": [20, 12, 7], "rotation_deg_xyz": [20, 30, 15]})["occurrence_name"]
    b.execute("add_constraint", {"type": "insert", "a_occurrence": fixed2, "a_ref": "Z Axis", "b_occurrence": moving2, "b_ref": "Z Axis", "offset_mm": 0, "insert_opposed": False})
    r2 = next(x for x in b.execute("get_assembly_bom", {})["occurrences"] if x["name"] == moving2)
    assert (r2["dof_translation"], r2["dof_rotation"]) == (0, 1)
    assert "dof_estimate" not in r2


def test_toolbaker_allowlist_is_the_governed_upstream_read_only_set():
    expected = {
        "health", "get_document_info", "list_open_documents", "list_parameters",
        "get_parameter", "get_iproperty", "get_mass_properties", "list_interfaces",
        "check_interference", "measure_min_distance", "get_assembly_bom", "list_constraints",
    }
    assert set(BAKED_ALLOWED) == expected
    assert set(_BAKED_ALLOWED) == expected


def test_toolbaker_rejects_write_macro_before_persistence(tmp_path):
    store = BakeStore()
    store.path = tmp_path / "bake.json"
    store.data = {"tools": {}, "patterns": {}, "suggestions": {
        "write": {
            "id": "write", "description": "bad", "status": "active",
            "payload_json": json.dumps({"steps": [{"command": "extrude", "params": {"distance_mm": 5}}]}),
        }
    }}
    with pytest.raises(ValueError, match="not allowed.*extrude"):
        store.prepare_accept("write", "bad_write")
    assert store.data["tools"] == {}


def test_toolbaker_accept_and_run_round_trip_through_host(tmp_path, monkeypatch):
    host = CadHost(CadEngine())
    host.start()
    try:
        server = McpServer("inventor", target_info=host.info)
        server.bake.path = tmp_path / "bake.json"
        server.bake.data = {"tools": {}, "patterns": {}, "suggestions": {
            "read": {
                "id": "read", "title": "inspect", "description": "read document",
                "source": "test", "score": 1.0, "status": "active",
                "payload_json": json.dumps({"steps": [{"command": "get_document_info", "params": {}}]}),
            }
        }}
        accepted = _tool_call(server, "accept_bake_suggestion", {"suggestionId": "read", "desiredName": "inspect_doc"})
        assert not accepted.get("isError", False)
        assert accepted["structuredContent"]["ok"] is True
        stored = server.bake.get_tool("inspect_doc")
        assert stored and stored["verified"] is True

        ran = _tool_call(server, "run_baked_tool", {"name": "inspect_doc", "paramsJson": "{}"})
        assert not ran.get("isError", False)
        payload = ran["structuredContent"]
        assert payload["ok"] is True
        assert payload["results"][0]["document_type"] == "part"
        assert server.bake.get_tool("inspect_doc")["usage_count"] == 1
    finally:
        host.close()
