from pathlib import Path

import cadquery as cq
import pytest

from standalonecad.core.engine import CadEngine
from standalonecad.mcp.tools import make_tools


def _box(e, l=20, w=12, h=8, name='Base'):
    return e.execute('create_box',{
        'length_mm':l,'width_mm':w,'height_mm':h,'origin_mm':[0,0,0],
        'centered':False,'operation':'new','replace':False,'name':name,
    })


def test_ipt_core_stays_58_and_edit_extensions_are_additive():
    assert len(make_tools('inventor')) == 58
    native = make_tools('cad', extensions=True)
    assert len(native) == 108
    names = {x['name'] for x in native}
    assert {'cad_edit_parameter','cad_edit_sketch','cad_edit_work_feature','cad_edit_solid'} <= names
    asm = make_tools('inventor', assembly_extensions=True)
    assert len(asm) == 68


def test_parameter_rename_and_delete_dependency_guard():
    e=CadEngine()
    e.execute('create_parameter',{'name':'P','expression':'10 mm','unit':'mm'})
    e.execute('create_parameter',{'name':'Q','expression':'P * 2','unit':'mm'})
    out=e.execute('edit_parameter',{'action':'rename','name':'P','new_name':'LengthParam'})
    assert out['new_name']=='LengthParam' and 'LengthParam' in e.doc.parameters
    assert e.doc.parameters['Q'].expression=='LengthParam * 2'
    with pytest.raises(ValueError,match='still referenced'):
        e.execute('edit_parameter',{'action':'delete','name':'LengthParam'})


def test_sketch_transform_copy_rename_and_consumed_delete_guard():
    e=CadEngine(); e.execute('create_sketch',{'plane':'XY'})
    line=e.execute('draw_line',{'x1':0,'y1':0,'x2':10,'y2':0})['entity_id']
    r=e.execute('edit_sketch',{'action':'transform','sketch_name':'Sketch1','entity_ids':[line],
                               'translate_mm':[5,2],'rotate_deg':90,'pivot_mm':[0,0],'copy':True})
    assert r['copied'] is True and len(e.doc.sketches['Sketch1'].entities)==2
    copy_id=r['entity_ids'][0]
    ent=next(x for x in e.doc.sketches['Sketch1'].entities if x.tag==copy_id)
    assert float(ent.data['x1'])==pytest.approx(5) and float(ent.data['y1'])==pytest.approx(2)
    assert float(ent.data['x2'])==pytest.approx(5) and float(ent.data['y2'])==pytest.approx(12)
    e.execute('edit_sketch',{'action':'rename','sketch_name':'Sketch1','new_name':'Construction'})
    assert 'Construction' in e.doc.sketches

    e2=CadEngine(); e2.execute('create_sketch',{'plane':'XY'})
    e2.execute('draw_circle',{'cx':0,'cy':0,'radius':5})
    e2.execute('close_sketch',{'sketch_name':'Sketch1'})
    e2.execute('extrude',{'sketch_name':'Sketch1','distance_mm':4,'operation':'join','direction':'positive'})
    e2.execute('edit_sketch',{'action':'rename','sketch_name':'Sketch1','new_name':'Profile'})
    assert e2.doc.features[0].params['sketch_name']=='Profile'
    with pytest.raises(ValueError,match='consumed'):
        e2.execute('edit_sketch',{'action':'delete','sketch_name':'Profile','delete_dependents':False})


def test_work_plane_redefine_rename_delete():
    e=CadEngine(); _box(e)
    r=e.execute('create_work_plane',{'type':'offset','refs':['XY Plane'],'offset_mm':5})
    name=r['work_plane']['name'] if isinstance(r.get('work_plane'),dict) else r.get('name','Work Plane 1')
    if name not in e.doc.work_planes:name='Work Plane 1'
    e.execute('edit_work_feature',{'action':'redefine','kind':'plane','name':name,'type':'offset','refs':['XY Plane'],'offset_mm':10})
    assert e.doc.work_planes[name]['origin'][2]==pytest.approx(10)
    out=e.execute('edit_work_feature',{'action':'rename','kind':'plane','name':name,'new_name':'MidPlane'})
    assert out['name']=='MidPlane' and 'MidPlane' in e.doc.work_planes
    d=e.execute('edit_work_feature',{'action':'delete','kind':'plane','name':'MidPlane'})
    assert d['deleted_work_feature']=='MidPlane'


def test_thread_feature_on_existing_cylindrical_face():
    e=CadEngine(); e.execute('create_cylinder',{
        'diameter_mm':10,'height_mm':20,'origin_mm':[0,0,0],'axis':[0,0,1],
        'operation':'new','replace':False,'name':'Shaft'})
    face=next(x for x in e.doc.topology()['faces'] if x['geom']=='CYLINDER')
    before=e.doc.shape.Volume()
    out=e.execute('edit_solid',{'action':'thread_face','face_ref':face['id'],'designation':'M10x1.5',
                                'full_depth':True,'offset_mm':0,'right_handed':True,'reverse_direction':False})
    assert out['feature_name'].startswith('Thread')
    assert e.doc.features[-1].kind=='thread_face'
    assert e.doc.shape.Volume() < before


def test_split_and_combine_body_history_edits():
    e=CadEngine(); _box(e,l=20,w=20,h=20)
    out=e.execute('edit_solid',{'action':'split_body','plane':'XY Plane','keep':'both','name':'Split'})
    # XY at z=0 touches the primitive base; use a real offset work plane for an internal split.
    # Roll back the touching split and perform the meaningful split on a second engine.
    e2=CadEngine(); _box(e2,l=20,w=20,h=20)
    e2.execute('create_work_plane',{'type':'offset','refs':['XY Plane'],'offset_mm':10})
    out=e2.execute('edit_solid',{'action':'split_body','plane':'Work Plane 1','keep':'both','name':'Split'})
    assert out['solid_count']==2
    out2=e2.execute('edit_solid',{'action':'combine_bodies','base_index':1,'tool_indices':[2],'operation':'join','keep_tools':False,'name':'Combine'})
    assert out2['solid_count']==1
    assert e2.doc.features[-1].kind=='combine_bodies'


def _save_part(path: Path, size: float):
    e=CadEngine(); e.execute('new_part',{'name':path.stem}); _box(e,size,size,size)
    e.execute('save_document',{'path':str(path)})


def test_occurrence_replace_and_state_share_existing_transform_tool(tmp_path: Path):
    a=tmp_path/'A.scad.json'; b=tmp_path/'B.scad.json'; _save_part(a,10); _save_part(b,16)
    e=CadEngine(); e.execute('new_assembly',{'name':'Asm'})
    occ=e.execute('place_occurrence',{'path':str(a),'grounded':False,'position_mm':[0,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    out=e.execute('transform_occurrence',{'occurrence_name':occ,'replacement_path':str(b),'replace_all':False,
                                          'grounded':True,'suppressed':False,'translate_mm':[2,3,4]})
    assert Path(out['path']).resolve()==b.resolve()
    assert out['grounded'] is True and out['position_mm']==pytest.approx([2,3,4])


def test_edit_constraint_can_rebind_and_convert_supported_type(tmp_path: Path):
    a=tmp_path/'A.scad.json'; b=tmp_path/'B.scad.json'; _save_part(a,10); _save_part(b,10)
    e=CadEngine(); e.execute('new_assembly',{'name':'Asm'})
    oa=e.execute('place_occurrence',{'path':str(a),'grounded':True,'position_mm':[0,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    ob=e.execute('place_occurrence',{'path':str(b),'grounded':False,'position_mm':[0,0,12],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    c=e.execute('add_constraint',{'type':'mate','a_occurrence':oa,'a_ref':'Z Axis','b_occurrence':ob,'b_ref':'Z Axis','offset_mm':0})['name']
    out=e.execute('edit_constraint',{'name':c,'type':'flush','a_occurrence':oa,'a_ref':'Z Axis','b_occurrence':ob,'b_ref':'Z Axis','offset_mm':0})
    assert out['constraint']['type']=='flush'


def test_edit_joint_can_change_definition_type(tmp_path: Path):
    a=tmp_path/'A.scad.json'; b=tmp_path/'B.scad.json'; _save_part(a,10); _save_part(b,10)
    e=CadEngine(); e.execute('new_assembly',{'name':'Asm'})
    oa=e.execute('place_occurrence',{'path':str(a),'grounded':True,'position_mm':[0,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    ob=e.execute('place_occurrence',{'path':str(b),'grounded':False,'position_mm':[0,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    j=e.execute('add_joint',{'type':'rigid','a_occurrence':oa,'a_ref':'Z Axis','b_occurrence':ob,'b_ref':'Z Axis'})['name']
    out=e.execute('edit_joint',{'name':j,'type':'rotational','a_occurrence':oa,'a_ref':'Z Axis','b_occurrence':ob,'b_ref':'Z Axis',
                                'angular_position_deg':0,'angular_start_deg':-90,'angular_end_deg':90})
    assert out['type']=='rotational'
    assert out['health']=='up_to_date'
