from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cadquery as cq


# ISO metric coarse pitch defaults (mm). Values are standard coarse-series defaults
# commonly used by ISO 261/262 fasteners. Explicit user pitch always wins.
METRIC_COARSE_PITCH: dict[float, float] = {
    1.6: 0.35, 2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8,
    6.0: 1.0, 8.0: 1.25, 10.0: 1.5, 12.0: 1.75, 14.0: 2.0, 16.0: 2.0,
    18.0: 2.5, 20.0: 2.5, 22.0: 2.5, 24.0: 3.0, 27.0: 3.0, 30.0: 3.5,
    33.0: 3.5, 36.0: 4.0, 39.0: 4.0, 42.0: 4.5, 45.0: 4.5, 48.0: 5.0,
}

# ISO 4017-style common hex-head defaults. These are used only when the user
# asks for a standard bolt and does not give the dimensions explicitly.
HEX_HEAD_DEFAULTS: dict[float, tuple[float, float]] = {
    3.0: (5.5, 2.0), 4.0: (7.0, 2.8), 5.0: (8.0, 3.5), 6.0: (10.0, 4.0),
    8.0: (13.0, 5.3), 10.0: (16.0, 6.4), 12.0: (18.0, 7.5), 14.0: (21.0, 8.8),
    16.0: (24.0, 10.0), 18.0: (27.0, 11.5), 20.0: (30.0, 12.5), 22.0: (34.0, 14.0),
    24.0: (36.0, 15.0), 27.0: (41.0, 17.0), 30.0: (46.0, 18.7),
}

# ISO 4032-style common hex-nut defaults: across-flats, nut height.
HEX_NUT_DEFAULTS: dict[float, tuple[float, float]] = {
    3.0: (5.5, 2.4), 4.0: (7.0, 3.2), 5.0: (8.0, 4.0), 6.0: (10.0, 5.0),
    8.0: (13.0, 6.5), 10.0: (16.0, 8.0), 12.0: (18.0, 10.0), 14.0: (21.0, 11.0),
    16.0: (24.0, 13.0), 18.0: (27.0, 15.0), 20.0: (30.0, 16.0), 22.0: (34.0, 18.0),
    24.0: (36.0, 19.0), 27.0: (41.0, 22.0), 30.0: (46.0, 24.0),
}

# ISO 7089-style normal-series washer defaults: inner diameter, outer diameter, thickness.
WASHER_DEFAULTS: dict[float, tuple[float, float, float]] = {
    3.0: (3.2, 7.0, 0.5), 4.0: (4.3, 9.0, 0.8), 5.0: (5.3, 10.0, 1.0),
    6.0: (6.4, 12.0, 1.6), 8.0: (8.4, 16.0, 1.6), 10.0: (10.5, 20.0, 2.0),
    12.0: (13.0, 24.0, 2.5), 14.0: (15.0, 28.0, 2.5), 16.0: (17.0, 30.0, 3.0),
    18.0: (19.0, 34.0, 3.0), 20.0: (21.0, 37.0, 3.0), 22.0: (23.0, 39.0, 3.0),
    24.0: (25.0, 44.0, 4.0), 27.0: (28.0, 50.0, 4.0), 30.0: (31.0, 56.0, 4.0),
}


def _nearest_key(table: dict[float, Any], diameter: float) -> float:
    d = float(diameter)
    for k in table:
        if abs(k - d) < 1e-9:
            return k
    raise ValueError(f"No built-in ISO default for M{d:g}; provide the missing dimensions explicitly")


def metric_pitch(diameter: float, pitch: float | None = None) -> float:
    if pitch is not None:
        p = float(pitch)
        if p <= 0:
            raise ValueError("pitch must be > 0")
        return p
    return METRIC_COARSE_PITCH[_nearest_key(METRIC_COARSE_PITCH, diameter)]


def _hex_prism(across_flats: float, height: float, z0: float = 0.0) -> cq.Shape:
    af = float(across_flats); h = float(height)
    if af <= 0 or h <= 0:
        raise ValueError("hex dimensions must be > 0")
    # CadQuery polygon diameter is across corners. Across flats = D*cos(30°).
    across_corners = af / math.cos(math.radians(30.0))
    shape = cq.Workplane("XY").polygon(6, across_corners).extrude(h).val()
    return shape.translate((0, 0, z0)) if z0 else shape


def _thread_depth_iso60(pitch: float) -> float:
    # Practical radial depth for a 60° ISO metric external thread. This intentionally
    # models a truncated V thread rather than a razor-sharp theoretical fundamental triangle.
    return 0.61343 * float(pitch)


def external_metric_thread(
    major_diameter: float,
    pitch: float,
    length: float,
    z0: float = 0.0,
    right_handed: bool = True,
    crest_flat_fraction: float = 0.125,
) -> tuple[cq.Shape, dict[str, float]]:
    """Generate a real helical external 60° metric thread as B-Rep geometry.

    The returned solid includes the root cylinder plus a swept truncated-V ridge.
    It is deterministic and independent of a proprietary CAD host.
    """
    d = float(major_diameter); p = float(pitch); L = float(length)
    if d <= 0 or p <= 0 or L <= 0:
        raise ValueError("major_diameter, pitch and length must be > 0")
    depth = min(_thread_depth_iso60(p), d * 0.22)
    r_major = d / 2.0
    r_minor = max(0.05, r_major - depth)
    radial_overlap = min(0.08, max(0.01, depth * 0.08))
    axial_overlap = min(0.10, max(0.02, p * 0.05)) if z0 > 0 else 0.0
    root = cq.Solid.makeCylinder(r_minor + radial_overlap/2.0, L + axial_overlap, (0, 0, z0-axial_overlap), (0, 0, 1))

    # Start the helix at the minor-radius surface. A trapezoidal section avoids a
    # zero-area/knife-edge crest and is more robust under booleans than a pure triangle.
    crest = max(0.02 * p, min(0.35 * p, float(crest_flat_fraction) * p))
    half_root = min(0.45 * p, p * 0.36)
    half_crest = crest / 2.0
    plane = cq.Workplane("XZ", origin=(0, 0, z0)).center(r_minor, 0)
    profile = (
        plane.polyline([
            (-radial_overlap, -half_root),
            (depth, -half_crest),
            (depth, half_crest),
            (-radial_overlap, half_root),
        ]).close().wire().val()
    )
    helix = cq.Wire.makeHelix(p, L, r_minor, center=(0, 0, z0), dir=(0, 0, 1), lefthand=not right_handed)
    ridge = cq.Solid.sweep(profile, [], helix, makeSolid=True, isFrenet=True)
    # Clip the run-out created by the finite profile so modeled thread length remains exact.
    clip = cq.Solid.makeCylinder(r_major + 0.1, L, (0, 0, z0), (0, 0, 1))
    ridge = cq.Workplane(obj=ridge).intersect(clip).val()
    try:
        candidate = cq.Workplane(obj=root).union(ridge).val()
        if not candidate.Solids():
            raise ValueError('empty thread union')
        shape = candidate
    except Exception:
        shape = cq.Compound.makeCompound([root, ridge])
    return shape, {
        "major_diameter_mm": d,
        "minor_diameter_mm": 2 * r_minor,
        "pitch_mm": p,
        "thread_length_mm": L,
        "thread_depth_mm": depth,
    }


def internal_metric_thread_cut(
    major_diameter: float,
    pitch: float,
    length: float,
    z0: float = 0.0,
    right_handed: bool = True,
) -> tuple[cq.Shape, dict[str, float]]:
    """Return a cutting tool for a real helical internal 60° thread."""
    d = float(major_diameter); p = float(pitch); L = float(length)
    if d <= 0 or p <= 0 or L <= 0:
        raise ValueError("major_diameter, pitch and length must be > 0")
    # Basic-profile internal minor diameter approximation D1 = D - 1.08253 P.
    r_major = d / 2.0
    r_minor = max(0.05, (d - 1.08253 * p) / 2.0)
    depth = r_major - r_minor
    radial_overlap = min(0.08, max(0.01, depth * 0.08))
    core = cq.Solid.makeCylinder(r_minor, L, (0, 0, z0), (0, 0, 1))
    half_root = min(0.45 * p, p * 0.36)
    half_crest = max(0.02 * p, p * 0.0625)
    profile = (
        cq.Workplane("XZ", origin=(0, 0, z0)).center(r_minor, 0)
        .polyline([(-radial_overlap, -half_root), (depth, -half_crest), (depth, half_crest), (-radial_overlap, half_root)])
        .close().wire().val()
    )
    helix = cq.Wire.makeHelix(p, L, r_minor, center=(0, 0, z0), dir=(0, 0, 1), lefthand=not right_handed)
    groove = cq.Solid.sweep(profile, [], helix, makeSolid=True, isFrenet=True)
    # The helical profile overlaps the core cylinder slightly so OCC can fuse both
    # into one connected cutter. Without this tiny overlap, tangent-only geometry can
    # make the helical flank disappear from a boolean cut on some kernels.
    try:
        cutter = cq.Workplane(obj=core).union(groove).val()
        if not cutter.Solids() or cutter.Volume() <= core.Volume() + 1e-6:
            raise ValueError("thread cutter fusion failed")
    except Exception:
        cutter = cq.Compound.makeCompound([core, groove])
    return cutter, {
        "major_diameter_mm": d,
        "minor_diameter_mm": 2 * r_minor,
        "pitch_mm": p,
        "thread_length_mm": L,
        "thread_depth_mm": depth,
    }


def metric_hex_bolt(
    diameter: float,
    length: float,
    pitch: float | None = None,
    head_across_flats: float | None = None,
    head_height: float | None = None,
    thread_length: float | None = None,
    right_handed: bool = True,
    modeled_thread: bool = True,
) -> tuple[cq.Shape, dict[str, Any]]:
    d = float(diameter); L = float(length)
    if d <= 0 or L <= 0:
        raise ValueError("diameter and under-head length must be > 0")
    p = metric_pitch(d, pitch)
    if head_across_flats is None or head_height is None:
        af0, h0 = HEX_HEAD_DEFAULTS[_nearest_key(HEX_HEAD_DEFAULTS, d)]
        af = af0 if head_across_flats is None else float(head_across_flats)
        hh = h0 if head_height is None else float(head_height)
    else:
        af = float(head_across_flats); hh = float(head_height)
    tl = L if thread_length is None else min(L, max(0.0, float(thread_length)))
    plain = max(0.0, L - tl)

    head = _hex_prism(af, hh, 0.0)
    z_shank = hh
    bodies: list[cq.Shape] = [head]
    if plain > 1e-9:
        bodies.append(cq.Solid.makeCylinder(d/2.0, plain, (0, 0, z_shank), (0, 0, 1)))
    thread_z0 = z_shank + plain
    if tl > 1e-9:
        if modeled_thread:
            threaded, thread_meta = external_metric_thread(d, p, tl, thread_z0, right_handed)
            bodies.append(threaded)
        else:
            bodies.append(cq.Solid.makeCylinder(d/2.0, tl, (0, 0, thread_z0), (0, 0, 1)))
            thread_meta = {"major_diameter_mm": d, "pitch_mm": p, "thread_length_mm": tl}
    else:
        thread_meta = {"major_diameter_mm": d, "pitch_mm": p, "thread_length_mm": 0.0}

    result = bodies[0]
    for b in bodies[1:]:
        try:
            candidate = cq.Workplane(obj=result).union(b).val()
            if not candidate.Solids(): raise ValueError('empty union')
            result = candidate
        except Exception:
            result = cq.Compound.makeCompound([result, b])
    meta = {
        "standard_family": "ISO metric hex bolt",
        "designation": f"M{d:g}x{p:g}",
        "diameter_mm": d,
        "pitch_mm": p,
        "under_head_length_mm": L,
        "thread_length_mm": tl,
        "plain_shank_length_mm": plain,
        "head_across_flats_mm": af,
        "head_height_mm": hh,
        "right_handed": bool(right_handed),
        "modeled_thread": bool(modeled_thread),
        **thread_meta,
    }
    return result, meta


def metric_hex_nut(
    diameter: float,
    pitch: float | None = None,
    across_flats: float | None = None,
    height: float | None = None,
    right_handed: bool = True,
    modeled_thread: bool = True,
) -> tuple[cq.Shape, dict[str, Any]]:
    d = float(diameter); p = metric_pitch(d, pitch)
    if across_flats is None or height is None:
        af0, h0 = HEX_NUT_DEFAULTS[_nearest_key(HEX_NUT_DEFAULTS, d)]
        af = af0 if across_flats is None else float(across_flats)
        h = h0 if height is None else float(height)
    else:
        af = float(across_flats); h = float(height)
    body = _hex_prism(af, h)
    if modeled_thread:
        cut, thread_meta = internal_metric_thread_cut(d, p, h, 0.0, right_handed)
    else:
        cut = cq.Solid.makeCylinder(d/2.0, h, (0,0,0), (0,0,1))
        thread_meta = {"major_diameter_mm": d, "pitch_mm": p, "thread_length_mm": h}
    solids = cut.Solids()
    if len(solids) == 1:
        result = cq.Workplane(obj=body).cut(solids[0]).val()
    else:
        result = body
        for tool_solid in solids:
            result = cq.Workplane(obj=result).cut(tool_solid).val()
    return result, {
        "standard_family": "ISO metric hex nut",
        "designation": f"M{d:g}x{p:g}",
        "diameter_mm": d,
        "pitch_mm": p,
        "across_flats_mm": af,
        "height_mm": h,
        "right_handed": bool(right_handed),
        "modeled_thread": bool(modeled_thread),
        **thread_meta,
    }


def metric_washer(
    diameter: float,
    inner_diameter: float | None = None,
    outer_diameter: float | None = None,
    thickness: float | None = None,
) -> tuple[cq.Shape, dict[str, Any]]:
    d = float(diameter)
    if inner_diameter is None or outer_diameter is None or thickness is None:
        i0, o0, t0 = WASHER_DEFAULTS[_nearest_key(WASHER_DEFAULTS, d)]
        di = i0 if inner_diameter is None else float(inner_diameter)
        do = o0 if outer_diameter is None else float(outer_diameter)
        t = t0 if thickness is None else float(thickness)
    else:
        di, do, t = float(inner_diameter), float(outer_diameter), float(thickness)
    if di <= 0 or do <= di or t <= 0:
        raise ValueError("washer requires 0 < inner_diameter < outer_diameter and thickness > 0")
    result = cq.Workplane("XY").circle(do/2.0).circle(di/2.0).extrude(t).val()
    return result, {
        "standard_family": "ISO normal-series plain washer",
        "designation": f"M{d:g}",
        "nominal_diameter_mm": d,
        "inner_diameter_mm": di,
        "outer_diameter_mm": do,
        "thickness_mm": t,
    }
