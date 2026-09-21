from __future__ import annotations

import os
import queue
import threading
from pathlib import Path

# VTK wheels ship a working Qt interactor, unlike the Tk rendering extension.
# Import the OpenGL implementation before creating vtkRenderWindow objects.
import vtkmodules.vtkInteractionStyle  # noqa: F401
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkRenderingCore import vtkCellPicker, vtkRenderer

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSlider,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from standalonecad import __version__
from standalonecad.bridge.host import CadHost
from standalonecad.codex_agent import CodexAgent
from standalonecad.core.engine import CadEngine
from standalonecad.core.render import set_camera_orientation, sample_edge_points
from standalonecad.planning import PlanExecutor
from standalonecad.recovery import execute_with_failure_recovery


DARK_STYLE = """
QWidget { background:#15171b; color:#eef1f6; font-family:'Segoe UI'; font-size:10pt; }
QFrame#panel { background:#1d2026; border:1px solid #343a46; border-radius:3px; }
QPushButton { background:#242831; border:0; padding:6px 10px; border-radius:3px; }
QPushButton:hover { background:#2b303a; }
QPushButton#primary { background:#5b7cff; color:white; font-weight:600; padding:8px 16px; }
QPushButton#danger { color:#e06c75; }
QPushButton:disabled { color:#697181; background:#20242b; }
QComboBox { background:#242831; border:1px solid #343a46; padding:5px 8px; }
QTreeWidget, QTextEdit, QPlainTextEdit { background:#1d2026; border:1px solid #343a46; selection-background-color:#5b7cff; }
QPlainTextEdit#prompt { background:#191d25; border:2px solid #5b7cff; padding:8px; }
QTabWidget::pane { border:1px solid #343a46; }
QTabBar::tab { background:#1d2026; color:#98a1b2; padding:7px 12px; }
QTabBar::tab:selected { background:#242831; color:#eef1f6; }
QLabel#muted { color:#98a1b2; }
QLabel#header { font-weight:600; font-size:11pt; }
"""

LIGHT_STYLE = """
QWidget { background:#f4f6f9; color:#1f2530; font-family:'Segoe UI'; font-size:10pt; }
QFrame#panel { background:#ffffff; border:1px solid #d5dae3; border-radius:3px; }
QPushButton { background:#e9edf3; color:#1f2530; border:0; padding:6px 10px; border-radius:3px; }
QPushButton:hover { background:#dde3eb; }
QPushButton#primary { background:#4e6ff2; color:white; font-weight:600; padding:8px 16px; }
QPushButton#danger { color:#c4424f; }
QPushButton:disabled { color:#9aa3b1; background:#eef1f5; }
QComboBox { background:#ffffff; color:#1f2530; border:1px solid #cfd5df; padding:5px 8px; }
QTreeWidget, QTextEdit, QPlainTextEdit { background:#ffffff; color:#1f2530; border:1px solid #d5dae3; selection-background-color:#6985f5; selection-color:white; }
QPlainTextEdit#prompt { background:#ffffff; border:2px solid #4e6ff2; padding:8px; }
QTabWidget::pane { border:1px solid #d5dae3; }
QTabBar::tab { background:#eef1f5; color:#667085; padding:7px 12px; }
QTabBar::tab:selected { background:#ffffff; color:#1f2530; }
QLabel#muted { color:#667085; }
QLabel#header { font-weight:600; font-size:11pt; }
"""

THEME_COLORS = {
    "dark": {
        "background": (0.067, 0.075, 0.098),
        "face": (0.72, 0.76, 0.84),
        "face_edge": (0.18, 0.21, 0.27),
        "edge": (0.45, 0.65, 1.0),
        "selected_face": (0.38, 0.55, 1.0),
        "selected_edge": (1.0, 0.72, 0.20),
        "overlay_add": (1.0, 0.62, 0.15),
        "overlay_cut": (1.0, 0.40, 0.28),
    },
    "light": {
        "background": (0.94, 0.95, 0.97),
        "face": (0.70, 0.74, 0.82),
        "face_edge": (0.24, 0.28, 0.35),
        "edge": (0.20, 0.42, 0.82),
        "selected_face": (0.22, 0.45, 0.92),
        "selected_edge": (0.94, 0.55, 0.05),
        "overlay_add": (0.95, 0.48, 0.05),
        "overlay_cut": (0.88, 0.25, 0.18),
    },
}



class UiEvents(QObject):
    cad_changed = Signal()
    agent_progress = Signal(object)
    agent_done = Signal(str)
    agent_error = Signal(str)
    mesh_ready = Signal(int, int, object)
    mesh_error = Signal(int, int, str)
    overlay_ready = Signal(int, int, str, object)
    overlay_error = Signal(int, int, str, str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"CADia AI v{__version__}")
        self.resize(1480, 900)
        self.setMinimumSize(1080, 700)
        self.current_theme = "dark"
        self.setStyleSheet(DARK_STYLE)

        self.events_queue: queue.Queue = queue.Queue()
        self.events = UiEvents()
        self.events.mesh_ready.connect(self._mesh_ready)
        self.events.mesh_error.connect(self._mesh_error)
        self.events.overlay_ready.connect(self._overlay_ready)
        self.events.overlay_error.connect(self._overlay_error)
        self.engine = CadEngine(lambda: self.events_queue.put(("cad_changed", None)))
        self.host = CadHost(self.engine)
        self.host.start()
        project_root = Path(__file__).resolve().parents[3]
        self.agent = CodexAgent(self.host.info["target_id"], project_root)
        self.executor = PlanExecutor(self.engine, self.host.info["target_id"], target_info=self.host.info)
        try:
            self.control_health = self.executor.health_check()
            self.control_ready = True
        except Exception as exc:
            self.control_health = {"ok": False, "error": str(exc)}
            self.control_ready = False

        self.agent_busy = False
        self.manual_busy = False
        self.actor_face = {}
        self.actor_edge = {}
        self._feature_overlay_actors = []
        self._selected_actor = None
        self.selected_face_ref = None
        self.selected_edge_ref = None
        self.selected_feature_name = None
        self._last_revision = -1
        self._camera_initialized = False
        self._last_actor_count = 0
        self._tree_rebuilding = False
        self._vtk_ready = False
        self._vtk_error = None
        self._mesh_cache = {}
        self._mesh_job_serial = 0
        self._pending_mesh_key = None
        self._last_topology = {"faces": [], "edges": [], "vertices": []}
        self._overlay_cache = {}
        self._overlay_job_serial = 0
        self._refresh_pending = False
        self._document_combo_rebuilding = False

        # View-only state. These controls never modify the CAD document/B-Rep.
        self._section_mode = "off"
        self._section_flip = False
        self._section_plane = None
        self._fallback_actor = None
        self._transparent_targets = {}
        self._actor_occurrence = {}

        self._build_ui()
        self._build_vtk()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(100)
        self.refresh(force=True)
        self.prompt.setFocus()

        if not self.control_ready:
            self.agent_status.setText("Control error")
            self._append_chat("error", "CAD CAD control initialization failed: " + str(self.control_health.get("error", "unknown error")))
        elif self.agent.codex_exe:
            self.agent_status.setText("Ready")
        else:
            self.agent_status.setText("AI sign-in required")

    # ---------- UI ----------
    def _panel(self):
        w = QFrame(); w.setObjectName("panel"); return w

    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(8, 8, 8, 8); outer.setSpacing(7)

        bar = self._panel(); bl = QHBoxLayout(bar); bl.setContentsMargins(12, 7, 12, 7)
        brand = QVBoxLayout(); title = QLabel("CADia"); title.setObjectName("header"); sub = QLabel("AI Parametric CAD"); sub.setObjectName("muted"); brand.addWidget(title); brand.addWidget(sub); bl.addLayout(brand); bl.addSpacing(14)
        for text, fn in (("New Part", self.new), ("New Assembly", self.new_assembly), ("Open", self.open), ("Save", self.save)):
            b=QPushButton(text); b.clicked.connect(fn); bl.addWidget(b)
        bl.addSpacing(8)
        doclab=QLabel("Document"); doclab.setObjectName("muted"); bl.addWidget(doclab)
        self.document_combo=QComboBox(); self.document_combo.setMinimumWidth(150); self.document_combo.currentIndexChanged.connect(self._activate_document_from_combo); bl.addWidget(self.document_combo)
        bl.addSpacing(8)
        undo=QPushButton("↶"); undo.setFixedWidth(36); undo.clicked.connect(lambda: self._engine_call_async(self.engine.undo,"Undo")); bl.addWidget(undo)
        redo=QPushButton("↷"); redo.setFixedWidth(36); redo.clicked.connect(lambda: self._engine_call_async(self.engine.redo,"Redo")); bl.addWidget(redo)
        save_all=QPushButton("Save All"); save_all.clicked.connect(lambda: self._engine_call_async(self.engine.save_all,"Save All")); bl.addWidget(save_all)
        delete_doc=QPushButton("Delete Document"); delete_doc.setObjectName("danger"); delete_doc.setToolTip("The current saved CADia Part/Assembly document file will be deleted from disk."); delete_doc.clicked.connect(self.delete_document_file); bl.addWidget(delete_doc)
        relink=QPushButton("Relink"); relink.setToolTip("Selected Assembly occurrence source Part/Subassembly file path will be relinked."); relink.clicked.connect(self.relink_selected_occurrence); bl.addWidget(relink)
        fit=QPushButton("Fit"); fit.clicked.connect(self.fit); bl.addWidget(fit)
        bl.addStretch(1)
        viewlab=QLabel("View"); viewlab.setObjectName("muted"); bl.addWidget(viewlab)
        self.orientation=QComboBox(); self.orientation.addItems(("iso_top_right","iso_top_left","front","back","top","bottom","left","right")); self.orientation.currentTextChanged.connect(self.set_orientation); bl.addWidget(self.orientation)
        themelab=QLabel("Theme"); themelab.setObjectName("muted"); bl.addWidget(themelab)
        self.theme_combo=QComboBox(); self.theme_combo.addItems(("Dark","Light")); self.theme_combo.setCurrentText("Dark"); self.theme_combo.currentTextChanged.connect(self._set_theme); bl.addWidget(self.theme_combo)
        outer.addWidget(bar)

        split=QSplitter(Qt.Horizontal); outer.addWidget(split,1)
        left=self._panel(); center=self._panel(); right=self._panel(); split.addWidget(left); split.addWidget(center); split.addWidget(right); split.setSizes([230,850,400]); split.setStretchFactor(1,1)

        # model tree
        ll=QVBoxLayout(left); ll.setContentsMargins(8,8,8,8)
        lh=QHBoxLayout(); lab=QLabel("Model"); lab.setObjectName("header"); self.doc_badge=QLabel("PART"); self.doc_badge.setObjectName("muted"); lh.addWidget(lab); lh.addStretch(); lh.addWidget(self.doc_badge); ll.addLayout(lh)
        self.tree=QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setSelectionMode(QTreeWidget.SingleSelection); self.tree.itemSelectionChanged.connect(self._tree_selection_changed); ll.addWidget(self.tree,1)
        self.doc_summary=QLabel("No geometry"); self.doc_summary.setObjectName("muted"); self.doc_summary.setWordWrap(True); ll.addWidget(self.doc_summary)

        # viewport
        cl=QVBoxLayout(center); cl.setContentsMargins(8,8,8,8)
        ch=QHBoxLayout(); cv=QLabel("3D View"); cv.setObjectName("header"); ch.addWidget(cv); ch.addStretch(); self.selection_text=QLabel("Left-drag rotate - middle-drag pan - wheel zoom"); self.selection_text.setObjectName("muted"); ch.addWidget(self.selection_text); self.selection_mode=QComboBox(); self.selection_mode.addItems(("Face","Edge")); self.selection_mode.currentTextChanged.connect(self._apply_pick_mode); ch.addWidget(self.selection_mode); cl.addLayout(ch)
        vh=QHBoxLayout(); section_label=QLabel("Section"); section_label.setObjectName("muted"); vh.addWidget(section_label); self.section_combo=QComboBox(); self.section_combo.addItems(("Off","XY","XZ","YZ")); self.section_combo.currentTextChanged.connect(self._section_mode_changed); vh.addWidget(self.section_combo); self.section_slider=QSlider(Qt.Horizontal); self.section_slider.setRange(0,1000); self.section_slider.setValue(500); self.section_slider.setMinimumWidth(150); self.section_slider.setEnabled(False); self.section_slider.valueChanged.connect(self._section_position_changed); vh.addWidget(self.section_slider,1); self.section_value=QLabel("-"); self.section_value.setObjectName("muted"); self.section_value.setMinimumWidth(78); vh.addWidget(self.section_value); self.section_flip_btn=QPushButton("Flip direction"); self.section_flip_btn.setEnabled(False); self.section_flip_btn.clicked.connect(self._flip_section); vh.addWidget(self.section_flip_btn); self.transparency_btn=QPushButton("Transparency"); self.transparency_btn.clicked.connect(self._toggle_selected_transparency); vh.addWidget(self.transparency_btn); cl.addLayout(vh)
        self.viewport_holder=QFrame(); self.viewport_holder.setObjectName("panel"); self.viewport_layout=QVBoxLayout(self.viewport_holder); self.viewport_layout.setContentsMargins(0,0,0,0); cl.addWidget(self.viewport_holder,1)

        # AI + inspector
        rl=QVBoxLayout(right); rl.setContentsMargins(8,8,8,8)
        rh=QHBoxLayout(); ai=QLabel("AI"); ai.setObjectName("header"); self.agent_status=QLabel("Starting…"); self.agent_status.setObjectName("muted"); rh.addWidget(ai); rh.addStretch(); rh.addWidget(self.agent_status); rl.addLayout(rh)
        chathead=QHBoxLayout(); x=QLabel("Conversation / execution log"); y=QLabel("Read-only"); y.setObjectName("muted"); chathead.addWidget(x); chathead.addStretch(); chathead.addWidget(y); rl.addLayout(chathead)
        self.chat=QTextEdit(); self.chat.setReadOnly(True); rl.addWidget(self.chat,1)
        compose_head=QHBoxLayout(); p1=QLabel("Enter modeling request"); p1.setObjectName("header"); p2=QLabel("Click here to type"); p2.setObjectName("muted"); compose_head.addWidget(p1); compose_head.addStretch(); compose_head.addWidget(p2); rl.addLayout(compose_head)
        self.prompt=QPlainTextEdit(); self.prompt.setObjectName("prompt"); self.prompt.setPlaceholderText("Example: M10 Create a standard hex bolt."); self.prompt.setFixedHeight(125); rl.addWidget(self.prompt)
        actions=QHBoxLayout(); self.mode=QComboBox(); self.mode.addItems(("Fast","Balanced","Maximum")); self.mode.setCurrentText("Balanced"); actions.addWidget(self.mode); hint=QLabel("Ctrl+Enter Run"); hint.setObjectName("muted"); actions.addWidget(hint); actions.addStretch(); self.send_btn=QPushButton("Run"); self.send_btn.setObjectName("primary"); self.send_btn.clicked.connect(self.send_prompt); self.stop_btn=QPushButton("Stop"); self.stop_btn.setObjectName("danger"); self.stop_btn.setEnabled(False); self.stop_btn.clicked.connect(self.stop_agent); actions.addWidget(self.send_btn); actions.addWidget(self.stop_btn); rl.addLayout(actions)
        self.progress=QProgressBar(); self.progress.setRange(0,100); self.progress.setValue(0); self.progress.setTextVisible(True); self.progress.setFormat("Estimated progress 0%"); rl.addWidget(self.progress)
        shortcut=QShortcut(QKeySequence("Ctrl+Return"), self.prompt); shortcut.activated.connect(self.send_prompt)
        shortcut2=QShortcut(QKeySequence("Ctrl+Enter"), self.prompt); shortcut2.activated.connect(self.send_prompt)

        tabs=QTabWidget(); self.params=QPlainTextEdit(); self.params.setReadOnly(True); self.info=QPlainTextEdit(); self.info.setReadOnly(True); tabs.addTab(self.params,"Properties"); tabs.addTab(self.info,"Info"); tabs.setMaximumHeight(185); rl.addWidget(tabs)

        statusbar=self._panel(); sl=QHBoxLayout(statusbar); sl.setContentsMargins(10,4,10,4); self.status=QLabel("Preparing…"); self.status.setObjectName("muted"); self.runtime_badge=QLabel("B-REP · Ready"); self.runtime_badge.setObjectName("muted"); sl.addWidget(self.status); sl.addStretch(); sl.addWidget(self.runtime_badge); outer.addWidget(statusbar)

    # ---------- theme / VTK viewport ----------
    def _theme_colors(self):
        return THEME_COLORS[self.current_theme]

    def _set_theme(self, text=None):
        theme = "light" if (text or "").strip() == "Light" else "dark"
        self.current_theme = theme
        self.setStyleSheet(LIGHT_STYLE if theme == "light" else DARK_STYLE)
        if not self._vtk_ready:
            return
        colors = self._theme_colors()
        self.ren.SetBackground(*colors["background"])
        for actor in self.actor_face:
            if actor is self._selected_actor and self.selected_face_ref:
                actor.GetProperty().SetColor(*colors["selected_face"])
            else:
                actor.GetProperty().SetColor(*colors["face"])
            actor.GetProperty().SetEdgeColor(*colors["face_edge"])
        for actor in self.actor_edge:
            if actor is self._selected_actor and self.selected_edge_ref:
                actor.GetProperty().SetColor(*colors["selected_edge"])
                actor.GetProperty().SetLineWidth(5.0)
            else:
                actor.GetProperty().SetColor(*colors["edge"])
                actor.GetProperty().SetLineWidth(3.0)
        # Rebuild only the lightweight overlay actors so their colors follow the theme.
        if self.selected_feature_name:
            key = (self.engine.revision, self.selected_feature_name)
            cached = self._overlay_cache.get(key)
            if cached is not None:
                self._apply_overlay_data(cached, render=False)
        self.vtk.GetRenderWindow().Render()

    def _build_vtk(self):
        try:
            self.vtk=QVTKRenderWindowInteractor(self.viewport_holder)
            self.viewport_layout.addWidget(self.vtk)
            self.ren=vtkRenderer(); self.vtk.GetRenderWindow().AddRenderer(self.ren)
            self.ren.SetBackground(*self._theme_colors()["background"])
            style=vtkInteractorStyleTrackballCamera(); style.AddObserver("LeftButtonPressEvent", self._pick_geometry)
            self.vtk.GetRenderWindow().GetInteractor().SetInteractorStyle(style); self._vtk_style=style
            self.vtk.Initialize()
            self._vtk_ready=True
        except Exception as exc:
            self._vtk_error=str(exc)
            msg=QLabel("3D Interaction initialization failed\n"+self._vtk_error); msg.setAlignment(Qt.AlignCenter); msg.setStyleSheet("color:#e06c75;"); self.viewport_layout.addWidget(msg)
            self._vtk_ready=False

    def _current_session_id(self):
        try:
            row=next((x for x in self.engine.document_sessions() if x.get("active")),None)
            return row.get("id") if row else None
        except Exception:
            return None

    def _section_mode_changed(self, text=None):
        mode=(text or "").strip().upper()
        self._section_mode=mode if mode in ("XY","XZ","YZ") else "off"
        enabled=self._section_mode!="off"
        self.section_slider.setEnabled(enabled); self.section_flip_btn.setEnabled(enabled)
        if enabled and self.section_slider.value()!=500:
            # Keep the user's last slider position when switching planes.
            pass
        elif enabled:
            self.section_slider.setValue(500)
        self._apply_section_view()

    def _section_position_changed(self, *_):
        if self._section_mode!="off":self._apply_section_view()

    def _flip_section(self):
        self._section_flip=not self._section_flip
        self._apply_section_view()

    def _section_definition(self):
        doc=self.engine.doc
        if doc is None or doc.shape is None or self._section_mode=="off":return None
        try:bb=doc.shape.BoundingBox()
        except Exception:return None
        axis={"YZ":0,"XZ":1,"XY":2}[self._section_mode]
        lows=(float(bb.xmin),float(bb.ymin),float(bb.zmin)); highs=(float(bb.xmax),float(bb.ymax),float(bb.zmax))
        t=float(self.section_slider.value())/1000.0; coord=lows[axis]+(highs[axis]-lows[axis])*t
        origin=[0.5*(float(bb.xmin)+float(bb.xmax)),0.5*(float(bb.ymin)+float(bb.ymax)),0.5*(float(bb.zmin)+float(bb.zmax))]; origin[axis]=coord
        normal=[0.0,0.0,0.0]; normal[axis]=-1.0 if self._section_flip else 1.0
        return origin,normal,coord

    def _iter_view_actors(self):
        actors=[]; seen=set()
        for actor in [*self.actor_face.keys(),*self.actor_edge.keys(),*self._feature_overlay_actors, self._fallback_actor]:
            if actor is None or id(actor) in seen:continue
            seen.add(id(actor)); actors.append(actor)
        return actors

    def _apply_section_view(self, render=True):
        if not self._vtk_ready:return
        from vtkmodules.vtkCommonDataModel import vtkPlane
        definition=self._section_definition()
        plane=None
        if definition is not None:
            origin,normal,coord=definition; plane=vtkPlane(); plane.SetOrigin(*origin); plane.SetNormal(*normal); self._section_plane=plane
            self.section_value.setText(f"{coord:.2f} mm")
        else:
            self._section_plane=None
            if hasattr(self,"section_value"):self.section_value.setText("-")
        for actor in self._iter_view_actors():
            try:
                mapper=actor.GetMapper(); mapper.RemoveAllClippingPlanes()
                if plane is not None:mapper.AddClippingPlane(plane)
            except Exception:pass
        if render:self.vtk.GetRenderWindow().Render()

    def _transparent_set(self):
        sid=self._current_session_id()
        if not sid:return set()
        return self._transparent_targets.setdefault(sid,set())

    def _rebuild_actor_occurrence_map(self):
        """Bind assembly viewport actors to their occurrence directly.

        Assembly mesh records are built per occurrence, so ownership is explicit and
        never inferred from compound face/edge ids.  This keeps transparency deterministic
        even for repeated identical components.
        """
        self._actor_occurrence={}
        doc=self.engine.doc
        if doc is None or doc.doc_type!="assembly":return
        for actor,rec in self.actor_face.items():
            owner=rec.get("occurrence_name")
            if owner is not None:self._actor_occurrence[actor]=str(owner)
        for actor,rec in self.actor_edge.items():
            owner=rec.get("occurrence_name")
            if owner is not None:self._actor_occurrence[actor]=str(owner)

    def _selected_occurrence_name(self):
        sel=dict(self.engine.selection or {})
        name=sel.get("occurrence_name")
        return str(name) if name else None

    def _apply_transparency(self, render=True):
        if not self._vtk_ready:return
        for actor in self.actor_face:
            try:actor.GetProperty().SetOpacity(1.0)
            except Exception:pass
        for actor in self.actor_edge:
            try:actor.GetProperty().SetOpacity(1.0)
            except Exception:pass
        if self._fallback_actor is not None:
            try:self._fallback_actor.GetProperty().SetOpacity(1.0)
            except Exception:pass
        doc=self.engine.doc; targets=self._transparent_set()
        if doc is not None and targets:
            whole_document=(doc.doc_type=="part" and "__PART__" in targets) or (doc.doc_type=="assembly" and "__ASSEMBLY__" in targets)
            if whole_document:
                # Part and Assembly deliberately share the same document-level toggle:
                # one click makes every visible model actor transparent; the next restores it.
                for actor in self.actor_face:
                    try:actor.GetProperty().SetOpacity(0.25)
                    except Exception:pass
                for actor in self.actor_edge:
                    try:actor.GetProperty().SetOpacity(0.25)
                    except Exception:pass
                if self._fallback_actor is not None:
                    try:self._fallback_actor.GetProperty().SetOpacity(0.25)
                    except Exception:pass
        self._sync_transparency_button()
        if render:self.vtk.GetRenderWindow().Render()

    def _sync_transparency_button(self):
        if not hasattr(self,"transparency_btn"):return
        doc=self.engine.doc; targets=self._transparent_set()
        selected=False
        if doc is not None and doc.doc_type=="part":selected="__PART__" in targets
        elif doc is not None and doc.doc_type=="assembly":selected="__ASSEMBLY__" in targets
        self.transparency_btn.setText("Disable transparency" if selected else "Transparency")

    def _toggle_selected_transparency(self):
        doc=self.engine.doc
        if doc is None:return
        targets=self._transparent_set()
        target="__PART__" if doc.doc_type=="part" else "__ASSEMBLY__"
        if target in targets:targets.remove(target)
        else:targets.add(target)
        self._apply_transparency()
        self.status.setText(("Disable transparency · " if target not in targets else "Transparency 25% · ")+doc.title)

    @staticmethod
    def _shape_mesh_data(cq_shape, tolerance=0.05):
        verts,tris=cq_shape.tessellate(tolerance)
        return {
            "verts": [(float(v.x), float(v.y), float(v.z)) for v in (verts or [])],
            "tris": [tuple(int(i) for i in tri) for tri in (tris or []) if len(tri)==3],
        }

    @staticmethod
    def _edge_mesh_data(edge, tolerance=0.08):
        return sample_edge_points(edge, tolerance)

    def _vtk_actor_from_mesh(self, mesh):
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkCellArray,vtkPolyData,vtkTriangle
        from vtkmodules.vtkRenderingCore import vtkActor,vtkPolyDataMapper
        verts=mesh.get("verts",[]); tris=mesh.get("tris",[])
        if not verts or not tris:return None
        points=vtkPoints()
        for x,y,z in verts:points.InsertNextPoint(x,y,z)
        cells=vtkCellArray()
        for tri in tris:
            cell=vtkTriangle(); cell.GetPointIds().SetId(0,tri[0]); cell.GetPointIds().SetId(1,tri[1]); cell.GetPointIds().SetId(2,tri[2]); cells.InsertNextCell(cell)
        poly=vtkPolyData(); poly.SetPoints(points); poly.SetPolys(cells)
        mapper=vtkPolyDataMapper(); mapper.SetInputData(poly)
        actor=vtkActor(); actor.SetMapper(mapper)
        colors=self._theme_colors(); actor.GetProperty().SetColor(*colors["face"]); actor.GetProperty().SetEdgeVisibility(False); actor.GetProperty().SetLineWidth(1.0)
        actor._standalone_poly=poly; actor._standalone_mapper=mapper
        return actor

    def _vtk_actor_from_polyline(self, pts):
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkCellArray,vtkPolyData,vtkPolyLine
        from vtkmodules.vtkRenderingCore import vtkActor,vtkPolyDataMapper
        if len(pts)<2:return None
        points=vtkPoints()
        for x,y,z in pts:points.InsertNextPoint(x,y,z)
        line=vtkPolyLine(); line.GetPointIds().SetNumberOfIds(len(pts))
        for i in range(len(pts)):line.GetPointIds().SetId(i,i)
        cells=vtkCellArray(); cells.InsertNextCell(line)
        poly=vtkPolyData(); poly.SetPoints(points); poly.SetLines(cells)
        mapper=vtkPolyDataMapper(); mapper.SetInputData(poly)
        actor=vtkActor(); actor.SetMapper(mapper)
        colors=self._theme_colors(); actor.GetProperty().SetColor(*colors["edge"]); actor.GetProperty().SetLineWidth(3.0)
        actor._standalone_poly=poly; actor._standalone_mapper=mapper
        return actor

    def _build_mesh_snapshot_worker(self, serial, revision, shape, build_faces, build_edges, assembly_shapes=None):
        try:
            from standalonecad.core.topology import face_records, edge_records
            payload={"faces":None,"edges":None,"fallback":None,"face_count":0,"edge_count":0,"volume":0.0,"face_topology":None,"edge_topology":None}
            # Assemblies are rendered directly from each transformed occurrence.
            # This makes occurrence ownership explicit at actor creation time instead of
            # trying to recover it later from the compound B-Rep.  The document shape and
            # its topology records remain untouched.
            if shape is None:
                if build_faces:
                    payload["faces"]=[]; payload["face_topology"]=[]
                if build_edges:
                    payload["edges"]=[]; payload["edge_topology"]=[]
            else:
                try:payload["volume"]=float(shape.Volume())
                except Exception:payload["volume"]=None

                # Keep the document-level topology snapshot for existing downstream UI/agent
                # contracts, while the visual actors below can be occurrence-scoped.
                if build_faces:
                    try:payload["face_topology"]=face_records(shape)
                    except Exception:payload["face_topology"]=[]
                if build_edges:
                    try:payload["edge_topology"]=edge_records(shape)
                    except Exception:payload["edge_topology"]=[]

                if assembly_shapes:
                    if build_faces:
                        built=[]; face_count=0
                        for occurrence_name, occurrence_shape in assembly_shapes:
                            try:
                                faces=list(occurrence_shape.Faces()); frecs=face_records(occurrence_shape); face_count+=len(faces)
                                for rec in frecs:
                                    try:
                                        face=faces[int(rec["index"])]
                                        tagged=dict(rec); tagged["occurrence_name"]=str(occurrence_name)
                                        mesh=self._shape_mesh_data(face,0.05)
                                        if mesh["verts"] and mesh["tris"]:built.append((tagged,mesh))
                                    except Exception:
                                        continue
                            except Exception:
                                continue
                        payload["face_count"]=face_count; payload["faces"]=built
                        if not built:
                            try:
                                mesh=self._shape_mesh_data(shape,0.05)
                                if mesh["verts"] and mesh["tris"]:payload["fallback"]=mesh
                            except Exception:
                                pass
                    else:
                        try:payload["face_count"]=len(shape.Faces())
                        except Exception:pass

                    if build_edges:
                        built_edges=[]; edge_count=0
                        for occurrence_name, occurrence_shape in assembly_shapes:
                            try:
                                edges=list(occurrence_shape.Edges()); erecs=edge_records(occurrence_shape); edge_count+=len(edges)
                                for rec in erecs:
                                    try:
                                        edge=edges[int(rec["index"])]
                                        tagged=dict(rec); tagged["occurrence_name"]=str(occurrence_name)
                                        pts=self._edge_mesh_data(edge,0.08)
                                        if len(pts)>=2:built_edges.append((tagged,pts))
                                    except Exception:
                                        continue
                            except Exception:
                                continue
                        payload["edge_count"]=edge_count; payload["edges"]=built_edges
                    else:
                        try:payload["edge_count"]=len(shape.Edges())
                        except Exception:pass
                else:
                    if build_faces:
                        faces=list(shape.Faces()); payload["face_count"]=len(faces); frecs=face_records(shape); built=[]
                        for rec in frecs:
                            try:
                                face=faces[int(rec["index"])]
                                mesh=self._shape_mesh_data(face,0.05)
                                if mesh["verts"] and mesh["tris"]:built.append((rec,mesh))
                            except Exception:
                                continue
                        payload["faces"]=built
                        if not built:
                            try:
                                mesh=self._shape_mesh_data(shape,0.05)
                                if mesh["verts"] and mesh["tris"]:payload["fallback"]=mesh
                            except Exception:
                                pass
                    else:
                        try:payload["face_count"]=len(shape.Faces())
                        except Exception:pass
                    if build_edges:
                        edges=list(shape.Edges()); payload["edge_count"]=len(edges); erecs=edge_records(shape); built_edges=[]
                        for rec in erecs:
                            try:
                                edge=edges[int(rec["index"])]
                                pts=self._edge_mesh_data(edge,0.08)
                                if len(pts)>=2:built_edges.append((rec,pts))
                            except Exception:
                                continue
                        payload["edges"]=built_edges
                    else:
                        try:payload["edge_count"]=len(shape.Edges())
                        except Exception:pass
            self.events.mesh_ready.emit(serial,revision,payload)
        except Exception as exc:
            self.events.mesh_error.emit(serial,revision,str(exc))

    def _schedule_mesh_build(self, revision, shape, include_edges=False, assembly_shapes=None):
        if not self._vtk_ready:return
        cached=self._mesh_cache.get(revision,{})
        build_faces=cached.get("faces") is None
        build_edges=bool(include_edges and cached.get("edges") is None)
        if not build_faces and not build_edges:
            self._apply_mesh_cache(revision)
            return
        key=(revision,build_faces,build_edges)
        if self._pending_mesh_key==key:return
        self._mesh_job_serial+=1; serial=self._mesh_job_serial; self._pending_mesh_key=key
        self.status.setText("3D Calculating view… · You can keep using the window")
        threading.Thread(target=self._build_mesh_snapshot_worker,args=(serial,revision,shape,build_faces,build_edges,assembly_shapes),daemon=True,name=f"CADia-Mesh-{revision}").start()

    def _mesh_ready(self, serial, revision, payload):
        if serial!=self._mesh_job_serial:return
        self._pending_mesh_key=None
        cache=self._mesh_cache.setdefault(revision,{})
        if payload.get("faces") is not None:
            cache["faces"]=payload.get("faces",[]); cache["fallback"]=payload.get("fallback"); cache["face_topology"]=payload.get("face_topology") or []
        if payload.get("edges") is not None:
            cache["edges"]=payload.get("edges",[]); cache["edge_topology"]=payload.get("edge_topology") or []
        for key in ("face_count","edge_count","volume"):
            if payload.get(key) is not None:cache[key]=payload.get(key)
        # Keep memory bounded; model geometry itself is not changed by this display cache.
        for old in sorted(list(self._mesh_cache))[:-2]:self._mesh_cache.pop(old,None)
        if revision==self.engine.revision:
            self._last_topology={"faces":cache.get("face_topology",[]),"edges":cache.get("edge_topology",[]),"vertices":[]}
            self._apply_mesh_cache(revision)
            self._update_geometry_stats(revision)

    def _mesh_error(self, serial, revision, message):
        if serial!=self._mesh_job_serial:return
        self._pending_mesh_key=None
        if revision==self.engine.revision:self.status.setText("3D View refresh failed - "+message)

    def _apply_mesh_cache(self, revision, render=True):
        if not self._vtk_ready or revision!=self.engine.revision:return
        cache=self._mesh_cache.get(revision)
        if not cache or cache.get("faces") is None:return
        self.ren.RemoveAllViewProps(); self.actor_face={}; self.actor_edge={}; self._actor_occurrence={}; self._feature_overlay_actors=[]; self._selected_actor=None; self._fallback_actor=None; count=0
        for rec,mesh in cache.get("faces",[]):
            actor=self._vtk_actor_from_mesh(mesh)
            if actor is None:continue
            self.ren.AddActor(actor); self.actor_face[actor]=rec; count+=1
        if count==0 and cache.get("fallback"):
            actor=self._vtk_actor_from_mesh(cache["fallback"])
            if actor is not None:self.ren.AddActor(actor); self._fallback_actor=actor; count=1
        for rec,pts in (cache.get("edges") or []):
            actor=self._vtk_actor_from_polyline(pts)
            if actor is None:continue
            actor.SetVisibility(False); actor.SetPickable(False); self.ren.AddActor(actor); self.actor_edge[actor]=rec
        self._rebuild_actor_occurrence_map()
        if not self._camera_initialized or self.engine.doc.view_fit:self._apply_camera_orientation(render=False)
        self._last_actor_count=count; self._apply_pick_mode(render=False)
        self._apply_transparency(render=False); self._apply_section_view(render=False)
        if self.selected_feature_name:
            key=(revision,self.selected_feature_name)
            overlay=self._overlay_cache.get(key)
            if overlay is not None:self._apply_overlay_data(overlay,render=False)
            else:self._show_feature_overlay(self.selected_feature_name,clear_first=False)
        if render:self.vtk.GetRenderWindow().Render()
        self.status.setText(f"Ready · revision {revision} · 3D Cache applied")

    def _apply_pick_mode(self,*_,render=True):
        if not self._vtk_ready:return
        edge_mode=self.selection_mode.currentText()=="Edge"
        for actor in self.actor_face:actor.SetPickable(not edge_mode)
        for actor in self.actor_edge:actor.SetVisibility(edge_mode); actor.SetPickable(edge_mode)
        self.selection_text.setText(("Edge click selection - " if edge_mode else "Face click selection - ")+"Left-drag rotate - middle-drag pan - wheel zoom")
        if edge_mode and self._mesh_cache.get(self.engine.revision,{}).get("edges") is None:
            self.status.setText("Preparing edge selection…")
            with self.engine.lock:
                doc=self.engine.doc; shape=doc.shape if doc is not None else None; revision=self.engine.revision
            self._schedule_mesh_build(revision,shape,include_edges=True)
        if render:self.vtk.GetRenderWindow().Render()

    def _clear_geometry_highlight(self):
        if self._selected_actor is not None:
            colors=self._theme_colors()
            try:
                if self._selected_actor in self.actor_face:self._selected_actor.GetProperty().SetColor(*colors["face"])
                elif self._selected_actor in self.actor_edge:
                    self._selected_actor.GetProperty().SetColor(*colors["edge"]); self._selected_actor.GetProperty().SetLineWidth(3.0)
            except Exception:pass
        self._selected_actor=None

    def _clear_feature_overlay(self,render=False):
        if not self._vtk_ready:return
        for actor in self._feature_overlay_actors:
            try:self.ren.RemoveActor(actor)
            except Exception:pass
        self._feature_overlay_actors=[]
        if render:self.vtk.GetRenderWindow().Render()

    def _overlay_worker(self,serial,revision,feature_name):
        try:
            with self.engine.lock:
                if revision!=self.engine.revision:return
                feat=self.engine.doc.find_feature(feature_name)
                add,cut=self.engine.doc._feature_effect(feat)
            data=[]
            for sh,kind in ((add,"add"),(cut,"cut")):
                if sh is None:continue
                try:
                    mesh=self._shape_mesh_data(sh,0.05)
                    if mesh["verts"] and mesh["tris"]:data.append((kind,mesh))
                except Exception:
                    continue
            self.events.overlay_ready.emit(serial,revision,feature_name,data)
        except Exception as exc:
            self.events.overlay_error.emit(serial,revision,feature_name,str(exc))

    def _show_feature_overlay(self,feature_name,clear_first=True):
        if clear_first:self._clear_feature_overlay(render=True)
        if not self._vtk_ready or not feature_name:return
        key=(self.engine.revision,feature_name)
        cached=self._overlay_cache.get(key)
        if cached is not None:
            self._apply_overlay_data(cached)
            return
        self._overlay_job_serial+=1; serial=self._overlay_job_serial; revision=self.engine.revision
        self.status.setText(f"Feature selected · {feature_name} · highlight Calculating…")
        threading.Thread(target=self._overlay_worker,args=(serial,revision,feature_name),daemon=True,name=f"CADia-Overlay-{feature_name}").start()

    def _overlay_ready(self,serial,revision,feature_name,data):
        if serial!=self._overlay_job_serial:return
        self._overlay_cache[(revision,feature_name)]=data
        # Keep only current/previous revision overlays.
        for key in list(self._overlay_cache):
            if key[0] < self.engine.revision-1:self._overlay_cache.pop(key,None)
        if revision==self.engine.revision and feature_name==self.selected_feature_name:self._apply_overlay_data(data)

    def _overlay_error(self,serial,revision,feature_name,message):
        if serial!=self._overlay_job_serial:return
        if revision==self.engine.revision and feature_name==self.selected_feature_name:self.status.setText(f"Feature selected · {feature_name} · highlight unavailable: {message}")

    def _apply_overlay_data(self,data,render=True):
        self._clear_feature_overlay(render=False)
        colors=self._theme_colors()
        for kind,mesh in data:
            actor=self._vtk_actor_from_mesh(mesh)
            if actor is None:continue
            actor.SetPickable(False); actor.GetProperty().SetColor(*(colors["overlay_add"] if kind=="add" else colors["overlay_cut"])); actor.GetProperty().SetOpacity(0.42); actor.GetProperty().SetEdgeVisibility(False); actor.GetProperty().SetLineWidth(2.5)
            self.ren.AddActor(actor); self._feature_overlay_actors.append(actor)
        self._apply_section_view(render=False)
        if render:self.vtk.GetRenderWindow().Render()

    def _pick_geometry(self,obj,_event):
        try:
            interactor=self.vtk.GetRenderWindow().GetInteractor(); x,y=interactor.GetEventPosition()
            edge_mode=self.selection_mode.currentText()=="Edge"
            picker=vtkCellPicker(); picker.SetTolerance(0.008 if edge_mode else 0.002); picker.PickFromListOn()
            targets=self.actor_edge if edge_mode else self.actor_face
            for candidate in targets:picker.AddPickList(candidate)
            picked=bool(picker.Pick(x,y,0,self.ren)); actor=picker.GetActor() if picked else None
            self._clear_geometry_highlight(); self._clear_feature_overlay(); self.selected_feature_name=None
            colors=self._theme_colors(); rec=(self.actor_edge.get(actor) if edge_mode else self.actor_face.get(actor)) if actor is not None else None
            if edge_mode and rec:
                self.selected_edge_ref=rec["id"]; self.selected_face_ref=None; owner=self._actor_occurrence.get(actor)
                selection={"type":"edge","edge_ref":rec["id"],"geom":rec.get("geom"),"direction":rec.get("direction"),"center":rec.get("center"),"length":rec.get("length"),"vertices":rec.get("vertices")}
                if owner:selection["occurrence_name"]=owner
                with self.engine.lock:self.engine.selection=selection
                actor.GetProperty().SetColor(*colors["selected_edge"]); actor.GetProperty().SetLineWidth(5.0); self._selected_actor=actor; self.status.setText(f"Selected edge · {rec['id']}"+(f" · {owner}" if owner else ""))
            elif (not edge_mode) and rec:
                self.selected_face_ref=rec["id"]; self.selected_edge_ref=None; owner=self._actor_occurrence.get(actor)
                selection={"type":"face","face_ref":rec["id"],"semantic":rec.get("semantic"),"direction":rec.get("direction"),"center":rec.get("center"),"normal":rec.get("normal")}
                if owner:selection["occurrence_name"]=owner
                with self.engine.lock:self.engine.selection=selection
                actor.GetProperty().SetColor(*colors["selected_face"]); self._selected_actor=actor; self.status.setText(f"Selected face · {rec['id']}"+(f" · {owner}" if owner else ""))
            else:
                self.selected_face_ref=None; self.selected_edge_ref=None
                with self.engine.lock:self.engine.selection={}
                self.status.setText("No selection")
            self._sync_transparency_button()
            self.vtk.GetRenderWindow().Render()
        finally:
            obj.OnLeftButtonDown()

    def _apply_camera_orientation(self,render=True):
        if not self._vtk_ready:return
        doc=self.engine.doc
        if doc is None:return
        cam=self.ren.GetActiveCamera(); set_camera_orientation(cam,doc.view_orientation)
        try:cam.OrthogonalizeViewUp()
        except Exception:pass
        self.ren.ResetCamera(); self._camera_initialized=True; doc.view_fit=False
        if render:self.vtk.GetRenderWindow().Render()

    def _render_locked(self):
        if not self._vtk_ready:return
        doc=self.engine.doc
        if doc is None:
            self.ren.RemoveAllViewProps(); self.actor_face={}; self.actor_edge={}; self._feature_overlay_actors=[]; self._fallback_actor=None; self.vtk.GetRenderWindow().Render(); return
        revision=self.engine.revision; shape=doc.shape
        edge_mode=self.selection_mode.currentText()=="Edge"
        if shape is None:
            # New Part/New Assembly must look like a genuinely blank document
            # immediately. Invalidate any in-flight mesh job from the previous
            # document and clear every viewport/selection overlay synchronously.
            self._mesh_job_serial+=1; self._pending_mesh_key=None
            self._mesh_cache[revision]={"faces":[],"edges":[],"fallback":None,"face_count":0,"edge_count":0,"volume":0.0,"face_topology":[],"edge_topology":[]}
            self._last_topology={"faces":[],"edges":[],"vertices":[]}
            self.ren.RemoveAllViewProps(); self.actor_face={}; self.actor_edge={}; self._feature_overlay_actors=[]; self._fallback_actor=None; self._selected_actor=None; self._last_actor_count=0
            self.vtk.GetRenderWindow().Render()
            return
        cache=self._mesh_cache.get(revision,{})
        if cache.get("faces") is not None and (not edge_mode or cache.get("edges") is not None):
            self._apply_mesh_cache(revision)
        else:
            assembly_shapes=None
            if doc.doc_type=="assembly":
                try:
                    from standalonecad.core.assembly import transform_shape
                    assembly_shapes=[]
                    for name,occ in doc.occurrences.items():
                        if occ.suppressed or occ.shape is None:continue
                        assembly_shapes.append((str(name),transform_shape(occ.shape,occ.position_mm,occ.rotation_deg_xyz)))
                except Exception:
                    assembly_shapes=None
            self._schedule_mesh_build(revision,shape,include_edges=edge_mode,assembly_shapes=assembly_shapes)

    # ---------- tree / selection ----------
    def _tree_selection_changed(self):
        if self._tree_rebuilding:return
        items=self.tree.selectedItems()
        if not items:return
        data=items[0].data(0,Qt.UserRole)
        if not isinstance(data,dict):return
        typ=data.get("type")
        if typ=="feature":
            name=data.get("name"); self.selected_feature_name=name; self.selected_face_ref=None; self.selected_edge_ref=None; self._clear_geometry_highlight()
            with self.engine.lock:
                feat=self.engine.doc.find_feature(name); self.engine.selection={"type":"feature","feature_name":feat.name,"kind":feat.kind,"operation":feat.operation,"params":feat.params}
            self._show_feature_overlay(name); self.status.setText(f"Selected feature · {name}")
        elif typ=="sketch":
            self.selected_feature_name=None; self._clear_feature_overlay(); self.selected_face_ref=None; self.selected_edge_ref=None
            with self.engine.lock:self.engine.selection={"type":"sketch","sketch_name":data.get("name")}
            self.status.setText(f"Selected sketch · {data.get('name')}")
        elif typ in ("work_plane","work_axis"):
            self.selected_feature_name=None; self._clear_feature_overlay(); self.selected_face_ref=None; self.selected_edge_ref=None
            with self.engine.lock:self.engine.selection={"type":typ,"name":data.get("name")}
            self.status.setText(f"Selected {typ.replace('_',' ')} · {data.get('name')}")
        elif typ=="occurrence":
            name=data.get('name'); self.selected_feature_name=None; self._clear_feature_overlay()
            with self.engine.lock:self.engine.selection={'type':'occurrence','occurrence_name':name}
            self._sync_transparency_button(); self.status.setText(f"Selected occurrence · {name}")

    # ---------- synchronization ----------
    def _sync_document_combo(self):
        if not hasattr(self,"document_combo"):return
        sessions=self.engine.document_sessions(); active_idx=-1
        self._document_combo_rebuilding=True; self.document_combo.blockSignals(True); self.document_combo.clear()
        for i,row in enumerate(sessions):
            label=f"{row['title']}{' *' if row.get('dirty') else ''} · {row['type'].upper()}"
            self.document_combo.addItem(label,row['id'])
            if row.get('active'):active_idx=i
        if active_idx>=0:self.document_combo.setCurrentIndex(active_idx)
        self.document_combo.setEnabled(bool(sessions))
        self.document_combo.blockSignals(False); self._document_combo_rebuilding=False

    def _activate_document_from_combo(self,index):
        if self._document_combo_rebuilding or index<0:return
        sid=self.document_combo.itemData(index)
        if sid:self._engine_call(lambda:self.engine.activate_document(str(sid)))

    def _poll(self):
        changed=False
        try:
            while True:
                kind,payload=self.events_queue.get_nowait()
                if kind=="cad_changed":changed=True
                elif kind=="agent_progress":self._update_agent_progress(payload)
                elif kind=="agent_done":self._agent_finished(payload,None)
                elif kind=="agent_error":self._agent_finished(None,payload)
                elif kind=="manual_done":self._manual_finished(payload,None)
                elif kind=="manual_error":self._manual_finished(None,payload)
        except queue.Empty:pass
        if changed or self.engine.revision!=self._last_revision:
            if self.agent_busy:
                # Do not tessellate every intermediate modeling mutation. The final
                # revision is rendered once when the agent finishes.
                self._refresh_pending=True
            else:
                # Camera/view changes intentionally keep the same geometry revision,
                # so a change notification must still force a UI refresh.
                self.refresh(force=changed)

    def refresh(self,force=False):
        if not force and self.engine.revision==self._last_revision:return
        with self.engine.lock:
            self._sync_document_combo()
            doc=self.engine.doc
            if doc is None:
                self.selected_feature_name=None; self.selected_face_ref=None; self.selected_edge_ref=None
                self._tree_rebuilding=True; self.tree.clear(); self.tree.addTopLevelItem(QTreeWidgetItem(["No open document"])); self._tree_rebuilding=False
                self.params.setPlainText("Open CAD document is not available.")
                self.doc_badge.setText("NONE"); self.doc_summary.setText("No open document")
                self.info.setPlainText(f"CADia v{__version__}\nTarget: {self.host.info['target_id']}\nKernel: Open CASCADE / CadQuery-OCP\nDocument: none\nRevision: {self.engine.revision}")
                if self._vtk_ready:
                    self.ren.RemoveAllViewProps(); self.actor_face={}; self.actor_edge={}; self._feature_overlay_actors=[]; self._fallback_actor=None; self.vtk.GetRenderWindow().Render()
                self._last_revision=self.engine.revision
                self.status.setText(f"Ready · revision {self._last_revision} · No open document")
                self.runtime_badge.setText("B-REP · VTK/Qt Interactive" if self._vtk_ready else "B-REP · 3D Preview")
                return
            keep_feature=self.selected_feature_name if any(f.name==self.selected_feature_name for f in doc.features) else None; self.selected_feature_name=keep_feature
            active_selection=dict(self.engine.selection)
            self.selected_face_ref=active_selection.get("face_ref") if active_selection.get("type")=="face" else None
            self.selected_edge_ref=active_selection.get("edge_ref") if active_selection.get("type")=="edge" else None
            # Keep the view selector synchronized when switching among open documents.
            self.orientation.blockSignals(True); self.orientation.setCurrentText(doc.view_orientation); self.orientation.blockSignals(False)
            self._tree_rebuilding=True; self.tree.clear(); root=QTreeWidgetItem([doc.title]); root.setExpanded(True); self.tree.addTopLevelItem(root)
            if doc.doc_type=="part":
                origin=QTreeWidgetItem(["Origin"]); root.addChild(origin)
                for name in ("XY Plane","XZ Plane","YZ Plane","X Axis","Y Axis","Z Axis"):origin.addChild(QTreeWidgetItem([name]))
                sketches=QTreeWidgetItem(["Sketches"]); sketches.setExpanded(True); root.addChild(sketches)
                for sk in doc.sketches.values():
                    item=QTreeWidgetItem([f"{sk.name}  [{sk.plane}]  · {len(sk.entities)} entities"]); item.setData(0,Qt.UserRole,{"type":"sketch","name":sk.name}); sketches.addChild(item)
                features=QTreeWidgetItem(["Features"]); features.setExpanded(True); root.addChild(features)
                selected_item=None
                for feat in doc.features:
                    item=QTreeWidgetItem([f"{feat.name}  · {feat.kind}"]); item.setData(0,Qt.UserRole,{"type":"feature","name":feat.name}); features.addChild(item)
                    if feat.name==keep_feature:selected_item=item
                work=QTreeWidgetItem(["Work Features"]); root.addChild(work)
                for itemdata in doc.work_planes.values():
                    item=QTreeWidgetItem([itemdata["name"]]); item.setData(0,Qt.UserRole,{"type":"work_plane","name":itemdata["name"]}); work.addChild(item)
                for itemdata in doc.work_axes.values():
                    item=QTreeWidgetItem([itemdata["name"]]); item.setData(0,Qt.UserRole,{"type":"work_axis","name":itemdata["name"]}); work.addChild(item)
                if selected_item is not None:self.tree.setCurrentItem(selected_item)
            else:
                occ=QTreeWidgetItem(["Occurrences"]); occ.setExpanded(True); root.addChild(occ)
                for item in doc.occurrences.values():
                    label=f"{item.name}{'  [grounded]' if item.grounded else ''}{'  [RELINK]' if getattr(item,'reference_status','resolved')!='resolved' else ''}"
                    node=QTreeWidgetItem([label]); node.setData(0,Qt.UserRole,{'type':'occurrence','name':item.name}); occ.addChild(node)
                cons=QTreeWidgetItem(["Constraints"]); cons.setExpanded(True); root.addChild(cons)
                for item in doc.constraints:cons.addChild(QTreeWidgetItem([f"{item.name}  · {item.type}  [{item.health}]"]))
                joints=QTreeWidgetItem(["Joints"]); joints.setExpanded(True); root.addChild(joints)
                for item in getattr(doc,'joints',[]):joints.addChild(QTreeWidgetItem([f"{item.name}  · {item.type}  [{item.health}]"]))
            self._tree_rebuilding=False
            try:vals=doc.numeric_params()
            except Exception:vals={}
            lines=[]
            if not doc.parameters:lines.append("No user parameters yet.")
            for param in doc.parameters.values():lines.append(f"{param.name:18} = {param.expression} {param.unit}   [{vals.get(param.name,'?')}]")
            self.params.setPlainText("\n".join(lines))
            self.doc_badge.setText(doc.doc_type.upper())
            self._update_geometry_stats(self.engine.revision)
            self._render_locked()
            self._sync_transparency_button()
            self._last_revision=self.engine.revision
        self.status.setText(f"Ready · revision {self._last_revision} · feature {len(doc.features)}"); self.runtime_badge.setText("B-REP · VTK/Qt Interactive" if self._vtk_ready else "B-REP · 3D Preview")

    def _update_geometry_stats(self,revision):
        if revision!=self.engine.revision:return
        doc=self.engine.doc
        if doc is None:
            self.doc_summary.setText("No open document"); return
        cache=self._mesh_cache.get(revision,{})
        faces=cache.get("face_count","…"); edges=cache.get("edge_count","…"); volume=cache.get("volume")
        volume_text=(f"{volume:,.3f} mm³" if isinstance(volume,(int,float)) else "Calculating…")
        feature_count=len(doc.features)
        self.doc_summary.setText(f"{feature_count} features · {faces} faces\nVolume: {volume_text}\nRevision: {revision}")
        self.info.setPlainText(f"CADia v{__version__}\nTarget: {self.host.info['target_id']}\nPID: {os.getpid()}\nPort: {self.host.port}\nKernel: Open CASCADE / CadQuery-OCP\nCAD control: {'ready' if self.control_ready else 'error'}\nMCP Compatible tools: 58 + Assembly Extensions: 10 + Native Extensions: 50\nDocument: {doc.doc_type}\nRevision: {revision}\nFaces: {faces}\nEdges: {edges}\nViewport: {'VTK + Qt interactive' if self._vtk_ready else 'ERROR'}\nViewport actors: {self._last_actor_count}\n"+(f"VTK error: {self._vtk_error}\n" if self._vtk_error else ""))

    # ---------- AI ----------
    def _append_chat(self,role,text):
        clean=str(text).strip(); labels={"user":"Me","assistant":"AI","error":"Error","system":"System"}; self.chat.append(f"<b>{labels.get(role,role)}</b><br>{clean.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace(chr(10),'<br>')}<br>")

    def _planning_state(self):
        with self.engine.lock:
            doc=self.engine.doc
            if doc is None:
                return {"document":None,"parameters":{},"sketches":[],"features":[],"selection":{},"faces":[],"edges":[],"origin_interfaces":["YZ Plane","XZ Plane","XY Plane","X Axis","Y Axis","Z Axis","Center Point"],"open_documents":self.engine.document_sessions()}
            def _interface_names(ifaces):
                out=[]
                for group in ("imates","work_planes","work_axes","work_points","origin_geometry"):
                    out.extend(str(x.get("name")) for x in (ifaces or {}).get(group,[]) if x.get("name"))
                return sorted(set(out))
            try:params={k:{"expression":v.expression,"unit":v.unit} for k,v in doc.parameters.items()}
            except Exception:params={}
            try:topo=doc.topology()
            except Exception:topo={"faces":[],"edges":[],"vertices":[]}
            selection=dict(self.engine.selection)
            if selection.get("type") in ("face","edge"):
                try:
                    ref=selection.get("face_ref") if selection.get("type")=="face" else selection.get("edge_ref")
                    prov=doc.selection_provenance(ref,selection.get("type")) if ref else None
                    if prov:selection["created_by_feature"]=prov
                except Exception:pass
            return {"document":{"title":doc.title,"type":doc.doc_type,"units":doc.units,"revision":self.engine.revision,"has_geometry":doc.shape is not None},"parameters":params,"sketches":[{"name":sk.name,"plane":sk.plane,"closed":sk.closed,"entities":[{"id":e.tag,"kind":e.kind,"data":e.data} for e in sk.entities],"dimensions":sk.dimensions,"constraints":sk.constraints} for sk in doc.sketches.values()],"sketches3d":[dict(v) for v in getattr(doc,"sketches3d",{}).values()],"work_features":{"planes":[dict(v) for v in getattr(doc,"work_planes",{}).values()],"axes":[dict(v) for v in getattr(doc,"work_axes",{}).values()],"points":[dict(v) for v in getattr(doc,"work_points",{}).values()]},"features":[{"name":f.name,"kind":f.kind,"operation":f.operation,"params":f.params} for f in doc.features],"origin_interfaces":_interface_names(doc.list_interfaces()),"occurrences":[{"name":o.name,"grounded":o.grounded,"position_mm":list(o.position_mm),"rotation_deg_xyz":list(o.rotation_deg_xyz),"interfaces":_interface_names(getattr(o,"interfaces",{}))} for o in getattr(doc,'occurrences',{}).values()],"constraints":[vars(c).copy() for c in getattr(doc,'constraints',[])],"joints":[vars(j).copy() for j in getattr(doc,'joints',[])],"selection":selection,"faces":topo.get("faces",[])[:160],"edges":topo.get("edges",[])[:240],"open_documents":self.engine.document_sessions()}

    def _update_agent_progress(self,payload):
        if isinstance(payload,dict):
            message=str(payload.get("message") or "Working…"); pct=payload.get("percent")
        else:
            message=str(payload); pct=None
        self.agent_status.setText(message)
        if pct is not None:
            value=max(0,min(100,int(pct))); self.progress.setValue(value); self.progress.setFormat(f"Estimated progress {value}% · {message}")

    def send_prompt(self):
        if self.agent_busy:return
        text=self.prompt.toPlainText().strip()
        if not text:return
        if not self.control_ready:QMessageBox.critical(self,"CAD control","CAD CAD control is not ready. Restart the app."); return
        self.prompt.clear(); self._append_chat("user",text); self.agent.reasoning={"Fast":"low","Balanced":"medium","Maximum":"high"}.get(self.mode.currentText(),"medium")
        agent_text=text
        with self.engine.lock:selection=dict(self.engine.selection)
        if selection:agent_text+=f"\n\nSelected CAD context: {selection}. If the request says this/selected/here, use this selection."
        self.agent_busy=True; self.send_btn.setEnabled(False); self.stop_btn.setEnabled(True); self.agent_status.setText("Preparing request…"); self.progress.setValue(2); self.progress.setFormat("Estimated progress 2% · Preparing request…")
        def on_event(kind,message):
            if kind=="progress":self.events_queue.put(("agent_progress",message))
        def worker():
            try:
                plan=self.agent.plan(agent_text,self._planning_state(),on_event=on_event)
                executed=execute_with_failure_recovery(
                    executor=self.executor, agent=self.agent, user_prompt=agent_text,
                    state_provider=self._planning_state, plan=plan, on_event=on_event,
                    initial_progress_span=(25,70), max_ai_repairs=2,
                )
                if not executed.ok:raise RuntimeError("Modeling failed: "+str(executed.error or "Unknown error"))
                self.events_queue.put(("agent_progress",{"message":"Checking final result…","percent":98}))
                self.events_queue.put(("agent_done",executed.message))
            except Exception as exc:self.events_queue.put(("agent_error",str(exc)))
        threading.Thread(target=worker,daemon=True,name="CADia-Agent").start()

    def _agent_finished(self,result,error):
        self.agent_busy=False; self._refresh_pending=False; self.send_btn.setEnabled(True); self.stop_btn.setEnabled(False)
        if error:
            self.agent_status.setText("Error"); self.progress.setFormat(f"Stopped - {self.progress.value()}%") ; self._append_chat("error",error)
        else:
            self.agent_status.setText("Ready"); self.progress.setValue(100); self.progress.setFormat("Estimated progress 100% · Done"); self._append_chat("assistant",result or "Task completed.")
        self.refresh(force=True); self.prompt.setFocus()

    def stop_agent(self):
        if self.agent_busy:self.agent.cancel(); self.agent_status.setText("Stopping…"); self.progress.setFormat(f"Stopping… · {self.progress.value()}%")

    # ---------- commands ----------
    def _engine_call_async(self,fn,label="CAD Task"):
        if self.manual_busy or self.agent_busy:
            self.status.setText("Another CAD task is in progress.")
            return None
        self.manual_busy=True; self.status.setText(f"{label} Processing…")
        def worker():
            try:
                with self.engine.lock:r=fn()
                self.host.refresh_descriptor()
                self.events_queue.put(("manual_done",(label,r)))
            except Exception as exc:
                self.events_queue.put(("manual_error",(label,str(exc))))
        threading.Thread(target=worker,daemon=True,name=f"CADia-Manual-{label}").start()
        return None

    def _manual_finished(self,result,error):
        self.manual_busy=False
        if error:
            label,message=error; self.status.setText(f"{label} Failed"); QMessageBox.critical(self,"CAD error",message)
        else:
            label,_value=result; self.refresh(force=True); self.status.setText(f"{label} Done")

    def _engine_call(self,fn):
        try:
            with self.engine.lock:r=fn()
            self.host.refresh_descriptor()
            self.refresh(force=True); return r
        except Exception as exc:QMessageBox.critical(self,"CAD error",str(exc)); return None
    def new(self):self._engine_call(lambda:self.engine.execute("new_part",{"name":"Untitled"}))
    def new_assembly(self):self._engine_call(lambda:self.engine.execute("new_assembly",{"name":"Untitled Assembly"}))
    def open(self):
        path,_=QFileDialog.getOpenFileName(self,"Open CAD document","","CADia document (*.scad.json);;STEP (*.step *.stp);;All files (*)")
        if path:self._engine_call_async(lambda:self.engine.execute("open_document",{"path":path}),"Open file")
    def save(self):
        doc=self.engine.doc
        if doc is None:QMessageBox.information(self,"Save CAD document","No open document to save."); return
        path=doc.path
        if not path or not str(path).lower().endswith(".scad.json"):
            path,_=QFileDialog.getSaveFileName(self,"Save CAD document","Untitled.scad.json","CADia document (*.scad.json)")
        if path:self._engine_call_async(lambda:self.engine.execute("save_document",{"path":path}),"Save")
    def delete_document_file(self):
        doc=self.engine.doc
        if doc is None:
            QMessageBox.information(self,'Delete Document','No open document to delete.'); return
        path=str(doc.path or '')
        if not path.lower().endswith('.scad.json'):
            QMessageBox.information(self,'Delete Document','Saved CADia document(.scad.json)can only be deleted. Save the document first.'); return
        dirty=bool(getattr(doc,'dirty',False))
        detail=f"This document file will be permanently deleted from disk.\n\n{Path(path).name}\n\nThis action cannot be undone by Undo."
        if dirty:detail += "\nUnsaved changes will also be lost."
        answer=QMessageBox.warning(self,'Permanently delete document file',detail,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        self._engine_call_async(
            lambda:self.engine.execute('delete_document_file',{'confirm':True,'discard_unsaved_changes':dirty}),
            'Delete Document'
        )

    def relink_selected_occurrence(self):
        sel=dict(self.engine.selection or {})
        if sel.get('type')!='occurrence':QMessageBox.information(self,'Relink','Assembly occurrenceSelect it first.'); return
        path,_=QFileDialog.getOpenFileName(self,'Relink component','','CADia / STEP / BREP (*.scad.json *.step *.stp *.brep *.brp);;All files (*)')
        if path:self._engine_call_async(lambda:self.engine.execute('relink_occurrence',{'occurrence_name':sel['occurrence_name'],'path':path}),'Relink')

    def fit(self):
        if self._vtk_ready and self.engine.doc is not None:self.ren.ResetCamera(); self.vtk.GetRenderWindow().Render()
    def set_orientation(self,*_):
        if not hasattr(self,"engine") or self.engine.doc is None:return
        value=self.orientation.currentText(); self._engine_call(lambda:self.engine.execute("set_view_orientation",{"orientation":value,"fit":True}))
    def closeEvent(self,event):
        dirty=[x for x in self.engine.document_sessions() if x.get('dirty')]
        if dirty:
            box=QMessageBox(self); box.setWindowTitle('Unsaved CAD documents'); box.setText(f'{len(dirty)}documents have unsaved changes.')
            save_btn=box.addButton('Save All',QMessageBox.AcceptRole); discard_btn=box.addButton('Discard',QMessageBox.DestructiveRole); cancel_btn=box.addButton('Cancel',QMessageBox.RejectRole); box.exec()
            clicked=box.clickedButton()
            if clicked is cancel_btn:event.ignore(); return
            if clicked is save_btn:
                try:
                    with self.engine.lock:self.engine.save_all()
                except Exception as exc:QMessageBox.critical(self,'Save All failed',str(exc)); event.ignore(); return
        try:self.agent.cancel(); self.host.close(); self.vtk.Finalize() if self._vtk_ready else None
        finally:event.accept()


def main():
    app=QApplication.instance() or QApplication([])
    win=MainWindow(); win.show(); app.exec()


if __name__=="__main__":main()
