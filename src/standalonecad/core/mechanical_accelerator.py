from __future__ import annotations

"""Deterministic mechanical-component generators.

This module is intentionally separate from the upstream ipt-mcp compatibility layer.
The public ``inventor_*`` contract stays frozen; these helpers back StandaloneCAD-only
``cad_*`` Design-Accelerator-style extensions.
"""

from typing import Any
import math
import cadquery as cq


def _positive(name: str, value: float) -> float:
    v = float(value)
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"{name} must be > 0")
    return v


def _nonnegative(name: str, value: float) -> float:
    v = float(value)
    if not math.isfinite(v) or v < 0:
        raise ValueError(f"{name} must be >= 0")
    return v


def _validate_shape(shape, label: str):
    if shape is None:
        raise RuntimeError(f"{label} produced no geometry")
    try:
        if hasattr(shape, "isValid") and not bool(shape.isValid()):
            raise RuntimeError(f"{label} produced an invalid B-Rep")
        bb = shape.BoundingBox()
        vals = (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax, shape.Volume(), shape.Area())
        if not all(math.isfinite(float(x)) for x in vals):
            raise RuntimeError(f"{label} produced non-finite geometry")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{label} geometry validation failed: {exc}") from exc
    return shape


def stepped_shaft(
    sections: list[dict[str, Any]],
    bore_diameter_mm: float = 0.0,
    keyway_width_mm: float | None = None,
    keyway_depth_mm: float | None = None,
    keyway_length_mm: float | None = None,
    keyway_start_mm: float = 0.0,
):
    """Build a coaxial stepped shaft along +Z, optionally with one axial keyway.

    ``sections`` is an ordered list of ``{"length_mm": ..., "diameter_mm": ...}``.
    This is deliberately deterministic and rebuild-friendly rather than sketch-driven.
    """
    if not isinstance(sections, list) or not sections:
        raise ValueError("shaft sections must contain at least one section")
    z = 0.0
    solids = []
    normalized = []
    max_d = 0.0
    for idx, row in enumerate(sections, 1):
        if not isinstance(row, dict):
            raise ValueError(f"shaft section {idx} must be an object")
        length = _positive(f"sections[{idx}].length_mm", row.get("length_mm", 0))
        diameter = _positive(f"sections[{idx}].diameter_mm", row.get("diameter_mm", 0))
        solids.append(cq.Solid.makeCylinder(diameter / 2.0, length, (0, 0, z), (0, 0, 1)))
        normalized.append({"length_mm": length, "diameter_mm": diameter, "z_start_mm": z})
        z += length
        max_d = max(max_d, diameter)
    shape = solids[0]
    for solid in solids[1:]:
        shape = cq.Workplane(obj=shape).union(solid).val()

    bore = _nonnegative("bore_diameter_mm", bore_diameter_mm)
    if bore > 0:
        if bore >= min(row["diameter_mm"] for row in normalized):
            raise ValueError("bore_diameter_mm must be smaller than every shaft section diameter")
        cutter = cq.Solid.makeCylinder(bore / 2.0, z + 0.2, (0, 0, -0.1), (0, 0, 1))
        shape = cq.Workplane(obj=shape).cut(cutter).val()

    have_keyway = any(v is not None for v in (keyway_width_mm, keyway_depth_mm, keyway_length_mm))
    if have_keyway:
        if None in (keyway_width_mm, keyway_depth_mm, keyway_length_mm):
            raise ValueError("keyway_width_mm, keyway_depth_mm and keyway_length_mm must be supplied together")
        kw = _positive("keyway_width_mm", keyway_width_mm)
        kd = _positive("keyway_depth_mm", keyway_depth_mm)
        kl = _positive("keyway_length_mm", keyway_length_mm)
        ks = _nonnegative("keyway_start_mm", keyway_start_mm)
        if ks + kl > z + 1e-9:
            raise ValueError("keyway extends beyond shaft length")
        # A robust axial rectangular keyway cut from the +X side.  The radial cutter is
        # intentionally long enough to cross the current outer surface regardless of
        # which stepped section it intersects.
        x0 = max_d / 2.0 - kd
        cutter = cq.Solid.makeBox(max_d, kw, kl, (x0, -kw / 2.0, ks))
        shape = cq.Workplane(obj=shape).cut(cutter).val()

    _validate_shape(shape, "Stepped shaft generator")
    return shape, {
        "generator": "shaft",
        "section_count": len(normalized),
        "total_length_mm": z,
        "max_diameter_mm": max_d,
        "bore_diameter_mm": bore,
        "keyway": bool(have_keyway),
    }


def parallel_key(width_mm: float, height_mm: float, length_mm: float, end_style: str = "square"):
    width = _positive("width_mm", width_mm)
    height = _positive("height_mm", height_mm)
    length = _positive("length_mm", length_mm)
    style = str(end_style or "square").lower()
    if style not in {"square", "round"}:
        raise ValueError("end_style must be square|round")
    if style == "square":
        shape = cq.Solid.makeBox(length, width, height, (0, -width / 2.0, 0))
    else:
        # Racetrack planform, extruded in +Z.  Overall X length is exactly length.
        if length < width:
            raise ValueError("round-end key requires length_mm >= width_mm")
        shape = cq.Workplane("XY").slot2D(length, width).extrude(height, combine=False).val()
    _validate_shape(shape, "Parallel key generator")
    return shape, {"generator": "parallel_key", "width_mm": width, "height_mm": height, "length_mm": length, "end_style": style}


def rolling_bearing(
    kind: str,
    bore_diameter_mm: float,
    outer_diameter_mm: float,
    width_mm: float,
    rolling_elements: int = 8,
    detailed: bool = True,
):
    """Create a deterministic bearing model with exact requested outer envelope.

    Internal race/rolling-element geometry is intentionally simplified.  It is suitable
    for assembly clearance and layout work, not catalogue load/life certification.
    """
    kind = str(kind or "deep_groove_ball").lower()
    if kind not in {"deep_groove_ball", "cylindrical_roller", "envelope"}:
        raise ValueError("bearing kind must be deep_groove_ball|cylindrical_roller|envelope")
    bore = _positive("bore_diameter_mm", bore_diameter_mm)
    outer = _positive("outer_diameter_mm", outer_diameter_mm)
    width = _positive("width_mm", width_mm)
    if outer <= bore:
        raise ValueError("outer_diameter_mm must be greater than bore_diameter_mm")
    count = int(rolling_elements)
    if count < 3 or count > 200:
        raise ValueError("rolling_elements must be between 3 and 200")

    if kind == "envelope" or not detailed:
        shape = cq.Workplane("XY").circle(outer / 2.0).circle(bore / 2.0).extrude(width, combine=False).val()
        _validate_shape(shape, "Bearing envelope generator")
        return shape, {"generator": "bearing", "bearing_kind": kind, "simplified": True, "bore_diameter_mm": bore, "outer_diameter_mm": outer, "width_mm": width}

    radial_space = (outer - bore) / 2.0
    ring_radial = radial_space * 0.28
    inner_outer_r = bore / 2.0 + ring_radial
    outer_inner_r = outer / 2.0 - ring_radial
    gap = outer_inner_r - inner_outer_r
    if gap <= 0:
        raise ValueError("bearing dimensions leave no room for rolling elements")

    inner = cq.Workplane("XY").circle(inner_outer_r).circle(bore / 2.0).extrude(width, combine=False).val()
    outer_ring = cq.Workplane("XY").circle(outer / 2.0).circle(outer_inner_r).extrude(width, combine=False).val()
    pitch_r = (inner_outer_r + outer_inner_r) / 2.0
    parts = [inner, outer_ring]
    if kind == "deep_groove_ball":
        element_r = min(gap * 0.40, width * 0.42)
        for idx in range(count):
            a = 2.0 * math.pi * idx / count
            parts.append(cq.Solid.makeSphere(element_r, (pitch_r * math.cos(a), pitch_r * math.sin(a), width / 2.0)))
    else:
        roller_d = min(gap * 0.75, width * 0.70)
        roller_len = width * 0.72
        for idx in range(count):
            a = 2.0 * math.pi * idx / count
            x, y = pitch_r * math.cos(a), pitch_r * math.sin(a)
            # Rollers remain parallel to the bearing Z axis; this is a simplified
            # cylindrical-roller representation with exact outside envelope.
            parts.append(cq.Solid.makeCylinder(roller_d / 2.0, roller_len, (x, y, (width - roller_len) / 2.0), (0, 0, 1)))
    shape = cq.Compound.makeCompound(parts)
    _validate_shape(shape, "Bearing generator")
    return shape, {
        "generator": "bearing",
        "bearing_kind": kind,
        "simplified": True,
        "bore_diameter_mm": bore,
        "outer_diameter_mm": outer,
        "width_mm": width,
        "rolling_elements": count,
    }


def compression_spring(
    wire_diameter_mm: float,
    mean_diameter_mm: float,
    free_length_mm: float,
    active_turns: float,
    right_handed: bool = True,
):
    wire = _positive("wire_diameter_mm", wire_diameter_mm)
    mean_d = _positive("mean_diameter_mm", mean_diameter_mm)
    length = _positive("free_length_mm", free_length_mm)
    turns = _positive("active_turns", active_turns)
    if mean_d <= wire:
        raise ValueError("mean_diameter_mm must be greater than wire_diameter_mm")
    if length <= wire:
        raise ValueError("free_length_mm must be greater than wire_diameter_mm")
    # Free length means the physical end-to-end envelope.  The helical centreline
    # therefore spans free_length-wire and starts half a wire diameter above Z=0.
    helix_height = length - wire
    pitch = helix_height / turns
    if pitch < wire * 0.55:
        raise ValueError("spring pitch is too small for the requested wire diameter")
    radius = mean_d / 2.0
    z0 = wire / 2.0
    helix = cq.Wire.makeHelix(pitch, helix_height, radius, center=(0, 0, z0), dir=(0, 0, 1), lefthand=not bool(right_handed))
    profile = cq.Workplane("XZ", origin=(0, 0, z0)).center(radius, 0).circle(wire / 2.0).wire().val()
    shape = cq.Solid.sweep(profile, [], helix, makeSolid=True, isFrenet=True)
    _validate_shape(shape, "Compression spring generator")
    return shape, {
        "generator": "compression_spring",
        "wire_diameter_mm": wire,
        "mean_diameter_mm": mean_d,
        "outside_diameter_mm": mean_d + wire,
        "inside_diameter_mm": mean_d - wire,
        "free_length_mm": length,
        "active_turns": turns,
        "pitch_mm": pitch,
        "right_handed": bool(right_handed),
    }


def belleville_spring(outer_diameter_mm: float, inner_diameter_mm: float, thickness_mm: float, free_height_mm: float):
    outer = _positive("outer_diameter_mm", outer_diameter_mm)
    inner = _positive("inner_diameter_mm", inner_diameter_mm)
    thick = _positive("thickness_mm", thickness_mm)
    height = _positive("free_height_mm", free_height_mm)
    if inner >= outer:
        raise ValueError("inner_diameter_mm must be smaller than outer_diameter_mm")
    if height < thick:
        raise ValueError("free_height_mm must be >= thickness_mm")
    # Revolved conical annulus in XZ; axis is global Z.
    shape = (cq.Workplane("XZ")
             .moveTo(inner / 2.0, 0.0)
             .lineTo(outer / 2.0, height - thick)
             .lineTo(outer / 2.0, height)
             .lineTo(inner / 2.0, thick)
             .close()
             .revolve(360.0, (0, 0), (0, 1), combine=False).val())
    _validate_shape(shape, "Belleville spring generator")
    return shape, {
        "generator": "belleville_spring",
        "outer_diameter_mm": outer,
        "inner_diameter_mm": inner,
        "thickness_mm": thick,
        "free_height_mm": height,
    }


def v_pulley(
    pitch_diameter_mm: float,
    width_mm: float,
    groove_count: int = 1,
    groove_angle_deg: float = 40.0,
    groove_depth_mm: float | None = None,
    groove_pitch_mm: float | None = None,
    bore_diameter_mm: float = 0.0,
):
    """Create a deterministic parametric V-pulley envelope with real V grooves.

    This is a geometry generator rather than a claim of a particular ISO/RMA belt
    section.  Explicit groove dimensions are retained as feature parameters so a future
    standards database can map belt sections without changing this feature contract.
    """
    pd = _positive("pitch_diameter_mm", pitch_diameter_mm)
    width = _positive("width_mm", width_mm)
    count = int(groove_count)
    if count < 1 or count > 24:
        raise ValueError("groove_count must be between 1 and 24")
    angle = float(groove_angle_deg)
    if not (20.0 <= angle <= 80.0):
        raise ValueError("groove_angle_deg must be between 20 and 80")
    depth = _positive("groove_depth_mm", groove_depth_mm if groove_depth_mm is not None else max(1.0, pd * 0.05))
    root_r = pd / 2.0
    outer_r = root_r + depth
    half_width = depth * math.tan(math.radians(angle / 2.0))
    pitch = _positive("groove_pitch_mm", groove_pitch_mm if groove_pitch_mm is not None else max(2.2 * half_width, width / count))
    required = (count - 1) * pitch + 2.0 * half_width
    if required > width + 1e-9:
        raise ValueError("grooves do not fit within width_mm; reduce groove_count/pitch/depth or increase width")
    shape = cq.Solid.makeCylinder(outer_r, width)
    start_z = (width - (count - 1) * pitch) / 2.0
    for idx in range(count):
        z = start_z + idx * pitch
        cutter = (cq.Workplane("XZ")
                  .moveTo(root_r, z)
                  .lineTo(outer_r + depth, z - half_width)
                  .lineTo(outer_r + depth, z + half_width)
                  .close()
                  .revolve(360.0, (0, 0), (0, 1), combine=False).val())
        shape = cq.Workplane(obj=shape).cut(cutter).val()
    bore = _nonnegative("bore_diameter_mm", bore_diameter_mm)
    if bore > 0:
        if bore >= 2.0 * root_r:
            raise ValueError("bore_diameter_mm must be smaller than pulley root diameter")
        cutter = cq.Solid.makeCylinder(bore / 2.0, width + 0.2, (0, 0, -0.1), (0, 0, 1))
        shape = cq.Workplane(obj=shape).cut(cutter).val()
    _validate_shape(shape, "V-pulley generator")
    return shape, {
        "generator": "v_pulley",
        "pitch_diameter_mm": pd,
        "outside_diameter_mm": 2.0 * outer_r,
        "width_mm": width,
        "groove_count": count,
        "groove_angle_deg": angle,
        "groove_depth_mm": depth,
        "groove_pitch_mm": pitch,
        "bore_diameter_mm": bore,
    }


def _cq_gears_module():
    try:
        import cq_gears  # type: ignore
        return cq_gears
    except Exception as exc:
        raise RuntimeError(
            "This gear type uses the Apache-2.0 cq_gears backend. Install the accelerator optional dependencies "
            "with Internet access to install the pinned backend, then retry."
        ) from exc


def advanced_gear(kind: str, **params):
    """Build gear families not covered by StandaloneCAD's native stable spur generator.

    The adapter deliberately keeps cq_gears behind one family-level feature so the
    planner does not get dozens of nearly-duplicate tools.
    """
    kind = str(kind or "").lower().replace("-", "_")
    module = _positive("module", params.get("module", 0))
    if kind == "worm_pair":
        if params.get("teeth") is None or params.get("width_mm") is None or params.get("length_mm") is None or params.get("lead_angle_deg") is None or params.get("thread_starts") is None:
            raise ValueError("worm_pair requires teeth, width_mm, length_mm, lead_angle_deg and thread_starts")
        return worm_gear_pair(module,int(params["teeth"]),float(params["width_mm"]),float(params["length_mm"]),float(params["lead_angle_deg"]),int(params["thread_starts"]),float(params.get("bore_diameter_mm",0) or 0),float(params.get("pressure_angle_deg",20) or 20))
    cg = _cq_gears_module()
    pressure = float(params.get("pressure_angle_deg", 20.0))
    backlash = _nonnegative("backlash_mm", params.get("backlash_mm", 0.0))
    bore = _nonnegative("bore_diameter_mm", params.get("bore_diameter_mm", 0.0))
    clearance = _nonnegative("clearance_mm", params.get("clearance_mm", 0.0))

    if kind in {"helical", "herringbone"}:
        teeth = int(params.get("teeth", 0)); width = _positive("width_mm", params.get("width_mm", 0)); helix = float(params.get("helix_angle_deg", 0))
        if teeth < 6: raise ValueError("teeth must be >= 6")
        if kind == "helical" and abs(helix) < 1e-9: raise ValueError("helical gear requires a non-zero helix_angle_deg")
        cls = cg.SpurGear if kind == "helical" else cg.HerringboneGear
        gear = cls(module=module, teeth_number=teeth, width=width, pressure_angle=pressure, helix_angle=helix, clearance=clearance, backlash=backlash)
        shape = gear.build(bore_d=bore or None)
        derived = {"teeth": teeth, "pitch_diameter_mm": module * teeth, "outside_diameter_mm": module * (teeth + 2), "width_mm": width, "helix_angle_deg": helix}
    elif kind == "ring":
        teeth = int(params.get("teeth", 0)); width = _positive("width_mm", params.get("width_mm", 0)); rim = _positive("rim_width_mm", params.get("rim_width_mm", 0)); helix = float(params.get("helix_angle_deg", 0))
        if teeth < 10: raise ValueError("ring gear teeth must be >= 10")
        gear = cg.RingGear(module=module, teeth_number=teeth, width=width, rim_width=rim, pressure_angle=pressure, helix_angle=helix, clearance=clearance, backlash=backlash)
        shape = gear.build()
        derived = {"teeth": teeth, "pitch_diameter_mm": module * teeth, "width_mm": width, "rim_width_mm": rim, "helix_angle_deg": helix}
    elif kind == "bevel":
        teeth = int(params.get("teeth", 0)); face_width = _positive("width_mm", params.get("width_mm", 0)); cone = _positive("cone_angle_deg", params.get("cone_angle_deg", 0)); helix = float(params.get("helix_angle_deg", 0))
        if teeth < 6: raise ValueError("teeth must be >= 6")
        if cone >= 90: raise ValueError("cone_angle_deg must be < 90")
        gear = cg.BevelGear(module=module, teeth_number=teeth, cone_angle=cone, face_width=face_width, pressure_angle=pressure, helix_angle=helix, clearance=clearance, backlash=backlash)
        shape = gear.build(bore_d=bore or None)
        derived = {"teeth": teeth, "pitch_diameter_mm": module * teeth, "face_width_mm": face_width, "cone_angle_deg": cone, "helix_angle_deg": helix}
    elif kind == "rack":
        length = _positive("length_mm", params.get("length_mm", 0)); width = _positive("width_mm", params.get("width_mm", 0)); height = _positive("height_mm", params.get("height_mm", 0)); helix = float(params.get("helix_angle_deg", 0))
        gear = cg.RackGear(module=module, length=length, width=width, height=height, pressure_angle=pressure, helix_angle=helix, clearance=clearance, backlash=backlash)
        shape = gear.build()
        derived = {"length_mm": length, "width_mm": width, "height_mm": height, "helix_angle_deg": helix}
    elif kind == "worm":
        lead = float(params.get("lead_angle_deg", 0)); threads = int(params.get("thread_starts", 0)); length = _positive("length_mm", params.get("length_mm", 0))
        if not (0 < abs(lead) < 80): raise ValueError("lead_angle_deg must be non-zero and have magnitude < 80")
        if threads < 1: raise ValueError("thread_starts must be >= 1")
        gear = cg.Worm(module=module, lead_angle=lead, n_threads=threads, length=length, pressure_angle=pressure, clearance=clearance, backlash=backlash)
        shape = gear.build(bore_d=bore or None)
        derived = {"thread_starts": threads, "lead_angle_deg": lead, "length_mm": length}
    elif kind == "planetary":
        sun = int(params.get("sun_teeth", 0)); planet = int(params.get("planet_teeth", 0)); width = _positive("width_mm", params.get("width_mm", 0)); rim = _positive("rim_width_mm", params.get("rim_width_mm", 0)); count = int(params.get("planet_count", 0)); helix = float(params.get("helix_angle_deg", 0))
        if sun < 6 or planet < 6: raise ValueError("sun_teeth and planet_teeth must be >= 6")
        if count < 2 or count > 12: raise ValueError("planet_count must be between 2 and 12")
        gear = cg.PlanetaryGearset(module=module, sun_teeth_number=sun, planet_teeth_number=planet, width=width, rim_width=rim, n_planets=count, pressure_angle=pressure, helix_angle=helix, clearance=clearance, backlash=backlash)
        shape = gear.build()
        derived = {"sun_teeth": sun, "planet_teeth": planet, "ring_teeth": sun + 2 * planet, "planet_count": count, "width_mm": width, "helix_angle_deg": helix, "compound_preview": True}
    else:
        raise ValueError("gear kind must be helical|herringbone|ring|bevel|rack|worm|planetary")

    _validate_shape(shape, f"{kind} gear generator")
    derived.update({"generator": "advanced_gear", "gear_kind": kind, "module": module, "pressure_angle_deg": pressure, "backlash_mm": backlash})
    return shape, derived


def flange_coupling(
    bore_diameter_mm: float,
    hub_diameter_mm: float,
    flange_diameter_mm: float,
    hub_length_mm: float,
    flange_thickness_mm: float,
    bolt_circle_diameter_mm: float | None = None,
    bolt_count: int = 4,
    bolt_hole_diameter_mm: float | None = None,
    keyway_width_mm: float | None = None,
    keyway_depth_mm: float | None = None,
):
    """Create a deterministic two-half rigid flange coupling layout.

    The two halves are returned as a compound so the assembly seam remains visible.
    Optional bolt holes and a straight keyway are physically cut into both halves.
    """
    bore=_positive('bore_diameter_mm',bore_diameter_mm); hub=_positive('hub_diameter_mm',hub_diameter_mm); flange=_positive('flange_diameter_mm',flange_diameter_mm)
    hl=_positive('hub_length_mm',hub_length_mm); ft=_positive('flange_thickness_mm',flange_thickness_mm)
    if not (bore < hub < flange):raise ValueError('coupling requires bore_diameter < hub_diameter < flange_diameter')
    bc=float(bolt_circle_diameter_mm) if bolt_circle_diameter_mm is not None else (hub+flange)/2.0
    if not (hub < bc < flange):raise ValueError('bolt_circle_diameter_mm must lie between hub and flange diameters')
    count=int(bolt_count)
    if count<0 or count>24:raise ValueError('bolt_count must be between 0 and 24')
    hole=float(bolt_hole_diameter_mm) if bolt_hole_diameter_mm is not None else max(2.0,flange*0.07)
    if count and (hole<=0 or hole >= (flange-hub)/2.0):raise ValueError('bolt_hole_diameter_mm is too large for the flange annulus')
    if (keyway_width_mm is None) != (keyway_depth_mm is None):raise ValueError('keyway_width_mm and keyway_depth_mm must be supplied together')
    kw=None if keyway_width_mm is None else _positive('keyway_width_mm',keyway_width_mm); kd=None if keyway_depth_mm is None else _positive('keyway_depth_mm',keyway_depth_mm)
    if kd is not None and kd >= (hub-bore)/2.0:raise ValueError('keyway_depth_mm is too large for hub wall')

    def half(z0, flange_first=False):
        if flange_first:
            flange_z=z0; hub_z=z0+ft
        else:
            hub_z=z0; flange_z=z0+hl
        hsolid=cq.Solid.makeCylinder(hub/2.0,hl,(0,0,hub_z),(0,0,1)); fsolid=cq.Solid.makeCylinder(flange/2.0,ft,(0,0,flange_z),(0,0,1))
        body=cq.Workplane(obj=hsolid).union(fsolid).val()
        total_start=min(hub_z,flange_z); total_end=max(hub_z+hl,flange_z+ft)
        body=cq.Workplane(obj=body).cut(cq.Solid.makeCylinder(bore/2.0,total_end-total_start+0.2,(0,0,total_start-0.1),(0,0,1))).val()
        if count:
            for i in range(count):
                a=2*math.pi*i/count; x=bc/2*math.cos(a); y=bc/2*math.sin(a)
                cutter=cq.Solid.makeCylinder(hole/2.0,ft+0.2,(x,y,flange_z-0.1),(0,0,1)); body=cq.Workplane(obj=body).cut(cutter).val()
        if kw is not None:
            # Axial keyway from the +X side through the complete half.
            x0=bore/2.0-kd; cutter=cq.Solid.makeBox(hub,kw,total_end-total_start+0.2,(x0,-kw/2,total_start-0.1)); body=cq.Workplane(obj=body).cut(cutter).val()
        return body

    first=half(0.0,False); second=half(hl+ft,True)
    shape=cq.Compound.makeCompound([first,second]); _validate_shape(shape,'Flange coupling generator')
    return shape,{'generator':'coupling','coupling_kind':'flange','bore_diameter_mm':bore,'hub_diameter_mm':hub,'flange_diameter_mm':flange,'overall_length_mm':2*(hl+ft),'hub_length_mm':hl,'flange_thickness_mm':ft,'bolt_circle_diameter_mm':bc,'bolt_count':count,'bolt_hole_diameter_mm':hole,'keyway':kw is not None}


def _trapezoidal_external_thread(major_diameter_mm:float,pitch_mm:float,length_mm:float,starts:int=1,thread_angle_deg:float=30.0,right_handed:bool=True,z0:float=0.0):
    d=_positive('major_diameter_mm',major_diameter_mm); p=_positive('pitch_mm',pitch_mm); L=_positive('length_mm',length_mm); starts=int(starts)
    if starts<1 or starts>8:raise ValueError('starts must be between 1 and 8')
    angle=float(thread_angle_deg)
    if not (20.0<=angle<=60.0):raise ValueError('thread_angle_deg must be between 20 and 60')
    depth=min(0.45*p,d*0.22); rmaj=d/2.0; rmin=rmaj-depth; lead=p*starts
    root=cq.Solid.makeCylinder(rmin+0.02,L,(0,0,z0),(0,0,1)); ridges=[]
    half_root=min(0.42*p,p*0.36); half_crest=max(0.06*p,half_root-depth*math.tan(math.radians(angle/2.0)))
    half_crest=max(0.02*p,min(half_root*0.9,half_crest))
    base_profile=(cq.Workplane('XZ',origin=(0,0,z0)).center(rmin,0).polyline([(-0.03,-half_root),(depth,-half_crest),(depth,half_crest),(-0.03,half_root)]).close().wire().val())
    for i in range(starts):
        helix=cq.Wire.makeHelix(lead,L,rmin,center=(0,0,z0),dir=(0,0,1),lefthand=not right_handed)
        ridge=cq.Solid.sweep(base_profile,[],helix,makeSolid=True,isFrenet=True)
        if i:ridge=ridge.rotate((0,0,0),(0,0,1),360.0*i/starts)
        ridges.append(ridge)
    result=root
    for ridge in ridges:
        try:result=cq.Workplane(obj=result).union(ridge).val()
        except Exception:result=cq.Compound.makeCompound([result,ridge])
    clip=cq.Solid.makeCylinder(rmaj+0.05,L,(0,0,z0),(0,0,1))
    try:result=cq.Workplane(obj=result).intersect(clip).val()
    except Exception:pass
    return result,{'major_diameter_mm':d,'minor_diameter_mm':2*rmin,'pitch_mm':p,'lead_mm':lead,'starts':starts,'thread_angle_deg':angle,'length_mm':L}


def lead_screw_nut(
    major_diameter_mm:float,
    pitch_mm:float,
    screw_length_mm:float,
    nut_length_mm:float,
    starts:int=1,
    thread_angle_deg:float=30.0,
    nut_outer_diameter_mm:float|None=None,
    clearance_mm:float=0.15,
    right_handed:bool=True,
):
    """Generate an engaged trapezoidal lead screw and cylindrical nut as a compound."""
    d=_positive('major_diameter_mm',major_diameter_mm); p=_positive('pitch_mm',pitch_mm); sl=_positive('screw_length_mm',screw_length_mm); nl=_positive('nut_length_mm',nut_length_mm)
    if nl>=sl:raise ValueError('nut_length_mm must be smaller than screw_length_mm')
    clear=_nonnegative('clearance_mm',clearance_mm); od=float(nut_outer_diameter_mm) if nut_outer_diameter_mm is not None else max(d*1.8,d+4*p)
    if od<=d+2*clear:raise ValueError('nut_outer_diameter_mm must exceed screw diameter plus clearance')
    screw,meta=_trapezoidal_external_thread(d,p,sl,starts,thread_angle_deg,right_handed,0.0)
    z=(sl-nl)/2.0; body=cq.Solid.makeCylinder(od/2.0,nl,(0,0,z),(0,0,1))
    # Robust mating approximation: bore at minor diameter plus a helical groove based on the same lead.
    depth=min(0.45*p,d*0.22); rmin=d/2.0-depth; lead=p*int(starts); core=cq.Solid.makeCylinder(rmin+clear,nl+0.2,(0,0,z-0.1),(0,0,1)); cutters=[core]
    half_root=min(0.42*p,p*0.36); half_crest=max(0.02*p,half_root*0.35)
    profile=(cq.Workplane('XZ',origin=(0,0,z)).center(rmin+clear,0).polyline([(-0.02,-half_root),(depth+clear,-half_crest),(depth+clear,half_crest),(-0.02,half_root)]).close().wire().val())
    for i in range(int(starts)):
        helix=cq.Wire.makeHelix(lead,nl,rmin+clear,center=(0,0,z),dir=(0,0,1),lefthand=not right_handed); groove=cq.Solid.sweep(profile,[],helix,makeSolid=True,isFrenet=True)
        if i:groove=groove.rotate((0,0,0),(0,0,1),360.0*i/int(starts))
        cutters.append(groove)
    nut=body
    for cutter in cutters:
        for solid in cutter.Solids():nut=cq.Workplane(obj=nut).cut(solid).val()
    shape=cq.Compound.makeCompound([screw,nut]); _validate_shape(shape,'Lead screw and nut generator')
    return shape,{'generator':'lead_screw_nut','thread_form':'trapezoidal','nut_outer_diameter_mm':od,'nut_length_mm':nl,'clearance_mm':clear,'right_handed':bool(right_handed),**meta}


def worm_gear_pair(module:float,wheel_teeth:int,wheel_width_mm:float,worm_length_mm:float,lead_angle_deg:float,thread_starts:int=1,bore_diameter_mm:float=0.0,pressure_angle_deg:float=20.0):
    """Create a deterministic worm + wheel layout compound without cq_gears.

    The worm is a true multi-start helical trapezoidal solid.  The wheel uses the
    native involute wheel profile, so this is a robust CAD/layout generator rather
    than a certified conjugate worm-wheel tooth-contact model.
    """
    m=_positive('module',module); z=int(wheel_teeth); ww=_positive('wheel_width_mm',wheel_width_mm); wl=_positive('worm_length_mm',worm_length_mm); starts=int(thread_starts); lead=float(lead_angle_deg)
    if z<8:raise ValueError('wheel_teeth must be >= 8')
    if starts<1:raise ValueError('thread_starts must be >= 1')
    if not (0<abs(lead)<80):raise ValueError('lead_angle_deg must be non-zero and < 80 degrees')
    worm_pitch_d=m*starts/math.tan(math.radians(abs(lead))); worm_major=worm_pitch_d+2*m; axial_pitch=math.pi*m
    worm,wmeta=_trapezoidal_external_thread(worm_major,axial_pitch,wl,starts,2*pressure_angle_deg,lead>0,0.0)
    wheel,wheelmeta=__import__('standalonecad.core.gears',fromlist=['involute_spur_gear']).involute_spur_gear(m,z,ww,bore_diameter_mm,pressure_angle_deg,0.0)
    center=(worm_pitch_d+m*z)/2.0
    wheel=wheel.rotate((0,0,0),(1,0,0),90.0).translate((center,ww/2.0,wl/2.0))
    shape=cq.Compound.makeCompound([worm,wheel]); _validate_shape(shape,'Worm gear pair generator')
    return shape,{'generator':'advanced_gear','gear_kind':'worm_pair','module':m,'wheel_teeth':z,'wheel_width_mm':ww,'worm_length_mm':wl,'thread_starts':starts,'lead_angle_deg':lead,'worm_pitch_diameter_mm':worm_pitch_d,'wheel_pitch_diameter_mm':m*z,'center_distance_mm':center,'layout_approximation':True,'wheel':wheelmeta,'worm':wmeta}
