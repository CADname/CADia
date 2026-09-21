from standalonecad.core.engine import CadEngine
from standalonecad.core.assembly import default_interfaces, find_interface, available_interface_names
from standalonecad.core.inventor_origin import ORIGIN_PLANE_ORDER, canonical_origin_name
from standalonecad.compat.inventor_semantics import InventorSemanticAdapter


def test_inventor_origin_plane_collection_order_and_public_names():
    assert ORIGIN_PLANE_ORDER == ("YZ Plane", "XZ Plane", "XY Plane")
    names = available_interface_names(default_interfaces())
    for name in ("XY Plane", "XZ Plane", "YZ Plane", "X Axis", "Y Axis", "Z Axis", "Center Point"):
        assert name in names
    # Compatibility aliases are recovery-only, never surfaced as public interfaces.
    assert "OriginPlaneXY" not in names
    assert "OriginAxisZ" not in names


def test_create_sketch_ipt_mcp_short_plane_is_persisted_as_inventor_plane_name():
    e = CadEngine()
    r = e.execute("create_sketch", {"plane": "XY"})
    assert r["plane"] == "XY Plane"
    assert e.doc.sketches[r["sketch_name"]].plane == "XY Plane"


def test_hidden_compatibility_alias_recovers_to_canonical_inventor_origin():
    e = CadEngine()
    r = e.execute("create_sketch", {"plane": "OriginPlaneXY"})
    assert r["plane"] == "XY Plane"
    assert canonical_origin_name("OriginPlaneXY") == "XY Plane"
    assert find_interface(default_interfaces(), "OriginPlaneXY")["name"] == "XY Plane"
    assert find_interface(default_interfaces(), "OriginAxisZ")["name"] == "Z Axis"


def test_unknown_sketch_plane_fails_early_with_ipt_mcp_contract_guidance():
    e = CadEngine()
    try:
        e.execute("create_sketch", {"plane": "TotallyFakePlane"})
    except ValueError as exc:
        text = str(exc)
        assert "Unknown sketch plane reference" in text
        assert "XY/XZ/YZ" in text
    else:
        raise AssertionError("unknown sketch plane must fail early")
