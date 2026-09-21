from __future__ import annotations

import copy
import re
from typing import Any, Callable

from standalonecad.planning import PlanResult
from standalonecad.core.inventor_origin import canonical_origin_name, canonical_plane_name


# Failure-only recovery.  Nothing in this module is consulted after a successful
# initial execution.  This is deliberate: the already-working modeling path
# remains the source of truth, while only explicit failures enter this module.

_FAILURE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("runtime_binding", (
        "runtime result does not contain occurrence_name", "runtime binding does not contain occurrence_name",
        "runtime result reference", "runtime binding", "does not produce occurrence_name",
    )),
    ("assembly_relationship", (
        "assembly constraint health", "assembly joint", "joint health", "degrees of freedom",
        "occurrence relationship", "constraint health is sick",
    )),
    ("topology_reference", (
        "reference not found", "reference could not be rebound", "ambiguous face reference",
        "ambiguous edge reference", "face selector matched", "edge reference", "face reference",
        "vertex reference", "refine/reselect",
    )),
    ("boolean_intersection", (
        "invalid intersection", "empty boolean", "does not intersect", "boolean", "bopalgo",
        "brep_algo", "command not done",
    )),
    ("sketch", (
        "sketch", "not closed", "wire is not closed", "profile", "constraint",
    )),
    ("fillet_chamfer", (
        "fillet", "chamfer", "radius", "blend",
    )),
    ("parameter", (
        "invalid argument", "expected number", "expected integer", "expected boolean",
        "required", "minimum is", "maximum is", "mutually exclusive", "parameter",
    )),
    ("invalid_geometry", (
        "invalid b-rep", "unreadable geometry", "non-finite geometry", "invalid bounding box",
        "produced no model body", "without changing the current document",
    )),
)


def classify_failure(error: str | None) -> str:
    text = (error or "").lower()
    for category, needles in _FAILURE_PATTERNS:
        if any(n in text for n in needles):
            return category
    return "unknown"


def _calls_from_plan(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return []
    calls = plan.get("calls")
    return copy.deepcopy(calls) if isinstance(calls, list) else []


def _failed_step(result: PlanResult) -> dict[str, Any]:
    calls = result.calls or []
    completed = len(result.results or [])
    failed_call = calls[completed] if completed < len(calls) else None
    return {
        "completed_call_count_before_rollback": completed,
        "failed_call_index": completed if failed_call is not None else None,
        "failed_call": copy.deepcopy(failed_call),
        "completed_calls": copy.deepcopy(calls[:completed]),
    }


def _safe_execute(executor, plan: dict[str, Any], on_event=None, progress_span=(25, 90)) -> PlanResult:
    """Convert planner/schema exceptions into the same failure object as kernel failures.

    PlanExecutor already atomically rolls back failures that occur after execution starts.
    A validation failure happens before any CAD mutation, so no rollback is required.
    """
    try:
        return executor.execute(plan, on_event=on_event, progress_span=progress_span)
    except Exception as exc:
        return PlanResult(
            False,
            "",
            _calls_from_plan(plan),
            [],
            str(exc),
            int(getattr(executor.engine, "revision", 0)),
            int(getattr(executor.engine, "revision", 0)),
        )


def _schema_branches(schema: dict[str, Any]) -> list[dict[str, Any]]:
    rows = schema.get("anyOf") if isinstance(schema, dict) else None
    return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else [schema]


def _coerce_scalar(schema: dict[str, Any], value: Any) -> tuple[Any, bool]:
    """Lossless planner-format repair only; never invent or alter a design value."""
    branches = _schema_branches(schema)
    if value is None:
        return value, False
    if isinstance(value, str):
        raw = value.strip()
        if raw.lower() == "null" and any(x.get("type") == "null" for x in branches):
            return None, True
        for branch in branches:
            typ = branch.get("type")
            try:
                if typ == "number" and re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", raw):
                    return float(raw), True
                if typ == "integer" and re.fullmatch(r"[-+]?\d+", raw):
                    return int(raw), True
                if typ == "boolean" and raw.lower() in {"true", "false"}:
                    return raw.lower() == "true", True
            except Exception:
                pass
    return value, False


def _coerce_to_schema(schema: dict[str, Any], value: Any) -> tuple[Any, bool]:
    value2, changed = _coerce_scalar(schema, value)
    if changed:
        value = value2
    # Pick a structural branch that matches the current value.
    branches = _schema_branches(schema)
    branch = next((x for x in branches if x.get("type") == "object" and isinstance(value, dict)), None)
    if branch is None:
        branch = next((x for x in branches if x.get("type") == "array" and isinstance(value, list)), None)
    if branch is None:
        return value, changed
    if branch.get("type") == "object":
        props = branch.get("properties") or {}
        out = copy.deepcopy(value)
        for key, val in list(out.items()):
            child = props.get(key)
            if isinstance(child, dict):
                new_val, child_changed = _coerce_to_schema(child, val)
                if child_changed:
                    out[key] = new_val
                    changed = True
        return out, changed
    if branch.get("type") == "array":
        item_schema = branch.get("items") or {}
        out = []
        for val in value:
            new_val, child_changed = _coerce_to_schema(item_schema, val)
            out.append(new_val)
            changed = changed or child_changed
        return out, changed
    return value, changed


def _coerce_plan_types(executor, plan: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    out = copy.deepcopy(plan)
    changed = False
    calls = out.get("calls")
    if not isinstance(calls, list):
        return out, False
    for call in calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("tool") or "")
        schema = getattr(executor, "schemas", {}).get(name)
        args = call.get("arguments")
        if not isinstance(schema, dict) or not isinstance(args, dict):
            continue
        new_args, child_changed = _coerce_to_schema(schema, args)
        if child_changed:
            call["arguments"] = new_args
            changed = True
    return out, changed


_FACE_ALIASES: dict[str, dict[str, Any]] = {
    "top_face": {"kind": "planar", "normal": "+Z", "extreme": "max"},
    "max_z_face": {"kind": "planar", "normal": "+Z", "extreme": "max"},
    "bottom_face": {"kind": "planar", "normal": "-Z", "extreme": "max"},
    "min_z_face": {"kind": "planar", "normal": "-Z", "extreme": "max"},
    "max_x_face": {"kind": "planar", "normal": "+X", "extreme": "max"},
    "min_x_face": {"kind": "planar", "normal": "-X", "extreme": "max"},
    "max_y_face": {"kind": "planar", "normal": "+Y", "extreme": "max"},
    "min_y_face": {"kind": "planar", "normal": "-Y", "extreme": "max"},
}


def _mean_point(points: Any) -> list[float] | None:
    if not isinstance(points, list) or not points:
        return None
    rows: list[list[float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 3:
            continue
        try:
            rows.append([float(point[0]), float(point[1]), float(point[2])])
        except Exception:
            continue
    if not rows:
        return None
    n = float(len(rows))
    return [sum(p[i] for p in rows) / n for i in range(3)]


def deterministic_recovery_plan(
    executor,
    failed_plan: dict[str, Any],
    result: PlanResult,
    current_state: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Return only intent-preserving, deterministic corrections.

    No dimensions, counts, feature sizes, or requested operations are changed here.
    If a correction would require guessing design intent, this function returns None and
    the failure goes to the constrained AI repair loop instead.
    """
    candidate, changed = _coerce_plan_types(executor, failed_plan)
    reasons: list[str] = ["planner scalar type normalization"] if changed else []

    calls = candidate.get("calls") if isinstance(candidate, dict) else None
    if not isinstance(calls, list) or not calls:
        return (candidate if changed else None), reasons

    failed_idx = len(result.results or [])
    if failed_idx >= len(calls):
        failed_idx = max(0, len(calls) - 1)
    call = calls[failed_idx]
    if not isinstance(call, dict):
        return (candidate if changed else None), reasons
    name = str(call.get("tool") or "")
    args = call.get("arguments")
    if not isinstance(args, dict):
        return (candidate if changed else None), reasons
    error = (result.error or "").lower()

    # Inventor/ipt-mcp named-reference recovery. Public interfaces remain canonical;
    # this failure-only rewrite corrects planner spelling variants without guessing geometry.
    if "unknown reference" in error or "unknown sketch plane reference" in error:
        ref_keys_by_tool = {
            "inventor_create_sketch": ("plane",),
            "inventor_add_constraint": ("a_ref", "b_ref"),
            "inventor_edit_constraint": ("a_ref", "b_ref"),
            "inventor_create_work_plane": ("refs",),
            "inventor_create_work_axis": ("refs",),
        }
        keys = ref_keys_by_tool.get(name, ())
        for key in keys:
            if key not in args:
                continue
            val = args.get(key)
            if isinstance(val, str):
                fixed = canonical_plane_name(val, allow_compat=True) if key == "plane" else canonical_origin_name(val, allow_compat=True)
                if fixed != val:
                    args[key] = fixed; changed = True
                    reasons.append(f"normalize Inventor origin reference {val!r} -> {fixed!r}")
            elif isinstance(val, list):
                fixed_list = [canonical_origin_name(x, allow_compat=True) if isinstance(x, str) else x for x in val]
                if fixed_list != val:
                    args[key] = fixed_list; changed = True
                    reasons.append(f"normalize Inventor origin references in {key}")

    # If the user explicitly selected an edge, a failed fillet/chamfer reference can be
    # rebound to that exact current selection without guessing a different edge.
    selection = (current_state or {}).get("selection") or {}
    selected_edge = selection.get("edge_ref") if selection.get("type") == "edge" else None
    if name in {"inventor_fillet", "inventor_chamfer"} and selected_edge and (
        "edge reference" in error or "reference not found" in error or "refine/reselect" in error
    ):
        if args.get("edgeIds") != [selected_edge]:
            args["edgeIds"] = [selected_edge]
            changed = True
            reasons.append("rebind failed edge reference to explicit user selection")

    # Assembly-joint limits only apply to coordinates that remain free.  A planner can
    # legitimately use linear/angular *position* to define a fixed relative pose, but a
    # min/max range on a constrained coordinate is redundant and can make the joint
    # definition invalid.  Removing only those redundant limits preserves the requested
    # kinematics and is product-agnostic.
    if name == "inventor_add_joint" and ("limits are not applicable" in error or "does not support" in error):
        typ = str(args.get("type") or "").strip().lower()
        if typ == "revolute":
            typ = "rotational"
        changed_fields = []
        if typ not in {"slider", "cylindrical"}:
            for key in ("linear_start_mm", "linear_end_mm"):
                if args.get(key) is not None:
                    args[key] = None
                    changed_fields.append(key)
        if typ not in {"rotational", "cylindrical", "planar"}:
            for key in ("angular_start_deg", "angular_end_deg"):
                if args.get(key) is not None:
                    args[key] = None
                    changed_fields.append(key)
        if changed_fields:
            changed = True
            reasons.append("remove redundant joint limits from constrained coordinates: " + ", ".join(changed_fields))

    if name == "inventor_hole":
        face = args.get("face")
        # Planner occasionally emits a semantic face name even though the ipt-mcp hole
        # schema expects a deterministic selector.  Translate only known exact aliases.
        if isinstance(face, str) and face in _FACE_ALIASES:
            args["face"] = copy.deepcopy(_FACE_ALIASES[face])
            face = args["face"]
            changed = True
            reasons.append("translate known semantic face alias to deterministic selector")

        # When a deterministic selector is still ambiguous, the already-requested hole
        # coordinates supply a non-invented near point.  This is exactly what near_mm is
        # for and does not alter hole size, count, or position.
        if "face selector matched" in error and isinstance(face, dict) and not face.get("near_mm"):
            near = _mean_point(args.get("points_mm"))
            if near is not None:
                face["near_mm"] = near
                changed = True
                reasons.append("disambiguate hole face using requested hole coordinates")

        # Resolve only logically redundant through/depth fields.  Never invent a depth.
        if args.get("through") is True and args.get("depth_mm") is not None and "mutually exclusive" in error:
            args["depth_mm"] = None
            changed = True
            reasons.append("remove redundant depth from through-hole request")
        elif "through" not in args and args.get("depth_mm") is not None:
            args["through"] = False
            changed = True
            reasons.append("derive blind-hole flag from explicitly supplied depth")

    return (candidate if changed else None), reasons


def _progress(on_event, message: str, percent: int) -> None:
    if on_event:
        on_event("progress", {"message": message, "percent": percent})


def execute_with_failure_recovery(
    *,
    executor,
    agent,
    user_prompt: str,
    state_provider: Callable[[], dict[str, Any]],
    plan: dict[str, Any],
    on_event=None,
    initial_progress_span: tuple[int, int] = (25, 70),
    max_ai_repairs: int = 2,
) -> PlanResult:
    """Execute the existing modeling path unchanged first; enter recovery only after an explicit failure."""
    initial = _safe_execute(executor, plan, on_event=on_event, progress_span=initial_progress_span)
    if initial.ok:
        return initial

    original_plan = copy.deepcopy(plan)
    current_plan = copy.deepcopy(plan)
    current_result = initial
    history: list[dict[str, Any]] = []

    category = classify_failure(current_result.error)
    history.append({"stage": "initial", "class": category, "error": current_result.error, **_failed_step(current_result)})
    _progress(on_event, f"Modeling interruption detected · checking recovery for {category}…", 72)

    # One conservative deterministic attempt.  If no safe rewrite exists, skip it.
    state = state_provider()
    deterministic, reasons = deterministic_recovery_plan(executor, current_plan, current_result, state)
    if deterministic is not None and deterministic != current_plan:
        _progress(on_event, "Retrying a safe automatic recovery path…", 75)
        det_result = _safe_execute(executor, deterministic, on_event=on_event, progress_span=(76, 82))
        if det_result.ok:
            return det_result
        history.append({
            "stage": "deterministic",
            "class": classify_failure(det_result.error),
            "error": det_result.error,
            "reasons": reasons,
            **_failed_step(det_result),
        })
        current_plan = deterministic
        current_result = det_result

    # Bounded observe -> repair -> execute loop, only after the original path failed.
    # Every failed execute is already atomically rolled back by PlanExecutor.
    for attempt in range(1, max(0, int(max_ai_repairs)) + 1):
        pct0 = 82 + (attempt - 1) * 7
        category = classify_failure(current_result.error)
        _progress(on_event, f"Computing recovery plan {attempt}/{max_ai_repairs} · {category}", min(94, pct0))
        context = {
            "failure_class": category,
            "attempt": attempt,
            "max_attempts": max_ai_repairs,
            "original_plan": original_plan,
            "failure_step": _failed_step(current_result),
            "recovery_history": history,
            "rules": [
                "The failed plan was rolled back atomically; return a complete replacement plan from CURRENT_STATE.",
                "Do not change requested dimensions/counts merely to make geometry succeed.",
                "Do not erase or replace pre-existing user geometry unless the original request explicitly asked for it.",
                "Prefer deterministic face/edge selectors and native standard-part generators when they exactly match the request.",
                "Do not repeat an operation with identical arguments when the recorded error already proves that exact operation fails.",
                "Only when the original request explicitly requires retained relative motion, prefer inventor_add_joint with the matching DOF and any explicitly known travel/angle limits over static constraints that would remove that motion.",
                "For occurrences created inside the replacement plan, use unique top-level bind fields on inventor_place_occurrence calls and reference the actual returned names with $bind.<name>.occurrence_name; do not rely on fragile call-number guesses.",
            ],
        }
        repaired = agent.repair(
            user_prompt,
            state_provider(),
            current_plan,
            current_result.error or "unknown CAD error",
            on_event=on_event,
            recovery_context=context,
            progress_span=(min(94, pct0), min(96, pct0 + 3)),
        )
        if repaired == current_plan:
            current_result = PlanResult(
                False, "", _calls_from_plan(repaired), [],
                "Recovery planner returned the identical failed plan",
                current_result.revision_before, current_result.revision_after,
            )
            history.append({"stage": f"ai_repair_{attempt}", "class": "repeated_plan", "error": current_result.error})
            continue
        repaired_result = _safe_execute(
            executor,
            repaired,
            on_event=on_event,
            progress_span=(min(96, pct0 + 3), min(98, pct0 + 7)),
        )
        if repaired_result.ok:
            return repaired_result
        history.append({
            "stage": f"ai_repair_{attempt}",
            "class": classify_failure(repaired_result.error),
            "error": repaired_result.error,
            **_failed_step(repaired_result),
        })
        current_plan = repaired
        current_result = repaired_result

    # Preserve the final concrete CAD error, while making it clear recovery was exhausted.
    tail = current_result.error or initial.error or "unknown CAD error"
    current_result.error = f"{tail} (failure-only recovery exhausted)"
    return current_result
