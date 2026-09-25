from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from standalonecad.bridge.host import CadHost
from standalonecad.core.engine import CadEngine
from standalonecad.planning import PlanExecutor

from .config import settings
from .mesh import build_web_mesh
from .state import public_state


SAFE_COMMANDS = {
    "new_part",
    "new_assembly",
    "close_document",
    "set_units",
    "set_material",
    "set_view_orientation",
    "view_fit",
    "undo",
    "redo",
}


def _safe_name(value: str, fallback: str = "document") -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", value.strip()).strip(".-")
    return (cleaned or fallback)[:96]


@dataclass
class CadRuntime:
    owner_id: str
    project_id: str
    root: Path
    engine: CadEngine = field(init=False)
    host: CadHost = field(init=False)
    executor: PlanExecutor = field(init=False)
    operation_lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    last_access: float = field(default_factory=time.monotonic)
    _mesh_cache: dict[tuple[int, bool], dict[str, Any]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("documents", "imports", "exports", "agent-workspace"):
            (self.root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine = CadEngine(self._changed)
        workspace = self.root / "workspace.json"
        if workspace.exists():
            snapshot = json.loads(workspace.read_text(encoding="utf-8"))
            self._validate_snapshot_paths(snapshot)
            self.engine._restore_workspace(snapshot, bump_revision=False)
        self.host = CadHost(self.engine)
        self.host.start()
        self.executor = PlanExecutor(self.engine, self.host.info["target_id"], target_info=self.host.info)
        self.executor.health_check()
        if not workspace.exists():
            self.save_active_document()
            self.persist()

    def _changed(self) -> None:
        self._mesh_cache.clear()

    def touch(self) -> None:
        self.last_access = time.monotonic()

    def _validate_snapshot_paths(self, snapshot: dict[str, Any]) -> None:
        for row in snapshot.get("sessions", []):
            document = row.get("doc") or {}
            raw = document.get("_runtime_path")
            if raw:
                document_path = self._ensure_project_path(raw, self.root)
                base = document_path.parent
            else:
                base = self.root
            self._validate_document_payload(document, base)

    def _ensure_project_path(self, raw: Any, base: Path) -> Path:
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = base / path
        path = path.resolve()
        root = self.root.resolve()
        if path != root and root not in path.parents:
            raise ValueError("CAD document cannot reference files outside this project.")
        return path

    def _validate_document_payload(self, payload: dict[str, Any], base: Path) -> None:
        import_source = payload.get("import_source")
        if import_source:
            self._ensure_project_path(import_source, base)
        stack = list((payload.get("occurrences") or {}).values())
        seen = 0
        while stack:
            seen += 1
            if seen > 10000:
                raise ValueError("CAD document exceeds the allowed number of assembly references.")
            row = stack.pop()
            if not isinstance(row, dict):
                raise ValueError("CAD document occurrence format is invalid.")
            if row.get("path"):
                self._ensure_project_path(row["path"], base)
            children = row.get("children") or []
            if not isinstance(children, list):
                raise ValueError("CAD document occurrence children format is invalid.")
            stack.extend(children)

    def persist(self) -> None:
        with self.engine.lock:
            snapshot = copy.deepcopy(self.engine._workspace_snapshot())
        target = self.root / "workspace.json"
        temporary = self.root / f"workspace.{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)

    def save_active_document(self) -> dict[str, Any]:
        with self.engine.lock:
            doc = self.engine.doc
            if doc is None:
                raise ValueError("NO_ACTIVE_DOCUMENT")
            session = self.engine._active_session()
            existing = Path(doc.path).resolve() if doc.path else None
            if existing and self.root.resolve() in existing.parents and existing.name.lower().endswith(".scad.json"):
                path = existing
            else:
                name = _safe_name(doc.title, "document")
                path = self.root / "documents" / f"{name}-{session.id[:8]}.scad.json"
            result = self.engine.execute("save_document", {"path": str(path)})
            self.persist()
            return result

    def state(self) -> dict[str, Any]:
        self.touch()
        return public_state(self.engine)

    def mesh(self, include_edges: bool = True) -> dict[str, Any]:
        self.touch()
        key = (int(self.engine.revision), bool(include_edges))
        if key not in self._mesh_cache:
            self._mesh_cache[key] = build_web_mesh(
                self.engine,
                settings.mesh_tolerance,
                settings.edge_tolerance,
                include_edges=include_edges,
            )
        return self._mesh_cache[key]

    def select(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.operation_lock, self.engine.lock:
            kind = payload.get("type")
            mode = str(payload.get("mode") or "replace").lower()
            if mode not in {"replace", "toggle", "clear"}:
                raise ValueError(f"Unsupported selection mode: {mode}")

            def build_multi_selection(kind_name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
                if not items:
                    return {}
                ref_key = "face_ref" if kind_name == "face" else "edge_ref"
                refs_key = "face_refs" if kind_name == "face" else "edge_refs"
                primary = items[-1]
                out: dict[str, Any] = {
                    "type": kind_name,
                    ref_key: primary[ref_key],
                    refs_key: [str(item[ref_key]) for item in items],
                    "items": [dict(item) for item in items],
                    "count": len(items),
                }
                if primary.get("occurrence_name"):
                    out["occurrence_name"] = primary["occurrence_name"]
                return out

            def existing_items(kind_name: str) -> list[dict[str, Any]]:
                current = dict(self.engine.selection or {})
                if current.get("type") != kind_name:
                    return []
                ref_key = "face_ref" if kind_name == "face" else "edge_ref"
                raw_items = current.get("items")
                if isinstance(raw_items, list):
                    normalized: list[dict[str, Any]] = []
                    for raw in raw_items:
                        if not isinstance(raw, dict) or not raw.get(ref_key):
                            continue
                        item = {"type": kind_name, ref_key: str(raw[ref_key])}
                        if raw.get("occurrence_name"):
                            item["occurrence_name"] = str(raw["occurrence_name"])
                        normalized.append(item)
                    if normalized:
                        return normalized
                if current.get(ref_key):
                    item = {"type": kind_name, ref_key: str(current[ref_key])}
                    if current.get("occurrence_name"):
                        item["occurrence_name"] = str(current["occurrence_name"])
                    return [item]
                return []

            def toggle_item(kind_name: str, item: dict[str, Any]) -> dict[str, Any]:
                ref_key = "face_ref" if kind_name == "face" else "edge_ref"
                items = existing_items(kind_name)
                target_key = (str(item[ref_key]), item.get("occurrence_name") or None)
                match_index = next(
                    (
                        index
                        for index, existing in enumerate(items)
                        if (str(existing[ref_key]), existing.get("occurrence_name") or None) == target_key
                    ),
                    None,
                )
                if match_index is None:
                    items.append(item)
                else:
                    items.pop(match_index)
                return build_multi_selection(kind_name, items)

            selection: dict[str, Any] = {}
            if mode == "clear" or not kind:
                selection = {}
            elif kind == "face" and payload.get("face_ref"):
                face_ref = str(payload["face_ref"])
                occurrence_name = str(payload["occurrence_name"]) if payload.get("occurrence_name") else None
                if not any(row["id"] == face_ref and row.get("occurrence_name") == occurrence_name for row in self.mesh(True)["faces"]):
                    raise ValueError("Could not find that face in the current model. Refresh the view.")
                item: dict[str, Any] = {"type": "face", "face_ref": face_ref}
                if occurrence_name:
                    item["occurrence_name"] = occurrence_name
                selection = toggle_item("face", item) if mode == "toggle" else build_multi_selection("face", [item])
            elif kind == "edge" and payload.get("edge_ref"):
                edge_ref = str(payload["edge_ref"])
                occurrence_name = str(payload["occurrence_name"]) if payload.get("occurrence_name") else None
                if not any(row["id"] == edge_ref and row.get("occurrence_name") == occurrence_name for row in self.mesh(True)["edges"]):
                    raise ValueError("Could not find that edge in the current model. Refresh the view.")
                item = {"type": "edge", "edge_ref": edge_ref}
                if occurrence_name:
                    item["occurrence_name"] = occurrence_name
                selection = toggle_item("edge", item) if mode == "toggle" else build_multi_selection("edge", [item])
            elif kind == "feature" and payload.get("feature_name"):
                feature = self.engine.doc.find_feature(str(payload["feature_name"]))
                selection = {"type": "feature", "feature_name": feature.name, "kind": feature.kind, "operation": feature.operation, "params": feature.params}
            elif kind == "occurrence" and payload.get("occurrence_name"):
                occurrence_name = str(payload["occurrence_name"])
                if occurrence_name not in getattr(self.engine.doc, "occurrences", {}):
                    raise ValueError("Could not find that occurrence in the current assembly.")
                selection = {"type": "occurrence", "occurrence_name": occurrence_name}
            elif kind == "part":
                doc = self.engine.doc
                if doc is None or doc.doc_type != "part" or doc.shape is None:
                    raise ValueError("The active document is not a selectable part.")
                selection = {"type": "part", "document_title": str(doc.title)}
            self.engine.selection = selection
            self.persist()
            return selection

    def command(self, command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if command not in SAFE_COMMANDS:
            raise ValueError(f"Web command is not allowed: {command}")
        with self.operation_lock:
            if command == "undo":
                result = self.engine.undo()
            elif command == "redo":
                result = self.engine.redo()
            else:
                result = self.engine.execute(command, arguments or {})
            self.persist()
            return result

    def activate_document(self, document_id: str) -> dict[str, Any]:
        with self.operation_lock:
            result = self.engine.activate_document(document_id)
            self.persist()
            return result

    def import_document(self, source: Path) -> dict[str, Any]:
        source = source.resolve()
        if self.root.resolve() not in source.parents:
            raise ValueError("Import path escapes project storage")
        if source.name.lower().endswith(".scad.json"):
            try:
                payload = json.loads(source.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"Invalid .scad.json document: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(".scad.json top-level value must be an object.")
            self._validate_document_payload(payload, source.parent)
        with self.operation_lock:
            result = self.engine.execute("open_document", {"path": str(source)})
            self.persist()
            return result

    def export(self, kind: str) -> Path:
        kind = kind.lower()
        if kind not in {"step", "stl"}:
            raise ValueError("Export format must be step or stl")
        with self.operation_lock, self.engine.lock:
            doc = self.engine.doc
            if doc is None:
                raise ValueError("NO_ACTIVE_DOCUMENT")
            filename = f"{_safe_name(doc.title)}-{uuid.uuid4().hex[:8]}.{kind}"
            output = self.root / "exports" / filename
            command = "export_step" if kind == "step" else "export_stl"
            self.engine.execute(command, {"output_path": str(output)})
            return output

    def close(self) -> None:
        try:
            self.persist()
        finally:
            self.host.close()


class CadRuntimeManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[tuple[str, str], CadRuntime] = {}

    def project_root(self, owner_id: str, project_id: str) -> Path:
        return settings.data_root / "projects" / owner_id / project_id

    def get(self, owner_id: str, project_id: str) -> CadRuntime:
        key = (owner_id, project_id)
        with self._lock:
            runtime = self._items.get(key)
            if runtime is None:
                runtime = CadRuntime(owner_id, project_id, self.project_root(owner_id, project_id))
                self._items[key] = runtime
            runtime.touch()
            return runtime

    def evict_idle(self) -> int:
        now = time.monotonic()
        stale: list[tuple[str, str]] = []
        with self._lock:
            for key, runtime in self._items.items():
                if now - runtime.last_access <= settings.engine_idle_seconds:
                    continue
                acquired = runtime.operation_lock.acquire(blocking=False)
                if acquired:
                    runtime.operation_lock.release()
                    stale.append(key)
            for key in stale:
                self._items.pop(key).close()
        return len(stale)

    def drop(self, owner_id: str, project_id: str) -> None:
        with self._lock:
            runtime = self._items.pop((owner_id, project_id), None)
        if runtime:
            runtime.close()

    def close_all(self) -> None:
        with self._lock:
            values = list(self._items.values())
            self._items.clear()
        for runtime in values:
            runtime.close()


runtime_manager = CadRuntimeManager()
