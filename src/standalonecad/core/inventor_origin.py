from __future__ import annotations

"""Autodesk Inventor-style origin geometry naming and compatibility normalization.

The public ipt-mcp contract exposes origin/work geometry by *names* and its
create-sketch tool documents XY/XZ/YZ as the canonical short plane selectors.
Autodesk Inventor itself exposes the three base WorkPlanes in collection order
YZ, XZ, XY (items 1, 2, 3), with the browser names "YZ Plane", "XZ Plane",
and "XY Plane".  The system origin WorkPoint is item 1.

Only the canonical Inventor names are ever surfaced by StandaloneCAD.  The
compatibility aliases below are deliberately hidden and exist only to recover
planner spelling variants (for example OriginPlaneXY) without changing the
public contract or persisted display names.
"""

# Inventor WorkPlanes.Item order: 1=YZ, 2=XZ, 3=XY.
ORIGIN_PLANE_ORDER = ("YZ Plane", "XZ Plane", "XY Plane")
ORIGIN_PLANES = {
    "YZ Plane": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    "XZ Plane": ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "XY Plane": ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
}
ORIGIN_AXES = {
    "X Axis": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    "Y Axis": ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "Z Axis": ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
}
CENTER_POINT_NAME = "Center Point"
CENTER_POINT = {CENTER_POINT_NAME: ((0.0, 0.0, 0.0), None)}

# Public short forms used by ipt-mcp's create-sketch contract.
_PUBLIC_ALIASES = {
    "XY": "XY Plane",
    "XZ": "XZ Plane",
    "YZ": "YZ Plane",
    "X": "X Axis",
    "Y": "Y Axis",
    "Z": "Z Axis",
}

# Hidden compatibility aliases.  These are NOT returned by list_interfaces and
# are not documented as canonical values.  They only make failure recovery and
# planner execution robust to common invented spellings.
_COMPAT_ALIASES = {
    "originplanexy": "XY Plane",
    "originplanexz": "XZ Plane",
    "originplaneyz": "YZ Plane",
    "originaxisx": "X Axis",
    "originaxisy": "Y Axis",
    "originaxisz": "Z Axis",
    "xaxis": "X Axis",
    "yaxis": "Y Axis",
    "zaxis": "Z Axis",
    "originpoint": CENTER_POINT_NAME,
    "origin point": CENTER_POINT_NAME,
    "centerpoint": CENTER_POINT_NAME,
    "centrepoint": CENTER_POINT_NAME,
    "centre point": CENTER_POINT_NAME,
    # API-oriented shorthand occasionally emitted by planners.  Inventor code
    # uses these collection indexes, but StandaloneCAD still surfaces names.
    "workplanes.item(1)": "YZ Plane",
    "workplanes.item(2)": "XZ Plane",
    "workplanes.item(3)": "XY Plane",
    "workplanes(1)": "YZ Plane",
    "workplanes(2)": "XZ Plane",
    "workplanes(3)": "XY Plane",
    "workpoints.item(1)": CENTER_POINT_NAME,
    "workpoints(1)": CENTER_POINT_NAME,
}


def canonical_origin_name(ref, *, allow_compat: bool = True):
    """Return a canonical Inventor origin name when *ref* denotes one.

    Unknown/custom work-feature names are returned unchanged so renamed user
    WorkPlanes/WorkAxes keep their exact names, matching Inventor's name-based
    Item lookup semantics.
    """
    if not isinstance(ref, str):
        return ref
    raw = ref.strip()
    if raw in ORIGIN_PLANES or raw in ORIGIN_AXES or raw == CENTER_POINT_NAME:
        return raw
    upper = raw.upper()
    if upper in _PUBLIC_ALIASES:
        return _PUBLIC_ALIASES[upper]
    if allow_compat:
        compact = raw.casefold().strip()
        if compact in _COMPAT_ALIASES:
            return _COMPAT_ALIASES[compact]
    return raw


def canonical_plane_name(ref, *, allow_compat: bool = True):
    out = canonical_origin_name(ref, allow_compat=allow_compat)
    return out if out in ORIGIN_PLANES else out


def canonical_axis_name(ref, *, allow_compat: bool = True):
    out = canonical_origin_name(ref, allow_compat=allow_compat)
    return out if out in ORIGIN_AXES else out


def canonical_point_name(ref, *, allow_compat: bool = True):
    return canonical_origin_name(ref, allow_compat=allow_compat)


def is_origin_plane(ref) -> bool:
    return canonical_origin_name(ref) in ORIGIN_PLANES


def is_origin_axis(ref) -> bool:
    return canonical_origin_name(ref) in ORIGIN_AXES


def origin_interface_names() -> list[str]:
    # Surface only official Inventor-style names; compatibility aliases remain hidden.
    return [*ORIGIN_PLANE_ORDER, *ORIGIN_AXES.keys(), CENTER_POINT_NAME]
