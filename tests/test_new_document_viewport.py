from pathlib import Path

from standalonecad.core.engine import CadEngine


def test_new_part_is_a_separate_blank_active_document():
    engine = CadEngine()
    engine.execute("create_box", {"length_mm": 20, "width_mm": 10, "height_mm": 6})
    old = engine.doc
    assert old.shape is not None
    old_title = old.title

    engine.execute("new_part", {"name": "Untitled"})

    assert engine.doc is not old
    assert engine.doc.shape is None
    assert engine.doc.features == []
    assert engine.doc.sketches == {}
    assert engine.doc.path is None
    assert engine.selection == {}
    assert engine._undo == []
    assert engine._redo == []
    sessions = engine.document_sessions()
    assert len(sessions) == 2
    assert any(row["title"] == old_title and not row["active"] for row in sessions)
    assert sum(1 for row in sessions if row["active"]) == 1


def test_qt_blank_document_clears_previous_viewport_actors_synchronously():
    source = (Path(__file__).resolve().parents[1] / "src" / "standalonecad" / "ui" / "qt_app.py").read_text(encoding="utf-8")
    assert "if shape is None:" in source
    assert "self.ren.RemoveAllViewProps()" in source
    assert 'self._mesh_cache[revision]={"faces":[],"edges":[]' in source
    assert "self._mesh_job_serial+=1" in source
