from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser
from ..cad_runtime import runtime_manager
from ..database import get_db
from ..mcp_gateway import ProjectMcpGateway
from ..models import McpAccessToken
from ..schemas import McpTokenCreate
from .projects import owned_project

router = APIRouter(tags=["mcp"])


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_token(row: McpAccessToken) -> dict[str, Any]:
    return {
        "id": row.id,
        "projectId": row.project_id,
        "label": row.label,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "lastUsedAt": row.last_used_at.isoformat() if row.last_used_at else None,
        "revokedAt": row.revoked_at.isoformat() if row.revoked_at else None,
    }


@router.post("/projects/{project_id}/mcp/tokens", status_code=201)
def create_token(project_id: str, payload: McpTokenCreate, request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    owned_project(db, user.id, project_id)
    token = "scad_mcp_" + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    row = McpAccessToken(
        project_id=project_id,
        owner_id=user.id,
        token_hash=_hash_token(token),
        label=payload.label.strip(),
        created_at=now,
        expires_at=now + timedelta(days=payload.expires_days),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    base = str(request.base_url).rstrip("/")
    return {
        **_public_token(row),
        "token": token,
        "server": base,
        "rpcUrl": f"{base}/api/mcp/projects/{project_id}",
        "bridgeDownload": f"{base}/api/mcp/bridge.py",
        "warning": "This token will not be shown again. Remote use is recommended only over HTTPS.",
    }


@router.get("/projects/{project_id}/mcp/tokens")
def list_tokens(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    owned_project(db, user.id, project_id)
    rows = db.scalars(
        select(McpAccessToken)
        .where(McpAccessToken.project_id == project_id, McpAccessToken.owner_id == user.id)
        .order_by(McpAccessToken.created_at.desc())
    ).all()
    return [_public_token(row) for row in rows]


@router.delete("/projects/{project_id}/mcp/tokens/{token_id}", status_code=204)
def revoke_token(project_id: str, token_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    owned_project(db, user.id, project_id)
    row = db.get(McpAccessToken, token_id)
    if row is None or row.project_id != project_id or row.owner_id != user.id:
        raise HTTPException(status_code=404, detail="MCP token not found.")
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=204)


def _token_row(project_id: str, authorization: str | None, db: Session) -> McpAccessToken:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="MCP Bearer token is required.")
    raw = authorization[7:].strip()
    if not raw.startswith("scad_mcp_"):
        raise HTTPException(status_code=401, detail="Invalid MCP token.")
    row = db.scalar(select(McpAccessToken).where(McpAccessToken.token_hash == _hash_token(raw)))
    now = datetime.now(timezone.utc)
    if row is None or row.project_id != project_id or row.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Invalid MCP token.")
    if row.expires_at is not None:
        expiry = row.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now:
            raise HTTPException(status_code=401, detail="MCP token has expired.")
    row.last_used_at = now
    db.commit()
    return row


@router.post("/mcp/projects/{project_id}")
def mcp_rpc(
    project_id: str,
    request_payload: dict[str, Any],
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    row = _token_row(project_id, authorization, db)
    owned_project(db, row.owner_id, project_id)
    runtime = runtime_manager.get(row.owner_id, project_id)
    response = ProjectMcpGateway(runtime).dispatch(request_payload)
    if response is None:
        return Response(status_code=204)
    return JSONResponse(response)


@router.get("/mcp/bridge.py")
def download_bridge():
    path = Path(__file__).resolve().parents[4] / "tools" / "cadia_web_mcp_bridge.py"
    if not path.exists():
        raise HTTPException(status_code=404, detail="MCP bridge script not found")
    return FileResponse(path, media_type="text/x-python", filename="cadia_web_mcp_bridge.py")
