from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _under(path: Path, root: Path) -> bool:
    try:
        a = os.path.normcase(os.path.abspath(str(path)))
        b = os.path.normcase(os.path.abspath(str(root)))
        return os.path.commonpath([a, b]) == b
    except Exception:
        return False


def validate_export_path(value: str) -> Path:
    """Validate that export output paths stay within allowed local roots."""
    if value is None or not str(value).strip():
        raise ValueError("output path is required")
    raw = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if not raw.is_absolute():
        raise ValueError("output path must be absolute")
    path = Path(os.path.abspath(str(raw)))
    roots = [Path.home(), Path(tempfile.gettempdir())]
    extra = os.environ.get("CADIA_INVENTOR_EXPORT_ROOT")
    if extra:
        x = Path(os.path.expandvars(os.path.expanduser(extra)))
        if x.is_absolute():
            roots.append(x)
    if not any(_under(path, r) for r in roots):
        raise PermissionError("output path is outside allowed export roots")
    return path


def validate_rectangular_pattern(p: dict) -> None:
    """Validate rectangular pattern arguments before geometry execution."""
    count1 = int(p.get("count1", 0))
    spacing1 = float(p.get("spacing_mm1", 0))
    if count1 < 2:
        raise ValueError("count1 must be at least 2")
    if spacing1 <= 0:
        raise ValueError("spacing_mm1 must be greater than 0")
    dir2 = p.get("dir2")
    count2 = p.get("count2")
    spacing2 = p.get("spacing_mm2")
    if dir2:
        if count2 is None or int(count2) < 2:
            raise ValueError("count2 must be at least 2 when dir2 is set")
        if spacing2 is None or float(spacing2) <= 0:
            raise ValueError("spacing_mm2 must be greater than 0 when dir2 is set")
    elif count2 not in (None, 0, 1) or spacing2 not in (None, 0, 0.0):
        raise ValueError("dir2 is required when second-direction count/spacing is provided")


def validate_circular_pattern(p: dict) -> None:
    if int(p.get("count", 0)) < 2:
        raise ValueError("count must be at least 2")


def _validate_near(value) -> None:
    if value is not None:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError("near_mm must contain exactly 3 numbers")
        [float(x) for x in value]


def validate_face_selector(selector: dict, allow_cylindrical: bool = True) -> None:
    """Validate face-selector arguments before geometry selection."""
    if not isinstance(selector, dict):
        raise ValueError("face selector must be an object")
    kind = str(selector.get("kind") or "").lower()
    allowed = {"planar", "cylindrical"} if allow_cylindrical else {"planar"}
    if kind not in allowed:
        raise ValueError("face selector kind is invalid")
    tokens = {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}
    normal = selector.get("normal")
    axis = selector.get("axis")
    if normal is not None and str(normal).upper() not in tokens:
        raise ValueError("face selector normal is invalid")
    if axis is not None and str(axis).upper() not in tokens:
        raise ValueError("face selector axis is invalid")
    if kind == "planar" and not normal:
        raise ValueError("planar face selector requires normal")
    if kind == "cylindrical":
        radius = selector.get("radius_mm")
        if radius is not None and float(radius) <= 0:
            raise ValueError("radius_mm must be greater than 0")
    _validate_near(selector.get("near_mm"))
    tol = selector.get("tolerance_deg")
    if tol is not None and not (0 < float(tol) < 90):
        raise ValueError("tolerance_deg must be greater than 0 and less than 90")
    rtol = selector.get("radius_tol_mm")
    if rtol is not None and float(rtol) <= 0:
        raise ValueError("radius_tol_mm must be greater than 0")
