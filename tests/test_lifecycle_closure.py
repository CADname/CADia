from pathlib import Path
import shutil
import pytest
import cadquery as cq
from cadquery import exporters

from standalonecad.core.engine import CadEngine
from standalonecad.core.document import CadDocument
from standalonecad.core.expressions import eval_expr


def _new_part_box(e, path, size=10):
    e.execute('new_part', {'name':'Part'})
    e.execute('create_box', {'length_mm':size,'width_mm':size,'height_mm':size,'replace':True})
    e.execute('save_document', {'path':str(path)})
    return e._active_session_id


def test_imported_step_is_embedded_and_survives_source_deletion(tmp_path):
    src=tmp_path/'base.step'; exporters.export(cq.Workplane('XY').box(10,10,10), str(src), exportType='STEP')
    d=CadDocument(); d.load(src)
    d.add_feature('box','Boss',{'length_mm':2,'width_mm':2,'height_mm':2,'origin_mm':[0,0,10],'centered':False},'join')
    expected=float(d.shape.Volume())
    out=tmp_path/'edited.scad.json'; d.save(out)
    src.unlink()
    d2=CadDocument(); d2.load(out)
    assert d2.imported_shape is not None
    assert float(d2.shape.Volume()) == pytest.approx(expected, rel=1e-8)


def test_project_folder_move_keeps_relative_component_links(tmp_path):
    project=tmp_path/'A'; project.mkdir(); part=project/'part.scad.json'; assy=project/'assy.scad.json'
    e=CadEngine(); psid=_new_part_box(e,part)
    e.execute('new_assembly', {'name':'Assembly'}); asid=e._active_session_id
    e.execute('place_occurrence', {'path':str(part),'grounded':True})
    e.execute('save_document', {'path':str(assy)})
    moved=tmp_path/'B'; shutil.copytree(project,moved); shutil.rmtree(project)
    d=CadDocument(); d.load(moved/'assy.scad.json')
    assert d.shape is not None and float(d.shape.Volume()) == pytest.approx(1000.0)
    occ=next(iter(d.occurrences.values()))
    assert Path(occ.path).parent == moved


def test_subassembly_can_be_placed_and_bom_is_recursive(tmp_path):
    part=tmp_path/'part.scad.json'; sub=tmp_path/'sub.scad.json'; main=tmp_path/'main.scad.json'
    e=CadEngine(); psid=_new_part_box(e,part)
    e.execute('new_assembly', {'name':'Sub'}); subid=e._active_session_id
    e.execute('place_occurrence', {'path':str(part),'grounded':True})
    e.execute('save_document', {'path':str(sub)})
    e.execute('new_assembly', {'name':'Main'}); mainid=e._active_session_id
    r=e.execute('place_occurrence', {'path':str(sub),'grounded':True})
    assert r['component_type']=='assembly'
    e.execute('save_document', {'path':str(main)})
    bom=e.execute('get_assembly_bom', {'max_rows':50})
    assert [x['depth'] for x in bom['occurrences']] == [0,1]
    assert bom['occurrences'][0]['name'].startswith('sub')


def test_part_undo_immediately_resyncs_open_parent_assembly(tmp_path):
    part=tmp_path/'part.scad.json'
    e=CadEngine(); psid=_new_part_box(e,part,10)
    e.execute('new_assembly', {'name':'A'}); asid=e._active_session_id
    e.execute('place_occurrence', {'path':str(part),'grounded':True})
    assert float(e.doc.shape.Volume()) == pytest.approx(1000)
    e.activate_document(psid)
    e.execute('create_box', {'length_mm':2,'width_mm':2,'height_mm':2,'origin_mm':[0,0,10],'replace':False,'operation':'join'})
    e.activate_document(asid); changed=float(e.doc.shape.Volume()); assert changed>1000
    e.activate_document(psid); e.execute('undo', {})
    e.activate_document(asid); assert float(e.doc.shape.Volume()) == pytest.approx(1000)


def test_save_as_updates_parent_occurrence_path_and_reopen(tmp_path):
    a=tmp_path/'a.scad.json'; b=tmp_path/'b.scad.json'; assy=tmp_path/'assy.scad.json'
    e=CadEngine(); psid=_new_part_box(e,a,10)
    e.execute('new_assembly', {'name':'A'}); asid=e._active_session_id
    e.execute('place_occurrence', {'path':str(a),'grounded':True}); e.execute('save_document', {'path':str(assy)})
    e.activate_document(psid); e.execute('create_box', {'length_mm':2,'width_mm':2,'height_mm':2,'origin_mm':[0,0,10],'replace':False,'operation':'join'})
    e.execute('save_document', {'path':str(b)})
    e.activate_document(asid); occ=next(iter(e.doc.occurrences.values()))
    assert Path(occ.path)==b.resolve()
    e.execute('save_document', {'path':str(assy)})
    a.unlink()
    d=CadDocument(); d.load(assy)
    assert float(d.shape.Volume())>1000


def test_dirty_and_save_all(tmp_path):
    p=tmp_path/'p.scad.json'; e=CadEngine(); sid=_new_part_box(e,p)
    assert not e.doc.dirty
    e.execute('create_box', {'length_mm':2,'width_mm':2,'height_mm':2,'origin_mm':[0,0,10],'replace':False,'operation':'join'})
    assert e.doc.dirty
    e.save_all(); assert not e.doc.dirty


def test_model_parameters_are_global_and_drive_sketch_and_feature():
    e=CadEngine(); e.execute('new_part',{})
    e.execute('create_sketch',{'plane':'XY','name':'S1'}); c=e.execute('draw_circle',{'sketch_name':'S1','radius_mm':5})['entity_id']
    r=e.execute('add_sketch_dimension',{'sketch_name':'S1','entity_id':c,'value_mm':5}); assert r['dimension_name']=='d0'
    e.execute('close_sketch',{'sketch_name':'S1'}); e.execute('extrude',{'sketch_name':'S1','distance_mm':10})
    assert 'd1' in e.doc.parameters and e.doc.parameters['d1'].kind=='model'
    v=float(e.doc.shape.Volume())
    e.execute('set_parameter',{'name':'d0','value':'8 mm'})
    assert float(e.doc.shape.Volume()) > v
    e.execute('create_sketch',{'plane':'XY','name':'S2'}); c2=e.execute('draw_circle',{'sketch_name':'S2','radius_mm':2})['entity_id']
    r2=e.execute('add_sketch_dimension',{'sketch_name':'S2','entity_id':c2,'value_mm':2}); assert r2['dimension_name']=='d2'


def test_parameter_units_and_explicit_angle_trig():
    e=CadEngine(); e.execute('new_part',{})
    r=e.execute('create_parameter',{'name':'L','expression':'1','unit':'in'})
    assert r['value']==pytest.approx(25.4)
    assert eval_expr('sin(30 deg)',{})==pytest.approx(0.5)


def test_iproperty_sets_do_not_collide():
    e=CadEngine(); e.execute('new_part',{})
    e.execute('set_iproperty',{'set_name':'Design Tracking Properties','prop_name':'Description','value':'A'})
    e.execute('set_iproperty',{'set_name':'Inventor User Defined Properties','prop_name':'Description','value':'B'})
    assert e.execute('get_iproperty',{'set_name':'Design Tracking Properties','prop_name':'Description'})['value']=='A'
    assert e.execute('get_iproperty',{'set_name':'Inventor User Defined Properties','prop_name':'Description'})['value']=='B'


def test_template_loads_actual_document_content(tmp_path):
    template=tmp_path/'template.scad.json'
    e=CadEngine(); _new_part_box(e,template,7)
    r=e.execute('new_part',{'template':str(template),'name':'FromTemplate'})
    assert r['title']=='FromTemplate'
    assert e.doc.path is None and float(e.doc.shape.Volume())==pytest.approx(343)

def test_multi_document_sync_is_atomic_on_parent_rebuild_failure(tmp_path, monkeypatch):
    part=tmp_path/'p.scad.json'
    e=CadEngine(); psid=_new_part_box(e,part,10)
    parent_ids=[]
    for title in ('A1','A2'):
        e.execute('new_assembly', {'name':title}); parent_ids.append(e._active_session_id)
        e.execute('place_occurrence', {'path':str(part),'grounded':True})
    baseline={sid:float(next(x for x in e._sessions if x.id==sid).doc.shape.Volume()) for sid in parent_ids}
    e.activate_document(psid)
    original=CadDocument.rebuild_assembly; state={'raised':False}
    def flaky(self,*a,**k):
        result=original(self,*a,**k)
        if self.title=='A2' and not state['raised']:
            state['raised']=True
            raise RuntimeError('injected parent rebuild failure')
        return result
    monkeypatch.setattr(CadDocument,'rebuild_assembly',flaky)
    with pytest.raises(RuntimeError,match='injected'):
        e.execute('create_box', {'length_mm':2,'width_mm':2,'height_mm':2,'origin_mm':[0,0,10],'replace':False,'operation':'join'})
    # Workspace rollback restores the part and every already-updated parent.
    assert float(e.doc.shape.Volume())==pytest.approx(1000)
    for sid in parent_ids:
        assert float(next(x for x in e._sessions if x.id==sid).doc.shape.Volume())==pytest.approx(baseline[sid])


def test_saving_assembly_commits_dirty_linked_component_first(tmp_path):
    part=tmp_path/'p.scad.json'; assy=tmp_path/'a.scad.json'
    e=CadEngine(); psid=_new_part_box(e,part,10)
    e.execute('new_assembly', {'name':'A'}); asid=e._active_session_id
    e.execute('place_occurrence', {'path':str(part),'grounded':True})
    e.activate_document(psid); e.execute('create_box', {'length_mm':2,'width_mm':2,'height_mm':2,'origin_mm':[0,0,10],'replace':False,'operation':'join'})
    assert e.doc.dirty
    e.activate_document(asid); e.execute('save_document', {'path':str(assy)})
    src=next(x for x in e._sessions if x.id==psid).doc
    assert not src.dirty
    reopened=CadDocument(); reopened.load(assy)
    assert float(reopened.shape.Volume())>1000


def test_discarding_dirty_part_restores_open_parent_to_disk_shape(tmp_path):
    part=tmp_path/'p.scad.json'
    e=CadEngine(); psid=_new_part_box(e,part,10)
    e.execute('new_assembly', {'name':'A'}); asid=e._active_session_id
    e.execute('place_occurrence', {'path':str(part),'grounded':True})
    e.activate_document(psid); e.execute('create_box', {'length_mm':2,'width_mm':2,'height_mm':2,'origin_mm':[0,0,10],'replace':False,'operation':'join'})
    e.activate_document(asid); assert float(e.doc.shape.Volume())>1000
    e.activate_document(psid); e.execute('close_document', {'save':False})
    e.activate_document(asid); assert float(e.doc.shape.Volume())==pytest.approx(1000)

def test_missing_component_reference_opens_from_embedded_occurrence_snapshot_and_can_relink(tmp_path):
    part=tmp_path/'p.scad.json'; assy=tmp_path/'a.scad.json'; replacement=tmp_path/'replacement.scad.json'
    e=CadEngine(); _new_part_box(e,part,10)
    e.execute('new_assembly', {'name':'A'}); e.execute('place_occurrence', {'path':str(part),'grounded':True}); e.execute('save_document', {'path':str(assy)})
    shutil.copy2(part,replacement); part.unlink()
    d=CadDocument(); d.load(assy)
    occ=next(iter(d.occurrences.values()))
    assert occ.reference_status=='snapshot_fallback'
    assert d.shape is not None and float(d.shape.Volume())==pytest.approx(1000)
    e2=CadEngine(); e2.execute('open_document',{'path':str(assy)})
    name=next(iter(e2.doc.occurrences))
    e2.execute('relink_occurrence',{'occurrence_name':name,'path':str(replacement)})
    assert e2.doc.occurrences[name].reference_status=='resolved'


def test_nested_live_sync_is_independent_of_open_order(tmp_path):
    part=tmp_path/'p.scad.json'; sub=tmp_path/'sub.scad.json'; main=tmp_path/'main.scad.json'
    seed=CadEngine(); psid=_new_part_box(seed,part,10)
    seed.execute('new_assembly', {'name':'Sub'}); seed.execute('place_occurrence', {'path':str(part),'grounded':True}); seed.execute('save_document', {'path':str(sub)})
    seed.execute('new_assembly', {'name':'Main'}); seed.execute('place_occurrence', {'path':str(sub),'grounded':True}); seed.execute('save_document', {'path':str(main)})
    # Open in reverse dependency order: main, sub, part.
    e=CadEngine(); e.execute('open_document',{'path':str(main)}); mainid=e._active_session_id
    e.execute('open_document',{'path':str(sub)}); subid=e._active_session_id
    e.execute('open_document',{'path':str(part)}); partid=e._active_session_id
    e.execute('create_box', {'length_mm':2,'width_mm':2,'height_mm':2,'origin_mm':[0,0,10],'replace':False,'operation':'join'})
    e.activate_document(mainid); assert float(e.doc.shape.Volume())>1000


def test_save_all_commits_nested_dependency_chain(tmp_path):
    part=tmp_path/'p.scad.json'; sub=tmp_path/'sub.scad.json'; main=tmp_path/'main.scad.json'
    e=CadEngine(); psid=_new_part_box(e,part,10)
    e.execute('new_assembly', {'name':'Sub'}); subid=e._active_session_id; e.execute('place_occurrence', {'path':str(part),'grounded':True}); e.execute('save_document', {'path':str(sub)})
    e.execute('new_assembly', {'name':'Main'}); mainid=e._active_session_id; e.execute('place_occurrence', {'path':str(sub),'grounded':True}); e.execute('save_document', {'path':str(main)})
    e.activate_document(psid); e.execute('create_box', {'length_mm':2,'width_mm':2,'height_mm':2,'origin_mm':[0,0,10],'replace':False,'operation':'join'})
    assert all(next(x for x in e._sessions if x.id==sid).doc.dirty for sid in (psid,subid,mainid))
    e.save_all()
    assert not any(next(x for x in e._sessions if x.id==sid).doc.dirty for sid in (psid,subid,mainid))
    reopened=CadDocument(); reopened.load(main); assert float(reopened.shape.Volume())>1000


def test_hole_pattern_and_workplane_numeric_drivers_are_model_parameters():
    from standalonecad.core.engine import CadEngine
    e=CadEngine()
    e.execute('create_box',{'length_mm':30,'width_mm':30,'height_mm':10,'replace':True,'name':'Base'})
    h=e.execute('hole',{
        'face':{'kind':'planar','normal':'+Z','extreme':'max'},'points_mm':[[15,15,10]],
        'diameter_mm':4,'kind':'drilled','through':True
    })
    assert h['ok']
    hf=e.doc.features[-1]
    assert isinstance(hf.params['diameter_mm'],str) and hf.params['diameter_mm'].startswith('d')
    dia=hf.params['diameter_mm']
    assert e.doc.parameters[dia].kind=='model'
    e.execute('set_parameter',{'name':dia,'value':'6'})
    assert abs(e.doc.value(dia)-6.0)<1e-9

    # Pattern spacing/angle are dimension drivers; counts intentionally remain integers.
    src=hf.name
    r=e.execute('rectangular_pattern',{'feature_names':[src],'dir1':'X Axis','count1':2,'spacing_mm1':12,'dir2':None,'count2':None,'spacing_mm2':None,'natural_direction1':True,'natural_direction2':True})
    assert r['ok']
    pf=e.doc.features[-1]
    assert isinstance(pf.params['spacing_mm1'],str) and e.doc.parameters[pf.params['spacing_mm1']].kind=='model'

    wp=e.execute('create_work_plane',{'type':'offset','refs':['XY'],'offset_mm':7})
    assert wp['ok']
    w=e.doc.work_planes[wp['work_plane_name']]
    assert isinstance(w['offset_mm'],str) and e.doc.parameters[w['offset_mm']].kind=='model'
    pname=w['offset_mm']
    e.execute('set_parameter',{'name':pname,'value':'11'})
    refreshed=e.doc._refresh_work_plane(wp['work_plane_name'])
    assert abs(float(refreshed['origin'][2])-11.0)<1e-7
