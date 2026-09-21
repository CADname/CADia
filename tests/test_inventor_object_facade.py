from __future__ import annotations

from pathlib import Path

import pytest

from standalonecad.core.document import CadDocument
from standalonecad.core.inventor_facade import (
    AssemblyComponentDefinition,
    PartComponentDefinition,
    ComponentOccurrence,
    GeometryProxy,
)


def _save_box(path: Path, size=10.0):
    d=CadDocument(); d.reset('part'); d.title=path.stem
    d.add_feature('box','Base',{'length_mm':size,'width_mm':size,'height_mm':size},'join')
    d.save(str(path))


def test_component_definition_facade_is_additive_and_cached(tmp_path):
    p=CadDocument(); p.reset('part')
    assert isinstance(p.component_definition, PartComponentDefinition)
    assert p.component_definition is p.component_definition
    assert p.component_definition.document is p

    a=CadDocument(); a.reset('assembly')
    assert isinstance(a.component_definition, AssemblyComponentDefinition)
    assert a.component_definition is a.component_definition
    assert a.reference_manager is a.reference_manager


def test_occurrence_collection_returns_objects_and_keeps_native_backend(tmp_path):
    part=tmp_path/'Block.scad.json'; _save_box(part)
    a=CadDocument(); a.reset('assembly')
    cd=a.component_definition
    occ=cd.occurrences.add(str(part), {'position_mm':[5,0,0], 'rotation_deg_xyz':[0,0,0]}, grounded=True)
    assert isinstance(occ, ComponentOccurrence)
    assert occ.name in a.occurrences
    assert occ.native_object is a.occurrences[occ.name]
    assert cd.occurrences.item(1).name == occ.name
    assert cd.occurrences.item(occ.name).native_object is occ.native_object
    assert occ.position_mm == [5.0,0.0,0.0]
    assert occ.grounded is True
    result=occ.creation_result()
    assert result and result['occurrence_name']==occ.name and 'bbox_mm' in result


def test_geometry_proxy_is_assembly_context_and_reference_key_rebinds_after_reload(tmp_path):
    part=tmp_path/'Block.scad.json'; _save_box(part)
    assy=tmp_path/'Asm.scad.json'
    a=CadDocument(); a.reset('assembly')
    occ=a.component_definition.occurrences.add(str(part), {'position_mm':[11,2,3], 'rotation_deg_xyz':[0,0,0]})
    proxy=occ.create_geometry_proxy('Z Axis')
    assert isinstance(proxy, GeometryProxy)
    frame=proxy.world_frame()
    assert frame['origin'] == pytest.approx([11,2,3])
    assert frame['direction'] == pytest.approx([0,0,1])

    occ_key=occ.reference_key(); proxy_key=proxy.reference_key()
    assert a.reference_manager.can_bind_key_to_object(occ_key)
    assert a.reference_manager.can_bind_key_to_object(proxy_key)
    a.save(str(assy))

    b=CadDocument(); b.load(str(assy))
    rebound_occ=b.reference_manager.bind_key_to_object(occ_key)
    rebound_proxy=b.reference_manager.bind_key_to_object(proxy_key)
    assert rebound_occ.name == occ.name
    assert rebound_proxy.world_frame()['origin'] == pytest.approx([11,2,3])


def test_facade_constraints_delegate_to_existing_constraint_solver(tmp_path):
    part=tmp_path/'Block.scad.json'; _save_box(part)
    a=CadDocument(); a.reset('assembly'); cd=a.component_definition
    fixed=cd.occurrences.add(str(part), {'position_mm':[0,0,0]}, grounded=True)
    moving=cd.occurrences.add(str(part), {'position_mm':[0,0,20]})
    rec=cd.constraints.add_flush(fixed,'XY Plane',moving,'XY Plane',offset_mm=10)
    assert rec['type']=='flush'
    assert len(a.constraints)==1
    assert a.constraints[0].health=='up_to_date'


def test_facade_joint_accepts_geometry_proxy_without_product_specific_logic(tmp_path):
    part=tmp_path/'Block.scad.json'; _save_box(part)
    a=CadDocument(); a.reset('assembly'); cd=a.component_definition
    fixed=cd.occurrences.add(str(part), grounded=True)
    moving=cd.occurrences.add(str(part), {'position_mm':[0,0,5]})
    pa=fixed.create_geometry_proxy('Z Axis'); pb=moving.create_geometry_proxy('Z Axis')
    rec=cd.joints.add('slider',pa,None,pb,None,linear_start_mm=0,linear_end_mm=40)
    assert rec['type']=='slider'
    assert rec['health']=='up_to_date'
    assert len(a.joints)==1
