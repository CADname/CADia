from __future__ import annotations


import pytest

from standalonecad.compat.inventor_semantics import UPSTREAM_HOST_COMMANDS
from standalonecad.core.engine import CadEngine


def _rectangle_part(engine: CadEngine, *, size=10.0, height=10.0):
    engine.execute("new_part", {"name": "ParityPart"})
    engine.execute("create_sketch", {"plane": "XY", "name": "Base"})
    engine.execute("draw_rectangle", {"sketch_name": "Base", "x1": 0, "y1": 0, "x2": size, "y2": size})
    engine.execute("close_sketch", {"sketch_name": "Base"})
    return engine.execute("extrude", {"sketch_name": "Base", "distance_mm": height, "operation": "join", "direction": "positive"})


def test_upstream_extrude_semantics_enrich_response_and_build_expected_body():
    e = CadEngine()
    r = _rectangle_part(e, size=10.0, height=10.0)
    assert r["operation"] == "join"
    assert r["distance_mm"] == pytest.approx(10.0)
    assert r["volume_mm3"] == pytest.approx(1000.0, rel=1e-8)
    assert r["feature_name"]
    assert e.doc.shape.isValid()


def test_parameter_expression_survives_semantic_adapter_without_being_flattened():
    e = CadEngine(); e.execute("new_part", {})
    e.execute("create_parameter", {"name": "T", "expression": "8", "unit": "mm"})
    e.execute("create_sketch", {"plane": "XY", "name": "Base"})
    e.execute("draw_rectangle", {"sketch_name": "Base", "x1": 0, "y1": 0, "x2": 20, "y2": 10})
    e.execute("close_sketch", {"sketch_name": "Base"})
    e.execute("extrude", {"sketch_name": "Base", "distance_mm": "T"})
    assert e.doc.features[-1].params["distance_mm"] == "T"
    assert e.doc.shape.Volume() == pytest.approx(1600.0)
    e.execute("set_parameter", {"name": "T", "value": "12"})
    assert e.doc.shape.Volume() == pytest.approx(2400.0)


def test_failed_sketch_overconstraint_is_atomic_and_does_not_pollute_undo():
    e = CadEngine(); e.execute("new_part", {}); e.execute("create_sketch", {"plane": "XY"})
    a = e.execute("draw_line", {"x1": 0, "y1": 0, "x2": 10, "y2": 0})["entity_id"]
    b = e.execute("draw_line", {"x1": 0, "y1": 5, "x2": 10, "y2": 5})["entity_id"]
    e.execute("add_sketch_constraint", {"type": "parallel", "entity_ids": [a, b]})
    before = e._snapshot(); undo_before = len(e._undo); rev_before = e.revision
    with pytest.raises(ValueError, match="constraint conflict|overconstraint"):
        e.execute("add_sketch_constraint", {"type": "perpendicular", "entity_ids": [a, b]})
    assert e._snapshot() == before
    assert len(e._undo) == undo_before
    assert e.revision == rev_before
    assert len(e.doc.sketches["Sketch1"].constraints) == 1


def test_upstream_equal_constraint_keeps_public_handler_line_line_semantics():
    e = CadEngine(); e.execute("new_part", {}); e.execute("create_sketch", {"plane": "XY"})
    c1 = e.execute("draw_circle", {"cx": 0, "cy": 0, "radius": 5})["entity_id"]
    c2 = e.execute("draw_circle", {"cx": 20, "cy": 0, "radius": 3})["entity_id"]
    before = e._snapshot(); undo_before = len(e._undo)
    with pytest.raises(ValueError, match="two sketch lines"):
        e.execute("add_sketch_constraint", {"type": "equal", "entity_ids": [c1, c2]})
    assert e._snapshot() == before
    assert len(e._undo) == undo_before


def test_native_extension_failure_uses_same_atomic_canonical_transaction():
    e = CadEngine(); _rectangle_part(e)
    before = e._snapshot(); undo_before = len(e._undo); rev_before = e.revision
    with pytest.raises(ValueError):
        e.execute("create_box", {"length_mm": 5, "width_mm": 5, "height_mm": 5, "operation": "not-an-operation", "replace": False})
    assert e._snapshot() == before
    assert len(e._undo) == undo_before
    assert e.revision == rev_before


def test_successful_mutation_is_one_undo_unit_after_verification():
    e = CadEngine(); e.execute("new_part", {})
    before = e._snapshot(); undo_before = len(e._undo)
    e.execute("create_box", {"length_mm": 10, "width_mm": 10, "height_mm": 10, "replace": True})
    assert len(e._undo) == undo_before + 1
    assert e.doc.shape is not None
    e.execute("undo", {})
    assert e._snapshot() == before


def test_health_reports_semantic_adapter_and_canonical_core_contract():
    r = CadEngine().execute("health", {})
    assert r["autodesk_inventor_attached"] is False
    assert "handler-semantics" in r["compatibility_contract"]
    assert "canonical-core" in r["compatibility_contract"]


def test_conflicting_assembly_relation_rolls_back_as_one_atomic_operation(tmp_path):
    part_path = tmp_path / "block.scad.json"
    p = CadEngine(); p.execute("new_part", {}); p.execute("create_box", {"length_mm":10,"width_mm":10,"height_mm":10,"replace":True}); p.execute("save_document", {"path":str(part_path)})

    e = CadEngine(); e.execute("new_assembly", {})
    a = e.execute("place_occurrence", {"path":str(part_path),"grounded":True})["occurrence_name"]
    b = e.execute("place_occurrence", {"path":str(part_path),"grounded":False,"position_mm":[20,0,0]})["occurrence_name"]
    e.execute("add_constraint", {"type":"mate","a_occurrence":a,"a_ref":"YZ Plane","b_occurrence":b,"b_ref":"YZ Plane","offset_mm":0})
    before=e._snapshot(); undo_before=len(e._undo)
    with pytest.raises(ValueError, match="Assembly constraint health"):
        e.execute("add_constraint", {"type":"flush","a_occurrence":a,"a_ref":"YZ Plane","b_occurrence":b,"b_ref":"YZ Plane","offset_mm":0})
    assert e._snapshot() == before
    assert len(e._undo) == undo_before
    assert len(e.doc.constraints) == 1
    assert e.doc.constraints[0].health == "up_to_date"


def test_undo_and_failed_transaction_preserve_real_document_path(tmp_path):
    path=tmp_path/'saved.scad.json'
    e=CadEngine(); e.execute('new_part',{}); e.execute('create_box',{'length_mm':5,'width_mm':5,'height_mm':5,'replace':True}); e.execute('save_document',{'path':str(path)})
    saved_path=e.doc.path
    e.execute('create_parameter',{'name':'A','expression':'3','unit':'mm'})
    e.execute('undo',{})
    assert e.doc.path == saved_path
    before=e._snapshot()
    with pytest.raises(ValueError):
        e.execute('create_box',{'length_mm':1,'width_mm':1,'height_mm':1,'operation':'bad','replace':False})
    assert e.doc.path == saved_path
    assert e._snapshot() == before
