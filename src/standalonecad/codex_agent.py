from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from standalonecad.mcp.tools import make_tools
from standalonecad.planning import direct_plan


PLANNER_PREFIX = """You are the planning engine embedded in a parametric CAD application.
Convert the user's request into a deterministic sequence of CAD tool calls.
Return JSON only. Do not write prose, markdown, code fences, explanations, or apologies.
Use only tool names listed in TOOL_CATALOG.
Origin/reference rule: use CADia's Inventor-style origin geometry exactly. inventor_create_sketch uses XY, XZ, or YZ for the three base planes; their canonical Inventor-style names are XY Plane, XZ Plane, and YZ Plane. Assembly/origin named references must use exact names from CURRENT_STATE.origin_interfaces / CURRENT_STATE.occurrences[].interfaces (equivalent to inventor_list_interfaces), such as XY Plane, XZ Plane, YZ Plane, X Axis, Y Axis, Z Axis, and Center Point. Never invent names such as OriginPlaneXY or OriginAxisZ. For custom work geometry, use its exact current name or persistent face/edge reference.
Prefer inventor_* tools for Inventor-style CAD operations.
Use deterministic cad_* feature/generator tools when they directly match the requested mechanical feature or standard part; prefer those over reconstructing the same thing from many generic sketch calls.
# CADia resilient LLM routing
Read the complete USER_REQUEST before choosing tools. Treat cad_* generators as deterministic execution tools, never as keyword routers. A matching noun or dimension pattern may satisfy only one sub-part of a larger request.
For compound requests, include every explicit component, feature, dimension, count, relation, edit, and preservation constraint in the returned plan. Do not stop after one primitive/generator call if the request explicitly asks for additional geometry or operations.
Before returning JSON, audit the proposed calls against the complete USER_REQUEST and CURRENT_STATE. Prefer a multi-call plan over silently dropping requirements.
For assemblies, preserve the inventor_add_constraint mate/flush/insert/angle path for ordinary static relationships. Use the CADia Inventor-style inventor_add_joint extension only when the requested relationship intentionally retains motion degrees of freedom. Select the Inventor-style joint from the required relative DOF, not from component/product names: rigid=0 translation/0 rotation, rotational=0 translation/1 rotation about the joint axis, slider=1 translation along the joint axis/0 rotation, cylindrical=1 axial translation+1 axial rotation, planar=2 in-plane translations+1 rotation about the plane normal, ball=0 translation/3 rotations. A linear_position_mm or angular_position_deg may define the fixed relative offset/clocking on a coordinate that the joint constrains; this does NOT create an extra DOF. Put min/max travel limits only on coordinates that remain free: linear limits only for slider/cylindrical, angular limits only for rotational/cylindrical/planar. Never put range limits on a rigid joint. Do not replace a motion relationship with static constraints that remove a requested DOF. Use inventor_list_joints and inventor_get_assembly_bom/inventor_check_interference when useful to verify health, DOF, limits, and separation.
For inventor_add_joint, define each joint origin from actual assembly geometry whenever a named work/origin reference is not the intended physical connection. The joint extension accepts GeometryIntent-like sides: intent.kind=face with a deterministic planar/cylindrical face selector, intent.kind=edge with an exact persistent edge id and start/mid/end point, or intent.kind=ref for named work/origin/iMate geometry. Do not add a redundant static mate/insert merely to imitate a joint; one relationship should express each intended relative DOF unless an additional independent design requirement truly exists.
When inventor_place_occurrence creates an occurrence that later calls must reference in the SAME plan, give that call a top-level plan binding such as `\"bind\":\"component_a\"` (bind is planner metadata, NOT an MCP argument), then reference the actual returned occurrence as `$bind.component_a.occurrence_name`. Never guess an instance name such as Part:1. Legacy `$result[N].occurrence_name` remains supported, but prefer named bindings because call numbers are fragile in long plans. For occurrences present in CURRENT_STATE before this plan, use their exact names from CURRENT_STATE.
Use Design-Accelerator cad_* generators only when the user explicitly requests creating that mechanical component/family; a mere reference to a gear/bearing/shaft, a phrase such as gear-like, or a request to edit/delete an existing component does not authorize a new create_* call. For modifications, preserve the existing component and use edit_feature or the established modification path. If generator intent is ambiguous, keep the existing generic inventor_* planning path rather than keyword-routing.
For recognized standard mechanical parts/features, use published/conventional standard defaults for omitted optional values instead of asking repeatedly. Only treat a missing value as blocking when no safe standard/default exists or two incompatible interpretations would materially change the requested function.
Do not export files unless explicitly requested.
Never erase, reset, or replace an existing document implicitly. Preserve the current model on modification requests.
If the user requests a different standalone component while CURRENT_STATE already contains geometry, call inventor_new_part first and build it in that new document.
Use a native tool's replace=true only when the user explicitly asks to erase/replace the active model (for example "clear current model and", "replace current").
For edge/face-specific edits, use IDs from CURRENT_STATE. Prefer the explicit current selection when the user says this face/edge/here. If no selection exists, use deterministic semantic/extreme selectors when possible; do not guess among genuinely ambiguous candidates. For modification requests, preserve design intent in this order: (1) existing model parameter, (2) sketch driving dimension/entity/constraint with cad_edit_sketch_dimension/cad_edit_sketch_entity/cad_edit_sketch_constraint, (3) typed feature definition with cad_edit_feature_definition, and only then (4) direct B-Rep editing. For broader Inventor-style edits, use cad_edit_parameter for parameter rename/delete; cad_edit_sketch for 2D sketch move/rotate/copy/rename/delete; cad_edit_sketch (3D actions) for Inventor-style 3D path sketch creation/editing; cad_edit_work_feature for work-plane/work-axis/work-point creation/redefinition/rename/delete; cad_edit_feature_definition before/after fields only when the user explicitly asks to reorder history; and cad_edit_solid for thread-on-existing-face, Split/Combine, Face Draft, Replace Face, Offset/Thicken-family edits, or multi-face Delete Face. Use cad_move_face for a selected planar face (it automatically attempts originating-feature parameter editing before direct B-Rep Move Face), cad_set_face_diameter for an explicitly selected cylindrical face, cad_delete_face for Inventor-style Delete Face with healing, cad_shell with the selected face as the opening, inventor_set_parameter/cad_edit_feature_definition/cad_edit_feature for known driving dimensions/features, inventor_edit_joint for full assembly joint definition/state/limits, inventor_transform_occurrence for occurrence pose/state/component replacement, and inventor_edit_constraint for existing mate/flush/insert/angle relationships. CURRENT_STATE includes sketch entity geometry, driving dimensions, and geometric constraints: use those exact sketch/entity/dimension/constraint names instead of reconstructing the part when the user's request is an edit. Use inventor_delete_occurrence only when the user explicitly asks to remove a placed component from the assembly; this preserves the source part/subassembly file while removing dependent relationships. Use inventor_delete_constraint/inventor_delete_joint for explicit relationship deletion. cad_delete_document_file is destructive and must only be used for an explicit request to delete the active saved document file; set confirm=true, never infer file deletion from words like remove/hide/suppress, and do not use it when the user only wants an occurrence removed from an assembly. Never create a replacement part merely to satisfy an edit request.
The response schema is exactly:
{{"calls":[{{"tool":"inventor_or_cad_tool_name","arguments":{{...}},"bind":"optional_runtime_binding_for_this_call"}}],"note":"short internal note"}}
Omit bind when the call result will not be referenced later.

TOOL_CATALOG:
{tool_catalog}

CURRENT_STATE:
{current_state}

USER_REQUEST:
{user_request}
"""

REPAIR_PREFIX = """You are repairing a failed CAD execution plan.
Return JSON only with the same schema: {{"calls":[{{"tool":"...","arguments":{{...}},"bind":"optional_runtime_binding_for_this_call"}}],"note":"..."}}.
Use only TOOL_CATALOG. Produce a corrected plan that satisfies the original request without repeating the exact failed operation when the error shows why it failed.
If the failure reports an unknown named reference, do not invent another name. Use the exact Available/list_interfaces name. Inventor base planes are XY Plane/XZ Plane/YZ Plane (inventor_create_sketch also accepts XY/XZ/YZ); base axes are X Axis/Y Axis/Z Axis and the origin point is Center Point.
Preserve current user geometry; the application rolled the failed plan back atomically before this repair.
If an assembly relationship is sick, re-evaluate the relative degrees of freedom required by ORIGINAL_REQUEST. Keep mate/flush/insert/angle for static relationships; when the request explicitly requires retained motion, select the Inventor-style inventor_add_joint type whose DOF exactly matches that requirement. Treat linear_position_mm/angular_position_deg separately from motion limits: a position can define a fixed offset or clocking on a constrained coordinate, while min/max limits belong only to a free coordinate (linear: slider/cylindrical; angular: rotational/cylindrical/planar). Never put range limits on a rigid joint. Never select a recovery path from component/product names, and do not invent travel or angle limits that were not requested or otherwise deterministically known.
Use the structured diagnostics embedded in an Assembly joint/constraint failure to repair the general relationship: fix invalid GeometryIntent/reference selection, remove only truly redundant/conflicting relationships, or choose the joint whose DOF matches ORIGINAL_REQUEST. Do not repeat the same sick relationship graph unchanged.
When referring to an occurrence placed earlier in the repaired plan, give its inventor_place_occurrence call a unique top-level bind and use `$bind.<name>.occurrence_name`. Do not invent Part:1-style names. Legacy `$result[N].occurrence_name` is allowed only when it points to the correct earlier inventor_place_occurrence call. Existing occurrences must use exact CURRENT_STATE names.
Never erase/reset the active document implicitly. If a different standalone component is requested while the current document contains geometry, create a new part first.

TOOL_CATALOG:
{tool_catalog}

CURRENT_STATE:
{current_state}

ORIGINAL_REQUEST:
{user_request}

FAILED_PLAN:
{failed_plan}

EXECUTION_ERROR:
{error}

RECOVERY_CONTEXT:
{recovery_context}
"""



CONTINUE_PREFIX = """You are continuing a CAD build after one tool call failed, while every earlier successful call is still present in CURRENT_STATE.
Return JSON only with the usual CAD plan schema: {{"calls":[{{"tool":"...","arguments":{{...}},"bind":"optional"}}],"note":"..."}}.
Use only TOOL_CATALOG.

Do NOT rebuild or repeat the already completed prefix. Continue from CURRENT_STATE.
Repair the failed operation using the concrete kernel error and the actual current CAD state, then complete every still-unfinished part of ORIGINAL_REQUEST.
Treat COMPLETED_PREFIX as facts already executed successfully. Treat FAILED_CALL as not executed. REMAINING_ORIGINAL_CALLS are hints only; rewrite or omit them when the current state/error proves a better path.
Never weaken or silently drop explicit dimensions, counts, relationships, edits, or preservation constraints from ORIGINAL_REQUEST merely to make the kernel succeed.
Never reset/delete/replace pre-existing user geometry unless ORIGINAL_REQUEST explicitly requires it.
Do not use $bind or $result references that belonged to COMPLETED_PREFIX; use exact occurrence/document/interface names visible in CURRENT_STATE. New bindings created inside this continuation plan are allowed.
For ambiguous face/edge targets, use exact CURRENT_STATE selection/topology information rather than guessing.

TOOL_CATALOG:
{tool_catalog}

ORIGINAL_REQUEST:
{user_request}

CURRENT_STATE:
{current_state}

COMPLETED_PREFIX:
{completed_prefix}

FAILED_CALL:
{failed_call}

KERNEL_ERROR:
{error}

REMAINING_ORIGINAL_CALLS:
{remaining_calls}
"""


def _versionish(path: Path) -> tuple[int, ...]:
    parts: list[int] = []
    for x in path.parent.name.replace("v", "").split("."):
        try:
            parts.append(int(x))
        except Exception:
            parts.append(-1)
    try:
        parts.append(int(path.stat().st_mtime))
    except Exception:
        parts.append(0)
    return tuple(parts)


def find_codex_exe() -> Path | None:
    explicit = os.environ.get("CODEX_CLI_PATH") or os.environ.get("CODEX_EXE")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    w = shutil.which("codex") or shutil.which("codex.exe")
    if w:
        return Path(w)
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            root = Path(local) / "OpenAI" / "Codex" / "bin"
            candidates: list[Path] = []
            if root.exists():
                candidates.extend(root.glob("*/codex.exe"))
                candidates.extend(root.glob("codex.exe"))
            candidates = [p for p in candidates if p.is_file()]
            if candidates:
                return max(candidates, key=_versionish)
    return None


def _strip_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    s = _strip_fence(text)
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    start = s.find("{")
    if start < 0:
        raise ValueError("Planner did not return JSON")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                obj = json.loads(s[start:i+1])
                if not isinstance(obj, dict):
                    raise ValueError("Planner JSON must be an object")
                return obj
    raise ValueError("Planner returned incomplete JSON")


# CADia resilient LLM routing

def _validate_planner_plan(plan: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate planner transport/schema facts only; CAD semantics remain in executor/verifier/recovery."""
    if not isinstance(plan, dict):
        raise ValueError("Planner result must be an object")
    calls = plan.get("calls")
    if not isinstance(calls, list) or not calls:
        raise ValueError("Planner result must contain at least one tool call")
    if len(calls) > 80:
        raise ValueError("Planner returned too many tool calls")
    allowed = {str(row.get("name")) for row in catalog if row.get("name")}
    for idx, call in enumerate(calls):
        if not isinstance(call, dict):
            raise ValueError(f"Planner call {idx} must be an object")
        tool = call.get("tool")
        args = call.get("arguments")
        if not isinstance(tool, str) or tool not in allowed:
            raise ValueError(f"Planner call {idx} uses an unknown tool: {tool!r}")
        if not isinstance(args, dict):
            raise ValueError(f"Planner call {idx} arguments must be an object")
        bind = call.get("bind")
        if bind is not None and (not isinstance(bind, str) or not bind.strip()):
            raise ValueError(f"Planner call {idx} bind must be a non-empty string")
    note = plan.get("note")
    if note is not None and not isinstance(note, str):
        raise ValueError("Planner note must be a string")
    return plan


def _legacy_direct_first_enabled() -> bool:
    value = os.environ.get("CADIA_PLANNER_ROUTING_MODE", "resilient_llm_first").strip().lower()
    return value in {"legacy", "legacy_direct_first", "direct_first"}


def _planner_cancelled(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(token in msg for token in ("cancel", "canceled", "cancelled", "사용자에 의해 취소"))


def _retry_planner_prompt(prompt: str, error: Exception, attempt: int) -> str:
    # Keep the same full request/current state/tool catalog. Only add structured feedback
    # about why the previous planner response could not be accepted.
    msg = str(error).replace("\x00", " ").strip()
    if len(msg) > 1200:
        msg = msg[-1200:]
    return (
        prompt
        + "\n\nPLANNER_RETRY_FEEDBACK:\n"
        + f"Attempt {attempt} could not be accepted: {msg}\n"
        + "Return a complete replacement plan for the ORIGINAL USER_REQUEST. Use only TOOL_CATALOG, valid JSON, known tool names, object arguments, and preserve every explicit requirement.\n"
    )


def _plan_llm_first_with_retry(agent: Any, user_prompt: str, current_state: dict[str, Any], on_event=None) -> dict[str, Any]:
    """LLM-first planning with one clean retry, then legacy deterministic fallback only as final availability fallback.

    Important: direct_plan never intercepts a successful LLM plan and is never used after
    CAD execution failure. Execution failures continue through the existing atomic rollback,
    deterministic recovery, and bounded AI repair loop in recovery.py.
    """
    if _legacy_direct_first_enabled():
        local = direct_plan(user_prompt, current_state)
        if local is not None:
            if on_event:
                on_event("progress", {"message": "Standard geometry plan completed", "percent": 20})
            return local

    base_prompt = PLANNER_PREFIX.format(
        tool_catalog=json.dumps(agent._catalog, ensure_ascii=False, separators=(",", ":")),
        current_state=json.dumps(current_state, ensure_ascii=False, separators=(",", ":")),
        user_request=user_prompt.strip(),
    )
    last_exc: Exception | None = None
    prompt = base_prompt
    for attempt in (1, 2):
        try:
            span = (5, 14) if attempt == 1 else (14, 20)
            planned = agent._run_planner(prompt, on_event=on_event, progress_span=span)
            return _validate_planner_plan(planned, agent._catalog)
        except Exception as exc:
            if _planner_cancelled(exc):
                raise
            last_exc = exc
            if attempt == 1:
                if on_event:
                    on_event("progress", {"message": "Planner response invalid; retrying with error feedback", "percent": 14})
                prompt = _retry_planner_prompt(base_prompt, exc, attempt)

    # Final availability fallback: use deterministic direct planning only after both
    # full-request planner attempts fail. It is NOT used as semantic recovery for a
    # CAD execution failure, so compound requests that reached a valid LLM plan cannot be
    # replaced by a partial primitive after execution fails.
    local = direct_plan(user_prompt, current_state)
    if local is not None:
        if on_event:
            on_event("progress", {"message": "AI planner unavailable; using deterministic compatibility fallback", "percent": 20})
        return local
    assert last_exc is not None
    raise last_exc

class CodexAgent:
    """Natural-language planner for the in-app CAD UI.

    Codex produces a structured tool plan without starting nested MCP servers.
    CADia validates and executes the plan in-process against the exact open document.
    """

    def __init__(
        self,
        target_id: str,
        project_root: Path,
        model: str = "gpt-5.6-sol",
        reasoning: str = "medium",
    ):
        self.target_id = str(target_id)
        self.project_root = Path(project_root).resolve()
        self.model = model
        self.reasoning = reasoning
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.workspace = Path.home() / ".standalonecad-mcp" / "agent-workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._catalog = self._make_catalog()

    @property
    def codex_exe(self) -> Path | None:
        return find_codex_exe()

    def reset(self) -> None:
        # Planning is deliberately stateless. The current CAD document is the source of truth.
        pass

    def cancel(self) -> None:
        with self._lock:
            p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    def _make_catalog(self) -> list[dict[str, Any]]:
        base = make_tools("inventor")
        inventor_extended = make_tools("inventor", assembly_extensions=True)
        base_inventory = {x['name'] for x in base}
        assembly_extensions = [x for x in inventor_extended if x['name'] not in base_inventory]
        native_all = make_tools("cad", extensions=True)
        base_names = {x['name'].replace('inventor_','cad_',1) for x in base}
        native_extensions = [x for x in native_all if x['name'] not in base_names]
        rows = base + assembly_extensions + native_extensions
        return [
            {
                "name": r["name"],
                "description": r.get("description", ""),
                "inputSchema": r.get("inputSchema", {}),
            }
            for r in rows
        ]

    def _require_codex(self) -> Path:
        exe = self.codex_exe
        if exe is None:
            raise FileNotFoundError("AI AI runner was not found. Sign in to the app and try again.")
        return exe

    def _exec_cmd(self, out_file: Path) -> list[str]:
        return [
            str(self._require_codex()),
            "exec",
            "--json",
            "--color",
            "never",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "-C",
            str(self.workspace),
            "--model",
            self.model,
            "--output-last-message",
            str(out_file),
            "-c",
            f"model_reasoning_effort={json.dumps(self.reasoning)}",
            "-c",
            'approval_policy="never"',
            "-",
        ]

    @staticmethod
    def _event_text(ev: dict[str, Any]) -> tuple[str | None, str | None]:
        typ = ev.get("type")
        if typ in ("turn.failed", "error"):
            return "error", ev.get("message") or str(ev.get("error") or ev)
        if typ == "turn.started":
            return "progress", "Interpreting request…"
        if typ == "turn.completed":
            return "progress", "Checking modeling plan…"
        return None, None

    def _run_planner(self, prompt: str, on_event: Callable[[str, Any], None] | None = None, progress_span: tuple[int, int] = (5, 20)) -> dict[str, Any]:
        fd, out_name = tempfile.mkstemp(prefix="standalonecad_plan_", suffix=".json")
        os.close(fd)
        out_file = Path(out_name)
        stderr_lines: list[str] = []
        try:
            env = os.environ.copy()
            env["STANDALONECAD_EMBEDDED_AGENT"] = "1"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            p = subprocess.Popen(
                self._exec_cmd(out_file),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=flags,
            )
            with self._lock:
                self.proc = p

            def _stderr():
                if p.stderr is None:
                    return
                for line in p.stderr:
                    stderr_lines.append(line.rstrip())
                    if len(stderr_lines) > 100:
                        del stderr_lines[:25]

            t = threading.Thread(target=_stderr, daemon=True)
            t.start()
            assert p.stdin is not None
            p.stdin.write(prompt)
            p.stdin.close()
            assert p.stdout is not None
            for line in p.stdout:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                kind, msg = self._event_text(ev)
                if kind and msg and on_event:
                    if kind == "progress":
                        start, end = progress_span
                        pct = start if ev.get("type") == "turn.started" else end
                        on_event(kind, {"message": msg, "percent": pct})
                    else:
                        on_event(kind, msg)
            rc = p.wait()
            t.join(timeout=1)
            with self._lock:
                self.proc = None
            try:
                final = out_file.read_text(encoding="utf-8").strip()
            except Exception:
                final = ""
            if rc != 0:
                tail = "\n".join(stderr_lines[-12:])
                raise RuntimeError((f"AI planner exited with code {rc}.\n{tail}").strip())
            return _extract_json_object(final)
        finally:
            try:
                out_file.unlink(missing_ok=True)
            except Exception:
                pass

    def plan(self, user_prompt: str, current_state: dict[str, Any], on_event=None) -> dict[str, Any]:
        """Plan free-form chat with full-request LLM reasoning first.

        Reliability order:
          1) full-request LLM plan
          2) one LLM retry with structured planner error feedback
          3) deterministic direct_plan only if both planner attempts fail

        CAD execution failures do not trigger direct_plan here; recovery.py
        handles them with atomic rollback, deterministic recovery, and bounded AI repairs.
        """
        return _plan_llm_first_with_retry(self, user_prompt, current_state, on_event=on_event)


    def continue_after_failure(
        self,
        user_prompt: str,
        current_state: dict[str, Any],
        completed_prefix: list[dict[str, Any]],
        failed_call: dict[str, Any],
        remaining_calls: list[dict[str, Any]],
        error: str,
        on_event=None,
        progress_span: tuple[int, int] = (72, 80),
    ) -> dict[str, Any]:
        """Plan only the remaining work from the real post-prefix CAD state."""
        prompt = CONTINUE_PREFIX.format(
            tool_catalog=json.dumps(self._catalog, ensure_ascii=False, separators=(",", ":")),
            user_request=user_prompt.strip(),
            current_state=json.dumps(current_state, ensure_ascii=False, separators=(",", ":")),
            completed_prefix=json.dumps(completed_prefix or [], ensure_ascii=False, separators=(",", ":")),
            failed_call=json.dumps(failed_call or {}, ensure_ascii=False, separators=(",", ":")),
            error=str(error),
            remaining_calls=json.dumps(remaining_calls or [], ensure_ascii=False, separators=(",", ":")),
        )
        planned = self._run_planner(prompt, on_event=on_event, progress_span=progress_span)
        return _validate_planner_plan(planned, self._catalog)

    def repair(self, user_prompt: str, current_state: dict[str, Any], failed_plan: dict[str, Any], error: str, on_event=None, recovery_context: dict[str, Any] | None = None, progress_span: tuple[int, int] = (72, 78)) -> dict[str, Any]:
        prompt = REPAIR_PREFIX.format(
            tool_catalog=json.dumps(self._catalog, ensure_ascii=False, separators=(",", ":")),
            current_state=json.dumps(current_state, ensure_ascii=False, separators=(",", ":")),
            user_request=user_prompt.strip(),
            failed_plan=json.dumps(failed_plan, ensure_ascii=False, separators=(",", ":")),
            error=str(error),
            recovery_context=json.dumps(recovery_context or {}, ensure_ascii=False, separators=(",", ":")),
        )
        return self._run_planner(prompt, on_event=on_event, progress_span=progress_span)
