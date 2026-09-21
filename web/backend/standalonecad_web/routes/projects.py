from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser
from ..cad_runtime import runtime_manager
from ..database import get_db
from ..models import ChatMessage, Project
from ..schemas import ProjectCreate, ProjectOut, ProjectUpdate


router = APIRouter(prefix="/projects", tags=["projects"])


def owned_project(db: Session, owner_id: str, project_id: str) -> Project:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == owner_id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(user: CurrentUser, db: Session = Depends(get_db)):
    return list(db.scalars(select(Project).where(Project.owner_id == user.id).order_by(Project.updated_at.desc())))


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, user: CurrentUser, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Please enter a project name.")
    project = Project(owner_id=user.id, name=name, description=payload.description.strip())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    return owned_project(db, user.id, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, payload: ProjectUpdate, user: CurrentUser, db: Session = Depends(get_db)):
    project = owned_project(db, user.id, project_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Please enter a project name.")
        project.name = name
    if payload.description is not None:
        project.description = payload.description.strip()
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    project = owned_project(db, user.id, project_id)
    runtime_manager.drop(user.id, project.id)
    root = runtime_manager.project_root(user.id, project.id)
    db.delete(project)
    db.commit()
    if root.exists():
        shutil.rmtree(root)
    return Response(status_code=204)


@router.get("/{project_id}/messages")
def list_messages(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    project = owned_project(db, user.id, project_id)
    rows = list(db.scalars(
        select(ChatMessage)
        .where(ChatMessage.project_id == project.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(200)
    ).all())
    rows.reverse()
    return [{"id": item.id, "role": item.role, "content": item.content, "created_at": item.created_at} for item in rows]
