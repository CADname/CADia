import math, tempfile
from pathlib import Path
from standalonecad.core.engine import CadEngine

def test_plate_hole_and_parameter_rebuild():
    e=CadEngine(); e.execute('new_part',{'name':'Plate'})
    e.execute('create_parameter',{'name':'T','expression':'8','unit':'mm'})
    e.execute('create_sketch',{'plane':'XY','name':'Base'})
    e.execute('draw_rectangle',{'sketch_name':'Base','x1':-40,'y1':-30,'x2':40,'y2':30})
    e.execute('close_sketch',{'sketch_name':'Base'})
    e.execute('extrude',{'sketch_name':'Base','distance_mm':'T','name':'BaseExtrude'})
    assert abs(e.doc.shape.Volume()-80*60*8) < 1e-4
    e.execute('hole',{'diameter_mm':10,'face_selector':'>Z','points':[[0,0]],'name':'Hole'})
    expected=80*60*8-math.pi*5*5*8
    assert abs(e.doc.shape.Volume()-expected) < 1e-2
    topo=e.execute('get_topology',{})
    assert topo['faces'] and topo['edges']

def test_step_export():
    e=CadEngine(); e.execute('create_sketch',{'plane':'XY','name':'S'}); e.execute('draw_circle',{'sketch_name':'S','radius_mm':5}); e.execute('close_sketch',{'sketch_name':'S'}); e.execute('extrude',{'sketch_name':'S','distance_mm':10})
    p=Path(tempfile.gettempdir())/'standalonecad_test.step'; e.execute('export_step',{'output_path':str(p)}); assert p.exists() and p.stat().st_size>100

def test_undo_redo():
    e=CadEngine(); e.execute('create_sketch',{'plane':'XY','name':'S'}); assert 'S' in e.doc.sketches; e.execute('undo',{}); assert 'S' not in e.doc.sketches; e.execute('redo',{}); assert 'S' in e.doc.sketches
