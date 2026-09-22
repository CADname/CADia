from __future__ import annotations

from pathlib import Path

import cadquery as cq
from cadquery import exporters
import pytest

from standalonecad.core.document import CadDocument
from standalonecad.core.engine import CadEngine
from standalonecad.core.inventor_facade import ComponentOccurrences, AssemblyConstraints, AssemblyJoints


def _step(path: Path, size: float):
    exporters.export(cq.Workplane('XY').box(size, size, size).val(), str(path), exportType='STEP')


def _summary(doc: CadDocument):
    return {
        'occurrences': {
            name: {
                'path': str(Path(o.path).resolve()),
                'grounded': o.grounded,
                'position_mm': [round(float(x), 8) for x in o.position_mm],
                'rotation_deg_xyz': [round(float(x), 8) for x in o.rotation_deg_xyz],
                'suppressed': o.suppressed,
            }
            for name, o in doc.occurrences.items()
        },
        'constraints': [vars(c).copy() for c in doc.constraints],
        'joints': [vars(j).copy() for j in doc.joints],
        'bom': doc.assembly_bom(),
        'interference': doc.check_interference(),
    }


def test_facade_and_native_backend_are_state_equivalent_for_static_assembly(tmp_path: Path):
    a = tmp_path/'A.step'; b = tmp_path/'B.step'
    _step(a, 20); _step(b, 10)

    native = CadDocument(); native.reset('assembly')
    na = native.place_occurrence(str(a), True, [0,0,0], [0,0,0])['occurrence_name']
    nb = native.place_occurrence(str(b), False, [0,0,30], [0,0,0])['occurrence_name']
    native.add_assembly_constraint('flush', na, 'XY Plane', nb, 'XY Plane', 10, None, True)

    wrapped = CadDocument(); wrapped.reset('assembly'); comp = wrapped.component_definition
    wa = comp.occurrences.add(str(a), {'position_mm':[0,0,0], 'rotation_deg_xyz':[0,0,0]}, True)
    wb = comp.occurrences.add(str(b), {'position_mm':[0,0,30], 'rotation_deg_xyz':[0,0,0]}, False)
    comp.constraints.add_flush(wa, 'XY Plane', wb, 'XY Plane', 10)

    assert _summary(wrapped) == _summary(native)


def test_facade_and_native_backend_are_state_equivalent_for_joint_assembly(tmp_path: Path):
    a = tmp_path/'Fixed.step'; b = tmp_path/'Moving.step'
    _step(a, 20); _step(b, 8)

    native = CadDocument(); native.reset('assembly')
    na = native.place_occurrence(str(a), True, [0,0,0], [0,0,0])['occurrence_name']
    nb = native.place_occurrence(str(b), False, [3,2,5], [7,9,11])['occurrence_name']
    nr = native.add_assembly_joint('slider', na, 'Z Axis', nb, 'Z Axis', 5, 0, 40, 15, None, None)

    wrapped = CadDocument(); wrapped.reset('assembly'); comp = wrapped.component_definition
    wa = comp.occurrences.add(str(a), {'position_mm':[0,0,0], 'rotation_deg_xyz':[0,0,0]}, True)
    wb = comp.occurrences.add(str(b), {'position_mm':[3,2,5], 'rotation_deg_xyz':[7,9,11]}, False)
    wr = comp.joints.add('slider', wa, 'Z Axis', wb, 'Z Axis',
                         linear_position_mm=5, linear_start_mm=0, linear_end_mm=40,
                         angular_position_deg=15)

    assert wr['health'] == nr['health'] == 'up_to_date'
    assert _summary(wrapped) == _summary(native)


def test_engine_assembly_dispatch_really_passes_through_facade(monkeypatch, tmp_path: Path):
    a = tmp_path/'A.step'; b = tmp_path/'B.step'
    _step(a, 20); _step(b, 8)
    calls = {'occ':0, 'constraint':0, 'joint':0}

    orig_occ = ComponentOccurrences.add
    orig_constraint = AssemblyConstraints.add
    orig_joint = AssemblyJoints.add

    def occ_add(self, *args, **kwargs):
        calls['occ'] += 1
        return orig_occ(self, *args, **kwargs)

    def constraint_add(self, *args, **kwargs):
        calls['constraint'] += 1
        return orig_constraint(self, *args, **kwargs)

    def joint_add(self, *args, **kwargs):
        calls['joint'] += 1
        return orig_joint(self, *args, **kwargs)

    monkeypatch.setattr(ComponentOccurrences, 'add', occ_add)
    monkeypatch.setattr(AssemblyConstraints, 'add', constraint_add)
    monkeypatch.setattr(AssemblyJoints, 'add', joint_add)

    e = CadEngine(); e.execute('new_assembly', {})
    oa = e.execute('place_occurrence', {'path':str(a), 'grounded':True})['occurrence_name']
    ob = e.execute('place_occurrence', {'path':str(b), 'grounded':False, 'position_mm':[0,0,5]})['occurrence_name']
    e.execute('add_constraint', {'type':'flush','a_occurrence':oa,'a_ref':'YZ Plane','b_occurrence':ob,'b_ref':'YZ Plane','offset_mm':0})
    e.execute('add_joint', {'type':'slider','a_occurrence':oa,'a_ref':'Z Axis','b_occurrence':ob,'b_ref':'Z Axis','linear_position_mm':5,'linear_start_mm':0,'linear_end_mm':40})

    assert calls == {'occ':2, 'constraint':1, 'joint':1}



def test_facade_routing_preserves_non_assembly_error_contracts():
    e = CadEngine()  # starts on a Part document
    # Strict commands are rejected by the semantic guard before backend dispatch.
    for command, params in [
        ('place_occurrence', {'path':'missing.step'}),
        ('add_constraint', {'type':'mate','a_occurrence':None,'a_ref':'XY Plane','b_occurrence':None,'b_ref':'XY Plane'}),
        ('check_interference', {}),
        ('get_assembly_bom', {}),
        ('list_constraints', {}),
    ]:
        with pytest.raises(ValueError, match='WRONG_DOCUMENT_TYPE|requires an active assembly'):
            e.execute(command, params)
    # Native joint extension uses the document-level validation error.
    with pytest.raises(ValueError, match='list_joints requires an active assembly'):
        e.execute('list_joints', {})
