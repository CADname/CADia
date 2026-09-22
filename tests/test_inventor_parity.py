import math
from pathlib import Path

from standalonecad.core.engine import CadEngine
from standalonecad.mcp.tools import make_tools
from standalonecad.planning import direct_plan


def test_exact_user_m10_hex_bolt_prompt_routes_to_modeled_standard_generator():
    text = "Create an M10 hex bolt with head height 6.4 mm, across flats 16 mm, and thread length 30 mm."
    plan = direct_plan(text)
    assert plan and plan["note"] == "deterministic_metric_hex_bolt"
    call = plan["calls"][0]
    assert call["tool"] == "cad_create_metric_hex_bolt"
    assert call["arguments"]["diameter_mm"] == 10
    assert call["arguments"]["length_mm"] == 30
    assert call["arguments"]["thread_length_mm"] == 30
    assert call["arguments"]["head_across_flats_mm"] == 16
    assert call["arguments"]["head_height_mm"] == 6.4

    e = CadEngine()
    out = e.execute("create_metric_hex_bolt", call["arguments"])
    assert out["designation"] == "M10x1.5"
    assert out["modeled_thread"] is True
    assert math.isclose(out["minor_diameter_mm"], 8.15971, abs_tol=1e-4)
    assert len(e.doc.shape.Solids()) == 1
    bb = e.doc.shape.BoundingBox()
    assert math.isclose(bb.ylen, 16.0, abs_tol=1e-3)  # across flats in current orientation
    assert math.isclose(bb.zlen, 36.4, abs_tol=1e-3)  # 30 under-head + 6.4 head
    # A plain hex+shaft would have very few faces; modeled helical thread must add topology.
    assert len(e.doc.shape.Faces()) > 20


def test_metric_nut_washer_and_real_tapped_hole():
    e = CadEngine()
    nut = e.execute("create_metric_hex_nut", {"diameter_mm":10,"replace":True,"modeled_thread":True,"right_handed":True})
    assert nut["designation"] == "M10x1.5"
    assert len(e.doc.shape.Solids()) == 1
    assert len(e.doc.shape.Faces()) > 10

    washer = e.execute("create_metric_washer", {"diameter_mm":10,"replace":True})
    assert washer["inner_diameter_mm"] == 10.5
    assert washer["outer_diameter_mm"] == 20.0

    e = CadEngine()
    e.execute("create_box", {"length_mm":30,"width_mm":30,"height_mm":15,"replace":True,"name":"Block"})
    e.execute("hole", {
        "face":{"kind":"planar","normal":"+Z","extreme":"max"},
        "points_mm":[[15,15,15]], "diameter_mm":10, "kind":"drilled",
        "through":False, "depth_mm":12, "tapped_designation":"M10",
        "tapped_class":"6H", "tapped_right_handed":True, "tapped_full_depth":True,
    })
    derived = e.doc.features[-1].params["tapped_derived"]
    assert derived["designation"] == "M10x1.5"
    assert math.isclose(derived["thread_length_mm"], 12.0, abs_tol=1e-6)
    assert len(e.doc.shape.Faces()) > 8


def test_persistent_face_and_edge_rebinding_survives_upstream_dimension_edit():
    e = CadEngine()
    e.execute("create_box", {"length_mm":20,"width_mm":10,"height_mm":8,"replace":True,"name":"Box1"})
    top = next(r for r in e.doc.topology()["faces"] if r.get("semantic") == "top_face")
    e.execute("shell", {"remove_face_ids":[top["id"]],"thickness_mm":-1.0,"name":"Shell2"})
    e.execute("edit_feature", {"feature_name":"Box1","updates":{"length_mm":30}})
    assert math.isclose(e.doc.shape.BoundingBox().xlen, 30.0, abs_tol=1e-6)
    assert e.doc.features[-1].kind == "shell"

    e = CadEngine()
    e.execute("create_box", {"length_mm":20,"width_mm":10,"height_mm":8,"replace":True,"name":"Box1"})
    edge = e.doc.topology()["edges"][0]
    e.execute("fillet", {"edge_ids":[edge["id"]],"radius_mm":1.0})
    e.execute("edit_feature", {"feature_name":"Box1","updates":{"length_mm":30}})
    assert math.isclose(e.doc.shape.BoundingBox().xlen, 30.0, abs_tol=1e-6)
    assert any(f.kind == "fillet" for f in e.doc.features)


def test_loft_sweep_and_history_controls():
    e = CadEngine()
    a = e.execute("create_sketch", {"plane":"XY"})["sketch_name"]
    e.execute("draw_rectangle", {"x1":-10,"y1":-5,"x2":10,"y2":5})
    e.execute("close_sketch", {"sketch_name":a})
    wp = e.execute("create_work_plane", {"type":"offset","refs":["XY Plane"],"offset_mm":20})["work_plane_name"]
    b = e.execute("create_sketch", {"plane":wp})["sketch_name"]
    e.execute("draw_circle", {"cx":0,"cy":0,"radius":4})
    e.execute("close_sketch", {"sketch_name":b})
    e.execute("loft", {"sketch_names":[a,b],"ruled":False,"operation":"new","name":"Loft1"})
    assert e.doc.shape.Volume() > 0
    assert math.isclose(e.doc.shape.BoundingBox().zlen, 20.0, abs_tol=1e-3)
    e.execute("suppress_feature", {"feature_name":"Loft1","suppressed":True})
    assert e.doc.shape is None
    e.execute("suppress_feature", {"feature_name":"Loft1","suppressed":False})
    assert e.doc.shape is not None

    e = CadEngine()
    sk = e.execute("create_sketch", {"plane":"YZ"})["sketch_name"]
    e.execute("draw_circle", {"cx":0,"cy":0,"radius":2})
    e.execute("close_sketch", {"sketch_name":sk})
    e.execute("sweep", {"profile_sketch_name":sk,"path_points_mm":[[0,0,0],[20,0,0],[20,10,0]],"smooth":False,"is_frenet":False,"transition":"round","operation":"new","name":"Sweep1"})
    assert e.doc.shape.Volume() > 300
    e.execute("delete_feature", {"feature_name":"Sweep1"})
    assert e.doc.shape is None


def test_common_primitive_prompts_have_no_ai_dependency():
    cases = {
        "Create a rectangular box 80x60x8":"cad_create_box",
        "Create a cube with side 20 mm":"cad_create_box",
        "cylinder diameter 20 height 30":"cad_create_cylinder",
        "Create an M10 threaded rod length 50 mm":"cad_create_external_thread",
    }
    for prompt, tool in cases.items():
        plan = direct_plan(prompt)
        assert plan is not None and plan["calls"][0]["tool"] == tool


def test_upstream_58_surface_and_native_extensions_are_available():
    base = make_tools("inventor")
    extended = make_tools("cad", extensions=True)
    assert len(base) == 58
    assert len(extended) == 108
    names = {x["name"] for x in extended}
    required = {
        "cad_loft","cad_sweep","cad_extrude_advanced","cad_shell","cad_create_coil",
        "cad_create_external_thread","cad_create_metric_hex_bolt","cad_create_metric_hex_nut",
        "cad_edit_feature","cad_suppress_feature","cad_delete_feature",
    }
    assert required <= names


def test_ui_contains_edge_pick_and_camera_persistence():
    text = (Path(__file__).resolve().parents[1] / "src/standalonecad/ui/app.py").read_text(encoding="utf-8")
    assert 'values=("Face","Edge")' in text
    assert 'self.actor_edge' in text and 'selected_edge_ref' in text
    assert 'if self.selected_face_ref or self.selected_edge_ref' in text
    assert '_camera_initialized' in text
    assert 'static fallback' in text


def test_selected_face_edge_direct_routes_use_current_selection():
    edge_state = {
        "selection": {
            "type": "edge", "edge_ref": "E_123",
            "center": [0.0, 0.0, 0.0], "direction": "+X"
        }
    }
    p = direct_plan("Apply a 2 mm fillet to this edge", edge_state)
    assert p is not None
    assert p["calls"][0]["tool"] == "inventor_fillet"
    assert p["calls"][0]["arguments"]["edgeIds"] == ["E_123"]
    assert math.isclose(p["calls"][0]["arguments"]["radius"], 2.0)

    face_state = {
        "selection": {
            "type": "face", "face_ref": "F_456",
            "center": [15.0, 15.0, 15.0], "direction": "+Z"
        }
    }
    p = direct_plan("Create an M10 tapped hole on this face with depth 12 mm", face_state)
    assert p is not None
    assert p["calls"][0]["tool"] == "inventor_hole"
    args = p["calls"][0]["arguments"]
    assert args["face"]["normal"] == "+Z"
    assert args["points_mm"] == [[15.0, 15.0, 15.0]]
    assert args["diameter_mm"] == 10.0
    assert args["through"] is False
    assert math.isclose(args["depth_mm"], 12.0)
    assert args["tapped"]["designation"] == "M10"
