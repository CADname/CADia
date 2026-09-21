from pathlib import Path
import tempfile

import pytest

from standalonecad.core.engine import CadEngine
from standalonecad.core.topology import face_records
from standalonecad.mcp.tools import make_tools
from standalonecad.planning import direct_plan


def _box(engine, name='Box1'):
    return engine.execute('create_box', {
        'length_mm':20,'width_mm':10,'height_mm':8,'origin_mm':[0,0,0],
        'centered':False,'operation':'new','replace':False,'name':name,
    })


def _face(engine, *, direction=None, geom=None):
    rows=face_records(engine.doc.shape)
    for row in rows:
        if direction is not None and row.get('direction') != direction:continue
        if geom is not None and row.get('geom') != geom:continue
        return row
    raise AssertionError((direction,geom,rows))


def test_strict_ipt_surface_is_frozen_and_edit_extensions_are_additive():
    assert len(make_tools('inventor')) == 58
    native={x['name'] for x in make_tools('cad',extensions=True)}
    assert len(make_tools('cad',extensions=True)) == 108
    assert {'cad_move_face','cad_delete_face','cad_set_face_diameter'} <= native
    asm={x['name'] for x in make_tools('inventor',assembly_extensions=True)}
    assert len(make_tools('inventor',assembly_extensions=True)) == 68
    assert {'inventor_transform_occurrence','inventor_edit_constraint'} <= asm


def test_selected_extrude_face_move_prefers_history_and_keeps_model_parameter():
    e=CadEngine()
    e.execute('create_sketch',{'plane':'XY'})
    e.execute('draw_rectangle',{'x1':0,'y1':0,'x2':20,'y2':10})
    e.execute('close_sketch',{'sketch_name':None})
    e.execute('extrude',{'sketch_name':'Sketch1','distance_mm':5,'operation':'join','direction':'positive'})
    top=_face(e,direction='+Z')
    result=e.execute('move_face',{'face_ref':top['id'],'distance_mm':3,'prefer_history':True,'name':None})
    assert result['mode']=='history' and result['feature_name']=='Extrude1'
    assert e.doc.features[0].params['distance_mm']=='d0'
    assert e.doc.parameters['d0'].expression=='8 mm'
    assert e.doc.shape.BoundingBox().zmax == pytest.approx(8.0)


def test_edit_feature_preserves_existing_driving_parameter_link():
    e=CadEngine()
    e.execute('create_sketch',{'plane':'XY'}); e.execute('draw_rectangle',{'x1':0,'y1':0,'x2':10,'y2':10}); e.execute('close_sketch',{'sketch_name':None})
    e.execute('extrude',{'sketch_name':'Sketch1','distance_mm':5,'operation':'join','direction':'positive'})
    assert e.doc.features[0].params['distance_mm']=='d0'
    e.execute('edit_feature',{'feature_name':'Extrude1','updates':{'distance_mm':12}})
    assert e.doc.features[0].params['distance_mm']=='d0'
    assert e.doc.parameters['d0'].expression=='12 mm'
    assert e.doc.shape.BoundingBox().zmax == pytest.approx(12.0)


def test_direct_move_face_fallback_is_history_recorded_and_undoable():
    e=CadEngine(); _box(e); top=_face(e,direction='+Z')
    result=e.execute('move_face',{'face_ref':top['id'],'distance_mm':-2,'prefer_history':False,'name':None})
    assert result['mode']=='direct' and e.doc.features[-1].kind=='move_face'
    assert e.doc.shape.BoundingBox().zmax == pytest.approx(6.0)
    e.undo()
    assert e.doc.shape.BoundingBox().zmax == pytest.approx(8.0)
    assert len(e.doc.features)==1


def test_invalid_direct_move_rolls_back_atomically():
    e=CadEngine(); e.execute('create_cylinder',{'diameter_mm':10,'height_mm':20,'origin_mm':[0,0,0],'axis':[0,0,1],'operation':'new','replace':False,'name':'Cyl'})
    cyl=_face(e,geom='CYLINDER'); before=e.doc.shape.Volume(); feature_count=len(e.doc.features)
    with pytest.raises(ValueError):
        e.execute('move_face',{'face_ref':cyl['id'],'distance_mm':2,'prefer_history':False,'name':None})
    assert e.doc.shape.Volume()==pytest.approx(before)
    assert len(e.doc.features)==feature_count


def test_cylindrical_face_diameter_edits_originating_feature_history():
    e=CadEngine(); e.execute('create_cylinder',{'diameter_mm':10,'height_mm':20,'origin_mm':[0,0,0],'axis':[0,0,1],'operation':'new','replace':False,'name':'Cyl'})
    cyl=_face(e,geom='CYLINDER')
    result=e.execute('set_face_diameter',{'face_ref':cyl['id'],'diameter_mm':14})
    assert result['mode']=='history' and result['feature_name']=='Cyl'
    assert e.doc.features[0].params['diameter_mm']==14.0
    assert e.doc.shape.BoundingBox().xlen == pytest.approx(14.0)


def test_hole_cylindrical_face_diameter_keeps_d_parameter():
    e=CadEngine(); e.execute('create_box',{'length_mm':20,'width_mm':20,'height_mm':10,'origin_mm':[0,0,0],'centered':False,'operation':'new','replace':False,'name':'B'})
    e.execute('hole',{'face':{'kind':'planar','normal':'+Z','extreme':'max'},'points_mm':[[10,10,10]],'diameter_mm':6,'kind':'drilled','through':True,'depth_mm':None,'cbore_diameter_mm':None,'cbore_depth_mm':None,'csink_diameter_mm':None,'csink_angle_deg':82,'tapped':None})
    cyl=_face(e,geom='CYLINDER'); assert e.doc.selection_provenance(cyl['id'],'face')['feature_kind']=='hole'
    e.execute('set_face_diameter',{'face_ref':cyl['id'],'diameter_mm':8})
    hole=e.doc.features[-1]
    assert hole.kind=='hole' and hole.params['diameter_mm']=='d0'
    assert e.doc.parameters['d0'].expression=='8 mm'


def test_delete_face_heals_a_simple_through_hole():
    e=CadEngine(); e.execute('create_box',{'length_mm':20,'width_mm':20,'height_mm':10,'origin_mm':[0,0,0],'centered':False,'operation':'new','replace':False,'name':'B'})
    e.execute('hole',{'face':{'kind':'planar','normal':'+Z','extreme':'max'},'points_mm':[[10,10,10]],'diameter_mm':6,'kind':'drilled','through':True,'depth_mm':None,'cbore_diameter_mm':None,'cbore_depth_mm':None,'csink_diameter_mm':None,'csink_angle_deg':82,'tapped':None})
    assert e.doc.shape.Volume() < 4000
    cyl=_face(e,geom='CYLINDER')
    result=e.execute('delete_face',{'face_ref':cyl['id'],'name':None})
    assert result['heal'] is True and e.doc.features[-1].kind=='delete_face'
    assert e.doc.shape.Volume()==pytest.approx(4000.0,abs=1e-6)


def test_direct_planner_routes_explicit_selected_geometry_without_llm():
    e=CadEngine(); _box(e); top=_face(e,direction='+Z')
    state={'selection':{'type':'face','face_ref':top['id'],'center':top['center'],'direction':top['direction'],'normal':top.get('normal')},'faces':face_records(e.doc.shape)}
    assert direct_plan('Push this face outward by 2 mm',state)['calls'][0]['tool']=='cad_move_face'
    assert direct_plan('Delete this face',state)['calls'][0]['tool']=='cad_delete_face'
    shell=direct_plan('Shell this face thickness=1mm',state)
    assert shell['calls'][0]['tool']=='cad_shell' and shell['calls'][0]['arguments']['remove_face_ids']==[top['id']]

    c=CadEngine(); c.execute('create_cylinder',{'diameter_mm':10,'height_mm':20,'origin_mm':[0,0,0],'axis':[0,0,1],'operation':'new','replace':False,'name':'Cyl'})
    side=_face(c,geom='CYLINDER')
    cstate={'selection':{'type':'face','face_ref':side['id'],'center':side['center'],'direction':side.get('direction'),'normal':side.get('normal')},'faces':face_records(c.doc.shape)}
    assert direct_plan('Change this cylindrical face diameter=18mm',cstate)['calls'][0]['tool']=='cad_set_face_diameter'


def test_assembly_occurrence_pose_and_constraint_are_editable(tmp_path):
    e=CadEngine(); _box(e,'PartBox'); part=tmp_path/'edit-part.scad.json'; e.execute('save_document',{'path':str(part)})
    e.execute('new_assembly',{})
    a=e.execute('place_occurrence',{'path':str(part),'grounded':True,'position_mm':[0,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    b=e.execute('place_occurrence',{'path':str(part),'grounded':False,'position_mm':[20,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    moved=e.execute('transform_occurrence',{'occurrence_name':b,'position_mm':None,'rotation_deg_xyz':None,'translate_mm':[5,2,0],'rotate_delta_deg_xyz':[0,0,10]})
    assert moved['position_mm']==[25.0,2.0,0.0] and moved['rotation_deg_xyz']==[0.0,0.0,10.0]
    con=e.execute('add_constraint',{'type':'mate','a_occurrence':a,'a_ref':'YZ Plane','b_occurrence':b,'b_ref':'YZ Plane','offset_mm':20,'angle_deg':None,'insert_opposed':True})
    edited=e.execute('edit_constraint',{'name':con['name'],'offset_mm':25,'angle_deg':None,'suppressed':None,'insert_opposed':None})
    assert edited['constraint']['offset_mm']==25.0 and edited['constraint']['health']=='up_to_date'
