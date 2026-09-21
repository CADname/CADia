import pytest

from standalonecad.core.engine import CadEngine
from standalonecad.mcp.tools import make_tools


def _circle_extrude(radius=5.0, height=4.0):
    e=CadEngine()
    e.execute('create_sketch',{'plane':'XY'})
    c=e.execute('draw_circle',{'cx':0,'cy':0,'radius':radius})['entity_id']
    d=e.execute('add_sketch_dimension',{'entity_id':c,'value_mm':radius})['dimension_name']
    e.execute('close_sketch',{'sketch_name':None})
    e.execute('extrude',{'sketch_name':'Sketch1','distance_mm':height,'operation':'join','direction':'positive'})
    return e,c,d


def test_strict_58_stays_frozen_and_new_edit_extensions_are_additive():
    assert len(make_tools('inventor'))==58
    native=make_tools('cad',extensions=True)
    names={x['name'] for x in native}
    assert len(native)==108
    assert {
        'cad_edit_sketch_entity','cad_delete_sketch_entity','cad_edit_sketch_dimension','cad_delete_sketch_dimension',
        'cad_edit_sketch_constraint','cad_delete_sketch_constraint','cad_edit_feature_definition'
    } <= names
    assert not any(x in {t['name'] for t in make_tools('inventor')} for x in {
        'inventor_edit_sketch_entity','inventor_edit_feature_definition'
    })


def test_edit_sketch_driving_dimension_updates_geometry_and_keeps_d_parameter():
    e,c,dim=_circle_extrude(5,4)
    assert e.doc.sketches['Sketch1'].entities[0].data['r']=='d0'
    out=e.execute('edit_sketch_dimension',{'sketch_name':'Sketch1','dimension_name':dim,'value_mm':8})
    assert out['parameter_name']=='d0'
    assert e.doc.parameters['d0'].expression=='8 mm'
    assert e.doc.sketches['Sketch1'].entities[0].data['r']=='d0'
    assert e.doc.shape.BoundingBox().xlen==pytest.approx(16.0)


def test_edit_line_driving_dimension_moves_endpoint_to_new_length():
    e=CadEngine(); e.execute('create_sketch',{'plane':'XY'})
    line=e.execute('draw_line',{'x1':0,'y1':0,'x2':6,'y2':8})['entity_id']
    dim=e.execute('add_sketch_dimension',{'entity_id':line,'value_mm':10})['dimension_name']
    e.execute('edit_sketch_dimension',{'sketch_name':'Sketch1','dimension_name':dim,'value_mm':20})
    ent=e.doc.sketches['Sketch1'].entities[0]
    length=((float(ent.data['x2'])-float(ent.data['x1']))**2+(float(ent.data['y2'])-float(ent.data['y1']))**2)**0.5
    assert length==pytest.approx(20.0)
    assert e.doc.parameters[dim].expression=='20 mm'


def test_edit_sketch_entity_preserves_bound_radius_parameter():
    e,c,dim=_circle_extrude(5,4)
    out=e.execute('edit_sketch_entity',{'sketch_name':'Sketch1','entity_id':c,'updates':{'radius_mm':7}})
    assert out['entity_id']==c
    assert e.doc.sketches['Sketch1'].entities[0].data['r']=='d0'
    assert e.doc.parameters['d0'].expression=='7 mm'
    assert e.doc.shape.BoundingBox().xlen==pytest.approx(14.0)


def test_edit_sketch_entity_repositions_profile_and_rebuilds_feature():
    e,c,_=_circle_extrude(5,4)
    e.execute('edit_sketch_entity',{'sketch_name':'Sketch1','entity_id':c,'updates':{'cx':10,'cy':2}})
    bb=e.doc.shape.BoundingBox()
    assert bb.xmin==pytest.approx(5.0) and bb.xmax==pytest.approx(15.0)
    assert bb.ymin==pytest.approx(-3.0) and bb.ymax==pytest.approx(7.0)


def test_invalid_sketch_edit_rolls_back_atomically():
    e,c,_=_circle_extrude(5,4)
    before=dict(e.doc.sketches['Sketch1'].entities[0].data); vol=e.doc.shape.Volume()
    with pytest.raises(ValueError):
        e.execute('edit_sketch_entity',{'sketch_name':'Sketch1','entity_id':c,'updates':{'radius_mm':-2}})
    assert e.doc.shape.Volume()==pytest.approx(vol)
    assert e.doc.sketches['Sketch1'].entities[0].data==before


def test_edit_and_delete_sketch_constraint():
    e=CadEngine(); e.execute('create_sketch',{'plane':'XY'})
    line=e.execute('draw_line',{'x1':0,'y1':0,'x2':10,'y2':2})['entity_id']
    c=e.execute('add_sketch_constraint',{'type':'horizontal','entity_ids':[line]})['constraint_name']
    assert e.doc.sketches['Sketch1'].constraints[0]['type']=='horizontal'
    out=e.execute('edit_sketch_constraint',{'sketch_name':'Sketch1','constraint_name':c,'constraint_type':'vertical','entity_ids':[line]})
    assert out['constraint']['type']=='vertical'
    ent=e.doc.sketches['Sketch1'].entities[0]
    assert float(ent.data['x2'])==pytest.approx(float(ent.data['x1']))
    d=e.execute('delete_sketch_constraint',{'sketch_name':'Sketch1','constraint_name':c})
    assert d['deleted_constraint']==c and e.doc.sketches['Sketch1'].constraints==[]


def test_delete_sketch_dimension_keeps_parameter_but_removes_constraint():
    e=CadEngine(); e.execute('create_sketch',{'plane':'XY'})
    c=e.execute('draw_circle',{'cx':0,'cy':0,'radius':5})['entity_id']
    dim=e.execute('add_sketch_dimension',{'entity_id':c,'value_mm':5})['dimension_name']
    out=e.execute('delete_sketch_dimension',{'sketch_name':'Sketch1','dimension_name':dim})
    assert e.doc.sketches['Sketch1'].dimensions==[]
    assert out['retained_parameter']==dim and dim in e.doc.parameters
    assert e.doc.sketches['Sketch1'].entities[0].data['r']==pytest.approx(5.0)


def test_delete_sketch_entity_cascades_its_sketch_relations():
    e=CadEngine(); e.execute('create_sketch',{'plane':'XY'})
    a=e.execute('draw_line',{'x1':0,'y1':0,'x2':10,'y2':0})['entity_id']
    b=e.execute('draw_line',{'x1':10,'y1':2,'x2':10,'y2':8})['entity_id']
    c=e.execute('add_sketch_constraint',{'type':'coincident','entity_ids':[a,b]})['constraint_name']
    dim=e.execute('add_sketch_dimension',{'entity_id':b,'value_mm':6})['dimension_name']
    out=e.execute('delete_sketch_entity',{'sketch_name':'Sketch1','entity_id':b})
    assert b not in [x.tag for x in e.doc.sketches['Sketch1'].entities]
    assert c in out['removed_constraints'] and dim in out['removed_dimensions']


def test_feature_definition_edit_preserves_extrude_parameter_and_operation():
    e,c,_=_circle_extrude(5,4)
    f=e.doc.features[0]
    assert f.params['distance_mm']=='d1'
    out=e.execute('edit_feature_definition',{'feature_name':'Extrude1','updates':{'distance_mm':9,'operation':'new'},'new_name':None})
    assert out['feature_name']=='Extrude1' and out['operation']=='new'
    assert e.doc.features[0].params['distance_mm']=='d1'
    assert e.doc.parameters['d1'].expression=='9 mm'
    assert e.doc.shape.BoundingBox().zmax==pytest.approx(9.0)


def test_feature_definition_rejects_wrong_fields_and_rolls_back():
    e,c,_=_circle_extrude(5,4)
    before=e.doc.shape.Volume(); params=dict(e.doc.features[0].params)
    with pytest.raises(ValueError,match='Unsupported extrude definition fields'):
        e.execute('edit_feature_definition',{'feature_name':'Extrude1','updates':{'diameter_mm':99},'new_name':None})
    assert e.doc.shape.Volume()==pytest.approx(before)
    assert e.doc.features[0].params==params


def test_feature_rename_updates_pattern_source_reference():
    e=CadEngine()
    e.execute('create_box',{'length_mm':20,'width_mm':20,'height_mm':2,'origin_mm':[-10,-10,0],'centered':False,'operation':'new','replace':False,'name':'Base'})
    e.execute('create_sketch',{'plane':'XY'})
    e.execute('draw_circle',{'cx':5,'cy':0,'radius':1})
    e.execute('close_sketch',{'sketch_name':None})
    e.execute('extrude',{'sketch_name':'Sketch1','distance_mm':4,'operation':'join','direction':'positive'})
    source=e.doc.features[1].name
    e.execute('circular_pattern',{'feature_names':[source],'axis':'Z Axis','count':3,'angle_deg':360,'natural_direction':True})
    out=e.execute('edit_feature_definition',{'feature_name':source,'updates':{'distance_mm':5},'new_name':'BossRenamed'})
    assert out['feature_name']=='BossRenamed'
    assert e.doc.features[-1].params['feature_names']==['BossRenamed']


def test_generic_edit_feature_operation_now_updates_record_not_dead_param():
    e=CadEngine()
    e.execute('create_box',{'length_mm':10,'width_mm':10,'height_mm':4,'origin_mm':[0,0,0],'centered':False,'operation':'new','replace':False,'name':'Base'})
    e.execute('create_cylinder',{'diameter_mm':4,'height_mm':4,'origin_mm':[5,5,0],'axis':[0,0,1],'operation':'join','replace':False,'name':'Boss'})
    joined=e.doc.shape.Volume()
    e.execute('edit_feature',{'feature_name':'Boss','updates':{'operation':'cut'}})
    assert e.doc.features[1].operation=='cut'
    assert 'operation' not in e.doc.features[1].params
    assert e.doc.shape.Volume()<joined
