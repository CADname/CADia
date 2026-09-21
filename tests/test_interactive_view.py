from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_uses_qt_vtk_frontend():
    req=(ROOT/'requirements.txt').read_text(encoding='utf-8')
    ui=(ROOT/'src/standalonecad/ui/qt_app.py').read_text(encoding='utf-8')
    assert 'PySide6-Essentials==6.11.2' in req
    assert 'QVTKRenderWindowInteractor' in ui
    assert 'vtkRenderingOpenGL2' in ui
    assert 'vtkTkRenderWindowInteractor' not in ui


def test_feature_tree_selection_is_model_linked():
    ui=(ROOT/'src/standalonecad/ui/qt_app.py').read_text(encoding='utf-8')
    assert 'itemSelectionChanged.connect(self._tree_selection_changed)' in ui
    assert '_show_feature_overlay' in ui
    assert '"type":"feature","feature_name"' in ui
    assert 'Selected feature' in ui


def test_interactive_controls_and_named_views_are_wired():
    ui=(ROOT/'src/standalonecad/ui/qt_app.py').read_text(encoding='utf-8')
    assert 'vtkInteractorStyleTrackballCamera' in ui
    assert 'MiddleButton' not in ui  # VTK style provides native pan without overriding it.
    assert 'wheel zoom' in ui
    assert 'set_camera_orientation' in ui
    assert 'self.ren.ResetCamera()' in ui
