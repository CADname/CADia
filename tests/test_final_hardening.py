import math
from pathlib import Path

import pytest

from standalonecad.core.engine import CadEngine


def test_work_axis_intersection_uses_true_offset_plane_intersection():
    e=CadEngine()
    pz=e.execute('create_work_plane',{'type':'offset','refs':['XY Plane'],'offset_mm':10})['work_plane_name']
    px=e.execute('create_work_plane',{'type':'offset','refs':['YZ Plane'],'offset_mm':5})['work_plane_name']
    name=e.execute('create_work_axis',{'type':'plane_intersection','refs':[pz,px]})['work_axis_name']
    ax=e.doc.work_axes[name]
    assert ax['origin']==pytest.approx([5,0,10],abs=1e-8)
    assert abs(ax['direction'][1])==pytest.approx(1.0,abs=1e-8)


def test_tangent_work_plane_really_uses_reference_plane_and_surface():
    e=CadEngine(); e.execute('create_cylinder',{'diameter_mm':10,'height_mm':20,'replace':True})
    side=next(x for x in e.doc.topology()['faces'] if x['geom']=='CYLINDER')
    name=e.execute('create_work_plane',{'type':'tangent','refs':[side['id'],'XZ Plane']})['work_plane_name']
    wp=e.doc.work_planes[name]
    assert wp['origin']==pytest.approx([0,5,0],abs=1e-8)
    assert wp['direction']==pytest.approx([0,1,0],abs=1e-8)


def test_work_axis_from_curved_edge_is_rejected_instead_of_guessed():
    e=CadEngine(); e.execute('create_cylinder',{'diameter_mm':10,'height_mm':10,'replace':True})
    curved=next(x for x in e.doc.topology()['edges'] if x['geom']=='CIRCLE')
    with pytest.raises(ValueError,match='linear edge'):
        e.execute('create_work_axis',{'type':'edge','refs':[curved['id']]})


def test_project_geometry_keeps_exact_circle_and_curves_are_not_single_chords():
    e=CadEngine(); e.execute('create_cylinder',{'diameter_mm':10,'height_mm':10,'replace':True})
    sk=e.execute('create_sketch',{'plane':'XY'})['sketch_name']
    circ=next(x for x in e.doc.topology()['edges'] if x['geom']=='CIRCLE')
    out=e.execute('project_geometry',{'edge_ids':[circ['id']]})
    ent=e.doc.sketches[sk].entities[-1]
    assert out['projected_count']==1 and ent.kind=='circle'
    assert float(ent.data['r'])==pytest.approx(5.0)


def test_material_drives_real_mass_instead_of_fixed_density():
    e=CadEngine(); e.execute('create_box',{'length_mm':10,'width_mm':10,'height_mm':10,'replace':True})
    e.execute('set_material',{'material_name':'Steel'})
    mp=e.execute('get_mass_properties',{})
    assert mp['density_g_cm3']==pytest.approx(7.85)
    assert mp['mass_g']==pytest.approx(7.85,rel=1e-8)
    e.execute('set_material',{'material_name':'Custom Unobtainium'})
    mp=e.execute('get_mass_properties',{})
    assert mp['mass_known'] is False and mp['mass_g'] is None


def test_sketch_constraints_are_reapplied_as_a_system():
    e=CadEngine(); sk=e.execute('create_sketch',{'plane':'XY'})['sketch_name']
    a=e.execute('draw_line',{'x1':0,'y1':0,'x2':10,'y2':2})['entity_id']
    b=e.execute('draw_line',{'x1':15,'y1':5,'x2':21,'y2':8})['entity_id']
    for typ,ids in [('horizontal',[a]),('parallel',[a,b]),('equal',[a,b]),('coincident',[a,b])]:
        r=e.execute('add_sketch_constraint',{'type':typ,'entity_ids':ids})
        assert r['health']=='up_to_date'
    out=e.execute('close_sketch',{'sketch_name':sk})
    assert all(x['health']=='up_to_date' for x in out['constraint_health'])
    l1=e.doc.sketches[sk].entities[0].data; l2=e.doc.sketches[sk].entities[1].data
    assert float(l1['y1'])==pytest.approx(float(l1['y2']))
    assert [float(l2['x1']),float(l2['y1'])]==pytest.approx([float(l1['x2']),float(l1['y2'])])
    assert math.dist((float(l1['x1']),float(l1['y1'])),(float(l1['x2']),float(l1['y2'])))==pytest.approx(math.dist((float(l2['x1']),float(l2['y1'])),(float(l2['x2']),float(l2['y2']))))


def test_generic_pattern_can_repeat_native_cut_feature_not_just_extrude_revolve_hole():
    e=CadEngine(); e.execute('create_box',{'length_mm':100,'width_mm':30,'height_mm':10,'replace':True,'name':'Base'})
    v0=e.doc.shape.Volume()
    e.execute('create_slot',{'length_mm':10,'width_mm':5,'depth_mm':10,'cx_mm':15,'cy_mm':15,'z0_mm':0,'operation':'cut','replace':False,'name':'Slot1'})
    v1=e.doc.shape.Volume(); one=v0-v1
    e.execute('rectangular_pattern',{'feature_names':['Slot1'],'dir1':'X Axis','count1':3,'spacing_mm1':30,'natural_direction1':True})
    assert e.doc.shape.Volume()==pytest.approx(v0-3*one,rel=1e-8)


def test_circular_pattern_uses_custom_work_axis_origin_not_global_origin():
    e=CadEngine(); e.execute('create_box',{'length_mm':40,'width_mm':40,'height_mm':5,'replace':True,'name':'Base'})
    px=e.execute('create_work_plane',{'type':'offset','refs':['YZ Plane'],'offset_mm':20})['work_plane_name']
    py=e.execute('create_work_plane',{'type':'offset','refs':['XZ Plane'],'offset_mm':20})['work_plane_name']
    axis=e.execute('create_work_axis',{'type':'plane_intersection','refs':[px,py]})['work_axis_name']
    assert e.doc.work_axes[axis]['origin']==pytest.approx([20,20,0],abs=1e-8)
    e.execute('create_cylinder',{'diameter_mm':4,'height_mm':5,'origin_mm':[25,20,0],'operation':'cut','replace':False,'name':'Cut1'})
    v1=e.doc.shape.Volume()
    e.execute('circular_pattern',{'feature_names':['Cut1'],'axis':axis,'count':2,'angle_deg':360,'natural_direction':True})
    assert e.doc.shape.Volume() < v1


def test_assembly_constraints_are_iteratively_reenforced(tmp_path):
    part=tmp_path/'part.scad.json'
    p=CadEngine(); p.execute('create_box',{'length_mm':10,'width_mm':10,'height_mm':10,'replace':True}); p.execute('save_document',{'path':str(part)})
    a=CadEngine(); a.execute('new_assembly',{})
    fixed=a.execute('place_occurrence',{'path':str(part),'grounded':True,'position_mm':[0,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    moving=a.execute('place_occurrence',{'path':str(part),'grounded':False,'position_mm':[20,12,7],'rotation_deg_xyz':[20,30,15]})['occurrence_name']
    rels=[('mate','XY Plane'),('flush','YZ Plane'),('mate','XZ Plane')]
    for typ,ref in rels:
        a.execute('add_constraint',{'type':typ,'a_occurrence':fixed,'a_ref':ref,'b_occurrence':moving,'b_ref':ref,'offset_mm':0,'insert_opposed':True})
    assert all(c.health=='up_to_date' for c in a.doc.constraints)
    assert a.doc.occurrences[moving].position_mm==pytest.approx([0,0,0],abs=1e-6)
    bom=a.execute('get_assembly_bom',{})
    row=next(x for x in bom['occurrences'] if x['name']==moving)
    assert row['dof_translation']==0 and row['dof_rotation']==0
    assert 'dof_estimate' not in row


def test_interference_reports_analysis_status_and_distance_never_fakes_zero(tmp_path):
    part=tmp_path/'part.scad.json'
    p=CadEngine(); p.execute('create_box',{'length_mm':10,'width_mm':10,'height_mm':10,'replace':True}); p.execute('save_document',{'path':str(part)})
    a=CadEngine(); a.execute('new_assembly',{})
    x=a.execute('place_occurrence',{'path':str(part),'grounded':True,'position_mm':[0,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    y=a.execute('place_occurrence',{'path':str(part),'grounded':False,'position_mm':[15,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
    inter=a.execute('check_interference',{})
    assert inter['analysis_complete'] is True and inter['count']==0 and inter['errors']==[]
    dist=a.execute('measure_min_distance',{'a_occurrence':x,'a_ref':None,'b_occurrence':y,'b_ref':None})
    assert dist['ok'] is True and dist['distance_mm']==pytest.approx(5.0,abs=1e-6)
