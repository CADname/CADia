from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import CurrentUser
from ..cad_runtime import _safe_name
from ..database import get_db
from ..manufacturing import analyze_print_dfm, manufacturing_capabilities, write_3mf
from ..manufacturing.printers import PrinterError, configured_printers, upload_gcode
from ..manufacturing.slicer import SlicerError, configured_build_volume_mm, slice_stl
from ..manufacturing.step_export import write_step_ap242
from .cad import runtime_for


router = APIRouter(prefix="/projects/{project_id}/manufacturing", tags=["manufacturing"])


class SliceRequest(BaseModel):
    overrides: dict[str, str] = Field(default_factory=dict)


class SliceUploadRequest(BaseModel):
    overrides: dict[str, str] = Field(default_factory=dict)
    start: bool = False


def _export_dir(runtime) -> Path:
    path = runtime.root / "exports"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _active_title(runtime) -> str:
    with runtime.engine.lock:
        doc = runtime.engine.doc
        return _safe_name(doc.title if doc is not None else "model", "model")


def _effective_build_volume(
    build_x: float | None = None,
    build_y: float | None = None,
    build_z: float | None = None,
) -> tuple[float, float, float]:
    # Keep DFM and slicing on the same source of truth. Explicit query values
    # remain available for ad-hoc checks, but omitted values come from the
    # configured slicer profile instead of the old 256 mm hard-coded default.
    configured = configured_build_volume_mm() or (256.0, 256.0, 256.0)
    return (
        float(build_x) if build_x is not None else configured[0],
        float(build_y) if build_y is not None else configured[1],
        float(build_z) if build_z is not None else configured[2],
    )


def _preflight_build_volume(runtime) -> None:
    volume = _effective_build_volume()
    report = analyze_print_dfm(runtime.mesh(include_edges=False), build_volume_mm=volume)
    check = next((row for row in report.get("checks", []) if row.get("id") == "build_volume"), None)
    if check and check.get("status") == "fail":
        raise HTTPException(status_code=422, detail=check.get("message") or "Model does not fit the configured build volume.")


@router.get("/capabilities")
def capabilities(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime_for(project_id, user, db)
    return manufacturing_capabilities()


@router.get("/dfm")
def dfm_precheck(
    project_id: str,
    user: CurrentUser,
    db: Session = Depends(get_db),
    build_x: float | None = Query(None, gt=0, le=5000),
    build_y: float | None = Query(None, gt=0, le=5000),
    build_z: float | None = Query(None, gt=0, le=5000),
    overhang_angle_deg: float = Query(45.0, ge=0, le=89),
):
    runtime = runtime_for(project_id, user, db)
    try:
        mesh = runtime.mesh(include_edges=False)
        return analyze_print_dfm(mesh, build_volume_mm=_effective_build_volume(build_x, build_y, build_z), overhang_angle_deg=overhang_angle_deg)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/export/3mf")
def export_3mf(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        title = _active_title(runtime)
        path = _export_dir(runtime) / f"{title}-{uuid.uuid4().hex[:8]}.3mf"
        result = write_3mf(runtime.mesh(include_edges=False), path, title=title)
        # Optional official lib3mf validation is deliberately not a hard dependency.
        from ..manufacturing.three_mf import validate_with_lib3mf
        result["validation"] = validate_with_lib3mf(path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(path, media_type="model/3mf", filename=path.name, headers={"X-CADia-3MF": "core"})


@router.get("/export/step-ap242")
def export_step_ap242(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        with runtime.operation_lock, runtime.engine.lock:
            doc = runtime.engine.doc
            if doc is None or doc.shape is None:
                raise ValueError("No active B-Rep part is available for export.")
            title = _safe_name(doc.title, "model")
            path = _export_dir(runtime) / f"{title}-{uuid.uuid4().hex[:8]}-AP242.step"
            write_step_ap242(doc.shape, path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(path, media_type="model/step", filename=path.name, headers={"X-CADia-STEP-Schema": "AP242"})


@router.post("/slice")
def slice_model(project_id: str, payload: SliceRequest, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        _preflight_build_volume(runtime)
        stl = runtime.export("stl")
        gcode = _export_dir(runtime) / f"{stl.stem}-{uuid.uuid4().hex[:8]}.gcode"
        slice_stl(stl, gcode, overrides=payload.overrides)
    except SlicerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(gcode, media_type="text/x.gcode", filename=gcode.name)


@router.post("/slice-upload/{target_name}")
def slice_and_upload(
    project_id: str,
    target_name: str,
    payload: SliceUploadRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    runtime = runtime_for(project_id, user, db)
    targets = configured_printers()
    target = targets.get(target_name)
    if target is None:
        raise HTTPException(status_code=404, detail="Configured printer target was not found.")
    try:
        _preflight_build_volume(runtime)
        stl = runtime.export("stl")
        gcode = _export_dir(runtime) / f"{stl.stem}-{uuid.uuid4().hex[:8]}.gcode"
        slicing = slice_stl(stl, gcode, overrides=payload.overrides)
        delivery = upload_gcode(target, gcode, start_print=payload.start)
        return {"slicing": slicing, "delivery": delivery, "file": gcode.name}
    except (SlicerError, PrinterError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
