from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import CurrentUser
from ..cad_runtime import _safe_name, runtime_manager
from ..config import settings
from ..database import get_db
from ..schemas import CommandRequest, SelectionRequest
from .projects import owned_project


router = APIRouter(prefix="/projects/{project_id}/cad", tags=["cad"])
ALLOWED_IMPORTS = {".step", ".stp", ".brep", ".brp", ".json"}


def runtime_for(project_id: str, user, db: Session):
    owned_project(db, user.id, project_id)
    try:
        return runtime_manager.get(user.id, project_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"CAD runtime start failed: {exc}") from exc


@router.get("/state")
def get_state(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    return runtime_for(project_id, user, db).state()


@router.get("/mesh")
def get_mesh(project_id: str, user: CurrentUser, db: Session = Depends(get_db), edges: bool = Query(True)):
    return runtime_for(project_id, user, db).mesh(include_edges=edges)


@router.post("/select")
def select_geometry(project_id: str, payload: SelectionRequest, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        selection = runtime.select(payload.model_dump(exclude_none=True))
        return {"selection": selection, "state": runtime.state()}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/command")
def run_command(project_id: str, payload: CommandRequest, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        result = runtime.command(payload.command, payload.arguments)
        return {"result": result, "state": runtime.state()}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/documents/{document_id}/activate")
def activate_document(project_id: str, document_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        result = runtime.activate_document(document_id)
        return {"result": result, "state": runtime.state()}
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/save")
def save_document(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        return {"result": runtime.save_active_document(), "state": runtime.state()}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import")
async def import_document(project_id: str, user: CurrentUser, db: Session = Depends(get_db), file: UploadFile = File(...)):
    runtime = runtime_for(project_id, user, db)
    original = Path(file.filename or "model.step").name
    lower = original.lower()
    is_scad = lower.endswith(".scad.json")
    if not is_scad and Path(lower).suffix not in ALLOWED_IMPORTS - {".json"}:
        raise HTTPException(status_code=415, detail="Only .scad.json, STEP, or BREP files can be opened.")
    suffix = ".scad.json" if is_scad else Path(lower).suffix
    target = runtime.root / "imports" / f"{_safe_name(original.removesuffix(suffix))}-{uuid.uuid4().hex[:8]}{suffix}"
    size = 0
    try:
        with target.open("xb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="The uploaded file exceeds the allowed size.")
                output.write(chunk)
        os.chmod(target, 0o600)
        result = runtime.import_document(target)
        return {"result": result, "state": runtime.state()}
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not open the file: {exc}") from exc
    finally:
        await file.close()


@router.get("/export/{kind}")
def export_document(project_id: str, kind: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        path = runtime.export(kind)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    media = "model/step" if kind.lower() == "step" else "model/stl"
    return FileResponse(path, media_type=media, filename=path.name)


@router.get("/download")
def download_document(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        runtime.save_active_document()
        path = Path(runtime.engine.doc.path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/json", filename=path.name)
