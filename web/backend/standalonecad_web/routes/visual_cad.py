from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..auth import CurrentUser
from ..cad_runtime import runtime_manager
from ..config import settings
from ..database import get_db
from ..visual_cad import ALLOWED_IMAGE_SUFFIXES, VisualCadJob, visual_cad_jobs
from .projects import owned_project


router = APIRouter(prefix="/projects/{project_id}/visual-cad", tags=["visual-cad"])


def _safe_upload_name(filename: str, fallback: str) -> tuple[str, str]:
    original = Path(filename or fallback).name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="Only PNG, JPG/JPEG, or WEBP engineering drawing images are supported.")
    return original, suffix


async def _save_upload(file: UploadFile, target: Path) -> None:
    size = 0
    try:
        with target.open("xb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="The engineering drawing image exceeds the allowed upload size.")
                output.write(chunk)
        os.chmod(target, 0o600)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post("/drawing/jobs")
async def create_drawing_job(
    project_id: str,
    user: CurrentUser,
    db: Session = Depends(get_db),
    primary: UploadFile = File(...),
    top: UploadFile | None = File(None),
    right: UploadFile | None = File(None),
    reference: UploadFile | None = File(None),
    notes: str = Form(""),
    dimension_hint: str = Form(""),
    model: str = Form("gpt-5.6-sol"),
    effort: str = Form("high"),
):
    owned_project(db, user.id, project_id)
    runtime = runtime_manager.get(user.id, project_id)
    job_id = uuid.uuid4().hex
    input_dir = runtime.root / "visual_cad" / job_id / "inputs"
    input_dir.mkdir(parents=True, exist_ok=False)

    uploads = {"primary": primary, "top": top, "right": right, "reference": reference}
    saved: dict[str, str] = {}
    try:
        for label, upload in uploads.items():
            if upload is None:
                continue
            _, suffix = _safe_upload_name(upload.filename or f"{label}.png", f"{label}.png")
            target = input_dir / f"{label}{suffix}"
            await _save_upload(upload, target)
            saved[label] = str(target)
    except Exception:
        for path in input_dir.glob("*"):
            path.unlink(missing_ok=True)
        try:
            input_dir.rmdir()
        except Exception:
            pass
        raise

    if not saved.get("primary"):
        raise HTTPException(status_code=422, detail="A primary engineering drawing image is required.")
    clean_effort = effort.strip().lower()
    if clean_effort not in {"low", "medium", "high"}:
        clean_effort = "high"
    job = VisualCadJob(
        id=job_id,
        user_id=user.id,
        project_id=project_id,
        input_files=saved,
        notes=notes.strip()[:4000],
        dimension_hint=dimension_hint.strip()[:1000],
        model=(model.strip() or "gpt-5.6-sol")[:100],
        effort=clean_effort,
    )
    visual_cad_jobs.create(job)
    return job.public()


@router.get("/jobs/{job_id}")
def get_job(project_id: str, job_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    owned_project(db, user.id, project_id)
    try:
        return visual_cad_jobs.get(job_id, user.id, project_id).public()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="The Drawing → CAD job could not be found.") from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_job(project_id: str, job_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    owned_project(db, user.id, project_id)
    try:
        return visual_cad_jobs.cancel(job_id, user.id, project_id).public()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="The Drawing → CAD job could not be found.") from exc
