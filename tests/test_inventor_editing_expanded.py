from pathlib import Path

import pytest

from standalonecad.core.engine import CadEngine
from standalonecad.mcp.tools import make_tools


def _box(e, l=20, w=12, h=8, name='Base'):
    return e.execute('create_box', {
        'length_mm': l, 'width_mm': w, 'height_mm': h, 'origin_mm': [0,0,0],
        'centered': False, 'operation': 'new', 'replace': False, 'name': name,
    })


def test_core_58_and_compact_extension_count_remain_frozen():
    assert len(make_tools('inventor')) == 58
    ext = make_tools('cad', extensions=True)
    assert len(ext) == 108
    by_name = {x['name']: x for x in ext}
    work_actions = by_name['cad_edit_work_feature']['inputSchema']['properties']['action']['enum']
    solid_actions = by_name['cad_edit_solid']['inputSchema']['properties']['action']['enum']
    sketch_actions = by_name['cad_edit_sketch']['inputSchema']['properties']['action']['enum']
    assert {'create','redefine','rename','delete'} <= set(work_actions)
    assert {'draft_face','replace_face','offset_body','delete_faces'} <= set(solid_actions)
    assert {'create_3d','add_3d_line','add_3d_spline','transform_3d','rename_3d','delete_3d'} <= set(sketch_actions)


def test_work_point_can_drive_work_axis_and_roundtrip(tmp_path: Path):
    e=CadEngine(); _box(e)
    e.execute('edit_work_feature', {'action':'create','kind':'point','type':'fixed','point_mm':[1,2,3],'name':'P1'})
    e.execute('edit_work_feature', {'action':'create','kind':'axis','type':'two_points','refs':['P1',[1,2,10]],'name':'A1'})
    assert e.doc.work_points['P1']['origin'] == pytest.approx([1,2,3])
    assert e.doc.work_axes['A1']['direction'] == pytest.approx([0,0,1])
    path=tmp_path/'wp.scad.json'; e.execute('save_document', {'path':str(path)})
    e2=CadEngine(); e2.execute('open_document', {'path':str(path)})
    assert e2.doc.work_points['P1']['origin'] == pytest.approx([1,2,3])


def test_3d_sketch_can_drive_sweep_path():
    e=CadEngine(); e.execute('create_sketch', {'plane':'XY'})
    e.execute('draw_circle', {'cx':0,'cy':0,'radius':2})
    e.execute('close_sketch', {'sketch_name':'Sketch1'})
    e.execute('edit_sketch', {'action':'create_3d','sketch_name':'Path3D'})
    e.execute('edit_sketch', {'action':'add_3d_line','sketch_name':'Path3D','start_mm':[0,0,0],'end_mm':[0,0,20]})
    out=e.execute('sweep', {'profile_sketch_name':'Sketch1','path_sketch3d_name':'Path3D','path_points_mm':None,
                            'smooth':False,'is_frenet':False,'transition':'transformed','operation':'new','name':'Sweep3D'})
    assert out['feature_name']=='Sweep3D'
    assert e.doc.shape.isValid()
    assert e.doc.shape.BoundingBox().zlen == pytest.approx(20, abs=1e-5)


def test_face_draft_replace_face_offset_and_multi_delete_are_history_edits():
    e=CadEngine(); _box(e)
    side=next(f for f in e.doc.topology()['faces'] if f['geom']=='PLANE' and f.get('normal') and f['normal'][0] > 0.9)
    out=e.execute('edit_solid', {'action':'draft_face','face_ref':side['id'],'angle_deg':5,
                                 'pull_direction':[0,0,1],'neutral_plane':'XY Plane','name':'Draft1'})
    assert out['feature_name']=='Draft1' and e.doc.shape.isValid()

    e2=CadEngine(); _box(e2); e2.execute('create_work_plane', {'type':'offset','refs':['XY Plane'],'offset_mm':12})
    top=next(f for f in e2.doc.topology()['faces'] if f['geom']=='PLANE' and f.get('normal') and f['normal'][2] > 0.9)
    out=e2.execute('edit_solid', {'action':'replace_face','face_ref':top['id'],'target_plane':'Work Plane 1','name':'ReplaceTop'})
    assert out['feature_name']=='ReplaceTop'
    assert e2.doc.shape.BoundingBox().zlen == pytest.approx(12, abs=1e-5)

    e3=CadEngine(); _box(e3); before=e3.doc.shape.BoundingBox().xlen
    e3.execute('edit_solid', {'action':'offset_body','distance_mm':1,'join':'arc','remove_internal_edges':True,'name':'Offset1'})
    assert e3.doc.shape.isValid() and e3.doc.shape.BoundingBox().xlen > before
    face=e3.doc.topology()['faces'][0]
    e3.execute('edit_solid', {'action':'delete_faces','face_refs':[face['id']],'name':'DeleteFaces1'})
    assert e3.doc.shape.isValid() and e3.doc.features[-1].kind=='delete_faces'


def test_feature_history_reposition_uses_existing_definition_tool():
    e=CadEngine(); _box(e,10,10,10,'Base')
    e.execute('create_box', {'length_mm':5,'width_mm':5,'height_mm':5,'origin_mm':[10,0,0],
                             'centered':False,'operation':'join','replace':False,'name':'Add'})
    out=e.execute('edit_feature_definition', {'feature_name':'Add','updates':{},'before':'Base'})
    assert out['feature_index']==0
    assert [f.name for f in e.doc.features][:2] == ['Add','Base']
    assert e.doc.shape.isValid()
