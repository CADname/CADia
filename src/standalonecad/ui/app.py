from __future__ import annotations

import os
import queue
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cadquery import exporters

from standalonecad import __version__
from standalonecad.bridge.host import CadHost
from standalonecad.codex_agent import CodexAgent
from standalonecad.planning import PlanExecutor
from standalonecad.recovery import execute_with_failure_recovery
from standalonecad.core.engine import CadEngine
from standalonecad.core.render import render_shape_png, sample_edge_points


PALETTE = {
    "bg": "#15171b",
    "panel": "#1d2026",
    "panel2": "#242831",
    "panel3": "#2b303a",
    "border": "#343a46",
    "text": "#eef1f6",
    "muted": "#98a1b2",
    "accent": "#5b7cff",
    "accent_hover": "#6d8aff",
    "success": "#59c98a",
    "warning": "#e1b866",
    "danger": "#e06c75",
    "viewport": "#111319",
}


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"CADia AI v{__version__}")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = min(1480, max(1120, sw - 48)), min(900, max(720, sh - 72))
        x, y = max(0, (sw - w)//2), max(0, (sh - h)//2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(1080, 700)
        self.root.configure(bg=PALETTE["bg"])

        self.events: queue.Queue = queue.Queue()
        self.engine = CadEngine(lambda: self.events.put(("cad_changed", None)))
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
        self._vtk_ready = False
        self._static_ready = False
        self._static_photo = None
        self._vtk_error = None
        self._whole_actor = None
        self.actor_face = {}
        self.actor_edge = {}
        self.selected_face_ref = None
        self.selected_edge_ref = None
        self._selected_actor = None
        self._camera_initialized = False
        self._last_revision = -1
        self._last_actor_count = 0
        self._setup_style()
        self._build()
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh(force=True)
        if not self.control_ready:
            self.agent_status.set("Control error")
            self._append_chat("error", "CAD CAD control initialization failed: " + str(self.control_health.get("error", "unknown error")))
        elif self.agent.codex_exe:
            self.agent_status.set("Ready")
        else:
            # Deterministic standard generators can still run; free-form planning needs the signed-in AI runtime.
            self.agent_status.set("AI sign-in required")

    # ---------- visual shell ----------
    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=PALETTE["bg"], foreground=PALETTE["text"], font=("Segoe UI", 10))
        style.configure("TFrame", background=PALETTE["bg"])
        style.configure("Panel.TFrame", background=PALETTE["panel"])
        style.configure("Card.TFrame", background=PALETTE["panel2"], relief="flat")
        style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
        style.configure("Panel.TLabel", background=PALETTE["panel"], foreground=PALETTE["text"])
        style.configure("Card.TLabel", background=PALETTE["panel2"], foreground=PALETTE["text"])
        style.configure("Muted.TLabel", background=PALETTE["panel"], foreground=PALETTE["muted"])
        style.configure("Header.TLabel", background=PALETTE["panel"], foreground=PALETTE["text"], font=("Segoe UI Semibold", 11))
        style.configure("Title.TLabel", background=PALETTE["bg"], foreground=PALETTE["text"], font=("Segoe UI Semibold", 12))
        style.configure("TButton", background=PALETTE["panel2"], foreground=PALETTE["text"], padding=(10, 6), borderwidth=0)
        style.map("TButton", background=[("active", PALETTE["panel3"])])
        style.configure("Primary.TButton", background=PALETTE["accent"], foreground="white", padding=(14, 8), font=("Segoe UI Semibold", 10))
        style.map("Primary.TButton", background=[("active", PALETTE["accent_hover"]), ("disabled", PALETTE["panel3"])])
        style.configure("Danger.TButton", background=PALETTE["panel2"], foreground=PALETTE["danger"], padding=(10, 6))
        style.configure("Treeview", background=PALETTE["panel"], fieldbackground=PALETTE["panel"], foreground=PALETTE["text"], rowheight=25, borderwidth=0)
        style.map("Treeview", background=[("selected", PALETTE["accent"])], foreground=[("selected", "white")])
        style.configure("TNotebook", background=PALETTE["panel"], borderwidth=0)
        style.configure("TNotebook.Tab", background=PALETTE["panel"], foreground=PALETTE["muted"], padding=(12, 7), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", PALETTE["panel2"])], foreground=[("selected", PALETTE["text"])])
        style.configure("TCombobox", fieldbackground=PALETTE["panel2"], background=PALETTE["panel2"], foreground=PALETTE["text"], arrowcolor=PALETTE["text"])
        style.configure("Horizontal.TSeparator", background=PALETTE["border"])
        style.configure("Dark.Vertical.TScrollbar", background=PALETTE["panel3"], troughcolor=PALETTE["panel"],
                        bordercolor=PALETTE["panel"], arrowcolor=PALETTE["muted"], lightcolor=PALETTE["panel3"],
                        darkcolor=PALETTE["panel3"], relief="flat")
        style.map("Dark.Vertical.TScrollbar", background=[("active", PALETTE["border"])])

    def _build(self):
        self._build_toolbar()

        main = ttk.Panedwindow(self.root, orient="horizontal")
        self.main_panes = main
        main.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.left = ttk.Frame(main, width=220, style="Panel.TFrame")
        self.center = ttk.Frame(main, style="Panel.TFrame")
        self.right = ttk.Frame(main, width=390, style="Panel.TFrame")
        main.add(self.left, weight=0)
        main.add(self.center, weight=1)
        main.add(self.right, weight=0)

        self._build_model_panel(self.left)
        self._build_viewport(self.center)
        self._build_right_panel(self.right)
        self._build_statusbar()
        self.root.after(80, self._set_initial_panes)


    def _set_initial_panes(self):
        """Give the viewport the flexible center area and keep sidebars compact."""
        try:
            self.root.update_idletasks()
            width = max(1080, self.main_panes.winfo_width())
            self.main_panes.sashpos(0, 220)
            self.main_panes.sashpos(1, max(700, width - 398))
        except Exception:
            pass

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, style="Panel.TFrame")
        bar.pack(fill="x", padx=10, pady=(10, 7))

        brand = ttk.Frame(bar, style="Panel.TFrame")
        brand.pack(side="left", padx=(10, 22), pady=5)
        ttk.Label(brand, text="CADia", style="Header.TLabel", font=("Segoe UI Semibold", 13)).pack(anchor="w")
        ttk.Label(brand, text="AI Parametric CAD", style="Muted.TLabel").pack(anchor="w")

        file_group = ttk.Frame(bar, style="Panel.TFrame")
        file_group.pack(side="left", pady=5)
        for text, cmd in [
            ("New Part", self.new),
            ("New Assembly", self.new_assembly),
            ("Open", self.open),
            ("Save", self.save),
            ("Delete Document", self.delete_document_file),
        ]:
            ttk.Button(file_group, text=text, command=cmd).pack(side="left", padx=(0, 4))

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8, pady=7)
        ttk.Button(bar, text="↶", width=3, command=lambda: self._engine_call(self.engine.undo)).pack(side="left", padx=2, pady=5)
        ttk.Button(bar, text="↷", width=3, command=lambda: self._engine_call(self.engine.redo)).pack(side="left", padx=2, pady=5)
        ttk.Button(bar, text="Fit", command=self.fit).pack(side="left", padx=(6, 2), pady=5)

        self.orientation = tk.StringVar(value="iso_top_right")
        orient = tk.Menubutton(
            bar, textvariable=self.orientation, bg=PALETTE["panel2"], fg=PALETTE["text"],
            activebackground=PALETTE["panel3"], activeforeground=PALETTE["text"],
            relief="flat", bd=0, padx=10, pady=6, font=("Segoe UI", 9), cursor="hand2"
        )
        orient_menu = tk.Menu(orient, tearoff=False, bg=PALETTE["panel2"], fg=PALETTE["text"],
                              activebackground=PALETTE["accent"], activeforeground="white", bd=0)
        for value in ("iso_top_right", "iso_top_left", "front", "back", "top", "bottom", "left", "right"):
            orient_menu.add_command(label=value, command=lambda v=value: (self.orientation.set(v), self.set_orientation()))
        orient.configure(menu=orient_menu)
        orient.pack(side="right", padx=(4, 10), pady=5)
        ttk.Label(bar, text="View", style="Muted.TLabel").pack(side="right", padx=(0, 2))

    def _build_model_panel(self, parent):
        head = ttk.Frame(parent, style="Panel.TFrame")
        head.pack(fill="x", padx=10, pady=(11, 5))
        ttk.Label(head, text="Model", style="Header.TLabel").pack(side="left")
        self.doc_badge = tk.StringVar(value="PART")
        ttk.Label(head, textvariable=self.doc_badge, style="Muted.TLabel").pack(side="right")

        self.tree = ttk.Treeview(parent, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        card = ttk.Frame(parent, style="Card.TFrame")
        card.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(card, text="Document", style="Card.TLabel", font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=10, pady=(9, 2))
        self.doc_summary = tk.StringVar(value="No geometry")
        ttk.Label(card, textvariable=self.doc_summary, style="Card.TLabel", justify="left").pack(anchor="w", padx=10, pady=(0, 10))

    def _build_viewport(self, parent):
        head = ttk.Frame(parent, style="Panel.TFrame")
        head.pack(fill="x", padx=10, pady=(10, 5))
        ttk.Label(head, text="3D View", style="Header.TLabel").pack(side="left")
        self.selection_mode = tk.StringVar(value="Face")
        mode = ttk.Combobox(head, textvariable=self.selection_mode, values=("Face","Edge"), width=5, state="readonly")
        mode.pack(side="right", padx=(6,0))
        mode.bind("<<ComboboxSelected>>", lambda _e: self._apply_pick_mode())
        self.selection_text = tk.StringVar(value="Left-drag rotate - middle-drag pan - wheel zoom")
        ttk.Label(head, textvariable=self.selection_text, style="Muted.TLabel").pack(side="right")
        self.view_container = ttk.Frame(parent, style="Panel.TFrame")
        self.view_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._build_vtk(self.view_container)

    def _build_right_panel(self, parent):
        # AI stays visible at all times; properties/diagnostics live in a compact inspector below.
        ai_wrap = ttk.Frame(parent, style="Panel.TFrame")
        ai_wrap.pack(fill="both", expand=True, padx=6, pady=(6, 3))
        self.ai_tab = ai_wrap
        self._build_ai_tab(ai_wrap)

        inspector_wrap = ttk.Frame(parent, style="Panel.TFrame", height=165)
        inspector_wrap.pack(fill="x", padx=6, pady=(3, 6))
        inspector_wrap.pack_propagate(False)
        inspector = ttk.Notebook(inspector_wrap)
        inspector.pack(fill="both", expand=True)
        self.props_tab = ttk.Frame(inspector, style="Panel.TFrame")
        self.diag_tab = ttk.Frame(inspector, style="Panel.TFrame")
        inspector.add(self.props_tab, text="Properties")
        inspector.add(self.diag_tab, text="Info")
        self._build_properties_tab(self.props_tab)
        self._build_diagnostics_tab(self.diag_tab)

    def _build_ai_tab(self, parent):
        # Keep the read-only conversation log and the editable CAD prompt visually
        # and functionally separate. Earlier builds used nearly identical dark
        # surfaces, which made the disabled conversation log look like the input.
        head = ttk.Frame(parent, style="Panel.TFrame")
        head.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(head, text="AI", style="Header.TLabel", font=("Segoe UI Semibold", 12)).pack(side="left")
        self.agent_status = tk.StringVar(value="Starting…")
        ttk.Label(head, textvariable=self.agent_status, style="Muted.TLabel").pack(side="right")

        # ----- read-only conversation / execution log -----
        chat_card = tk.Frame(
            parent, bg=PALETTE["panel"],
            highlightthickness=1, highlightbackground=PALETTE["border"], bd=0,
        )
        chat_card.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        chat_head = tk.Frame(chat_card, bg=PALETTE["panel"])
        chat_head.pack(fill="x", padx=10, pady=(8, 5))
        tk.Label(
            chat_head, text="Conversation / execution log", bg=PALETTE["panel"], fg=PALETTE["text"],
            font=("Segoe UI Semibold", 9), anchor="w",
        ).pack(side="left")
        tk.Label(
            chat_head, text="Read-only", bg=PALETTE["panel"], fg=PALETTE["muted"],
            font=("Segoe UI", 8), anchor="e",
        ).pack(side="right")

        chat_frame = tk.Frame(chat_card, bg=PALETTE["panel"])
        chat_frame.pack(fill="both", expand=True, padx=(1, 1), pady=(0, 1))
        self.chat = tk.Text(
            chat_frame, width=34, height=8, wrap="word", state="disabled",
            bg=PALETTE["panel"], fg=PALETTE["text"], insertbackground=PALETTE["text"],
            selectbackground=PALETTE["accent"],
            relief="flat", padx=12, pady=8, font=("Segoe UI", 10),
            borderwidth=0, highlightthickness=0, cursor="arrow", takefocus=False,
        )
        chat_scroll = ttk.Scrollbar(chat_frame, orient="vertical", command=self.chat.yview, style="Dark.Vertical.TScrollbar")
        self.chat.configure(yscrollcommand=chat_scroll.set)
        chat_scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("user_label", foreground="#9bb4ff", font=("Segoe UI Semibold", 9), spacing1=8, spacing3=2)
        self.chat.tag_configure("user", foreground=PALETTE["text"], lmargin1=18, lmargin2=18, rmargin=6, spacing3=8)
        self.chat.tag_configure("assistant_label", foreground=PALETTE["success"], font=("Segoe UI Semibold", 9), spacing1=8, spacing3=2)
        self.chat.tag_configure("assistant", foreground=PALETTE["text"], lmargin1=18, lmargin2=18, rmargin=6, spacing3=8)
        self.chat.tag_configure("system", foreground=PALETTE["muted"], font=("Segoe UI", 9), lmargin1=6, lmargin2=6, spacing1=5, spacing3=6)
        self.chat.tag_configure("error", foreground=PALETTE["danger"], font=("Segoe UI", 9), lmargin1=6, lmargin2=6, spacing1=5, spacing3=6)

        # ----- editable modeling prompt -----
        compose = tk.Frame(
            parent, bg=PALETTE["panel2"],
            highlightthickness=2, highlightbackground=PALETTE["accent"], bd=0,
            cursor="xterm",
        )
        compose.pack(fill="x", padx=8, pady=(0, 8))

        compose_head = tk.Frame(compose, bg=PALETTE["panel2"])
        compose_head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(
            compose_head, text="Enter modeling request", bg=PALETTE["panel2"], fg=PALETTE["text"],
            font=("Segoe UI Semibold", 9), anchor="w",
        ).pack(side="left")
        tk.Label(
            compose_head, text="Click here to type", bg=PALETTE["panel2"], fg="#9bb4ff",
            font=("Segoe UI", 8), anchor="e",
        ).pack(side="right")

        self.prompt = tk.Text(
            compose, height=5, wrap="word", state="normal",
            bg="#191d25", fg=PALETTE["text"], insertbackground="white",
            selectbackground=PALETTE["accent"], relief="flat",
            padx=11, pady=9, font=("Segoe UI", 10), borderwidth=0,
            highlightthickness=1, highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["accent"], takefocus=True, cursor="xterm",
            undo=True, autoseparators=True, maxundo=100,
        )
        self.prompt.pack(fill="x", padx=9, pady=(0, 6))
        self.prompt.bind("<Control-Return>", self._ctrl_enter)
        self.prompt.bind("<Button-1>", lambda _e: self.root.after_idle(self.prompt.focus_set), add="+")
        self.prompt.bind("<FocusIn>", lambda _e: compose.configure(highlightbackground=PALETTE["accent"]), add="+")
        self.prompt.bind("<FocusOut>", lambda _e: compose.configure(highlightbackground=PALETTE["border"]), add="+")
        # Clicking the card header/padding also places the caret in the real input.
        for widget in (compose, compose_head):
            widget.bind("<Button-1>", lambda _e: self.root.after_idle(self.prompt.focus_set), add="+")

        action = tk.Frame(compose, bg=PALETTE["panel2"])
        action.pack(fill="x", padx=9, pady=(0, 9))
        self.mode = tk.StringVar(value="Balanced")
        mode_button = tk.Menubutton(
            action, textvariable=self.mode, bg=PALETTE["panel3"], fg=PALETTE["text"],
            activebackground=PALETTE["panel3"], activeforeground=PALETTE["text"],
            relief="flat", bd=0, padx=10, pady=6, font=("Segoe UI", 9), cursor="hand2"
        )
        mode_menu = tk.Menu(mode_button, tearoff=False, bg=PALETTE["panel2"], fg=PALETTE["text"],
                            activebackground=PALETTE["accent"], activeforeground="white", bd=0)
        for value in ("Fast", "Balanced", "Maximum"):
            mode_menu.add_command(label=value, command=lambda v=value: self.mode.set(v))
        mode_button.configure(menu=mode_menu)
        mode_button.pack(side="left")
        tk.Label(action, text="Ctrl+Enter Run", bg=PALETTE["panel2"], fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(side="left", padx=8)
        self.stop_btn = ttk.Button(action, text="Stop", style="Danger.TButton", command=self.stop_agent, state="disabled")
        self.stop_btn.pack(side="right", padx=(5, 0))
        self.send_btn = ttk.Button(action, text="Run", style="Primary.TButton", command=self.send_prompt)
        self.send_btn.pack(side="right")

        # Be ready to type immediately after launch instead of leaving focus on VTK.
        self.root.after(250, self._focus_prompt)

    def _focus_prompt(self):
        try:
            if str(self.prompt.cget("state")) != "normal":
                self.prompt.configure(state="normal")
            self.prompt.focus_set()
            self.prompt.mark_set("insert", "end-1c")
            self.prompt.see("insert")
        except Exception:
            pass

    def _build_properties_tab(self, parent):
        ttk.Label(parent, text="Parameters", style="Header.TLabel").pack(anchor="w", padx=9, pady=(10, 4))
        body = ttk.Frame(parent, style="Panel.TFrame")
        body.pack(fill="both", expand=True, padx=7, pady=(0, 7))
        self.params = tk.Text(body, width=30, height=5, bg=PALETTE["panel"], fg=PALETTE["text"], insertbackground=PALETTE["text"], relief="flat", font=("Cascadia Mono", 9), borderwidth=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.params.yview, style="Dark.Vertical.TScrollbar")
        self.params.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.params.pack(side="left", fill="both", expand=True)

    def _build_diagnostics_tab(self, parent):
        ttk.Label(parent, text="Diagnostics", style="Header.TLabel").pack(anchor="w", padx=9, pady=(10, 4))
        body = ttk.Frame(parent, style="Panel.TFrame")
        body.pack(fill="both", expand=True, padx=7, pady=(0, 7))
        self.info = tk.Text(body, width=30, height=5, bg=PALETTE["panel"], fg=PALETTE["muted"], insertbackground=PALETTE["text"], relief="flat", font=("Cascadia Mono", 9), borderwidth=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.info.yview, style="Dark.Vertical.TScrollbar")
        self.info.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.info.pack(side="left", fill="both", expand=True)

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, style="Panel.TFrame")
        bar.pack(fill="x", padx=8, pady=(0, 8))
        self.status = tk.StringVar(value="Preparing…")
        ttk.Label(bar, textvariable=self.status, style="Muted.TLabel").pack(side="left", padx=8, pady=5)
        self.runtime_badge = tk.StringVar(value="B-REP · Ready")
        ttk.Label(bar, textvariable=self.runtime_badge, style="Muted.TLabel").pack(side="right", padx=8, pady=5)

    # ---------- viewport ----------
    def _build_vtk(self, parent):
        try:
            from vtkmodules.tk.vtkTkRenderWindowInteractor import vtkTkRenderWindowInteractor
            from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
            from vtkmodules.vtkRenderingCore import vtkRenderer

            self.vtk = vtkTkRenderWindowInteractor(parent, width=850, height=760)
            self.vtk.pack(fill="both", expand=True)
            self.ren = vtkRenderer()
            self.vtk.GetRenderWindow().AddRenderer(self.ren)
            style = vtkInteractorStyleTrackballCamera()
            style.AddObserver("LeftButtonPressEvent", self._pick_geometry)
            self.vtk.GetRenderWindow().GetInteractor().SetInteractorStyle(style)
            self._vtk_style = style
            self.ren.SetBackground(0.067, 0.075, 0.098)
            self.vtk.Initialize()
            self._vtk_ready = True
        except Exception as exc:
            self._vtk_error = str(exc)
            self.static_view = tk.Label(
                parent,
                bg=PALETTE["viewport"],
                fg=PALETTE["warning"],
                text="3D Interaction initialization failed · Static preview mode\n" + self._vtk_error,
                font=("Segoe UI", 10),
                compound="center",
            )
            self.static_view.pack(fill="both", expand=True)
            self._static_ready = True

    def _vtk_actor_from_shape(self, cq_shape):
        """Tessellate a CadQuery/OCP shape directly into one VTK actor.

        Direct tessellation avoids the isolated-face STL exporter failure that
        caused blank viewports in earlier builds and preserves one actor per
        face for click selection.
        """
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData, vtkTriangle
        from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

        verts, tris = cq_shape.tessellate(0.05)
        if not verts or not tris:
            return None
        points = vtkPoints()
        for v in verts:
            points.InsertNextPoint(float(v.x), float(v.y), float(v.z))
        cells = vtkCellArray()
        for tri in tris:
            if len(tri) != 3:
                continue
            cell = vtkTriangle()
            cell.GetPointIds().SetId(0, int(tri[0]))
            cell.GetPointIds().SetId(1, int(tri[1]))
            cell.GetPointIds().SetId(2, int(tri[2]))
            cells.InsertNextCell(cell)
        poly = vtkPolyData()
        poly.SetPoints(points)
        poly.SetPolys(cells)
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(poly)
        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.72, 0.76, 0.84)
        actor.GetProperty().SetEdgeVisibility(True)
        actor.GetProperty().SetEdgeColor(0.18, 0.21, 0.27)
        actor.GetProperty().SetLineWidth(1.0)
        # Keep Python wrappers alive for the actor lifetime.
        actor._standalone_poly = poly
        actor._standalone_mapper = mapper
        return actor

    def _vtk_actor_from_edge(self, edge):
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData, vtkPolyLine
        from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper
        pts = sample_edge_points(edge, 0.08)
        if len(pts) < 2:
            return None
        points = vtkPoints()
        for x, y, z in pts:
            points.InsertNextPoint(x, y, z)
        line = vtkPolyLine(); line.GetPointIds().SetNumberOfIds(len(pts))
        for i in range(len(pts)): line.GetPointIds().SetId(i, i)
        cells = vtkCellArray(); cells.InsertNextCell(line)
        poly = vtkPolyData(); poly.SetPoints(points); poly.SetLines(cells)
        mapper = vtkPolyDataMapper(); mapper.SetInputData(poly)
        actor = vtkActor(); actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.45, 0.65, 1.0); actor.GetProperty().SetLineWidth(3.0)
        actor._standalone_poly = poly; actor._standalone_mapper = mapper
        return actor

    def _apply_pick_mode(self):
        if not self._vtk_ready:
            return
        edge_mode = self.selection_mode.get() == "Edge"
        for actor in self.actor_face:
            actor.SetPickable(not edge_mode)
        for actor in self.actor_edge:
            actor.SetVisibility(edge_mode); actor.SetPickable(edge_mode)
        self.selection_text.set(("Edge click selection - " if edge_mode else "Face click selection - ") + "Left-drag rotate - middle-drag pan - wheel zoom")
        try: self.vtk.GetRenderWindow().Render()
        except Exception: pass

    def _clear_pick_highlight(self):
        if self._selected_actor is None:
            return
        try:
            if self._selected_actor in self.actor_face:
                self._selected_actor.GetProperty().SetColor(0.72, 0.76, 0.84)
            elif self._selected_actor in self.actor_edge:
                self._selected_actor.GetProperty().SetColor(0.45, 0.65, 1.0)
                self._selected_actor.GetProperty().SetLineWidth(3.0)
        except Exception:
            pass
        self._selected_actor = None

    def _render_locked(self):
        shape = self.engine.doc.shape
        if self._vtk_ready:
            self.ren.RemoveAllViewProps()
            self.actor_face = {}
            self.actor_edge = {}
            self._whole_actor = None
            self._selected_actor = None
            actor_count = 0
            if shape is not None:
                topo = self.engine.doc.topology()["faces"]
                for face, rec in zip(shape.Faces(), topo):
                    try:
                        actor = self._vtk_actor_from_shape(face)
                        if actor is None:
                            continue
                        self.ren.AddActor(actor)
                        self.actor_face[actor] = rec
                        actor_count += 1
                    except Exception:
                        continue

                # Build separate edge pick actors. They stay hidden during normal
                # face-selection mode and are exposed only when the user selects Edge.
                try:
                    for edge, rec in zip(shape.Edges(), self.engine.doc.topology()["edges"]):
                        actor = self._vtk_actor_from_edge(edge)
                        if actor is None: continue
                        actor.SetVisibility(False); actor.SetPickable(False)
                        self.ren.AddActor(actor); self.actor_edge[actor] = rec
                except Exception:
                    self.actor_edge = {}

                # Whole-solid fallback is only needed if a particular OCP build
                # cannot tessellate faces individually. Never leave a valid B-Rep blank.
                if actor_count == 0:
                    try:
                        actor = self._vtk_actor_from_shape(shape)
                        if actor is not None:
                            self.ren.AddActor(actor)
                            self._whole_actor = actor
                            actor_count = 1
                    except Exception:
                        pass
            self._last_actor_count = actor_count
            if shape is not None and (not self._camera_initialized or self.engine.doc.view_fit):
                self.ren.ResetCamera(); self._camera_initialized = True; self.engine.doc.view_fit = False
            self._apply_pick_mode()
            try:
                self.vtk.GetRenderWindow().Render()
            except Exception:
                pass
            return

        if self._static_ready:
            if shape is None:
                self.static_view.configure(image="", text="")
                self._static_photo = None
                return
            try:
                width = max(480, self.static_view.winfo_width() or 800)
                height = max(360, self.static_view.winfo_height() or 650)
                out = render_shape_png(shape, min(width, 1400), min(height, 1000), self.engine.doc.view_orientation)
                self._static_photo = tk.PhotoImage(data=out["base64"])
                self.static_view.configure(image=self._static_photo, text=("\n3D interaction unavailable - static preview" if self._vtk_error else ""), compound="top")
                self._last_actor_count = 1
            except Exception as exc:
                self.static_view.configure(image="", text=f"Preview render failed:\n{exc}")
                self._static_photo = None
                self._last_actor_count = 0

    def _pick_geometry(self, obj, _event):
        try:
            from vtkmodules.vtkRenderingCore import vtkPropPicker
            interactor = self.vtk.GetRenderWindow().GetInteractor()
            x, y = interactor.GetEventPosition()
            picker = vtkPropPicker(); picker.Pick(x, y, 0, self.ren)
            actor = picker.GetActor()
            self._clear_pick_highlight()
            if self.selection_mode.get() == "Edge":
                rec = self.actor_edge.get(actor)
                if rec:
                    self.selected_edge_ref = rec["id"]; self.selected_face_ref = None
                    with self.engine.lock:
                        self.engine.selection = {"type":"edge","edge_ref":rec["id"],"geom":rec.get("geom"),"direction":rec.get("direction"),"center":rec.get("center"),"length":rec.get("length"),"vertices":rec.get("vertices")}
                    actor.GetProperty().SetColor(1.0, 0.72, 0.2); actor.GetProperty().SetLineWidth(5.0); self._selected_actor=actor
                    self.status.set(f"Selected edge · {rec['id']}")
            else:
                rec = self.actor_face.get(actor)
                if rec:
                    self.selected_face_ref = rec["id"]; self.selected_edge_ref = None
                    with self.engine.lock:
                        self.engine.selection = {"type":"face","face_ref":rec["id"],"semantic":rec.get("semantic"),"direction":rec.get("direction"),"center":rec.get("center"),"normal":rec.get("normal")}
                    actor.GetProperty().SetColor(0.38, 0.55, 1.0); self._selected_actor=actor
                    self.status.set(f"Selected face · {rec['id']}")
            try: self.vtk.GetRenderWindow().Render()
            except Exception: pass
        finally:
            obj.OnLeftButtonDown()

    # ---------- model/UI synchronization ----------
    def _poll(self):
        cad_changed = False
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "cad_changed":
                    cad_changed = True
                elif kind == "agent_progress":
                    self.agent_status.set(payload)
                elif kind == "agent_done":
                    self._agent_finished(payload, None)
                elif kind == "agent_error":
                    self._agent_finished(None, payload)
        except queue.Empty:
            pass
        if cad_changed or self.engine.revision != self._last_revision:
            self.refresh()
        self.root.after(100, self._poll)

    def refresh(self, force=False):
        if not force and self.engine.revision == self._last_revision:
            return
        with self.engine.lock:
            doc = self.engine.doc
            self.tree.delete(*self.tree.get_children())
            root = self.tree.insert("", "end", text=doc.title, open=True)
            if doc.doc_type == "part":
                origin = self.tree.insert(root, "end", text="Origin", open=False)
                for name in ("XY Plane", "XZ Plane", "YZ Plane", "X Axis", "Y Axis", "Z Axis"):
                    self.tree.insert(origin, "end", text=name)
                sketches = self.tree.insert(root, "end", text="Sketches", open=True)
                for sketch in doc.sketches.values():
                    self.tree.insert(sketches, "end", text=f"{sketch.name}  [{sketch.plane}]  · {len(sketch.entities)} entities")
                features = self.tree.insert(root, "end", text="Features", open=True)
                for feat in doc.features:
                    self.tree.insert(features, "end", text=f"{feat.name}  · {feat.kind}")
                work = self.tree.insert(root, "end", text="Work Features", open=False)
                for item in doc.work_planes.values(): self.tree.insert(work, "end", text=item["name"])
                for item in doc.work_axes.values(): self.tree.insert(work, "end", text=item["name"])
                for item in getattr(doc, "work_points", {}).values(): self.tree.insert(work, "end", text=item["name"])
                if doc.imates:
                    mates = self.tree.insert(root, "end", text="iMates", open=False)
                    for item in doc.imates.values(): self.tree.insert(mates, "end", text=f"{item['name']}  · {item['type']}")
            else:
                occ = self.tree.insert(root, "end", text="Occurrences", open=True)
                for item in doc.occurrences.values():
                    self.tree.insert(occ, "end", text=f"{item.name}{'  [grounded]' if item.grounded else ''}")
                cons = self.tree.insert(root, "end", text="Constraints", open=True)
                for item in doc.constraints:
                    self.tree.insert(cons, "end", text=f"{item.name}  · {item.type}  [{item.health}]")
                joints = self.tree.insert(root, "end", text="Joints", open=True)
                for item in getattr(doc, 'joints', []):
                    self.tree.insert(joints, "end", text=f"{item.name}  · {item.type}  [{item.health}]")

            try:
                values = doc.numeric_params()
            except Exception:
                values = {}
            self.params.configure(state="normal")
            self.params.delete("1.0", "end")
            if not doc.parameters:
                self.params.insert("end", "No user parameters yet.\n")
            for param in doc.parameters.values():
                self.params.insert("end", f"{param.name:18} = {param.expression} {param.unit}   [{values.get(param.name, '?')}]\n")
            self.params.configure(state="disabled")

            topo = doc.topology()
            volume = 0.0
            if doc.shape is not None:
                try:
                    volume = float(doc.shape.Volume())
                except Exception:
                    volume = 0.0
            self.doc_badge.set(doc.doc_type.upper())
            self.doc_summary.set(
                f"{len(doc.features)} features · {len(topo['faces'])} faces\n"
                f"Volume: {volume:,.3f} mm³\n"
                f"Revision: {self.engine.revision}"
            )
            # Render before diagnostics so actor/preview state reflects this exact revision.
            self._render_locked()
            self.info.configure(state="normal")
            self.info.delete("1.0", "end")
            self.info.insert(
                "end",
                f"CADia v{__version__}\n"
                f"Target: {self.host.info['target_id']}\n"
                f"PID: {os.getpid()}\nPort: {self.host.port}\n"
                f"Kernel: Open CASCADE / CadQuery-OCP\n"
                f"CAD control: {'ready' if self.control_ready else 'error'}\n"
                f"MCP Compatible tools: 58 + Assembly Extensions: 10 + Native Extensions: 50\n"
                f"Document: {doc.doc_type}\nRevision: {self.engine.revision}\n"
                f"Faces: {len(topo['faces'])}\nEdges: {len(topo['edges'])}\n"
                f"Viewport: {'VTK interactive' if self._vtk_ready else 'static fallback'}\n"
                f"Viewport actors: {self._last_actor_count}\n"
                + (f"VTK error: {self._vtk_error}\n" if self._vtk_error else ""),
            )
            self.info.configure(state="disabled")
            self._last_revision = self.engine.revision

        self.status.set(f"Ready · revision {self._last_revision} · feature {len(self.engine.doc.features)}")
        self.runtime_badge.set("B-REP · VTK Interactive" if self._vtk_ready else "B-REP · Static preview")

    # ---------- integrated AI ----------
    def _append_chat(self, role, text):
        self.chat.configure(state="normal")
        clean = str(text).strip()
        if role == "user":
            self.chat.insert("end", "\nMe\n", "user_label")
            self.chat.insert("end", clean + "\n", "user")
        elif role == "assistant":
            self.chat.insert("end", "\nAI\n", "assistant_label")
            self.chat.insert("end", clean + "\n", "assistant")
        else:
            self.chat.insert("end", "\n" + clean + "\n", role)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _ctrl_enter(self, _event):
        self.send_prompt()
        return "break"

    def _planning_state(self):
        with self.engine.lock:
            doc = self.engine.doc
            def _interface_names(ifaces):
                out = []
                for group in ("imates", "work_planes", "work_axes", "work_points", "origin_geometry"):
                    out.extend(str(x.get("name")) for x in (ifaces or {}).get(group, []) if x.get("name"))
                return sorted(set(out))
            try:
                params = {k: {"expression": v.expression, "unit": v.unit} for k, v in doc.parameters.items()}
            except Exception:
                params = {}
            try:
                topo = doc.topology()
            except Exception:
                topo = {"faces": [], "edges": [], "vertices": []}
            return {
                "document": {
                    "title": doc.title,
                    "type": doc.doc_type,
                    "units": doc.units,
                    "revision": self.engine.revision,
                },
                "parameters": params,
                "sketches": [
                    {"name": sk.name, "plane": sk.plane, "closed": sk.closed,
                     "entities": [{"id": e.tag, "kind": e.kind, "data": e.data} for e in sk.entities],
                     "dimensions": sk.dimensions, "constraints": sk.constraints}
                    for sk in doc.sketches.values()
                ],
                "sketches3d": [dict(v) for v in getattr(doc, "sketches3d", {}).values()],
                "work_features": {"planes": [dict(v) for v in getattr(doc, "work_planes", {}).values()], "axes": [dict(v) for v in getattr(doc, "work_axes", {}).values()], "points": [dict(v) for v in getattr(doc, "work_points", {}).values()]},
                "features": [
                    {"name": f.name, "kind": f.kind, "operation": f.operation, "params": f.params}
                    for f in doc.features
                ],
                "origin_interfaces": _interface_names(doc.list_interfaces()),
                "occurrences": [{"name": o.name, "grounded": o.grounded, "position_mm": list(o.position_mm), "rotation_deg_xyz": list(o.rotation_deg_xyz), "interfaces": _interface_names(getattr(o, "interfaces", {}))} for o in getattr(doc, "occurrences", {}).values()],
                "constraints": [vars(c).copy() for c in getattr(doc, "constraints", [])],
                "joints": [vars(j).copy() for j in getattr(doc, "joints", [])],
                "selection": dict(self.engine.selection),
                "faces": topo.get("faces", [])[:160],
                "edges": topo.get("edges", [])[:240],
            }

    def send_prompt(self):
        if self.agent_busy:
            return
        text = self.prompt.get("1.0", "end").strip()
        if not text:
            return
        if not self.control_ready:
            messagebox.showerror("CAD control", "CAD CAD control is not ready. Restart the app.")
            return
        self.prompt.delete("1.0", "end")
        self._append_chat("user", text)
        self.agent.reasoning = {"Fast": "low", "Balanced": "medium", "Maximum": "high"}.get(self.mode.get(), "medium")
        agent_text = text
        if self.selected_face_ref or self.selected_edge_ref:
            with self.engine.lock:
                selection = dict(self.engine.selection)
            agent_text += f"\n\nSelected geometry context: {selection}. If the request says this face/edge/here, use this selection."

        self.agent_busy = True
        self.send_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.agent_status.set("Working")

        def on_event(kind, message):
            if kind == "progress":
                self.events.put(("agent_progress", message))

        def worker():
            try:
                state = self._planning_state()
                plan = self.agent.plan(agent_text, state, on_event=on_event)
                executed = execute_with_failure_recovery(
                    executor=self.executor, agent=self.agent, user_prompt=agent_text,
                    state_provider=self._planning_state, plan=plan, on_event=on_event,
                    initial_progress_span=(25, 70), max_ai_repairs=2,
                )
                if not executed.ok:
                    raise RuntimeError("Modeling failed: " + str(executed.error or "Unknown error"))
                self.events.put(("agent_done", executed.message))
            except Exception as exc:
                self.events.put(("agent_error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="CADia-Agent").start()

    def _agent_finished(self, result, error):
        self.agent_busy = False
        self.send_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if error:
            self.agent_status.set("Stopped")
            self._append_chat("error", error)
        else:
            self.agent_status.set("Ready")
            self._append_chat("assistant", result or "Task completed.")
        self.refresh(force=True)
        self.root.after_idle(self._focus_prompt)

    def stop_agent(self):
        if self.agent_busy:
            self.agent.cancel()
            self.agent_status.set("Stopping…")

    # ---------- document commands ----------
    def _engine_call(self, fn):
        try:
            with self.engine.lock:
                result = fn()
            self.refresh(force=True)
            return result
        except Exception as exc:
            messagebox.showerror("CAD error", str(exc))
            return None

    def new(self):
        self._engine_call(lambda: self.engine.execute("new_part", {"name": "Untitled"}))

    def new_assembly(self):
        self._engine_call(lambda: self.engine.execute("new_assembly", {"name": "Untitled Assembly"}))

    def open(self):
        path = filedialog.askopenfilename(
            filetypes=[("CADia document", "*.scad.json"), ("STEP", "*.step *.stp"), ("All files", "*.*")]
        )
        if path:
            self._engine_call(lambda: self.engine.execute("open_document", {"path": path}))

    def save(self):
        path = self.engine.doc.path
        if not path or Path(path).suffix.lower() not in (".json",):
            path = filedialog.asksaveasfilename(
                defaultextension=".scad.json",
                filetypes=[("CADia document", "*.scad.json")],
            )
        if path:
            self._engine_call(lambda: self.engine.execute("save_document", {"path": path}))

    def delete_document_file(self):
        doc=self.engine.doc
        if doc is None:
            messagebox.showinfo("Delete Document", "No open document to delete."); return
        path=str(doc.path or "")
        if not path.lower().endswith(".scad.json"):
            messagebox.showinfo("Delete Document", "Saved CADia document(.scad.json)can only be deleted. Save the document first."); return
        dirty=bool(getattr(doc,"dirty",False))
        text=f"This document file will be permanently deleted from disk.\n\n{Path(path).name}\n\nThis action cannot be undone by Undo."
        if dirty:text += "\nUnsaved changes will also be lost."
        if not messagebox.askyesno("Permanently delete document file", text, icon="warning"):return
        self._engine_call(lambda:self.engine.execute("delete_document_file", {"confirm":True,"discard_unsaved_changes":dirty}))

    def fit(self):
        if self._vtk_ready:
            self.ren.ResetCamera()
            self.vtk.GetRenderWindow().Render()
        elif self._static_ready:
            with self.engine.lock:
                self._render_locked()

    def set_orientation(self):
        value = self.orientation.get()
        self._engine_call(lambda: self.engine.execute("set_view_orientation", {"orientation": value, "fit": True}))

    def close(self):
        try:
            self.agent.cancel()
            self.host.close()
        finally:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    App().run()


if __name__ == "__main__":
    main()
