from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ui() -> str:
    return (ROOT/'src/standalonecad/ui/qt_app.py').read_text(encoding='utf-8')


def test_view_mesh_generation_is_off_the_gui_thread_and_cached():
    ui=_ui()
    assert '_build_mesh_snapshot_worker' in ui
    assert 'CADia-Mesh-' in ui or 'StandaloneCAD-Mesh-' in ui
    assert 'self._mesh_cache' in ui
    assert 'threading.Thread(target=self._build_mesh_snapshot_worker' in ui
    render_body=ui.split('def _render_locked(self):',1)[1].split('# ---------- tree / selection ----------',1)[0]
    assert '.tessellate(' not in render_body
    assert '.discretize(' not in render_body
    refresh_body=ui.split('def refresh(self,force=False):',1)[1].split('def _update_geometry_stats',1)[0]
    assert 'doc.topology()' not in refresh_body


def test_edges_are_built_lazily_only_for_edge_mode():
    ui=_ui()
    assert 'if build_edges:' in ui
    assert 'include_edges=edge_mode' in ui
    assert '.get("edges") is None' in ui


def test_intermediate_ai_revisions_do_not_trigger_heavy_view_rebuild():
    ui=_ui()
    assert 'if self.agent_busy:' in ui
    assert 'self._refresh_pending=True' in ui
    assert 'The final\n                # revision is rendered once when the agent finishes.' in ui
    assert '_engine_call_async' in ui
    assert 'Open file' in ui and ('CADia-Manual-' in ui or 'StandaloneCAD-Manual-' in ui)


def test_dark_and_light_themes_exist_with_dark_default():
    ui=_ui()
    assert 'DARK_STYLE' in ui and 'LIGHT_STYLE' in ui
    assert 'self.current_theme = "dark"' in ui
    assert 'self.theme_combo.addItems(("Dark","Light"))' in ui
    assert 'self.theme_combo.setCurrentText("Dark")' in ui
    assert 'THEME_COLORS' in ui


def test_ui_guarantees_are_documented_for_submission():
    # Submission documentation covers the user-facing CAD interaction guarantees.
    docs=((ROOT/'README.md').read_text(encoding='utf-8') + '\n' + (ROOT/'DEVPOST_SUBMISSION.md').read_text(encoding='utf-8'))
    assert 'B-Rep' in docs
    assert 'Face' in docs or 'face' in docs
    assert 'edge' in docs.lower()
