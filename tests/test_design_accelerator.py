from __future__ import annotations

import math
import pytest

from standalonecad.core.engine import CadEngine
from standalonecad.mcp.tools import make_tools
from standalonecad.planning import direct_plan, PlanExecutor


def _bb(engine):
    return engine.doc.shape.BoundingBox()


def test_upstream_ipt_mcp_surface_remains_exactly_58_tools():
    tools=make_tools('inventor')
    assert len(tools)==58
    names={x['name'] for x in tools}
    assert 'inventor_extrude' in names
    assert 'inventor_add_constraint' in names
    assert 'inventor_create_gear' not in names


def test_accelerator_is_native_extension_only():
    base={x['name'] for x in make_tools('cad')}
    ext={x['name'] for x in make_tools('cad',extensions=True)}
    for name in ('cad_create_gear','cad_create_shaft','cad_create_parallel_key','cad_create_bearing','cad_create_spring','cad_create_v_pulley'):
        assert name in ext
        assert name not in base


def test_stepped_shaft_keyway_and_edit_rebuild():
    e=CadEngine()
    r=e.execute('create_shaft',{
        'sections':[{'length_mm':20,'diameter_mm':20},{'length_mm':30,'diameter_mm':30}],
        'bore_diameter_mm':5,'keyway_width_mm':6,'keyway_depth_mm':2,'keyway_length_mm':15,'keyway_start_mm':25,
        'replace':True,'operation':'new','name':'Shaft1',
    })
    assert r['generator']=='shaft' and r['section_count']==2
    assert e.doc.shape.isValid()
    assert _bb(e).zlen==pytest.approx(50,abs=1e-6)
    before=e.doc.shape.Volume()
    e.execute('edit_feature',{'feature_name':'Shaft1','updates':{'sections':[{'length_mm':25,'diameter_mm':20},{'length_mm':35,'diameter_mm':30}]}})
    assert _bb(e).zlen==pytest.approx(60,abs=1e-6)
    assert e.doc.shape.Volume()!=pytest.approx(before)


def test_parallel_key_round_end_has_requested_envelope():
    e=CadEngine(); e.execute('create_parallel_key',{'width_mm':6,'height_mm':6,'length_mm':30,'end_style':'round','replace':True,'operation':'new','name':'Key1'})
    bb=_bb(e)
    assert (bb.xlen,bb.ylen,bb.zlen)==pytest.approx((30,6,6),abs=1e-6)


def test_bearing_exact_external_envelope_and_compound_elements():
    e=CadEngine(); r=e.execute('create_bearing',{'kind':'deep_groove_ball','bore_diameter_mm':20,'outer_diameter_mm':47,'width_mm':14,'rolling_elements':8,'detailed':True,'replace':True,'operation':'new','name':'Bearing1'})
    bb=_bb(e)
    assert r['simplified'] is True
    assert (bb.xlen,bb.ylen,bb.zlen)==pytest.approx((47,47,14),abs=1e-6)
    assert len(e.doc.shape.Solids())>=10


def test_compression_spring_free_length_means_physical_envelope():
    e=CadEngine(); r=e.execute('create_spring',{'kind':'compression','wire_diameter_mm':2,'mean_diameter_mm':20,'free_length_mm':40,'active_turns':8,'right_handed':True,'replace':True,'operation':'new','name':'Spring1'})
    bb=_bb(e)
    assert r['outside_diameter_mm']==pytest.approx(22)
    assert bb.zlen==pytest.approx(40,abs=1e-4)


def test_belleville_spring_envelope():
    e=CadEngine(); e.execute('create_spring',{'kind':'belleville','outer_diameter_mm':50,'inner_diameter_mm':25,'thickness_mm':2,'free_height_mm':6,'replace':True,'operation':'new','name':'Belleville1'})
    bb=_bb(e)
    assert bb.xlen==pytest.approx(50,abs=1e-6)
    assert bb.zlen==pytest.approx(6,abs=1e-6)


def test_v_pulley_real_grooves_and_bore():
    e=CadEngine(); r=e.execute('create_v_pulley',{'pitch_diameter_mm':60,'width_mm':20,'groove_count':2,'groove_angle_deg':40,'bore_diameter_mm':10,'replace':True,'operation':'new','name':'Pulley1'})
    assert r['groove_count']==2
    assert _bb(e).zlen==pytest.approx(20,abs=1e-6)
    assert e.doc.shape.isValid()


def test_create_gear_spur_preserves_native_stable_generator():
    e=CadEngine(); r=e.execute('create_gear',{'kind':'spur','module':2,'teeth':24,'width_mm':10,'bore_diameter_mm':8,'pressure_angle_deg':20,'helix_angle_deg':0,'backlash_mm':0,'clearance_mm':0,'replace':True,'operation':'new','name':'Gear1'})
    assert r['gear_kind']=='spur'
    assert r['pitch_diameter_mm']==pytest.approx(48)
    assert r['outside_diameter_mm']==pytest.approx(52)
    assert e.doc.shape.isValid()


def test_external_advanced_gear_backend_missing_fails_truthfully_and_rolls_back(monkeypatch):
    import standalonecad.core.document as document_module
    def unavailable(*args, **kwargs):
        raise RuntimeError("cq_gears optional backend unavailable")
    monkeypatch.setattr(document_module, "advanced_gear", unavailable)
    e=CadEngine(); e.execute('create_box',{'length_mm':10,'width_mm':10,'height_mm':10,'origin_mm':[0,0,0],'centered':False,'operation':'new','replace':True,'name':'Base'})
    before=e.doc.shape.Volume(); features=len(e.doc.features)
    with pytest.raises(RuntimeError,match='cq_gears'):
        e.execute('create_gear',{'kind':'helical','module':2,'teeth':24,'width_mm':10,'helix_angle_deg':20,'replace':False,'operation':'join','name':'Helical1'})
    assert e.doc.shape.Volume()==pytest.approx(before)
    assert len(e.doc.features)==features


def test_natural_language_high_confidence_helical_route_only():
    q='create helical gear module=2 teeth=24 helix angle=20 face width=15mm'
    plan=direct_plan(q,{})
    assert plan and plan['calls'][-1]['tool']=='cad_create_gear'
    a=plan['calls'][-1]['arguments']
    assert a['kind']=='helical' and a['module']==2 and a['teeth']==24 and a['helix_angle_deg']==20 and a['width_mm']==15
    assert direct_plan('Make this area gear-like',{}) is None
    assert direct_plan('Edit the helical gear to module 2, 24 teeth, 15 mm face width, 20 deg helix angle',{}) is None
    assert direct_plan('Create a worm gear set',{}) is None
    worm=direct_plan('create worm screw module=2 lead angle=18 starts=2 length=50mm',{})
    assert worm and worm['calls'][-1]['arguments']['kind']=='worm'


def test_direct_plan_schema_validation_for_accelerator_prompt():
    e=CadEngine(); ex=PlanExecutor(e,'test-target',target_info={'id':'test-target','host':'127.0.0.1','port':1,'token':'x','host_app':'StandaloneCAD'})
    plan=direct_plan('create compression spring wire diameter=2mm mean diameter=20mm free length=40mm coils=8',{})
    calls=ex.validate_plan(plan)
    assert calls[0]['tool']=='cad_create_spring'

@pytest.mark.parametrize('kind,params',[
    ('helical',{'module':2,'teeth':24,'width_mm':10,'helix_angle_deg':20}),
    ('herringbone',{'module':2,'teeth':24,'width_mm':10,'helix_angle_deg':20}),
    ('ring',{'module':2,'teeth':48,'width_mm':10,'rim_width_mm':5,'helix_angle_deg':0}),
    ('bevel',{'module':2,'teeth':24,'width_mm':8,'cone_angle_deg':45,'helix_angle_deg':0}),
    ('rack',{'module':2,'length_mm':80,'width_mm':10,'height_mm':8,'helix_angle_deg':0}),
    ('worm',{'module':2,'lead_angle_deg':18,'thread_starts':2,'length_mm':50}),
    ('planetary',{'module':1,'sun_teeth':20,'planet_teeth':10,'width_mm':8,'rim_width_mm':4,'planet_count':3,'helix_angle_deg':0}),
])
def test_optional_gear_adapter_dispatch_contract_without_network(monkeypatch,kind,params):
    import types
    import cadquery as cq
    import standalonecad.core.mechanical_accelerator as ma

    class FakeGear:
        def __init__(self, *args, **kwargs):
            self.args=args; self.kwargs=kwargs
        def build(self, **kwargs):
            return cq.Solid.makeBox(5,5,5)

    fake=types.SimpleNamespace(
        SpurGear=FakeGear,HerringboneGear=FakeGear,RingGear=FakeGear,
        BevelGear=FakeGear,RackGear=FakeGear,Worm=FakeGear,PlanetaryGearset=FakeGear,
    )
    monkeypatch.setattr(ma,'_cq_gears_module',lambda:fake)
    shape,derived=ma.advanced_gear(kind,**params)
    assert shape.isValid()
    assert derived['gear_kind']==kind
