from __future__ import annotations

import pytest

from standalonecad.core.engine import CadEngine
from standalonecad.planning import PlanExecutor
from standalonecad.recovery import classify_failure


def _executor():
    e=CadEngine()
    return PlanExecutor(e,'binding-test',target_info={
        'id':'binding-test','host':'127.0.0.1','port':1,'token':'x','host_app':'StandaloneCAD'
    })


def _constraint(a: str, b: str):
    return {
        'tool':'inventor_add_constraint',
        'arguments':{
            'type':'flush',
            'a':{'occurrence':a,'ref':'XY Plane'},
            'b':{'occurrence':b,'ref':'XY Plane'},
            'offset_mm':0.0,
            'angle_deg':None,
            'insert_opposed':False,
        },
    }


def test_named_occurrence_bindings_validate_without_touching_mcp_schema():
    ex=_executor()
    plan={'calls':[
        {'tool':'inventor_place_occurrence','arguments':{'path':'A.scad.json'},'bind':'component_a'},
        {'tool':'inventor_place_occurrence','arguments':{'path':'B.scad.json'},'bind':'component_b'},
        _constraint('$bind.component_a.occurrence_name','$bind.component_b.occurrence_name'),
    ]}
    calls=ex.validate_plan(plan)
    assert calls[0]['bind']=='component_a'
    assert calls[1]['bind']=='component_b'
    # bind is planner metadata only; inventor_place_occurrence input schema remains strict upstream shape.
    assert 'bind' not in ex.schemas['inventor_place_occurrence']['properties']


def test_named_binding_resolves_actual_runtime_occurrence_name():
    ex=_executor()
    value={'a_occurrence':'$bind.component_a.occurrence_name'}
    resolved=ex._resolve_runtime_refs(value,[],{'component_a':{'occurrence_name':'ActualPart:7'}})
    assert resolved['a_occurrence']=='ActualPart:7'


def test_legacy_result_occurrence_reference_remains_supported():
    ex=_executor()
    plan={'calls':[
        {'tool':'inventor_place_occurrence','arguments':{'path':'A.scad.json'}},
        _constraint('$result[1].occurrence_name','$result[1].occurrence_name'),
    ]}
    calls=ex.validate_plan(plan)
    resolved=ex._resolve_runtime_refs(
        calls[1]['arguments'],
        [{'tool':'inventor_place_occurrence','result':{'occurrence_name':'A:1'}}],
        {},
    )
    assert resolved['a']['occurrence']=='A:1'


def test_bad_positional_occurrence_reference_fails_preflight_before_cad_mutation():
    ex=_executor()
    plan={'calls':[
        {'tool':'inventor_place_occurrence','arguments':{'path':'A.scad.json'}},
        {'tool':'inventor_get_document_info','arguments':{}},
        _constraint('$result[2].occurrence_name','$result[1].occurrence_name'),
    ]}
    with pytest.raises(ValueError,match=r'call 2 .*does not produce occurrence_name'):
        ex.validate_plan(plan)


def test_future_or_unknown_named_binding_fails_preflight():
    ex=_executor()
    plan={'calls':[
        {'tool':'inventor_place_occurrence','arguments':{'path':'A.scad.json'},'bind':'a'},
        _constraint('$bind.a.occurrence_name','$bind.missing.occurrence_name'),
    ]}
    with pytest.raises(ValueError,match='unknown or not-yet-available runtime binding'):
        ex.validate_plan(plan)


def test_runtime_binding_errors_get_dedicated_recovery_class():
    assert classify_failure('Runtime result does not contain occurrence_name: $result[44].occurrence_name') == 'runtime_binding'
    assert classify_failure('calls[8]: $result[4].occurrence_name points to call 4 (inventor_fillet), which does not produce occurrence_name') == 'runtime_binding'
