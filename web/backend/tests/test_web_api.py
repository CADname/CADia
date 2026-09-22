from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from standalonecad_web.cad_runtime import runtime_manager
from standalonecad_web.config import settings
from standalonecad_web.database import SessionLocal
from standalonecad_web.main import app
from standalonecad_web.models import AIProviderConnection, ChatMessage, McpAccessToken, Project, User


def register(client: TestClient, email: str):
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "test-password-123", "display_name": "Web Tester"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_guest_logout_permanently_removes_guest_data():
    with TestClient(app) as client:
        launched = client.post("/api/auth/guest")
        assert launched.status_code == 201, launched.text
        guest = launched.json()["user"]
        project = launched.json()["project"]
        user_id = guest["id"]
        project_id = project["id"]

        runtime = runtime_manager.get(user_id, project_id)
        marker = runtime.root / "guest-cleanup-marker.txt"
        marker.write_text("temporary guest data", encoding="utf-8")
        codex_root = settings.data_root / "codex-users" / user_id
        codex_root.mkdir(parents=True, exist_ok=True)
        (codex_root / "guest-auth-marker.txt").write_text("temporary auth data", encoding="utf-8")

        with SessionLocal() as db:
            db.add(ChatMessage(project_id=project_id, role="user", content="temporary guest message"))
            db.add(AIProviderConnection(user_id=user_id, provider="openai", encrypted_api_key="temporary", selected=True))
            db.add(McpAccessToken(project_id=project_id, owner_id=user_id, token_hash="guest-cleanup-token", label="temporary"))
            db.commit()

        logged_out = client.post("/api/auth/logout")
        assert logged_out.status_code == 204, logged_out.text
        assert client.get("/api/auth/me").status_code == 401
        assert not (settings.data_root / "projects" / user_id).exists()
        assert not codex_root.exists()

        with SessionLocal() as db:
            assert db.get(User, user_id) is None
            assert db.get(Project, project_id) is None
            assert db.scalar(select(ChatMessage).where(ChatMessage.project_id == project_id)) is None
            assert db.scalar(select(AIProviderConnection).where(AIProviderConnection.user_id == user_id)) is None
            assert db.scalar(select(McpAccessToken).where(McpAccessToken.owner_id == user_id)) is None


def test_registered_user_logout_preserves_account_and_project():
    with TestClient(app) as client:
        user = register(client, "persistent-logout@example.com")
        project = client.post("/api/projects", json={"name": "Persistent project"}).json()
        assert client.post("/api/auth/logout").status_code == 204

        with SessionLocal() as db:
            assert db.get(User, user["id"]) is not None
            assert db.get(Project, project["id"]) is not None


def test_auth_project_isolation_and_direct_cad_modeling():
    with TestClient(app) as client:
        first = register(client, "first@example.com")
        project_response = client.post("/api/projects", json={"name": "Cube project", "description": "isolation test"})
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        prompt = client.post(
            f"/api/ai/projects/{project_id}/prompt",
            json={"prompt": "Create a 10 mm cube.", "effort": "medium"},
        )
        assert prompt.status_code == 200, prompt.text
        assert prompt.json()["state"]["features"][0]["kind"] == "box"

        mesh = client.get(f"/api/projects/{project_id}/cad/mesh").json()
        assert len(mesh["faces"]) == 6
        assert len(mesh["edges"]) == 12
        assert abs(mesh["stats"]["volume_mm3"] - 1000.0) < 1e-6
        face = mesh["faces"][0]
        assert face["id"].startswith("F_")
        assert len(face["positions"]) >= 9
        assert len(face["indices"]) >= 3

        selected = client.post(
            f"/api/projects/{project_id}/cad/select",
            json={"type": "face", "face_ref": face["id"]},
        )
        assert selected.status_code == 200
        assert selected.json()["state"]["selection"]["face_ref"] == face["id"]

        edge = mesh["edges"][0]
        assert edge["id"].startswith("E_")
        assert len(edge["positions"]) >= 6
        selected_edge = client.post(
            f"/api/projects/{project_id}/cad/select",
            json={"type": "edge", "edge_ref": edge["id"]},
        )
        assert selected_edge.status_code == 200
        assert selected_edge.json()["state"]["selection"]["type"] == "edge"
        assert selected_edge.json()["state"]["selection"]["edge_ref"] == edge["id"]

        runtime_manager.drop(first["id"], project_id)
        restored = client.get(f"/api/projects/{project_id}/cad/state")
        assert restored.status_code == 200
        assert restored.json()["features"][0]["kind"] == "box"

        assert client.post("/api/auth/logout").status_code == 204
        register(client, "second@example.com")
        assert client.get(f"/api/projects/{project_id}").status_code == 404
        assert client.get(f"/api/projects/{project_id}/cad/state").status_code == 404


def test_undo_redo_and_document_lifecycle():
    with TestClient(app) as client:
        register(client, "lifecycle@example.com")
        project_id = client.post("/api/projects", json={"name": "Lifecycle"}).json()["id"]
        client.post(f"/api/ai/projects/{project_id}/prompt", json={"prompt": "Create a 12 mm cube."})

        undo = client.post(f"/api/projects/{project_id}/cad/command", json={"command": "undo", "arguments": {}})
        assert undo.status_code == 200
        assert undo.json()["state"]["document"]["has_geometry"] is False
        redo = client.post(f"/api/projects/{project_id}/cad/command", json={"command": "redo", "arguments": {}})
        assert redo.status_code == 200
        assert redo.json()["state"]["document"]["has_geometry"] is True

        new_part = client.post(
            f"/api/projects/{project_id}/cad/command",
            json={"command": "new_part", "arguments": {"name": "Second"}},
        )
        assert new_part.status_code == 200
        assert len(new_part.json()["state"]["open_documents"]) == 2

        forbidden = client.post(
            f"/api/projects/{project_id}/cad/command",
            json={"command": "delete_document_file", "arguments": {"confirm": True}},
        )
        assert forbidden.status_code == 422


def test_websocket_prompt_streams_state_result_and_mesh():
    with TestClient(app) as client:
        register(client, "socket@example.com")
        project_id = client.post("/api/projects", json={"name": "Socket"}).json()["id"]
        with client.websocket_connect(
            f"/api/ws/projects/{project_id}",
            headers={"origin": "http://localhost:5173"},
        ) as socket:
            assert socket.receive_json()["type"] == "state"
            assert socket.receive_json()["type"] == "mesh"
            socket.send_json({"type": "prompt", "payload": {"prompt": "Create an 8 mm cube."}})
            seen = set()
            result = None
            for _ in range(12):
                event = socket.receive_json()
                seen.add(event["type"])
                if event["type"] == "result":
                    result = event["payload"]
                if event["type"] == "busy" and event["payload"] is False:
                    break
            assert {"busy", "progress", "result", "mesh"} <= seen
            assert result["state"]["features"][0]["kind"] == "box"


def test_security_headers_and_project_name_validation():
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        register(client, "validation@example.com")
        assert client.post("/api/projects", json={"name": "   "}).status_code == 422


def test_uploaded_scad_document_cannot_reference_server_files():
    with TestClient(app) as client:
        register(client, "paths@example.com")
        project_id = client.post("/api/projects", json={"name": "Path policy"}).json()["id"]
        malicious = {
            "format": "StandaloneCAD-MCP",
            "version": 3,
            "title": "escape",
            "doc_type": "assembly",
            "occurrences": {"Outside:1": {"path": "/etc/passwd", "snapshot_brep_b64": "unused"}},
        }
        response = client.post(
            f"/api/projects/{project_id}/cad/import",
            files={"file": ("escape.scad.json", json.dumps(malicious), "application/json")},
        )
        assert response.status_code == 422
        assert "outside this project" in response.json()["detail"]
