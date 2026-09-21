from __future__ import annotations

import math
from pathlib import Path

import pytest

from standalonecad.core.engine import CadEngine
from standalonecad.bridge.host import CadHost
from standalonecad.core.gears import _half_tooth_angle_at_radius, involute_spur_gear
from standalonecad.core.render import set_camera_orientation
from standalonecad.planning import NON_REVISION_COMMANDS, PlanExecutor, direct_plan


def test_external_spur_gear_involute_narrows_toward_addendum():
    m=2.0; z=24; alpha=math.radians(20.0)
    rp=m*z/2.0; rb=rp*math.cos(alpha); ra=rp+m
    pitch=_half_tooth_angle_at_radius(rp,pitch_radius=rp,base_radius=rb,teeth=z)
    tip=_half_tooth_angle_at_radius(ra,pitch_radius=rp,base_radius=rb,teeth=z)
    assert math.degrees(pitch) == pytest.approx(3.75,abs=1e-10)
    assert math.degrees(tip) == pytest.approx(1.576846918,abs=1e-6)
    assert 0 < tip < pitch

    shape,meta=involute_spur_gear(m,z,10.0,8.0,20.0,0.0)
    assert shape.isValid()
    assert len(shape.Solids()) == 1
    assert meta['pitch_diameter_mm'] == pytest.approx(48.0)
    assert meta['outside_diameter_mm'] == pytest.approx(52.0)
    assert meta['root_diameter_mm'] == pytest.approx(43.0)
    assert meta['tip_half_tooth_angle_deg'] < meta['pitch_half_tooth_angle_deg']
    assert meta['tip_tooth_thickness_mm'] < meta['pitch_tooth_thickness_mm']


def test_new_part_adds_document_and_preserves_existing_geometry():
    e=CadEngine()
    first_id=e.document_sessions()[0]['id']
    e.execute('create_box',{'length_mm':10,'width_mm':20,'height_mm':30,'replace':False,'name':'Original'})
    first_volume=float(e.doc.shape.Volume())
    e.execute('new_part',{'name':'Second'})
    docs=e.document_sessions()
    assert len(docs)==2
    assert sum(bool(x['active']) for x in docs)==1
    assert e.doc.title=='Second'
    assert e.doc.shape is None

    e.activate_document(first_id)
    assert e.doc.title=='Original'
    assert float(e.doc.shape.Volume()) == pytest.approx(first_volume)
    listing=e.execute('list_open_documents',{})['documents']
    assert len(listing)==2
    assert sum(bool(x['active']) for x in listing)==1


def test_close_last_document_leaves_true_no_document_state():
    e=CadEngine()
    r=e.execute('close_document',{'save':False})
    assert r['document_open'] is False
    assert e.doc is None
    assert e.execute('list_open_documents',{})['documents']==[]
    assert e.execute('health',{})['document_open'] is False
    e.execute('new_assembly',{'name':'Assembly A'})
    assert e.doc is not None and e.doc.doc_type=='assembly'


def test_direct_generator_does_not_silently_erase_current_model():
    state={'document':{'type':'part','has_geometry':True},'selection':{}}
    plan=direct_plan('create spur gear module=2 teeth=24 pressure angle=20 face width=10mm bore diameter=8mm',state)
    assert [x['tool'] for x in plan['calls']][:2] == ['inventor_new_part','cad_create_spur_gear']
    assert plan['calls'][-1]['arguments']['replace'] is False

    explicit=direct_plan('clear current model and create spur gear module=2 teeth=24 pressure angle=20 face width=10mm bore diameter=8mm',state)
    assert [x['tool'] for x in explicit['calls']] == ['cad_create_spur_gear']
    assert explicit['calls'][0]['arguments']['replace'] is True

    blank={'document':{'type':'part','has_geometry':False},'selection':{}}
    blank_plan=direct_plan('create spur gear module=2 teeth=24 pressure angle=20 face width=10mm bore diameter=8mm',blank)
    assert [x['tool'] for x in blank_plan['calls']] == ['cad_create_spur_gear']
    assert blank_plan['calls'][0]['arguments']['replace'] is False


def test_failed_plan_restores_multi_document_workspace():
    e=CadEngine()
    e.execute('create_box',{'length_mm':10,'width_mm':10,'height_mm':10,'replace':False,'name':'KeepMe'})
    original_sessions=e.document_sessions()
    original_volume=float(e.doc.shape.Volume())
    host=CadHost(e); host.start()
    try:
        ex=PlanExecutor(e,host.info['target_id'],target_info=host.info)
        plan={'calls':[
            {'tool':'inventor_new_part','arguments':{}},
            {'tool':'inventor_extrude','arguments':{'sketchName':'SketchThatDoesNotExist','distance':10.0}},
        ]}
        result=ex.execute(plan)
        assert result.ok is False
        assert len(e.document_sessions())==len(original_sessions)
        assert e.doc.title=='KeepMe'
        assert float(e.doc.shape.Volume()) == pytest.approx(original_volume)
    finally:
        host.close()


def test_view_commands_do_not_invalidate_geometry_revision_and_camera_changes():
    e=CadEngine()
    e.execute('create_box',{'length_mm':10,'width_mm':20,'height_mm':30,'replace':False,'name':'Box'})
    before=e.revision
    e.execute('set_view_orientation',{'orientation':'front','fit':True})
    assert e.revision==before
    assert e.doc.view_orientation=='front'
    assert e.doc.view_fit is True
    e.execute('view_fit',{})
    assert e.revision==before
    assert 'set_view_orientation' in NON_REVISION_COMMANDS
    assert 'view_fit' in NON_REVISION_COMMANDS

    vtk=pytest.importorskip('vtkmodules.vtkRenderingCore')
    cam=vtk.vtkCamera()
    set_camera_orientation(cam,'front'); front=tuple(cam.GetDirectionOfProjection())
    set_camera_orientation(cam,'top'); top=tuple(cam.GetDirectionOfProjection())
    set_camera_orientation(cam,'right'); right=tuple(cam.GetDirectionOfProjection())
    assert front != top != right


def test_qt_viewport_source_uses_real_edge_picker_clean_faces_and_progress():
    source=Path('src/standalonecad/ui/qt_app.py').read_text(encoding='utf-8')
    assert 'vtkCellPicker' in source
    assert 'vtkPropPicker' not in source
    assert 'SetEdgeVisibility(False)' in source
    assert 'QProgressBar' in source
    assert 'Estimated progress' in source
    assert 'CADia document (*.scad.json)' in source or 'StandaloneCAD document (*.scad.json)' in source
    assert 'StandaloneCAD project (*.scad.json)' not in source
    assert 'self.document_combo' in source


def test_plan_executor_emits_stage_progress_percentages():
    e=CadEngine()
    host=CadHost(e); host.start()
    try:
        ex=PlanExecutor(e,host.info['target_id'],target_info=host.info)
        events=[]
        plan={'calls':[{'tool':'cad_create_box','arguments':{'length_mm':10,'width_mm':10,'height_mm':10,'replace':False,'name':'Box'}}]}
        result=ex.execute(plan,on_event=lambda kind,payload:events.append((kind,payload)),progress_span=(25,70))
        assert result.ok
        percents=[p['percent'] for k,p in events if k=='progress' and isinstance(p,dict) and 'percent' in p]
        assert percents
        assert min(percents)>=25
        assert max(percents)>=70
        assert percents==sorted(percents)
    finally:
        host.close()
