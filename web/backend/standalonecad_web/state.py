from __future__ import annotations

from typing import Any


def _interface_names(interfaces: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    for group in ("imates", "work_planes", "work_axes", "work_points", "origin_geometry"):
        out.extend(str(x.get("name")) for x in (interfaces or {}).get(group, []) if x.get("name"))
    return sorted(set(out))


def planning_state(engine) -> dict[str, Any]:
    """Byte-semantics parity with Qt desktop `_planning_state`.

    Keep this deliberately *smaller* than public_state.  The planner must receive
    the same keys, values, ordering, topology limits, selection provenance, and
    document metadata as the desktop-compatible MCP/UI planning state.
    """
    with engine.lock:
        doc = engine.doc
        if doc is None:
            return {
                "document": None,
                "parameters": {},
                "sketches": [],
                "features": [],
                "selection": {},
                "faces": [],
                "edges": [],
                "origin_interfaces": ["YZ Plane", "XZ Plane", "XY Plane", "X Axis", "Y Axis", "Z Axis", "Center Point"],
                "open_documents": engine.document_sessions(),
            }

        try:
            params = {k: {"expression": v.expression, "unit": v.unit} for k, v in doc.parameters.items()}
        except Exception:
            params = {}
        try:
            topo = doc.topology()
        except Exception:
            topo = {"faces": [], "edges": [], "vertices": []}
        selection = dict(engine.selection)
        if selection.get("type") in ("face", "edge"):
            try:
                ref = selection.get("face_ref") if selection.get("type") == "face" else selection.get("edge_ref")
                prov = doc.selection_provenance(ref, selection.get("type")) if ref else None
                if prov:
                    selection["created_by_feature"] = prov
            except Exception:
                pass
        return {
            "document": {
                "title": doc.title,
                "type": doc.doc_type,
                "units": doc.units,
                "revision": engine.revision,
                "has_geometry": doc.shape is not None,
            },
            "parameters": params,
            "sketches": [
                {
                    "name": sk.name,
                    "plane": sk.plane,
                    "closed": sk.closed,
                    "entities": [{"id": e.tag, "kind": e.kind, "data": e.data} for e in sk.entities],
                    "dimensions": sk.dimensions,
                    "constraints": sk.constraints,
                }
                for sk in doc.sketches.values()
            ],
            "sketches3d": [dict(v) for v in getattr(doc, "sketches3d", {}).values()],
            "work_features": {
                "planes": [dict(v) for v in getattr(doc, "work_planes", {}).values()],
                "axes": [dict(v) for v in getattr(doc, "work_axes", {}).values()],
                "points": [dict(v) for v in getattr(doc, "work_points", {}).values()],
            },
            "features": [{"name": f.name, "kind": f.kind, "operation": f.operation, "params": f.params} for f in doc.features],
            "origin_interfaces": _interface_names(doc.list_interfaces()),
            "occurrences": [
                {
                    "name": o.name,
                    "grounded": o.grounded,
                    "position_mm": list(o.position_mm),
                    "rotation_deg_xyz": list(o.rotation_deg_xyz),
                    "interfaces": _interface_names(getattr(o, "interfaces", {})),
                }
                for o in getattr(doc, "occurrences", {}).values()
            ],
            "constraints": [vars(c).copy() for c in getattr(doc, "constraints", [])],
            "joints": [vars(j).copy() for j in getattr(doc, "joints", [])],
            "selection": selection,
            "faces": topo.get("faces", [])[:160],
            "edges": topo.get("edges", [])[:240],
            "open_documents": engine.document_sessions(),
        }


def public_state(engine) -> dict[str, Any]:
    """Browser/UI state.  UI-only metadata is added *after* planner parity state."""
    state = planning_state(engine)
    state.pop("faces", None)
    state.pop("edges", None)
    with engine.lock:
        doc = engine.doc
        if doc is None:
            state.setdefault("sketches3d", [])
            state.setdefault("work_features", {"planes": [], "axes": [], "points": []})
            state.setdefault("occurrences", [])
            state.setdefault("constraints", [])
            state.setdefault("joints", [])
            state["undo_available"] = False
            state["redo_available"] = False
        else:
            state["document"] = dict(state.get("document") or {})
            state["document"]["dirty"] = bool(doc.dirty)
            state["document"]["view_orientation"] = doc.view_orientation
            feature_by_name = {f.name: f for f in doc.features}
            for row in state.get("features", []):
                feature = feature_by_name.get(row.get("name"))
                row["suppressed"] = bool(getattr(feature, "suppressed", False)) if feature else False
            occurrence_by_name = {o.name: o for o in getattr(doc, "occurrences", {}).values()}
            for row in state.get("occurrences", []):
                occurrence = occurrence_by_name.get(row.get("name"))
                row["suppressed"] = bool(getattr(occurrence, "suppressed", False)) if occurrence else False
                row["reference_status"] = getattr(occurrence, "reference_status", "resolved") if occurrence else "resolved"
            state["undo_available"] = bool(engine._undo)
            state["redo_available"] = bool(engine._redo)
    state["open_documents"] = [
        {key: value for key, value in row.items() if key != "path"}
        for row in state.get("open_documents", [])
    ]
    return state
