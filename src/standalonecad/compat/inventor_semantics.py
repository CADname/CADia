from __future__ import annotations
from standalonecad.core.inventor_origin import canonical_plane_name

import copy
from typing import Any

from standalonecad.core.canonical import CanonicalOperation

# These are the 49 commands that the public bimwright/ipt-mcp default 58-tool
# surface actually round-trips to the Inventor add-in.  The remaining nine tools
# are server-side target/meta + ToolBaker lifecycle commands.
UPSTREAM_HOST_COMMANDS = frozenset({
    "health", "list_open_documents", "get_document_info",
    "new_part", "new_assembly", "open_document", "save_document", "close_document", "set_units", "set_material",
    "list_parameters", "get_parameter", "set_parameter", "create_parameter",
    "get_iproperty", "set_iproperty", "get_mass_properties",
    "create_sketch", "project_geometry", "draw_line", "draw_circle", "draw_rectangle", "draw_arc",
    "add_sketch_dimension", "add_sketch_constraint", "close_sketch",
    "extrude", "revolve", "fillet", "chamfer", "create_work_plane", "create_work_axis", "hole",
    "circular_pattern", "rectangular_pattern",
    "capture_view", "export_step", "export_stl", "export_dxf", "view_fit", "set_view_orientation",
    "place_occurrence", "add_constraint", "create_imate",
    "list_interfaces", "check_interference", "measure_min_distance", "get_assembly_bom", "list_constraints",
})

PART_ONLY = frozenset({
    "set_material", "create_sketch", "project_geometry", "draw_line", "draw_circle", "draw_rectangle", "draw_arc",
    "add_sketch_dimension", "add_sketch_constraint", "close_sketch",
    "extrude", "revolve", "fillet", "chamfer", "create_work_plane", "create_work_axis", "hole",
    "circular_pattern", "rectangular_pattern", "create_imate",
})
ASSEMBLY_ONLY = frozenset({
    "place_occurrence", "add_constraint", "check_interference", "measure_min_distance", "get_assembly_bom", "list_constraints",
})


def _need(p: dict[str, Any], key: str):
    if key not in p or p[key] is None or (isinstance(p[key], str) and not p[key].strip()):
        raise ValueError(f"{key} is required")
    return p[key]


def _positive(value, name: str) -> float:
    x = float(value)
    if x <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return x


class InventorSemanticAdapter:
    """Port the public ipt-mcp handler semantics onto the standalone canonical core.

    This is deliberately *not* a second CAD engine and not a fallback.  It performs
    the same role as bimwright's Inventor API handlers: validate/normalize the wire
    contract, then invoke one canonical modeling operation.  The canonical operation
    is executed by the existing OCCT/CadQuery history engine.
    """

    def __init__(self, engine, canonical_core):
        self.engine = engine
        self.core = canonical_core

    def _positive_model_value(self, value, name: str):
        """Validate a positive model value without destroying parameter expressions.

        The public ipt-mcp schemas send numeric millimetres, while StandaloneCAD also
        permits its existing parameter expressions when the core is called directly.
        Keeping the original expression preserves parametric rebuild semantics.
        """
        try:
            x=float(self.engine.doc.value(value))
        except Exception:
            x=float(value)
        if x <= 0:
            raise ValueError(f"{name} must be greater than 0")
        return value

    def dispatch(self, command: str, params: dict[str, Any] | None, *, read_only: bool = False):
        if command not in UPSTREAM_HOST_COMMANDS:
            raise KeyError(f"Not an upstream ipt-mcp host command: {command}")
        p = copy.deepcopy(params or {})
        self._document_guard(command)
        self._normalize(command, p)
        result = self.core.dispatch(CanonicalOperation(command, p, source="ipt-mcp"), read_only=read_only)
        self._post_validate(command, p, result)
        return self._response(command, p, result)

    def _document_guard(self, command: str) -> None:
        doc = self.engine.doc
        if doc is None:
            if command in {"health", "list_open_documents", "new_part", "new_assembly", "open_document"}:
                return
            raise ValueError(f"NO_ACTIVE_DOCUMENT: {command} requires an active document")
        typ = doc.doc_type
        if command in PART_ONLY and typ != "part":
            raise ValueError(f"WRONG_DOCUMENT_TYPE: {command} requires an active part document")
        if command in ASSEMBLY_ONLY and typ != "assembly":
            raise ValueError(f"WRONG_DOCUMENT_TYPE: {command} requires an active assembly document")

    def _normalize(self, command: str, p: dict[str, Any]) -> None:
        if command == "create_sketch":
            # ipt-mcp public contract: XY/XZ/YZ or an exact face/work-plane reference.
            # Canonical Inventor display names are used internally for origin geometry.
            p["plane"] = canonical_plane_name(p.get("plane") or "XY", allow_compat=True)
        elif command in {"draw_circle", "draw_arc"}:
            key = "radius" if "radius" in p else "radius_mm"
            self._positive_model_value(_need(p, key), key)
        elif command == "draw_rectangle":
            if float(_need(p, "x1")) == float(_need(p, "x2")) or float(_need(p, "y1")) == float(_need(p, "y2")):
                raise ValueError("rectangle requires non-zero width and height")
        elif command == "add_sketch_dimension":
            _need(p, "entity_id")
            p["value_mm"] = self._positive_model_value(_need(p, "value_mm"), "value_mm")
            sm = self.engine._sketch(p.get("sketch_name"))
            ent = self.engine._entity(sm, p["entity_id"])
            if ent.kind not in {"line", "circle", "arc"}:
                raise ValueError("dimension tool supports line, circle, arc")
        elif command == "add_sketch_constraint":
            typ = str(_need(p, "type")).strip().lower(); p["type"] = typ
            ids = list(_need(p, "entity_ids")); p["entity_ids"] = ids
            if not ids: raise ValueError("entity_ids[] is required and must be non-empty")
            if typ in {"horizontal", "vertical"} and len(ids) < 1: raise ValueError(f"constraint '{typ}' needs 1 entity_id")
            if typ in {"coincident", "parallel", "perpendicular", "tangent", "concentric", "equal", "collinear"} and len(ids) < 2:
                raise ValueError(f"constraint '{typ}' needs at least 2 entity_ids")
            if typ == "symmetric" and len(ids) < 3:
                raise ValueError("symmetric needs 3 entity_ids: [entityOne, entityTwo, symmetryLine]")
            sm = self.engine._sketch(p.get("sketch_name")); ents = [self.engine._entity(sm, i) for i in ids]
            if typ in {"horizontal", "vertical"} and ents[0].kind != "line":
                raise ValueError(f"{typ} constraint requires a sketch line")
            if typ in {"parallel", "perpendicular", "collinear"} and any(e.kind != "line" for e in ents[:2]):
                raise ValueError(f"{typ} constraint requires two sketch lines")
            # Upstream AddEqualLength is explicitly line-line only.
            if typ == "equal" and any(e.kind != "line" for e in ents[:2]):
                raise ValueError("equal constraint requires two sketch lines")
            if typ == "concentric" and any(e.kind not in {"circle", "arc"} for e in ents[:2]):
                raise ValueError("concentric constraint requires circles/arcs")
            if typ == "symmetric" and ents[2].kind != "line":
                raise ValueError("symmetric constraint requires a sketch line as the third entity")
        elif command == "extrude":
            _need(p, "sketch_name"); p["distance_mm"] = self._positive_model_value(_need(p, "distance_mm"), "distance_mm")
            p["operation"] = str(p.get("operation") or "join").lower(); p["direction"] = str(p.get("direction") or "positive").lower()
            if p["operation"] not in {"join", "cut", "intersect"}: raise ValueError("operation must be join|cut|intersect")
            if p["direction"] not in {"positive", "negative", "symmetric"}: raise ValueError("direction must be positive|negative|symmetric")
        elif command == "revolve":
            _need(p, "sketch_name"); _need(p, "axis_id")
            # Upstream RevolveHandler deliberately accepts zero and signed angles:
            # 0 or ±360 => full revolve; a negative partial angle means the same
            # magnitude in the negative extent direction.  Preserve the signed
            # input instead of applying the generic positive-length validator.
            raw_angle = _need(p, "angle_deg")
            try:
                angle = float(self.engine.doc.value(raw_angle))
            except Exception:
                angle = float(raw_angle)
            import math as _math
            if not _math.isfinite(angle):
                raise ValueError("angle_deg must be finite")
            p["angle_deg"] = raw_angle
            p["operation"] = str(p.get("operation") or "join").lower()
            if p["operation"] not in {"join", "cut", "intersect"}: raise ValueError("operation must be join|cut|intersect")
        elif command in {"fillet", "chamfer"}:
            ids = list(_need(p, "edge_ids"))
            if not ids: raise ValueError("edge_ids[] must be non-empty")
            key = "radius_mm" if command == "fillet" else "distance_mm"
            p[key] = self._positive_model_value(_need(p, key), key)
        elif command == "create_work_plane":
            typ = str(_need(p, "type")).lower(); refs = list(_need(p, "refs")); p["type"],p["refs"] = typ,refs
            arity = {"offset":1, "three_points":3, "tangent":2}
            if typ not in arity: raise ValueError("type must be offset|three_points|tangent")
            if len(refs) != arity[typ]: raise ValueError(f"{typ} work plane requires {arity[typ]} refs")
            if typ == "offset": p["offset_mm"] = float(p.get("offset_mm") or 0.0)
        elif command == "create_work_axis":
            typ = str(_need(p, "type")).lower(); refs = list(_need(p, "refs")); p["type"],p["refs"] = typ,refs
            arity = {"two_points":2, "edge":1, "plane_intersection":2, "normal_to_face_through_point":2}
            if typ not in arity: raise ValueError("type must be two_points|edge|plane_intersection|normal_to_face_through_point")
            if len(refs) != arity[typ]: raise ValueError(f"{typ} work axis requires {arity[typ]} refs")
        elif command == "hole":
            p["diameter_mm"] = self._positive_model_value(_need(p, "diameter_mm"), "diameter_mm")
            # The public upstream contract uses 3D points_mm.  Keep the existing
            # StandaloneCAD direct-call 2D points alias as a backwards-compatible
            # superset; the canonical core maps those points onto the selected face.
            raw_pts = p.get("points_mm") if p.get("points_mm") is not None else p.get("points")
            if raw_pts is None: raise ValueError("points_mm is required")
            pts=list(raw_pts)
            if not pts: raise ValueError("points_mm[] must be non-empty")
            if p.get("points_mm") is not None:
                if any(not isinstance(q,(list,tuple)) or len(q)!=3 for q in pts): raise ValueError("each points_mm item must be [x,y,z]")
            elif any(not isinstance(q,(list,tuple)) or len(q) not in {2,3} for q in pts):
                raise ValueError("each points item must be [u,v] or [x,y,z]")
            kind = str(p.get("kind") or "drilled").lower(); p["kind"] = kind
            if kind not in {"drilled", "counterbore", "countersink"}: raise ValueError("kind must be drilled|counterbore|countersink")
            through = bool(p.get("through", True)); p["through"] = through
            if through and p.get("depth_mm") is not None: raise ValueError("through=true and depth_mm are mutually exclusive")
            if not through: p["depth_mm"] = self._positive_model_value(_need(p,"depth_mm"), "depth_mm")
            if kind == "counterbore":
                p["cbore_diameter_mm"] = self._positive_model_value(_need(p,"cbore_diameter_mm"), "cbore_diameter_mm")
                p["cbore_depth_mm"] = self._positive_model_value(_need(p,"cbore_depth_mm"), "cbore_depth_mm")
            if kind == "countersink":
                p["csink_diameter_mm"] = self._positive_model_value(_need(p,"csink_diameter_mm"), "csink_diameter_mm")
                p["csink_angle_deg"] = self._positive_model_value(p.get("csink_angle_deg",82.0), "csink_angle_deg")
        elif command == "circular_pattern":
            if int(_need(p,"count")) < 2: raise ValueError("count must be >= 2")
            if not list(_need(p,"feature_names")): raise ValueError("feature_names[] must be non-empty")
            _need(p,"axis"); p["angle_deg"] = float(p.get("angle_deg",360.0)); p["natural_direction"] = bool(p.get("natural_direction",True))
        elif command == "rectangular_pattern":
            if int(_need(p,"count1")) < 1: raise ValueError("count1 must be >= 1")
            if not list(_need(p,"feature_names")): raise ValueError("feature_names[] must be non-empty")
            _need(p,"dir1"); float(_need(p,"spacing_mm1"))
            if p.get("dir2") is not None and int(p.get("count2") or 1) < 1: raise ValueError("count2 must be >= 1")
        elif command == "add_constraint":
            typ = str(_need(p,"type")).lower(); p["type"] = typ
            if typ not in {"mate","flush","insert","angle"}: raise ValueError("type must be mate|flush|insert|angle")
            if typ == "angle" and p.get("angle_deg") is None: raise ValueError("angle constraint requires angle_deg")
            _need(p,"a_ref"); _need(p,"b_ref")
        elif command == "create_imate":
            _need(p,"name"); typ=str(_need(p,"type")).lower(); p["type"] = typ
            if typ not in {"mate","flush","insert"}: raise ValueError("iMate type must be mate|flush|insert")
            _need(p,"selector")

    def _post_validate(self, command: str, p: dict[str, Any], result: Any) -> None:
        # Treat a sick sketch/assembly result like Inventor API failure: the outer
        # transaction will restore the exact previous document state.
        if command in {"add_sketch_dimension", "add_sketch_constraint"}:
            sm = self.engine._sketch(p.get("sketch_name"))
            sick = [x for x in self.engine.doc.sketch_constraint_health(sm) if x.get("health") != "up_to_date"]
            if sick:
                raise ValueError("Sketch constraint conflict/overconstraint: " + "; ".join(f"{x['type']}:{x.get('reason') or x.get('error')}" for x in sick[:4]))
        if command == "add_constraint" and isinstance(result, dict) and result.get("health") not in {None,"up_to_date"}:
            raise ValueError(f"Assembly constraint health is {result.get('health')}")

    def _response(self, command: str, p: dict[str, Any], result: Any):
        if not isinstance(result, dict):
            return result
        out = dict(result)
        # Match high-value fields returned by the public upstream handlers.  Extra
        # standalone diagnostics are preserved because they are backwards compatible.
        if command == "add_sketch_dimension":
            sm = self.engine._sketch(p.get("sketch_name")); out.update(sketch_name=sm.name, value_mm=float(self.engine.doc.value(p["value_mm"])))
        elif command == "add_sketch_constraint":
            sm = self.engine._sketch(p.get("sketch_name")); out.update(sketch_name=sm.name, constraint_type=p["type"])
        elif command == "extrude":
            out.update(operation=p["operation"], distance_mm=float(self.engine.doc.value(p["distance_mm"])), volume_mm3=float(self.engine.doc.shape.Volume()))
        elif command == "revolve":
            out.update(operation=p["operation"], angle_deg=float(self.engine.doc.value(p["angle_deg"])), volume_mm3=float(self.engine.doc.shape.Volume()))
        elif command == "fillet":
            out.update(radius_mm=float(self.engine.doc.value(p["radius_mm"])), volume_mm3=float(self.engine.doc.shape.Volume()))
        elif command == "chamfer":
            out.update(distance_mm=float(self.engine.doc.value(p["distance_mm"])), volume_mm3=float(self.engine.doc.shape.Volume()))
        return out
