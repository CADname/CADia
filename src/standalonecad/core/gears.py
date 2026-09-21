from __future__ import annotations

import math
import cadquery as cq


def _involute_value_from_radius(radius: float, base_radius: float) -> float:
    """Return inv(alpha_r) for a radius on an involute of ``base_radius``.

    For an external involute gear, ``cos(alpha_r) = rb / r`` and
    ``inv(alpha_r) = tan(alpha_r) - alpha_r``.  Written in terms of the
    standard involute parameter t this is ``t - atan(t)``.
    """
    r = float(radius)
    rb = float(base_radius)
    if rb <= 0 or r < rb:
        raise ValueError("involute radius must be >= base radius > 0")
    t = math.sqrt(max(0.0, (r / rb) ** 2 - 1.0))
    return t - math.atan(t)


def _half_tooth_angle_at_radius(
    radius: float,
    *,
    pitch_radius: float,
    base_radius: float,
    teeth: int,
    backlash: float = 0.0,
) -> float:
    """Half angular tooth thickness at ``radius`` for a standard external gear.

    Tooth thickness is defined at the pitch circle and decreases toward the
    addendum on an external involute gear:

        beta(r) = beta(pitch) + inv(alpha) - inv(alpha_r)

    This sign is important.  Reversing it makes the tooth flare outward, which
    is geometrically wrong even though the resulting B-Rep can still be valid.
    """
    rp = float(pitch_radius)
    rb = float(base_radius)
    z = int(teeth)
    if rp <= 0 or rb <= 0 or z <= 0:
        raise ValueError("invalid gear radii/tooth count")
    pitch_half = math.pi / (2.0 * z) - float(backlash) / (2.0 * rp)
    if pitch_half <= 0:
        raise ValueError("backlash is too large for this module/tooth count")
    inv_pitch = _involute_value_from_radius(rp, rb)
    inv_r = _involute_value_from_radius(float(radius), rb)
    return pitch_half + inv_pitch - inv_r


def involute_spur_gear(
    module: float,
    teeth: int,
    thickness: float,
    bore_diameter: float = 0.0,
    pressure_angle_deg: float = 20.0,
    backlash: float = 0.0,
    flank_samples: int = 12,
):
    """Build a deterministic external spur gear with a sampled involute flank.

    Standard full-depth proportions are used by default:
      addendum = 1.0*m, dedendum = 1.25*m.

    ``backlash`` is a circumferential tooth-thickness reduction at the pitch
    circle.  The involute is exact in definition and sampled into a polygon for
    the OCCT profile.  When the root circle lies below the base circle, the root
    is connected radially to the first involute point.  This is a deliberate,
    simple root transition rather than a claim of a hob-generated trochoid.
    """
    m = float(module)
    z = int(teeth)
    t = float(thickness)
    bore = float(bore_diameter or 0.0)
    pa = float(pressure_angle_deg)
    backlash = float(backlash or 0.0)
    if not (m > 0 and t > 0):
        raise ValueError("module and thickness must be positive")
    if z < 6:
        raise ValueError("teeth must be >= 6")
    if not (5.0 <= pa <= 35.0):
        raise ValueError("pressure_angle_deg must be between 5 and 35 degrees")

    rp = m * z / 2.0
    ra = rp + m
    rf = max(m * 0.2, rp - 1.25 * m)
    alpha = math.radians(pa)
    rb = rp * math.cos(alpha)
    if bore < 0 or bore >= 2.0 * rf:
        raise ValueError("bore_diameter must be >= 0 and smaller than the root diameter")

    pitch_half = math.pi / (2.0 * z) - backlash / (2.0 * rp)
    if pitch_half <= 0:
        raise ValueError("backlash is too large for this module/tooth count")

    start_r = max(rf, rb)
    start_t = math.sqrt(max(0.0, (start_r / rb) ** 2 - 1.0))
    end_t = math.sqrt(max(0.0, (ra / rb) ** 2 - 1.0))
    n = max(6, min(40, int(flank_samples)))

    # Right flank: at the pitch circle the polar angle is exactly pitch_half;
    # toward the tip it decreases.  This is the standard external involute tooth
    # thickness relationship and prevents the outward-flaring profile that the
    # previous sign error produced.
    right: list[tuple[float, float]] = []
    base_half = _half_tooth_angle_at_radius(
        rb, pitch_radius=rp, base_radius=rb, teeth=z, backlash=backlash
    )
    if rf < rb:
        right.append((rf * math.cos(base_half), rf * math.sin(base_half)))

    for i in range(n):
        u = i / (n - 1)
        q = start_t + (end_t - start_t) * u
        radius = rb * math.sqrt(1.0 + q * q)
        half_angle = _half_tooth_angle_at_radius(
            radius, pitch_radius=rp, base_radius=rb, teeth=z, backlash=backlash
        )
        right.append((radius * math.cos(half_angle), radius * math.sin(half_angle)))

    tip_half = math.atan2(right[-1][1], right[-1][0])
    if tip_half <= 0:
        raise ValueError(
            "tooth tip thickness is non-positive for this gear definition; "
            "reduce backlash or use a compatible tooth count/pressure angle"
        )

    profile = list(right)
    # Circular addendum land between the two involute flanks.
    for j in range(1, 7):
        a = tip_half - 2.0 * tip_half * j / 6.0
        profile.append((ra * math.cos(a), ra * math.sin(a)))
    left = [(x, -y) for x, y in right]
    profile.extend(reversed(left[:-1]))

    tooth = cq.Workplane("XY").polyline(profile).close().extrude(t, combine=False).val()
    shape = cq.Solid.makeCylinder(rf, t)
    for k in range(z):
        rotated = cq.Workplane(obj=tooth).rotate((0, 0, 0), (0, 0, 1), 360.0 * k / z).val()
        shape = shape.fuse(rotated)
    if bore > 0:
        shape = shape.cut(cq.Solid.makeCylinder(bore / 2.0, t))
    if not shape.isValid() or len(shape.Solids()) != 1:
        raise RuntimeError("spur gear B-Rep construction did not produce one valid solid")

    tip_tooth_thickness = 2.0 * ra * tip_half
    pitch_tooth_thickness = 2.0 * rp * pitch_half
    return shape, {
        "module": m,
        "teeth": z,
        "thickness_mm": t,
        "bore_diameter_mm": bore,
        "pressure_angle_deg": pa,
        "backlash_mm": backlash,
        "pitch_diameter_mm": 2.0 * rp,
        "outside_diameter_mm": 2.0 * ra,
        "root_diameter_mm": 2.0 * rf,
        "base_diameter_mm": 2.0 * rb,
        "pitch_tooth_thickness_mm": pitch_tooth_thickness,
        "tip_tooth_thickness_mm": tip_tooth_thickness,
        "pitch_half_tooth_angle_deg": math.degrees(pitch_half),
        "tip_half_tooth_angle_deg": math.degrees(tip_half),
        "root_transition": "radial_to_base_circle",
    }
