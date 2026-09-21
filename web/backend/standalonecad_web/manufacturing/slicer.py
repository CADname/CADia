from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class SlicerError(RuntimeError):
    pass


# Request-time overrides are intentionally narrow. Administrator-owned profiles
# remain the source of truth for machine/material settings. This prevents a user
# from turning a slicer CLI option into an arbitrary file/path/process control.
SAFE_PRUSA_OVERRIDES = {
    "layer_height", "first_layer_height", "fill_density", "fill_pattern",
    "perimeters", "top_solid_layers", "bottom_solid_layers",
    "support_material", "support_material_auto", "support_material_threshold",
    "brim_width", "skirts", "skirt_distance", "seam_position",
    "infill_speed", "perimeter_speed", "external_perimeter_speed", "travel_speed",
}
SAFE_CURA_OVERRIDES = {
    "layer_height", "layer_height_0", "infill_sparse_density", "infill_pattern",
    "wall_line_count", "top_layers", "bottom_layers", "support_enable",
    "support_angle", "adhesion_type", "speed_print", "speed_travel",
    "material_print_temperature", "material_bed_temperature",
}


def _timeout_from_env() -> int:
    try:
        value = int(os.getenv("CADIA_SLICER_TIMEOUT_SECONDS", "180"))
    except (TypeError, ValueError):
        value = 180
    return max(10, min(1800, value))


@dataclass(frozen=True)
class SlicerConfig:
    backend: str
    binary: str
    profile: str = ""
    cura_definition: str = ""
    timeout_seconds: int = 180

    @classmethod
    def from_env(cls) -> "SlicerConfig":
        backend = os.getenv("CADIA_SLICER_BACKEND", "none").strip().lower()
        if backend not in {"none", "prusa", "cura"}:
            backend = "none"
        default_bin = {"prusa": "prusa-slicer", "cura": "CuraEngine"}.get(backend, "")
        return cls(
            backend=backend,
            binary=os.getenv("CADIA_SLICER_BIN", default_bin).strip(),
            profile=os.getenv("CADIA_SLICER_PROFILE", "").strip(),
            cura_definition=os.getenv("CADIA_CURA_DEFINITION", "").strip(),
            timeout_seconds=_timeout_from_env(),
        )

    def resolved_binary(self) -> str | None:
        if not self.binary:
            return None
        if os.path.isabs(self.binary):
            return self.binary if Path(self.binary).is_file() else None
        return shutil.which(self.binary)


def _flat_profile_value(profile: Path, key: str) -> str | None:
    """Read one key from PrusaSlicer-style flat INI without executing it."""
    try:
        lines = profile.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    wanted = key.strip().lower()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip().lower() == wanted:
            return value.strip()
    return None


def prusa_profile_build_volume(profile: str | Path) -> tuple[float, float, float] | None:
    """Return the rectangular envelope of a PrusaSlicer bed profile in mm.

    bed_shape may describe a rectangular or polygonal bed.  For DFM we use the
    axis-aligned envelope, which matches the configured profile for the bundled
    rectangular 220 mm bed and avoids maintaining a second hard-coded size.
    """
    path = Path(profile)
    if not path.is_file():
        return None
    bed_shape = _flat_profile_value(path, "bed_shape")
    max_height = _flat_profile_value(path, "max_print_height")
    if not bed_shape or not max_height:
        return None
    points = [
        (float(x), float(y))
        for x, y in re.findall(r"(-?\d+(?:\.\d+)?)\s*x\s*(-?\d+(?:\.\d+)?)", bed_shape)
    ]
    if len(points) < 3:
        return None
    try:
        height = float(max_height)
    except ValueError:
        return None
    if height <= 0:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width = max(xs) - min(xs)
    depth = max(ys) - min(ys)
    if width <= 0 or depth <= 0:
        return None
    return (width, depth, height)


def configured_build_volume_mm(config: "SlicerConfig" | None = None) -> tuple[float, float, float] | None:
    """Return build volume from the exact slicer profile currently in use."""
    config = config or SlicerConfig.from_env()
    if config.backend == "prusa" and config.profile:
        return prusa_profile_build_volume(config.profile)
    return None


def slicer_capability(config: SlicerConfig | None = None) -> dict:
    config = config or SlicerConfig.from_env()
    executable = config.resolved_binary()
    configured = config.backend != "none"
    profile_ok = True
    detail = ""
    if config.backend == "prusa" and config.profile:
        profile_ok = Path(config.profile).is_file()
        if not profile_ok:
            detail = "The configured PrusaSlicer profile file was not found."
    if config.backend == "cura":
        profile_ok = bool(config.cura_definition and Path(config.cura_definition).is_file())
        if not profile_ok:
            detail = "A CuraEngine definition JSON file is required."
    if configured and not executable and not detail:
        detail = "The configured slicer executable was not found."
    build_volume = configured_build_volume_mm(config)
    return {
        "backend": config.backend,
        "configured": configured,
        "available": bool(configured and executable and profile_ok),
        "binary": executable,
        "profile_configured": bool(config.profile or config.cura_definition),
        "build_volume_mm": ({"x": build_volume[0], "y": build_volume[1], "z": build_volume[2]} if build_volume else None),
        "detail": detail,
        "license_boundary": "external_optional_agpl" if config.backend in {"prusa", "cura"} else "none",
    }


def _safe_tail(value: str, max_chars: int = 6000) -> str:
    value = value or ""
    return value[-max_chars:]


def _validated_overrides(values: dict[str, str], allowed: set[str], backend: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key).strip().lower().replace("-", "_")
        value = str(raw_value).strip()
        if key not in allowed:
            raise SlicerError(f"Unsupported per-request {backend} setting: {raw_key}")
        if not value or len(value) > 160 or "\x00" in value or "\n" in value or "\r" in value:
            raise SlicerError(f"Invalid slicer setting value: {raw_key}")
        result[key] = value
    return result


def slice_stl(input_stl: Path, output_gcode: Path, *, overrides: dict[str, str] | None = None, config: SlicerConfig | None = None) -> dict:
    config = config or SlicerConfig.from_env()
    capability = slicer_capability(config)
    if not capability["available"]:
        raise SlicerError(capability.get("detail") or "No slicer is configured on this server.")
    if not input_stl.is_file() or input_stl.stat().st_size <= 0:
        raise SlicerError("No STL file is available for slicing.")
    executable = str(capability["binary"])
    output_gcode.parent.mkdir(parents=True, exist_ok=True)
    raw_overrides = {str(k): str(v) for k, v in (overrides or {}).items()}

    if config.backend == "prusa":
        overrides = _validated_overrides(raw_overrides, SAFE_PRUSA_OVERRIDES, "PrusaSlicer")
        command = [executable, "--export-gcode", "--output", str(output_gcode)]
        if config.profile:
            command += ["--load", str(Path(config.profile).resolve())]
        for key, value in sorted(overrides.items()):
            command += [f"--{key.replace('_', '-')}", value]
        command.append(str(input_stl))
    elif config.backend == "cura":
        overrides = _validated_overrides(raw_overrides, SAFE_CURA_OVERRIDES, "CuraEngine")
        command = [executable, "slice", "-v", "-j", str(Path(config.cura_definition).resolve()), "-o", str(output_gcode)]
        for key, value in sorted(overrides.items()):
            command += ["-s", f"{key}={value}"]
        command += ["-l", str(input_stl)]
    else:
        raise SlicerError("Unsupported slicer backend.")

    try:
        completed = subprocess.run(
            command,
            cwd=str(input_stl.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=config.timeout_seconds,
            check=False,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise SlicerError(f"Slicing exceeded the {config.timeout_seconds}-second timeout.") from exc
    except OSError as exc:
        raise SlicerError(f"Could not start the slicer process: {exc}") from exc
    if completed.returncode != 0 or not output_gcode.exists() or output_gcode.stat().st_size <= 0:
        combined = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
        if "objects could not fit on the bed" in combined or "could not fit on the bed" in combined:
            volume = configured_build_volume_mm(config)
            suffix = (
                f" ({volume[0]:.0f}×{volume[1]:.0f}×{volume[2]:.0f} mm)"
                if volume else ""
            )
            raise SlicerError(
                "Model does not fit within the configured printer build volume"
                f"{suffix}. Resize/reorient the model or select a larger printer profile."
            )
        raise SlicerError(
            f"{config.backend} slicing failed (exit={completed.returncode}). "
            f"stdout={_safe_tail(completed.stdout)} stderr={_safe_tail(completed.stderr)}"
        )
    return {
        "backend": config.backend,
        "path": str(output_gcode),
        "bytes": output_gcode.stat().st_size,
        "stdout_tail": _safe_tail(completed.stdout, 2000),
        "stderr_tail": _safe_tail(completed.stderr, 2000),
    }
