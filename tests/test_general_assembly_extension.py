from pathlib import Path

import cadquery as cq
from cadquery import exporters
import pytest

from standalonecad.core.assembly import Occurrence, default_interfaces, AssemblyConstraint, solve_constraint, assembly_degrees_of_freedom
from standalonecad.core.joints import AssemblyJoint, solve_joint, joint_state, assembly_kinematic_degrees_of_freedom
from standalonecad.core.engine import CadEngine
from standalonecad.mcp.tools import make_tools
from standalonecad.planning import PlanExecutor
from standalonecad.bridge.host import CadHost


def _pair():
    fixed = Occurrence('Fixed','',grounded=True,interfaces=default_interfaces())
    moving = Occurrence('Moving','',grounded=False,position_mm=[5,7,10],rotation_deg_xyz=[20,10,30],interfaces=default_interfaces())
    return {'Fixed': fixed, 'Moving': moving}


@pytest.mark.parametrize('typ,expected',[
    ('rigid',(0,0)),('rotational',(0,1)),('slider',(1,0)),
    ('cylindrical',(1,1)),('planar',(2,1)),('ball',(0,3)),
])
def test_general_joint_types_have_expected_dof(typ, expected):
    occ = _pair()
    kwargs = {}
    if typ == 'slider':
        kwargs = dict(linear_position_mm=10, linear_start_mm=0, linear_end_mm=40)
    j = AssemblyJoint('J', typ, 'Fixed', 'Z Axis', 'Moving', 'Z Axis', **kwargs)
    solve_joint(occ, j, default_interfaces())
    assert j.health == 'up_to_date'
    dof = assembly_kinematic_degrees_of_freedom(occ, [], [j], default_interfaces())
    assert dof['Moving'] == expected


def test_slider_limits_are_general_and_preserve_one_translation_dof():
    occ = _pair()
    j = AssemblyJoint('LinearJoint','slider','Fixed','Z Axis','Moving','Z Axis',10,0,40)
    solve_joint(occ,j,default_interfaces())
    assert joint_state(occ,j,default_interfaces())['linear_position_mm'] == pytest.approx(10)
    j.linear_position_mm = 100
    solve_joint(occ,j,default_interfaces())
    assert j.linear_position_mm == pytest.approx(40)
    assert assembly_kinematic_degrees_of_freedom(occ,[],[j],default_interfaces())['Moving'] == (1,0)


def test_legacy_constraint_behavior_is_unchanged_when_no_joints_exist():
    occ = _pair()
    c = AssemblyConstraint('C1','insert','Fixed','Z Axis','Moving','Z Axis',0,None,False)
    solve_constraint(occ,c,default_interfaces())
    assert c.health == 'up_to_date'
    assert assembly_degrees_of_freedom(occ,[c],default_interfaces())['Moving'] == (0,1)


def test_strict_ipt_surface_stays_58_and_joint_extension_is_separate():
    strict = make_tools('inventor')
    extended = make_tools('inventor', assembly_extensions=True)
    assert len(strict) == 58
    strict_names = {x['name'] for x in strict}
    ext_names = {x['name'] for x in extended} - strict_names
    assert ext_names == {
        'inventor_add_joint','inventor_edit_joint','inventor_set_joint_limits',
        'inventor_drive_joint','inventor_list_joints','inventor_delete_joint',
        'inventor_transform_occurrence','inventor_edit_constraint',
        'inventor_delete_constraint','inventor_delete_occurrence',
    }
    assert len(extended) == 68


def test_engine_joint_extension_serializes_and_restores(tmp_path: Path):
    a_path = tmp_path/'Fixed.step'; b_path = tmp_path/'Moving.step'
    exporters.export(cq.Workplane('XY').box(40,40,60).val(), str(a_path), exportType='STEP')
    exporters.export(cq.Workplane('XY').box(20,20,10).val(), str(b_path), exportType='STEP')
    e = CadEngine(); e.execute('new_assembly',{})
    a = e.execute('place_occurrence',{'path':str(a_path),'grounded':True})['occurrence_name']
    b = e.execute('place_occurrence',{'path':str(b_path),'grounded':False,'position_mm':[3,2,10],'rotation_deg_xyz':[5,8,15]})['occurrence_name']
    r = e.execute('add_joint',{'type':'slider','a_occurrence':a,'a_ref':'Z Axis','b_occurrence':b,'b_ref':'Z Axis','linear_position_mm':10,'linear_start_mm':0,'linear_end_mm':40})
    assert r['health'] == 'up_to_date'
    row = next(x for x in e.execute('get_assembly_bom',{})['occurrences'] if x['name']==b)
    assert (row['dof_translation'],row['dof_rotation']) == (1,0)
    e.execute('drive_joint',{'name':'Joint1','linear_position_mm':40,'angular_position_deg':None})
    assert e.execute('list_joints',{})['joints'][0]['state']['linear_position_mm'] == pytest.approx(40)
    save = tmp_path/'assembly.scad.json'; e.execute('save_document',{'path':str(save)})
    e2 = CadEngine(); e2.execute('open_document',{'path':str(save)})
    js = e2.execute('list_joints',{})['joints']
    assert len(js)==1 and js[0]['type']=='slider' and js[0]['linear_end_mm']==pytest.approx(40)


def _part(e: CadEngine, path: Path, name: str):
    e.execute('new_part', {'name': name})
    e.execute('create_box', {'length_mm':10,'width_mm':10,'height_mm':10,'centered':True,'name':'Body','operation':'new'})
    e.execute('save_document', {'path': str(path)})


def test_runtime_occurrence_binding_uses_actual_place_result(tmp_path: Path):
    e = CadEngine()
    p1 = tmp_path/'FixedBase.scad.json'; p2 = tmp_path/'MovingBlock.scad.json'
    _part(e,p1,'FixedBase'); _part(e,p2,'MovingBlock')
    e.execute('new_assembly', {'name':'A'})
    host = CadHost(e); host.start()
    try:
        ex = PlanExecutor(e, host.info['target_id'], target_info=host.info)
        plan={'calls':[
            {'tool':'inventor_place_occurrence','arguments':{'path':str(p1),'grounded':True,'position_mm':[0,0,0],'rotation_deg_xyz':[0,0,0]}},
            {'tool':'inventor_place_occurrence','arguments':{'path':str(p2),'grounded':False,'position_mm':[0,0,2],'rotation_deg_xyz':[0,0,0]}},
            {'tool':'inventor_add_joint','arguments':{'type':'slider','a':{'occurrence':'$result[1].occurrence_name','ref':'Z Axis'},'b':{'occurrence':'$result[2].occurrence_name','ref':'Z Axis'},'name':'Slide','linear_position_mm':2,'linear_start_mm':0,'linear_end_mm':40,'angular_position_deg':None,'angular_start_deg':None,'angular_end_deg':None}},
        ]}
        result = ex.execute(plan)
        assert result.ok, result.error
        actual=set(e.doc.occurrences)
        assert e.doc.joints[-1].a_occurrence in actual
        assert e.doc.joints[-1].b_occurrence in actual
    finally:
        host.close()


def test_failure_recovery_has_no_product_specific_routing():
    text=(Path(__file__).parents[1]/'src/standalonecad/recovery.py').read_text(encoding='utf-8').lower()
    forbidden=('piston','piston','cylinder','cylinder','rack-pinion-specific','lead-screw-specific')
    assert not any(x in text for x in forbidden)

from standalonecad.core.joints import solve_assembly_relationships, assembly_relationship_diagnostics


def test_global_solver_handles_compatible_legacy_constraint_and_joint_together():
    occ = _pair()
    # General mixed-relationship case: slider retains axial translation while a flush
    # relation reinforces an already-compatible orientation/plane relation.
    j = AssemblyJoint('J','slider','Fixed','Z Axis','Moving','Z Axis',10,0,40)
    c = AssemblyConstraint('C','flush','Fixed','YZ Plane','Moving','YZ Plane',0,None,True)
    out = solve_assembly_relationships(occ,[c],[j],default_interfaces())
    assert out['converged'], out
    assert c.health == 'up_to_date'
    assert j.health == 'up_to_date'
    assert joint_state(occ,j,default_interfaces())['linear_position_mm'] == pytest.approx(10, abs=1e-4)


def test_global_solver_reports_generic_conflict_instead_of_product_specific_repair():
    occ = _pair()
    # These relations intentionally disagree on the same relative axial position.
    j = AssemblyJoint('J','slider','Fixed','Z Axis','Moving','Z Axis',10,0,40)
    c = AssemblyConstraint('C','insert','Fixed','Z Axis','Moving','Z Axis',0,None,False)
    solve_assembly_relationships(occ,[c],[j],default_interfaces())
    diag = assembly_relationship_diagnostics(occ,[c],[j],default_interfaces())
    assert diag['sick']
    assert diag['possible_conflicts']
    assert {'C','J'} == {diag['possible_conflicts'][0]['relationship_a'],diag['possible_conflicts'][0]['relationship_b']}


def test_geometry_intent_face_selector_is_general(tmp_path: Path):
    a_path = tmp_path/'BodyA.step'; b_path = tmp_path/'BodyB.step'
    exporters.export(cq.Workplane('XY').box(20,20,20).val(), str(a_path), exportType='STEP')
    exporters.export(cq.Workplane('XY').box(8,8,8).val(), str(b_path), exportType='STEP')
    e = CadEngine(); e.execute('new_assembly',{})
    a=e.execute('place_occurrence',{'path':str(a_path),'grounded':True})['occurrence_name']
    b=e.execute('place_occurrence',{'path':str(b_path),'grounded':False,'position_mm':[7,4,30],'rotation_deg_xyz':[11,17,23]})['occurrence_name']
    side_a={'kind':'face','selector':{'kind':'planar','normal':'+Z','extreme':'max'}}
    side_b={'kind':'face','selector':{'kind':'planar','normal':'-Z','extreme':'max'}}
    r=e.execute('add_joint',{
        'type':'rigid','a_occurrence':a,'a_ref':None,'a_intent':side_a,
        'b_occurrence':b,'b_ref':None,'b_intent':side_b,
        'flip_origin_direction':True,'flip_alignment_direction':False,
    })
    assert r['health']=='up_to_date'
    assert r['state']['axis_alignment_deg'] == pytest.approx(0, abs=1e-3)


def test_no_product_specific_terms_in_joint_routing_or_recovery():
    root=Path(__file__).parents[1]/'src/standalonecad'
    # Geometry code necessarily contains generic words such as CYLINDER.  What is
    # forbidden is routing/recovery keyed to a named product/example.
    files=[root/'codex_agent.py',root/'planning.py',root/'recovery.py']
    forbidden=('piston','pistonhead')
    for file in files:
        text=file.read_text(encoding='utf-8').lower()
        assert not any(term in text for term in forbidden), (file, [t for t in forbidden if t in text])


def test_generic_annular_housing_and_sliding_member_travel_without_interference(tmp_path: Path):
    # Regression for a generic axial mechanism; deliberately no product-name routing.
    housing = cq.Workplane('XY').circle(20).circle(15).extrude(70).val()
    member = cq.Workplane('XY').circle(14.8).extrude(8).workplane(offset=8).circle(5).extrude(62).val()
    a_path=tmp_path/'BodyA.step'; b_path=tmp_path/'BodyB.step'
    exporters.export(housing,str(a_path),exportType='STEP')
    exporters.export(member,str(b_path),exportType='STEP')
    e=CadEngine(); e.execute('new_assembly',{})
    a=e.execute('place_occurrence',{'path':str(a_path),'grounded':True})['occurrence_name']
    b=e.execute('place_occurrence',{'path':str(b_path),'grounded':False,'position_mm':[4,-3,12],'rotation_deg_xyz':[8,11,17]})['occurrence_name']
    r=e.execute('add_joint',{'type':'slider','a_occurrence':a,'a_ref':'Z Axis','b_occurrence':b,'b_ref':'Z Axis','linear_position_mm':10,'linear_start_mm':0,'linear_end_mm':40})
    assert r['health']=='up_to_date'
    for position in (0,20,40):
        rr=e.execute('drive_joint',{'name':'Joint1','linear_position_mm':position,'angular_position_deg':None})
        assert rr['health']=='up_to_date'
        assert rr['state']['linear_position_mm']==pytest.approx(position,abs=1e-3)
        interference=e.execute('check_interference',{})
        assert interference['count']==0

from standalonecad.core.joints import validate_joint_definition, joint_residual
from standalonecad.recovery import deterministic_recovery_plan
from standalonecad.planning import PlanResult


def test_rigid_joint_allows_fixed_angular_clocking_without_creating_dof():
    occ = _pair()
    j = AssemblyJoint('RigidClock','rigid','Fixed','Z Axis','Moving','Z Axis',angular_position_deg=35)
    validate_joint_definition(j)
    solve_joint(occ,j,default_interfaces())
    assert j.health == 'up_to_date'
    st = joint_state(occ,j,default_interfaces())
    assert st['angular_position_deg'] == pytest.approx(35, abs=1e-3)
    assert assembly_kinematic_degrees_of_freedom(occ,[],[j],default_interfaces())['Moving'] == (0,0)


def test_rotational_joint_allows_fixed_axial_offset_plus_free_rotation():
    occ = _pair()
    j = AssemblyJoint('HingeOffset','rotational','Fixed','Z Axis','Moving','Z Axis',linear_position_mm=12)
    validate_joint_definition(j)
    solve_joint(occ,j,default_interfaces())
    assert j.health == 'up_to_date'
    assert joint_state(occ,j,default_interfaces())['linear_position_mm'] == pytest.approx(12, abs=1e-3)
    assert assembly_kinematic_degrees_of_freedom(occ,[],[j],default_interfaces())['Moving'] == (0,1)


def test_slider_joint_allows_fixed_clocking_plus_free_translation():
    occ = _pair()
    j = AssemblyJoint('SlideClock','slider','Fixed','Z Axis','Moving','Z Axis',linear_position_mm=5,linear_start_mm=0,linear_end_mm=20,angular_position_deg=22)
    validate_joint_definition(j)
    solve_joint(occ,j,default_interfaces())
    assert j.health == 'up_to_date'
    st=joint_state(occ,j,default_interfaces())
    assert st['linear_position_mm'] == pytest.approx(5, abs=1e-3)
    assert st['angular_position_deg'] == pytest.approx(22, abs=1e-3)
    assert assembly_kinematic_degrees_of_freedom(occ,[],[j],default_interfaces())['Moving'] == (1,0)


def test_rigid_range_limits_are_rejected_because_rigid_has_no_free_coordinate():
    j = AssemblyJoint('Bad','rigid','Fixed','Z Axis','Moving','Z Axis',angular_position_deg=20,angular_start_deg=-30,angular_end_deg=30)
    with pytest.raises(ValueError, match='angular limits are not applicable'):
        validate_joint_definition(j)


def test_failure_only_recovery_removes_only_redundant_limits_from_constrained_joint_coordinate():
    class E:
        schemas={}
    plan={'calls':[{'tool':'inventor_add_joint','arguments':{
        'type':'rigid','a':{'occurrence':'A','ref':'Z Axis'},'b':{'occurrence':'B','ref':'Z Axis'},
        'angular_position_deg':20,'angular_start_deg':-30,'angular_end_deg':30,
        'linear_position_mm':4,'linear_start_mm':0,'linear_end_mm':10,
    }}]}
    result=PlanResult(False,'',plan['calls'],[],'rigid joint has no free axial rotation; angular limits are not applicable',0,0)
    fixed,reasons=deterministic_recovery_plan(E(),plan,result,{})
    args=fixed['calls'][0]['arguments']
    assert args['angular_position_deg']==20 and args['linear_position_mm']==4
    assert args['angular_start_deg'] is None and args['angular_end_deg'] is None
    assert args['linear_start_mm'] is None and args['linear_end_mm'] is None
    assert reasons
