from __future__ import annotations

import copy
import json
import math
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from standalonecad.core.render import render_shape_png
from standalonecad.recovery import execute_with_failure_recovery

from .cad_runtime import CadRuntime, runtime_manager
from .config import settings
from .providers import selected_provider_name
from .providers.app_server import app_server_manager
from .state import planning_state
from .web_agent import DesktopParityCodexAgent, desktop_catalog


# Drawing images are analyzed into a typed engineering spec, then converted into
# CADia tool calls that create editable B-Rep feature history.

ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_REFINEMENTS = 0
VISUAL_CAD_WORKERS = max(1, min(2, settings.max_concurrent_ai_turns))


DRAWING_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "part_name": {"type": "string"},
        "units": {"type": "string"},
        "views_detected": {"type": "array", "items": {"type": "string"}},
        "overall_dimensions_mm": {
            "type": "object",
            "properties": {
                "x": {"type": ["number", "null"]},
                "y": {"type": ["number", "null"]},
                "z": {"type": ["number", "null"]},
            },
            "required": ["x", "y", "z"],
            "additionalProperties": False,
        },
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "description": {"type": "string"},
                    "count": {"type": ["integer", "null"]},
                    "axis": {"type": ["string", "null"]},
                    "operation_hint": {"type": ["string", "null"]},
                    "dimensions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value_mm": {"type": ["number", "null"]},
                            },
                            "required": ["name", "value_mm"],
                            "additionalProperties": False,
                        },
                    },
                    "location_mm": {
                        "type": "object",
                        "properties": {
                            "x": {"type": ["number", "null"]},
                            "y": {"type": ["number", "null"]},
                            "z": {"type": ["number", "null"]},
                        },
                        "required": ["x", "y", "z"],
                        "additionalProperties": False,
                    },
                },
                "required": ["kind", "description", "count", "axis", "operation_hint", "dimensions", "location_mm"],
                "additionalProperties": False,
            },
        },
        "symmetry": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": [
        "part_name",
        "units",
        "views_detected",
        "overall_dimensions_mm",
        "features",
        "symmetry",
        "assumptions",
        "uncertainties",
        "confidence",
    ],
    "additionalProperties": False,
}


VISUAL_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "calls": {
            "type": "array",
            "minItems": 1,
            "maxItems": 80,
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "arguments_json": {"type": "string"},
                    "bind": {"type": ["string", "null"]},
                },
                "required": ["tool", "arguments_json", "bind"],
                "additionalProperties": False,
            },
        },
        "note": {"type": "string"},
    },
    "required": ["calls", "note"],
    "additionalProperties": False,
}


REFINEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["accept", "refine"]},
        "confidence": {"type": "number"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "calls": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "arguments_json": {"type": "string"},
                    "bind": {"type": ["string", "null"]},
                },
                "required": ["tool", "arguments_json", "bind"],
                "additionalProperties": False,
            },
        },
        "note": {"type": "string"},
    },
    "required": ["action", "confidence", "issues", "calls", "note"],
    "additionalProperties": False,
}


@dataclass
class VisualCadJob:
    id: str
    user_id: str
    project_id: str
    input_files: dict[str, str]
    notes: str = ""
    dimension_hint: str = ""
    model: str = "gpt-5.6-sol"
    effort: str = "high"
    status: str = "queued"
    progress: int = 0
    message: str = "Queued"
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class VisualCadJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, VisualCadJob] = {}
        self._pool = ThreadPoolExecutor(max_workers=VISUAL_CAD_WORKERS, thread_name_prefix="cadia-visual-cad")

    def _job_state_path(self, job_id: str, user_id: str, project_id: str) -> Path:
        return runtime_manager.project_root(user_id, project_id) / "visual_cad" / "jobs" / f"{job_id}.json"

    def _persist_job(self, job: VisualCadJob) -> None:
        try:
            path = self._job_state_path(job.id, job.user_id, job.project_id)
            _write_json(path, {
                "id": job.id,
                "user_id": job.user_id,
                "project_id": job.project_id,
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "error": job.error,
                "result": job.result,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            })
        except Exception:
            # Job persistence must never make a modeling operation fail.
            pass

    def _load_persisted(self, job_id: str, user_id: str, project_id: str) -> VisualCadJob | None:
        path = self._job_state_path(job_id, user_id, project_id)
        if not path.is_file():
            return None
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            status = str(row.get("status") or "failed")
            error = row.get("error")
            message = str(row.get("message") or "")
            if status in {"queued", "running", "cancelling"}:
                status = "failed"
                error = "Visual CAD job was interrupted by a server restart."
                message = "Interrupted by server restart"
            return VisualCadJob(
                id=job_id, user_id=user_id, project_id=project_id, input_files={},
                status=status, progress=int(row.get("progress") or 0), message=message,
                error=None if error is None else str(error), result=row.get("result"),
                created_at=float(row.get("created_at") or time.time()),
                updated_at=float(row.get("updated_at") or time.time()),
            )
        except Exception:
            return None

    def create(self, job: VisualCadJob) -> VisualCadJob:
        with self._lock:
            self._jobs[job.id] = job
            self._purge_locked()
            self._persist_job(job)
        self._pool.submit(self._run, job.id)
        return job

    def get(self, job_id: str, user_id: str, project_id: str) -> VisualCadJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                job = self._load_persisted(job_id, user_id, project_id)
                if job is not None:
                    self._jobs[job.id] = job
            if job is None or job.user_id != user_id or job.project_id != project_id:
                raise KeyError(job_id)
            return job

    def cancel(self, job_id: str, user_id: str, project_id: str) -> VisualCadJob:
        job = self.get(job_id, user_id, project_id)
        job.cancel_event.set()
        self._update(job, status="cancelling", message="Cancelling…")
        return job

    def _purge_locked(self) -> None:
        cutoff = time.time() - 6 * 3600
        stale = [key for key, value in self._jobs.items() if value.updated_at < cutoff and value.status in {"done", "failed", "cancelled"}]
        for key in stale:
            self._jobs.pop(key, None)

    def _update(self, job: VisualCadJob, *, status: str | None = None, progress: int | None = None, message: str | None = None) -> None:
        with self._lock:
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = max(job.progress, max(0, min(100, int(progress))))
            if message is not None:
                job.message = message
            job.updated_at = time.time()
            self._persist_job(job)

    def _check_cancel(self, job: VisualCadJob) -> None:
        if job.cancel_event.is_set():
            raise InterruptedError("Visual CAD job was cancelled.")

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return
        try:
            if selected_provider_name(job.user_id) != "codex":
                raise RuntimeError("Drawing → CAD Phase 1 currently requires the ChatGPT / Codex provider. Connect ChatGPT / Codex and try again.")
            self._update(job, status="running", progress=3, message="Preparing drawing reconstruction…")
            result = self._reconstruct(job)
            job.result = result
            self._update(job, status="done", progress=100, message="Editable CAD reconstructed")
        except InterruptedError as exc:
            job.error = str(exc)
            self._update(job, status="cancelled", message="Cancelled")
        except Exception as exc:
            job.error = str(exc)
            self._update(job, status="failed", message="Drawing reconstruction failed")

    def _reconstruct(self, job: VisualCadJob) -> dict[str, Any]:
        """Convert uploaded engineering drawings into an editable CADia B-Rep model.

        Pipeline:
          image views -> structured DrawingSpec -> typed CAD tool plan ->
          validated execution with recovery -> kernel/B-Rep validation.
        """
        runtime = runtime_manager.get(job.user_id, job.project_id)
        client = app_server_manager.get(job.user_id)
        job_dir = (client.workspace / "visual-cad" / job.id).resolve()
        job_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        report_dir = runtime.root / "visual_cad" / job.id
        report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        image_paths: dict[str, Path] = {}
        for label, raw in job.input_files.items():
            source = Path(raw).resolve()
            suffix = source.suffix.lower()
            target = job_dir / f"input_{label}{suffix}"
            shutil.copy2(source, target)
            os.chmod(target, 0o600)
            image_paths[label] = target

        agent = VisualDrawingCodexAgent(
            client=client,
            target_id=f"visual-cad:{job.project_id}:{job.id}",
            project_root=runtime.root,
            model=job.model or "gpt-5.6-sol",
            reasoning=job.effort or "high",
        )

        def event(kind: str, value: Any) -> None:
            self._check_cancel(job)
            if kind != "progress":
                return
            text = str(value.get("message") or "Analyzing…") if isinstance(value, dict) else str(value)
            pct = 18 if job.progress < 35 else 55 if job.progress < 70 else 82
            self._update(job, progress=max(job.progress, pct), message=text)

        self._check_cancel(job)
        self._update(job, progress=10, message="Analyzing engineering drawing…")
        spec = agent.extract_spec(
            image_paths,
            notes=job.notes,
            dimension_hint=job.dimension_hint,
            on_event=event,
        )
        spec = _normalize_spec(spec)
        _validate_extraction(spec)
        _write_json(report_dir / "drawing_spec.json", spec)

        self._check_cancel(job)
        self._update(job, progress=38, message="Planning editable CAD features…")
        with runtime.operation_lock:
            initial_snapshot = copy.deepcopy(runtime.engine._workspace_snapshot())
            try:
                plan = agent.plan_from_drawing(
                    spec,
                    image_paths,
                    planning_state(runtime.engine),
                    on_event=event,
                )
                _validate_plan(plan)
                executed = execute_with_failure_recovery(
                    executor=runtime.executor,
                    agent=agent,
                    user_prompt=agent.visual_request_text(spec, image_paths, job.notes, job.dimension_hint),
                    state_provider=lambda: planning_state(runtime.engine),
                    plan=plan,
                    on_event=event,
                    initial_progress_span=(48, 82),
                    max_ai_repairs=2,
                )
                if not executed.ok:
                    raise RuntimeError(str(executed.error or "Drawing reconstruction failed"))
                validation = _validate_result(runtime, spec)
                if not validation.get("solid_present") or not validation.get("brep_valid"):
                    raise RuntimeError("Drawing reconstruction did not produce a valid editable B-Rep.")
                runtime.persist()
                state = runtime.state()
            except Exception:
                runtime.engine._restore_workspace(copy.deepcopy(initial_snapshot), bump_revision=True)
                raise

        report = {
            "job_id": job.id,
            "pipeline": "CADia DrawingSpec typed-CAD pipeline",
            "provider": "codex",
            "model": job.model,
            "spec": spec,
            "validation": validation,
            "state_revision": (state.get("document") or {}).get("revision"),
        }
        _write_json(report_dir / "report.json", report)
        report["report"] = f"visual_cad/{job.id}/report.json"
        return {"state": state, **report}


class VisualDrawingCodexAgent(DesktopParityCodexAgent):
    def _image_summary(self, images: dict[str, Path]) -> str:
        return ", ".join(images.keys())

    def extract_spec(
        self,
        images: dict[str, Path],
        *,
        notes: str,
        dimension_hint: str,
        on_event: Callable[[str, Any], None] | None = None,
    ) -> dict[str, Any]:
        prompt = f"""You are the structured extraction stage of an engineering Drawing-to-CAD system.
The attached local images are the actual drawing inputs ({self._image_summary(images)}). Inspect the attached images directly; do not call view_image, shell tools, Python, or filesystem tools.
If one sheet contains multiple orthographic views, inspect every view on that sheet.

Interpret the images as mechanical engineering drawings, not artistic references.
Coordinate convention for the CAD model:
- front-view horizontal = +X
- front-view vertical = +Z
- top-view vertical/depth = +Y
- right-view horizontal/depth = +Y and vertical = +Z
Use millimetres. Read written dimensions literally when legible. Never invent a precise labelled dimension from pixel size.
If a required dimension is absent, use null and record the ambiguity.
Capture holes, slots, pockets, bosses, counterbores/countersinks, fillets/chamfers, repeated patterns, axes/centrelines and symmetry when visible.
Every feature object must use the provided schema. Put human-readable detail in description and name/value pairs in dimensions.

User notes: {notes or '(none)'}
Known dimension/reference hint: {dimension_hint or '(none)'}

Return only schema-conforming JSON.
"""
        return self._web_client.complete_json(
            prompt,
            output_schema=DRAWING_SPEC_SCHEMA,
            model=self.model,
            effort=self.reasoning,
            on_event=on_event,
            local_images=list(images.values()),
        )

    def visual_request_text(self, spec: dict[str, Any], images: dict[str, Path], notes: str, dimension_hint: str) -> str:
        return (
            "Reconstruct a new editable parametric part from the supplied engineering drawing. "
            "Preserve feature intent and all explicit dimensions. "
            f"Drawing spec: {json.dumps(spec, ensure_ascii=False)}. "
            f"User notes: {notes or '(none)'}. Dimension hint: {dimension_hint or '(none)'}. "
            f"Images: {', '.join(str(p) for p in images.values())}."
        )

    def plan_from_drawing(
        self,
        spec: dict[str, Any],
        images: dict[str, Path],
        current_state: dict[str, Any],
        *,
        on_event: Callable[[str, Any], None] | None = None,
    ) -> dict[str, Any]:
        catalog = json.dumps(self._catalog, ensure_ascii=False, separators=(",", ":"))
        prompt = f"""You are CADia's Drawing-to-CAD planning engine.
The attached drawing has already been inspected and normalized into DRAWING_SPEC. Do not use image/file/shell tools in this stage.

A structured extraction was produced first:
DRAWING_SPEC={json.dumps(spec, ensure_ascii=False, separators=(',', ':'))}

CURRENT_STATE={json.dumps(current_state, ensure_ascii=False, separators=(',', ':'))}
TOOL_CATALOG={catalog}

Create a NEW editable part from the drawing using only TOOL_CATALOG operations. Never emit Python, shell, CadQuery source, eval/exec, file operations, network calls, or prose.
The new model must be feature-based and editable. Prefer a compact engineering history: sketches + extrude/revolve, then holes/cuts, patterns, fillets/chamfers. Prefer native/Inventor-style operations and deterministic CADia tools over long chains of primitive booleans.
Use explicit drawing dimensions exactly. Do not estimate a labelled dimension from pixels. For omitted dimensions, choose a minimal symmetric/standard assumption and keep it easy to edit.
If CURRENT_STATE already has a document or geometry, start with inventor_new_part so the existing model is not overwritten.
Map orthographic views using X/Y/Z convention stated in the extraction stage.
For each call, encode the complete tool arguments object as a JSON string in arguments_json.
Use bind=null unless a later call needs the result.
Return only schema-conforming JSON.
"""
        raw = self._web_client.complete_json(
            prompt,
            output_schema=VISUAL_PLAN_SCHEMA,
            model=self.model,
            effort=self.reasoning,
            on_event=on_event,
        )
        return _decode_visual_plan(raw, allow_empty=False)


    def refine_from_views(
        self,
        *,
        spec: dict[str, Any],
        originals: dict[str, Path],
        rendered: dict[str, Path],
        current_state: dict[str, Any],
        pass_index: int,
        on_event: Callable[[str, Any], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        all_images = {**{f"original_{k}": v for k, v in originals.items()}, **{f"cad_{k}": v for k, v in rendered.items()}}
        prompt = f"""You are the visual verification/refinement stage of an engineering Drawing-to-CAD pipeline.
The attached local images contain both original drawing views and CAD render views ({self._image_summary(all_images)}). Inspect the attachments directly; do not call view_image, shell, Python, or filesystem tools.

Compare the reconstructed CAD renders to the original drawing views. This is verification, not a fresh redesign.
DRAWING_SPEC={json.dumps(spec, ensure_ascii=False, separators=(',', ':'))}
CURRENT_STATE={json.dumps(current_state, ensure_ascii=False, separators=(',', ':'))}
TOOL_CATALOG={json.dumps(self._catalog, ensure_ascii=False, separators=(',', ':'))}

Check silhouette, relative proportions, hole/slot count and placement, symmetry, visible pockets/bosses, and explicit dimensions from the spec. Do not change a correct explicit dimension merely to improve perspective similarity. Do not add cosmetic details unsupported by the drawing.
If the model is already consistent enough, action must be "accept" and calls must be [].
If there is a clear engineering mismatch that can be corrected safely, action must be "refine" and calls may contain only TOOL_CATALOG operations that EDIT the current model. Do not create a new replacement part during visual refinement.
Pass number: {pass_index + 1}
For every refinement call, encode the complete tool arguments object as JSON text in arguments_json and use bind=null unless needed.
If action is accept, calls must be an empty array.
Return only schema-conforming JSON.
"""
        raw = self._web_client.complete_json(
            prompt,
            output_schema=REFINEMENT_SCHEMA,
            model=self.model,
            effort=self.reasoning,
            on_event=on_event,
            local_images=list(all_images.values()),
            cancel_check=cancel_check,
            stall_timeout_seconds=180,
            hard_timeout_seconds=900,
        )
        return _decode_refinement(raw)


visual_cad_jobs = VisualCadJobManager()


def _normalize_spec(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Drawing extraction did not return an object.")
    dims = raw.get("overall_dimensions_mm") if isinstance(raw.get("overall_dimensions_mm"), dict) else {}
    normalized = {
        "part_name": str(raw.get("part_name") or "Drawing Part")[:120],
        "units": "mm",
        "views_detected": [str(x)[:40] for x in (raw.get("views_detected") or [])][:12],
        "overall_dimensions_mm": {
            "x": _positive_or_none(dims.get("x")),
            "y": _positive_or_none(dims.get("y")),
            "z": _positive_or_none(dims.get("z")),
        },
        "features": [x for x in (raw.get("features") or []) if isinstance(x, dict)][:80],
        "symmetry": [str(x)[:300] for x in (raw.get("symmetry") or [])][:20],
        "assumptions": [str(x)[:500] for x in (raw.get("assumptions") or [])][:30],
        "uncertainties": [str(x)[:500] for x in (raw.get("uncertainties") or [])][:30],
        "confidence": max(0.0, min(1.0, _finite_float(raw.get("confidence"), 0.0))),
    }
    return normalized


def _decode_call_rows(rows: Any, *, allow_empty: bool) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("CAD plan calls are not an array.")
    if not rows and not allow_empty:
        raise ValueError("CAD plan contains no tool calls.")
    decoded: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"CAD plan call {index} is invalid.")
        raw_args = row.get("arguments_json")
        if not isinstance(raw_args, str):
            raise ValueError(f"CAD plan call {index} arguments_json is missing.")
        try:
            arguments = json.loads(raw_args)
        except Exception as exc:
            raise ValueError(f"CAD plan call {index} arguments_json is not valid JSON.") from exc
        if not isinstance(arguments, dict):
            raise ValueError(f"CAD plan call {index} arguments must decode to an object.")
        call: dict[str, Any] = {"tool": str(row.get("tool") or ""), "arguments": arguments}
        bind = row.get("bind")
        if isinstance(bind, str) and bind.strip():
            call["bind"] = bind.strip()
        decoded.append(call)
    return decoded


def _decode_visual_plan(raw: dict[str, Any], *, allow_empty: bool) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("CAD plan is not an object.")
    return {
        "calls": _decode_call_rows(raw.get("calls"), allow_empty=allow_empty),
        "note": str(raw.get("note") or ""),
    }


def _decode_refinement(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Visual refinement response is not an object.")
    action = str(raw.get("action") or "accept").lower()
    if action not in {"accept", "refine"}:
        raise ValueError("Visual refinement action is invalid.")
    calls = _decode_call_rows(raw.get("calls"), allow_empty=(action == "accept"))
    if action == "accept" and calls:
        calls = []
    if action == "refine" and not calls:
        raise ValueError("Visual refinement requested refine but returned no tool calls.")
    return {
        "action": action,
        "confidence": _finite_float(raw.get("confidence"), 0.0),
        "issues": [str(x) for x in (raw.get("issues") or [])][:20],
        "calls": calls,
        "note": str(raw.get("note") or ""),
    }


def _validate_extraction(spec: dict[str, Any]) -> None:
    dims = spec.get("overall_dimensions_mm") or {}
    has_dimension = any(_positive_or_none(dims.get(axis)) is not None for axis in ("x", "y", "z"))
    has_features = bool(spec.get("features"))
    has_views = bool(spec.get("views_detected"))
    confidence = _finite_float(spec.get("confidence"), 0.0)
    if confidence <= 0.0 or not has_views or (not has_dimension and not has_features):
        raise RuntimeError(
            "Drawing image analysis failed: no reliable geometry/dimensions were extracted from the attached image. "
            "The CAD model was not generated."
        )


def _positive_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _finite_float(value: Any, default: float) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except Exception:
        return default


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("CAD plan is not an object.")
    calls = plan.get("calls")
    if not isinstance(calls, list) or not calls:
        raise ValueError("CAD plan contains no tool calls.")
    if len(calls) > 80:
        raise ValueError("CAD plan is too large.")
    allowed = {row["name"] for row in desktop_catalog()}
    for index, call in enumerate(calls, 1):
        if not isinstance(call, dict):
            raise ValueError(f"CAD plan call {index} is invalid.")
        tool = str(call.get("tool") or "")
        if tool not in allowed:
            raise ValueError(f"CAD plan attempted a non-CAD/unknown tool: {tool}")
        if not isinstance(call.get("arguments"), dict):
            raise ValueError(f"CAD plan call {index} arguments must be an object.")


def _render_validation_views(runtime: CadRuntime, job_dir: Path, *, pass_index: int) -> dict[str, Path]:
    with runtime.engine.lock:
        doc = runtime.engine.doc
        shape = doc.shape if doc is not None else None
        if shape is None:
            return {}
        shape_copy = shape
    output: dict[str, Path] = {}
    for orientation in ("front", "top", "right", "iso_top_right"):
        path = job_dir / f"render_p{pass_index + 1}_{orientation}.png"
        try:
            render_shape_png(shape_copy, 960, 720, orientation, str(path))
            if path.is_file() and path.stat().st_size > 100:
                output[orientation] = path
        except Exception:
            # Visual refinement is advisory. A missing VTK headless backend must
            # never destroy an otherwise valid CAD reconstruction.
            continue
    return output


def _validate_result(runtime: CadRuntime, spec: dict[str, Any]) -> dict[str, Any]:
    with runtime.engine.lock:
        doc = runtime.engine.doc
        shape = doc.shape if doc is not None else None
        if shape is None:
            return {"solid_present": False, "brep_valid": False, "dimension_checks": [], "score": 0.0}
        try:
            brep_valid = bool(shape.isValid())
        except Exception:
            brep_valid = True
        try:
            box = shape.BoundingBox()
            actual = {"x": float(box.xlen), "y": float(box.ylen), "z": float(box.zlen)}
        except Exception:
            actual = {"x": None, "y": None, "z": None}

    checks: list[dict[str, Any]] = []
    expected = spec.get("overall_dimensions_mm") or {}
    for axis in ("x", "y", "z"):
        exp = _positive_or_none(expected.get(axis))
        act = _positive_or_none(actual.get(axis))
        if exp is None or act is None:
            continue
        rel = abs(act - exp) / max(exp, 1e-9)
        checks.append({
            "axis": axis,
            "expected_mm": round(exp, 5),
            "actual_mm": round(act, 5),
            "relative_error": round(rel, 6),
            "pass": rel <= 0.025,
        })
    dimension_score = 1.0 if not checks else sum(1.0 if row["pass"] else max(0.0, 1.0 - row["relative_error"]) for row in checks) / len(checks)
    score = (0.55 if brep_valid else 0.0) + 0.45 * dimension_score
    return {
        "solid_present": True,
        "brep_valid": brep_valid,
        "bounding_box_mm": actual,
        "dimension_checks": checks,
        "score": round(max(0.0, min(1.0, score)), 4),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
