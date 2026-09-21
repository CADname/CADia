from __future__ import annotations

from pathlib import Path

from standalonecad_web.manufacturing.slicer import prusa_profile_build_volume


def test_bundled_prusa_profile_build_volume_matches_actual_profile():
    profile = Path(__file__).parents[1] / "standalonecad_web" / "manufacturing" / "profiles" / "generic_pla_220.ini"
    assert prusa_profile_build_volume(profile) == (220.0, 220.0, 250.0)


def test_prusa_profile_parser_handles_offset_rectangular_bed(tmp_path: Path):
    profile = tmp_path / "offset.ini"
    profile.write_text("bed_shape = -10x-20,210x-20,210x180,-10x180\nmax_print_height = 300\n")
    assert prusa_profile_build_volume(profile) == (220.0, 200.0, 300.0)


def test_prusa_bed_fit_runtime_error_is_sanitized(monkeypatch, tmp_path: Path):
    import subprocess
    from standalonecad_web.manufacturing import slicer as slicer_mod

    profile = tmp_path / "printer.ini"
    profile.write_text("bed_shape = 0x0,220x0,220x220,0x220\nmax_print_height = 250\n")
    stl = tmp_path / "model.stl"
    stl.write_text("solid x\nendsolid x\n")
    out = tmp_path / "model.gcode"
    config = slicer_mod.SlicerConfig(backend="prusa", binary="/usr/bin/prusa-slicer", profile=str(profile), timeout_seconds=10)

    monkeypatch.setattr(slicer_mod, "slicer_capability", lambda _config=None: {
        "available": True,
        "binary": "/usr/bin/prusa-slicer",
        "detail": "",
    })
    monkeypatch.setattr(slicer_mod.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args=args[0], returncode=-6, stdout="", stderr="Objects could not fit on the bed"
    ))

    try:
        slicer_mod.slice_stl(stl, out, config=config)
    except slicer_mod.SlicerError as exc:
        message = str(exc)
    else:
        raise AssertionError("SlicerError was not raised")
    assert "220×220×250 mm" in message
    assert "Objects could not fit" not in message
