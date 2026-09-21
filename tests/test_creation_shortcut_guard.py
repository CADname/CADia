from standalonecad.planning import direct_plan


def test_creation_shortcuts_do_not_hijack_edit_requests():
    assert direct_plan('Edit the 80x60x8 plate length to 100 mm', {}) is None
    assert direct_plan('Edit the module 2, 25 tooth, 20 mm face width, 15 mm bore spur gear length', {}) is None
    assert direct_plan('Change the M12 hex bolt overall length from 50 mm to 60 mm', {}) is None


def test_existing_deterministic_creation_routes_are_preserved():
    plate = direct_plan('Create an 80x60x8 plate', {})
    assert plate and plate['calls'][-1]['tool'] == 'cad_create_box'

    gear = direct_plan('create spur gear module=2 teeth=25 face width=20mm bore diameter=15mm', {})
    assert gear and gear['calls'][-1]['tool'] == 'cad_create_spur_gear'

    bolt = direct_plan('create M12 hex bolt overall length=50mm thread length=30mm', {})
    assert bolt and bolt['calls'][-1]['tool'] == 'cad_create_metric_hex_bolt'


def test_explicit_replace_current_still_uses_deterministic_creation():
    state={'document':{'type':'part','has_geometry':True},'selection':{}}
    plan=direct_plan('clear current model and Create an 80x60x8 plate', state)
    assert plan and [c['tool'] for c in plan['calls']] == ['cad_create_box']
    assert plan['calls'][0]['arguments']['replace'] is True


def test_mounting_plate_prompt_keeps_corner_holes_in_fast_path():
    plan = direct_plan('Create an 80 x 60 x 8 mm mounting plate with four 6 mm holes positioned 10 mm from each corner.', {})
    assert plan is not None
    assert [c['tool'] for c in plan['calls']] == ['cad_create_box', 'inventor_hole']
    box = plan['calls'][0]['arguments']
    hole = plan['calls'][1]['arguments']
    assert box['length_mm'] == 80.0
    assert box['width_mm'] == 60.0
    assert box['height_mm'] == 8.0
    assert hole['diameter_mm'] == 6.0
    assert len(hole['points_mm']) == 4
    assert hole['points_mm'][0] == [10.0, 10.0, 8.0]
    assert hole['points_mm'][-1] == [70.0, 50.0, 8.0]
    assert hole['through'] is True
