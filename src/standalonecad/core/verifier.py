from __future__ import annotations

import math

# Commands that are expected to leave valid 3D part geometry when they succeed.
GEOMETRY_MUTATIONS = {
    "extrude", "revolve", "fillet", "chamfer", "hole",
    "circular_pattern", "rectangular_pattern",
    "extrude_advanced", "loft", "sweep", "shell", "mirror_body",
    "transform_body", "create_box", "create_cylinder", "create_cone",
    "create_sphere", "create_torus", "create_slot", "create_coil",
    "create_spur_gear", "create_gear", "create_shaft", "create_parallel_key", "create_bearing", "create_spring", "create_v_pulley", "create_coupling", "create_lead_screw_nut", "create_metric_hex_bolt", "create_metric_hex_nut",
    "create_metric_washer", "create_external_thread", "create_sheet_metal_base", "add_sheet_metal_flange",
}


def _finite(values):
    return all(math.isfinite(float(x)) for x in values)


def verify_after_command(engine, command: str) -> None:
    """Reject geometry states that a successful CAD mutation must never commit.

    This intentionally checks invariants, not design intent.  It therefore does not
    guess dimensions or simplify topology; it only prevents a failed/invalid OCCT
    result from being reported as a successful Inventor-compatible operation.
    """

    if command not in GEOMETRY_MUTATIONS:
        return
    doc = engine.doc
    if doc.doc_type != "part":
        return
    shape = doc.shape
    if shape is None:
        raise RuntimeError(f"{command} produced no model body")
    try:
        if hasattr(shape, "isValid") and not bool(shape.isValid()):
            raise RuntimeError(f"{command} produced an invalid B-Rep")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{command} B-Rep validity check failed: {exc}") from exc
    try:
        bb = shape.BoundingBox()
        values = [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax, shape.Volume(), shape.Area()]
    except Exception as exc:
        raise RuntimeError(f"{command} produced unreadable geometry: {exc}") from exc
    if not _finite(values):
        raise RuntimeError(f"{command} produced non-finite geometry")
    if bb.xlen < -1e-9 or bb.ylen < -1e-9 or bb.zlen < -1e-9:
        raise RuntimeError(f"{command} produced an invalid bounding box")
