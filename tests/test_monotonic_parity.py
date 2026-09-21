from __future__ import annotations
import math
import cadquery as cq

from standalonecad.core.document import CadDocument, SketchModel, SketchEntity
from standalonecad.core.sketch_solver import solve_constraints
from standalonecad.core.expressions import eval_expr


def test_legacy_successful_sketch_is_not_changed():
    sm=SketchModel('S')
    sm.entities.append(SketchEntity('line','e1',{'x1':0.0,'y1':0.0,'x2':10.0,'y2':0.0}))
    sm.constraints.append({'type':'horizontal','entity_ids':['e1']})
    before=dict(sm.entities[0].data)
    solve_constraints(sm, lambda x: float(x))
    assert sm.entities[0].data == before


def test_global_fallback_can_honor_driving_dimension_left_unsatisfied_by_legacy_constraint_pass():
    sm=SketchModel('S')
    sm.entities.append(SketchEntity('line','e1',{'x1':0.0,'y1':0.0,'x2':20.0,'y2':0.0}))
    sm.constraints.append({'type':'horizontal','entity_ids':['e1']})
    sm.dimensions.append({'name':'d1','entity_id':'e1','value_mm':10.0,'driving':True})
    solve_constraints(sm, lambda x: float(x))
    e=sm.entities[0].data
    assert abs(math.dist((e['x1'],e['y1']),(e['x2'],e['y2']))-10.0) < 1e-4
    assert abs(e['y2']-e['y1']) < 1e-6


def test_expression_evaluator_is_legacy_first_but_expands_explicit_units():
    assert eval_expr('10 mm',{}) == 10.0
    assert eval_expr('2+3',{}) == 5.0
    assert abs(eval_expr('1 in + 25.4 mm',{})-50.8) < 1e-9
    assert abs(eval_expr('1 ft',{})-304.8) < 1e-9
    assert abs(eval_expr('3.141592653589793 rad',{})-180.0) < 1e-8


def test_project_geometry_persists_source_edge_reference_without_changing_projected_shape():
    d=CadDocument(); d.reset('part'); d.shape=cq.Workplane('XY').box(20,10,5).val()
    sm=SketchModel('S','XY'); d.sketches['S']=sm
    edge_id=d.topology()['edges'][0]['id']
    tags=d.project_edges_to_sketch(sm,[edge_id])
    assert len(tags)==1
    ent=sm.entities[0]
    assert ent.data.get('projected') is True
    assert isinstance(ent.data.get('source_edge_ref'),dict)
    assert ent.data['source_edge_ref'].get('id')
