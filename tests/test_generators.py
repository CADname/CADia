import math
from standalonecad.core.engine import CadEngine
from standalonecad.mcp.tools import make_tools


def test_upstream_surface_stays_58_and_native_extensions_are_108():
    assert len(make_tools('inventor', extensions=False)) == 58
    assert len(make_tools('inventor', extensions=True)) == 108


def test_flange_coupling_generator_builds_valid_brep():
    e=CadEngine()
    r=e.execute('create_coupling',{
        'bore_diameter_mm':20,'hub_diameter_mm':40,'flange_diameter_mm':80,
        'hub_length_mm':30,'flange_thickness_mm':12,'bolt_circle_diameter_mm':60,
        'bolt_count':4,'bolt_hole_diameter_mm':7,'keyway_width_mm':6,'keyway_depth_mm':3,
        'replace':True,'name':'FlangeCoupling'
    })
    assert r['ok']
    assert r['generator']=='coupling'
    assert e.doc.shape is not None and e.doc.shape.Volume() > 0
    assert e.doc.features[-1].kind == 'accelerator_coupling'


def test_trapezoidal_lead_screw_and_nut_builds_valid_brep():
    e=CadEngine()
    r=e.execute('create_lead_screw_nut',{
        'major_diameter_mm':20,'pitch_mm':4,'screw_length_mm':50,'nut_length_mm':18,
        'starts':1,'thread_angle_deg':30,'nut_outer_diameter_mm':36,'clearance_mm':0.15,
        'right_handed':True,'replace':True,'name':'LeadScrewNut'
    })
    assert r['ok']
    assert r['generator']=='lead_screw_nut'
    assert math.isclose(r['lead_mm'],4.0,rel_tol=0,abs_tol=1e-9)
    assert e.doc.shape is not None and e.doc.shape.Volume() > 0


def test_worm_pair_is_native_and_does_not_require_cq_gears():
    e=CadEngine()
    r=e.execute('create_gear',{
        'kind':'worm_pair','module':2,'teeth':30,'width_mm':12,'length_mm':45,
        'lead_angle_deg':20,'thread_starts':1,'bore_diameter_mm':10,
        'pressure_angle_deg':20,'replace':True,'name':'WormPair'
    })
    assert r['ok']
    assert r['gear_kind']=='worm_pair'
    assert r['layout_approximation'] is True
    assert e.doc.shape is not None and e.doc.shape.Volume() > 0
