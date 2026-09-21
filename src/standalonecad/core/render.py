from __future__ import annotations
import base64, tempfile
from pathlib import Path
from cadquery import exporters



def sample_edge_points(edge, tolerance=0.08):
    """Return display/picking polyline points for a CadQuery Edge.

    CadQuery 2.8 Edge does not provide ``discretize``.  The supported
    1-D API is ``sample``; using it here keeps edge rendering/picking
    independent of UI toolkit code.  Closed edges explicitly repeat the
    first point so VTK receives a closed polyline rather than a nearly
    complete open loop.
    """
    tol=max(1.0e-4, float(tolerance))
    try:
        pts, _params = edge.sample(tol)
    except Exception:
        # Conservative fallback for unusual/degenerate curve adaptors.
        try:
            pts, _params = edge.sample(16)
        except Exception:
            pts=[v.Center() for v in edge.Vertices()]
    pts=list(pts or [])
    if len(pts) >= 2:
        try:
            closed=bool(edge.IsClosed())
        except Exception:
            closed=False
        if closed:
            a,b=pts[0],pts[-1]
            if (a-b).Length > 1.0e-9:
                pts.append(a)
    return [(float(v.x),float(v.y),float(v.z)) for v in pts]

def render_shape_png(shape,width=1280,height=720,orientation='iso_top_right',output_path=None):
    if shape is None: raise ValueError('No model to capture')
    width=max(16,min(4096,int(width))); height=max(16,min(4096,int(height)))
    from vtkmodules.vtkIOGeometry import vtkSTLReader
    from vtkmodules.vtkRenderingCore import vtkRenderer,vtkRenderWindow,vtkPolyDataMapper,vtkActor
    from vtkmodules.vtkIOImage import vtkPNGWriter
    from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
    stl=Path(tempfile.gettempdir())/'standalonecad_capture.stl'
    exporters.export(shape,str(stl),exportType='STL',tolerance=0.05,angularTolerance=0.15)
    r=vtkSTLReader(); r.SetFileName(str(stl)); r.Update()
    mapper=vtkPolyDataMapper(); mapper.SetInputConnection(r.GetOutputPort())
    actor=vtkActor(); actor.SetMapper(mapper)
    ren=vtkRenderer(); ren.AddActor(actor); ren.SetBackground(0.12,0.13,0.15)
    win=vtkRenderWindow(); win.SetOffScreenRendering(1); win.SetSize(width,height); win.AddRenderer(ren)
    cam=ren.GetActiveCamera(); set_camera_orientation(cam,orientation); ren.ResetCamera(); win.Render()
    filt=vtkWindowToImageFilter(); filt.SetInput(win); filt.SetInputBufferTypeToRGB(); filt.ReadFrontBufferOff(); filt.Update()
    if output_path:
        out=Path(output_path).expanduser().resolve(); out.parent.mkdir(parents=True,exist_ok=True)
    else: out=Path(tempfile.gettempdir())/'standalonecad_capture.png'
    w=vtkPNGWriter(); w.SetFileName(str(out)); w.SetInputConnection(filt.GetOutputPort()); w.Write()
    data=out.read_bytes()
    return {'ok':True,'path':str(out)} if output_path else {'ok':True,'mime_type':'image/png','width':width,'height':height,'base64':base64.b64encode(data).decode('ascii')}

def set_camera_orientation(cam,orientation):
    o=(orientation or 'iso_top_right').lower()
    table={
        'iso_top_right':((1,-1,1),(0,0,1)), 'iso_top_left':((-1,-1,1),(0,0,1)),
        'iso_bottom_right':((1,-1,-1),(0,0,1)), 'iso_bottom_left':((-1,-1,-1),(0,0,1)),
        'front':((0,-1,0),(0,0,1)), 'back':((0,1,0),(0,0,1)), 'top':((0,0,1),(0,1,0)),
        'bottom':((0,0,-1),(0,1,0)), 'left':((-1,0,0),(0,0,1)), 'right':((1,0,0),(0,0,1)),
    }
    pos,up=table.get(o,table['iso_top_right']); cam.SetPosition(*(x*10 for x in pos)); cam.SetFocalPoint(0,0,0); cam.SetViewUp(*up)
