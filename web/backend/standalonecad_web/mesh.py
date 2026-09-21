from __future__ import annotations

from typing import Any

from standalonecad.core.render import sample_edge_points
from standalonecad.core.topology import edge_records, face_records


def _flat_points(points) -> list[float]:
    out: list[float] = []
    for point in points or []:
        if hasattr(point, "x"):
            out.extend((float(point.x), float(point.y), float(point.z)))
        else:
            out.extend((float(point[0]), float(point[1]), float(point[2])))
    return out


def _mesh_face(face, record: dict[str, Any], tolerance: float, occurrence: str | None = None) -> dict[str, Any] | None:
    vertices, triangles = face.tessellate(tolerance)
    indices = [int(i) for tri in triangles or [] if len(tri) == 3 for i in tri]
    if not vertices or not indices:
        return None
    topology = {k: v for k, v in record.items() if k != "index"}
    return {
        "id": str(record["id"]),
        "occurrence_name": occurrence,
        "positions": _flat_points(vertices),
        "indices": indices,
        "topology": topology,
    }


def _mesh_edge(edge, record: dict[str, Any], tolerance: float, occurrence: str | None = None) -> dict[str, Any] | None:
    points = sample_edge_points(edge, tolerance)
    if len(points) < 2:
        return None
    topology = {k: v for k, v in record.items() if k != "index"}
    return {
        "id": str(record["id"]),
        "occurrence_name": occurrence,
        "positions": _flat_points(points),
        "topology": topology,
    }


def _shape_rows(shape, face_tolerance: float, edge_tolerance: float, include_edges: bool, occurrence: str | None = None):
    faces_out: list[dict[str, Any]] = []
    edges_out: list[dict[str, Any]] = []
    faces = list(shape.Faces())
    for record in face_records(shape):
        try:
            row = _mesh_face(faces[int(record["index"])], record, face_tolerance, occurrence)
            if row:
                faces_out.append(row)
        except Exception:
            continue
    if include_edges:
        edges = list(shape.Edges())
        for record in edge_records(shape):
            try:
                row = _mesh_edge(edges[int(record["index"])], record, edge_tolerance, occurrence)
                if row:
                    edges_out.append(row)
            except Exception:
                continue
    return faces_out, edges_out


def build_web_mesh(engine, face_tolerance: float, edge_tolerance: float, include_edges: bool = True) -> dict[str, Any]:
    """Create browser geometry while retaining the engine's persistent face/edge IDs."""
    with engine.lock:
        doc = engine.doc
        revision = int(engine.revision)
        if doc is None or doc.shape is None:
            return {
                "revision": revision,
                "document_type": doc.doc_type if doc else None,
                "faces": [],
                "edges": [],
                "bounds": None,
                "stats": {"faces": 0, "edges": 0, "volume_mm3": 0.0},
            }
        shape = doc.shape
        faces_out: list[dict[str, Any]] = []
        edges_out: list[dict[str, Any]] = []
        if doc.doc_type == "assembly":
            from standalonecad.core.assembly import transform_shape

            for name, occurrence in doc.occurrences.items():
                if occurrence.suppressed or occurrence.shape is None:
                    continue
                transformed = transform_shape(occurrence.shape, occurrence.position_mm, occurrence.rotation_deg_xyz)
                faces, edges = _shape_rows(transformed, face_tolerance, edge_tolerance, include_edges, str(name))
                faces_out.extend(faces)
                edges_out.extend(edges)
        else:
            faces_out, edges_out = _shape_rows(shape, face_tolerance, edge_tolerance, include_edges)
        bbox = shape.BoundingBox()
        try:
            volume = float(shape.Volume())
        except Exception:
            volume = 0.0
        return {
            "revision": revision,
            "document_type": doc.doc_type,
            "faces": faces_out,
            "edges": edges_out,
            "bounds": {
                "min": [float(bbox.xmin), float(bbox.ymin), float(bbox.zmin)],
                "max": [float(bbox.xmax), float(bbox.ymax), float(bbox.zmax)],
            },
            "stats": {"faces": len(faces_out), "edges": len(edges_out), "volume_mm3": volume},
        }
