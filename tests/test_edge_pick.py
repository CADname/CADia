from pathlib import Path

import cadquery as cq

from standalonecad.core.render import sample_edge_points


def test_edge_sampler_works_with_cadquery_280_line_edges():
    shape = cq.Workplane("XY").box(20, 10, 6).val()
    pts = sample_edge_points(shape.Edges()[0], 0.08)
    assert len(pts) >= 2
    assert all(len(p) == 3 for p in pts)


def test_edge_sampler_closes_closed_circle_for_vtk_polyline():
    shape = cq.Workplane("XY").circle(5).extrude(2).val()
    circle = next(e for e in shape.Edges() if e.geomType() == "CIRCLE")
    pts = sample_edge_points(circle, 0.08)
    assert len(pts) >= 4
    assert pts[0] == pts[-1]


def test_qt_edge_path_no_longer_calls_missing_discretize_api():
    source = Path("src/standalonecad/ui/qt_app.py").read_text(encoding="utf-8")
    assert "sample_edge_points(edge, tolerance)" in source
    assert "edge.discretize" not in source
    assert "vtkCellPicker" in source
