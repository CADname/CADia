from pathlib import Path

import pytest

from standalonecad.core.document import CadDocument
from standalonecad.core.engine import CadEngine
from standalonecad.mcp.tools import make_tools
from standalonecad.planning import direct_plan


def _save_box(path: Path, size=10.0):
    d=CadDocument(); d.reset('part'); d.title=path.stem
    d.add_feature('box','Base',{'length_mm':size,'width_mm':size,'height_mm':size},'join')
    d.save(str(path))


def _engine_box(e: CadEngine):
    return e.execute('create_box', {
        'length_mm':10,'width_mm':10,'height_mm':10,'origin_mm':[0,0,0],
        'centered':False,'operation':'new','replace':False,'name':'Base',
    })


def test_strict_surface_frozen_and_delete_extensions_are_additive():
    assert len(make_tools('inventor')) == 58
    asm=make_tools('inventor',assembly_extensions=True)
    native=make_tools('cad',extensions=True)
    assert len(asm) == 68
    assert len(native) == 108
    assert {'inventor_delete_occurrence','inventor_delete_constraint'} <= {x['name'] for x in asm}
    assert 'cad_delete_document_file' in {x['name'] for x in native}
    assert 'inventor_delete_occurrence' not in {x['name'] for x in make_tools('inventor')}


def test_component_occurrence_delete_removes_instance_not_source_file(tmp_path):
    part=tmp_path/'Part.scad.json'; _save_box(part)
    a=CadDocument(); a.reset('assembly'); cd=a.component_definition
    occ=cd.occurrences.add(str(part),grounded=True)
    name=occ.name
    result=occ.delete()
    assert result['deleted_occurrence']==name
    assert name not in a.occurrences
    assert part.exists(), 'deleting an assembly occurrence must never delete its source document'


def test_occurrence_delete_cascades_dependent_constraint_and_joint(tmp_path):
    part=tmp_path/'Part.scad.json'; _save_box(part)
    a=CadDocument(); a.reset('assembly'); cd=a.component_definition
    fixed=cd.occurrences.add(str(part),grounded=True)
    moving=cd.occurrences.add(str(part),{'position_mm':[0,0,20]})
    c=cd.constraints.add_flush(fixed,'XY Plane',moving,'XY Plane',offset_mm=10)
    assert len(a.constraints)==1
    result=cd.occurrences.delete(moving.name)
    assert c['name'] in result['deleted_constraints']
    assert len(a.constraints)==0 and moving.name not in a.occurrences

    moving2=cd.occurrences.add(str(part),{'position_mm':[0,0,5]})
    j=cd.joints.add('slider',fixed,'Z Axis',moving2,'Z Axis',linear_start_mm=0,linear_end_mm=40)
    assert len(a.joints)==1
    result2=cd.occurrences.delete(moving2.name)
    assert j['name'] in result2['deleted_joints']
    assert len(a.joints)==0 and moving2.name not in a.occurrences


def test_constraint_delete_matches_inventor_style_and_is_undoable(tmp_path):
    part=tmp_path/'Part.scad.json'; _save_box(part)
    e=CadEngine(); e.execute('new_assembly',{})
    a=e.execute('place_occurrence',{'path':str(part),'grounded':True})['occurrence_name']
    b=e.execute('place_occurrence',{'path':str(part),'grounded':False,'position_mm':[0,0,20]})['occurrence_name']
    c=e.execute('add_constraint',{'type':'flush','a_occurrence':a,'a_ref':'XY Plane','b_occurrence':b,'b_ref':'XY Plane','offset_mm':10,'angle_deg':None,'insert_opposed':True})
    out=e.execute('delete_constraint',{'name':c['name']})
    assert out['deleted']==c['name'] and e.doc.constraints==[]
    e.undo()
    assert [x.name for x in e.doc.constraints]==[c['name']]


def test_occurrence_delete_engine_uses_explicit_selection_and_undo(tmp_path):
    part=tmp_path/'Part.scad.json'; _save_box(part)
    e=CadEngine(); e.execute('new_assembly',{})
    name=e.execute('place_occurrence',{'path':str(part),'grounded':False})['occurrence_name']
    e.selection={'type':'occurrence','occurrence_name':name}
    out=e.execute('delete_occurrence',{'occurrence_name':None})
    assert out['deleted_occurrence']==name and name not in e.doc.occurrences
    assert e.selection=={}
    e.undo()
    assert name in e.doc.occurrences


def test_selected_occurrence_delete_has_deterministic_plan():
    state={'selection':{'type':'occurrence','occurrence_name':'Block:2'}}
    plan=direct_plan('Remove this component from the assembly',state)
    assert plan is not None
    assert plan['calls']==[{'tool':'inventor_delete_occurrence','arguments':{'occurrence_name':'Block:2'}}]


def test_delete_document_file_requires_confirmation_and_clean_saved_document(tmp_path):
    p=tmp_path/'Victim.scad.json'
    e=CadEngine(); _engine_box(e); e.execute('save_document',{'path':str(p)})
    with pytest.raises(ValueError,match='confirm=true'):
        e.execute('delete_document_file',{'confirm':False,'discard_unsaved_changes':False})
    assert p.exists()

    e.execute('create_parameter',{'name':'X','expression':'1','unit':'mm'})
    with pytest.raises(ValueError,match='unsaved changes'):
        e.execute('delete_document_file',{'confirm':True,'discard_unsaved_changes':False})
    assert p.exists()

    out=e.execute('delete_document_file',{'confirm':True,'discard_unsaved_changes':True})
    assert out['deleted_path']==str(p.resolve())
    assert not p.exists() and e.doc is None


def test_delete_document_file_refuses_open_assembly_reference(tmp_path):
    p=tmp_path/'Referenced.scad.json'
    e=CadEngine(); _engine_box(e); e.execute('save_document',{'path':str(p)})
    part_session=next(x['id'] for x in e.document_sessions() if x['active'])
    e.execute('new_assembly',{})
    e.execute('place_occurrence',{'path':str(p),'grounded':False})
    e.activate_document(part_session)
    with pytest.raises(ValueError,match='referenced by an open assembly'):
        e.execute('delete_document_file',{'confirm':True,'discard_unsaved_changes':False})
    assert p.exists()


def test_delete_document_file_never_recursively_deletes_neighbor_files(tmp_path):
    p=tmp_path/'Victim.scad.json'; neighbor=tmp_path/'keep.txt'; neighbor.write_text('keep')
    e=CadEngine(); _engine_box(e); e.execute('save_document',{'path':str(p)})
    e.execute('delete_document_file',{'confirm':True,'discard_unsaved_changes':False})
    assert not p.exists()
    assert neighbor.read_text()=='keep'
