from __future__ import annotations

from pathlib import Path

from standalonecad.codex_agent import CodexAgent
from standalonecad_web.web_agent import DesktopParityCodexAgent, desktop_catalog


def test_web_parity_agent_inherits_desktop_algorithm_methods_exactly():
    # Planner algorithm methods are not copied/reimplemented in the web layer.
    assert DesktopParityCodexAgent.plan is CodexAgent.plan
    assert DesktopParityCodexAgent.repair is CodexAgent.repair
    assert DesktopParityCodexAgent._make_catalog is CodexAgent._make_catalog
    assert DesktopParityCodexAgent._run_planner is CodexAgent._run_planner
    assert DesktopParityCodexAgent._exec_cmd is CodexAgent._exec_cmd
    assert DesktopParityCodexAgent.cancel is CodexAgent.cancel


def test_web_catalog_is_exact_desktop_catalog(tmp_path: Path):
    desktop = CodexAgent("parity-test", tmp_path)
    assert desktop_catalog() == desktop._catalog
    assert len(desktop_catalog()) == len(desktop._catalog)
    assert {row["name"] for row in desktop_catalog()} == {row["name"] for row in desktop._catalog}


def test_parity_agent_codex_exec_uses_user_codex_home_and_original_exec_path(tmp_path: Path):
    import os

    fake = tmp_path / "fake-codex"
    fake.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
if len(sys.argv) > 1 and sys.argv[1] == 'exec':
    assert os.environ.get('CODEX_HOME', '').endswith('/codex-home')
    assert '--json' in sys.argv and '--sandbox' in sys.argv and 'read-only' in sys.argv
    out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])
    _ = sys.stdin.read()
    out.write_text(json.dumps({'calls':[{'tool':'cad_create_box','arguments':{'length_mm':1,'width_mm':1,'height_mm':1,'origin_mm':[0,0,0],'centered':False,'operation':'new','replace':True,'name':'Box'}}],'note':'fake'}))
    print(json.dumps({'type':'turn.started'}))
    print(json.dumps({'type':'turn.completed'}))
    raise SystemExit(0)
raise SystemExit(0)
""",
        encoding="utf-8",
    )
    os.chmod(fake, 0o755)

    class FakeClient:
        def __init__(self):
            self.codex_home = tmp_path / "codex-home"
            self.workspace = tmp_path / "workspace"
            self.codex_home.mkdir()
            self.workspace.mkdir()
        def _resolve_executable(self):
            return str(fake)

    agent = DesktopParityCodexAgent(FakeClient(), "parity", tmp_path, model="gpt-5.6-sol", reasoning="medium")
    plan = agent.plan("PARITY_FAKE_CODEX_SENTINEL", {"document": None})
    assert plan["note"] == "fake"
    assert plan["calls"][0]["tool"] == "cad_create_box"
