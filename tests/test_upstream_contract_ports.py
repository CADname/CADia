import pytest
from standalonecad.core.upstream_contracts import validate_export_path,validate_rectangular_pattern,validate_circular_pattern,validate_face_selector


def test_export_policy(tmp_path):
    assert validate_export_path(str(tmp_path/'x.step')).is_absolute()
    with pytest.raises(ValueError): validate_export_path('relative.step')


def test_pattern_validation():
    validate_rectangular_pattern({'count1':2,'spacing_mm1':1})
    validate_rectangular_pattern({'count1':2,'spacing_mm1':1,'dir2':'Y Axis','count2':2,'spacing_mm2':2})
    with pytest.raises(ValueError): validate_rectangular_pattern({'count1':1,'spacing_mm1':1})
    with pytest.raises(ValueError): validate_rectangular_pattern({'count1':2,'spacing_mm1':0})
    with pytest.raises(ValueError): validate_rectangular_pattern({'count1':2,'spacing_mm1':1,'dir2':'Y Axis','count2':1,'spacing_mm2':2})
    validate_circular_pattern({'count':2})
    with pytest.raises(ValueError): validate_circular_pattern({'count':1})


def test_face_selector_validation():
    validate_face_selector({'kind':'planar','normal':'+Z','near_mm':[0,0,0]},False)
    validate_face_selector({'kind':'cylindrical','axis':'+Z','radius_mm':5,'radius_tol_mm':.1},True)
    with pytest.raises(ValueError): validate_face_selector({'kind':'planar'},False)
    with pytest.raises(ValueError): validate_face_selector({'kind':'planar','normal':'+Z','near_mm':[0,0]},False)
