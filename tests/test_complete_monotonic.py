from pathlib import Path
import math
import pytest

from standalonecad.core.engine import CadEngine
from standalonecad.core.parameter_graph import dependent_closure, earliest_affected_feature
from standalonecad.mcp.tools import make_tools


def test_upstream_surface_is_still_exact_58_and_new_range_is_native_only():
    assert len(make_tools('inventor')) == 58
    ext = make_tools('cad', extensions=True)
    assert len(ext) == 108
    names = {x['name'] for x in ext}
    assert 'cad_create_sheet_metal_base' in names
    assert 'cad_add_sheet_metal_flange' in names
    assert 'inventor_create_sheet_metal_base' not in {x['name'] for x in make_tools('inventor')}


def test_normal_part_path_does_not_become_sheet_metal_implicitly(tmp_path):
    e = CadEngine()
    e.execute('create_box', {'length_mm': 30, 'width_mm': 20, 'height_mm': 2, 'replace': True})
    assert e.doc.shape.isValid()
    assert len(e.doc.shape.Solids()) == 1
    assert e.doc.sheet_metal is None
    out = tmp_path / 'not-flat.dxf'
    with pytest.raises(ValueError, match='WRONG_DOCUMENT_TYPE.*real sheet-metal flat pattern'):
        e.execute('export_dxf', {'output_path': str(out), 'source': 'flat_pattern'})
    assert not out.exists()


@pytest.mark.parametrize('edge', ['top','bottom','left','right'])
@pytest.mark.parametrize('angle', [90.0, -90.0])
def test_new_sheet_metal_range_is_valid_single_solid_and_exports_flat_pattern(tmp_path, edge, angle):
    e = CadEngine()
    e.execute('create_sheet_metal_base', {
        'width_mm': 100, 'height_mm': 60, 'thickness_mm': 2,
        'bend_radius_mm': 2, 'k_factor': 0.44, 'replace': True,
    })
    e.execute('add_sheet_metal_flange', {'edge': edge, 'length_mm': 20, 'angle_deg': angle})
    assert e.doc.shape.isValid()
    assert len(e.doc.shape.Solids()) == 1
    assert e.doc.has_flat_pattern()
    path = tmp_path / f'{edge}-{angle:g}.dxf'
    result = e.execute('export_dxf', {'output_path': str(path), 'source': 'flat_pattern'})
    assert path.exists() and path.stat().st_size > 100
    assert result['source'] == 'flat_pattern'
    bend = result['flat_pattern']['bends'][0]
    expected = math.radians(90.0) * (2.0 + 0.44 * 2.0)
    assert bend['bend_allowance_mm'] == pytest.approx(expected)


def test_parameter_dependency_graph_is_conservative_and_transitive():
    e = CadEngine(); e.execute('new_part', {})
    e.execute('create_parameter', {'name':'A','expression':'10','unit':'mm'})
    e.execute('create_parameter', {'name':'B','expression':'A*2','unit':'mm'})
    e.execute('create_parameter', {'name':'C','expression':'B+5','unit':'mm'})
    assert dependent_closure(e.doc.parameters, 'A') == {'A','B','C'}
    # No geometry references any of these yet, so a parameter-only change has no
    # reason to invalidate a verified body.
    assert earliest_affected_feature(e.doc, 'A') is None


def test_existing_successful_box_route_remains_plain_part_and_same_geometry():
    e = CadEngine()
    r = e.execute('create_box', {'length_mm':80,'width_mm':60,'height_mm':8,'replace':True})
    assert r['ok'] is True
    assert e.doc.sheet_metal is None
    assert e.doc.shape.isValid()
    assert len(e.doc.shape.Solids()) == 1
    assert e.doc.shape.Volume() == pytest.approx(80*60*8)


def test_no_external_original_ipt_gateway_files_exist():
    root = Path(__file__).resolve().parents[1]
    forbidden = [
        'src/standalonecad/original_gateway.py',
        'src/standalonecad/bridge/ipt_compat.py',
        'VERIFY_UPSTREAM_RUNTIME.bat',
        'scripts/prepare_upstream_ipt_mcp.py',
        'scripts/check_upstream_server.py',
        'scripts/verify_upstream_runtime.py',
    ]
    assert all(not (root / x).exists() for x in forbidden)
