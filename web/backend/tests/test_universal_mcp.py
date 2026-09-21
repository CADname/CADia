from __future__ import annotations

from fastapi.testclient import TestClient

from standalonecad_web.main import app


def register(client: TestClient, email: str):
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "test-password-123", "display_name": "MCP Tester"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_project_scoped_mcp_token_and_tool_execution():
    with TestClient(app) as client:
        register(client, "universal-mcp@example.com")
        project_id = client.post("/api/projects", json={"name": "MCP project"}).json()["id"]

        created = client.post(
            f"/api/projects/{project_id}/mcp/tokens",
            json={"label": "pytest", "expires_days": 7},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        token = body["token"]
        token_id = body["id"]
        assert token.startswith("scad_mcp_")

        bridge = client.get("/api/mcp/bridge.py")
        assert bridge.status_code == 200
        assert b"CADia Web project MCP stdio bridge" in bridge.content

        headers = {"Authorization": f"Bearer {token}"}
        init = client.post(
            f"/api/mcp/projects/{project_id}",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        )
        assert init.status_code == 200, init.text
        assert init.json()["result"]["serverInfo"]["name"] == "CADia-Web-MCP"

        listing = client.post(
            f"/api/mcp/projects/{project_id}",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listing.status_code == 200, listing.text
        tools = listing.json()["result"]["tools"]
        names = [row["name"] for row in tools]
        assert len(tools) == 118
        assert "cad_create_box" in names
        assert "inventor_health" in names

        call = client.post(
            f"/api/mcp/projects/{project_id}",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "cad_create_box",
                    "arguments": {
                        "length_mm": 10,
                        "width_mm": 10,
                        "height_mm": 10,
                        "origin_mm": [0, 0, 0],
                        "centered": False,
                        "operation": "new",
                        "replace": False,
                        "name": "McpBox",
                    },
                },
            },
        )
        assert call.status_code == 200, call.text
        assert call.json()["result"].get("isError") is not True
        mesh = client.get(f"/api/projects/{project_id}/cad/mesh").json()
        assert abs(mesh["stats"]["volume_mm3"] - 1000.0) < 1e-6

        revoked = client.delete(f"/api/projects/{project_id}/mcp/tokens/{token_id}")
        assert revoked.status_code == 204
        denied = client.post(
            f"/api/mcp/projects/{project_id}",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}},
        )
        assert denied.status_code == 401
