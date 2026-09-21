from __future__ import annotations

from dataclasses import dataclass, asdict
import copy
import math
from typing import Any

import numpy as np

from .assembly import (
    Occurrence,
    _unit,
    euler_matrix_xyz,
    matrix_to_euler_xyz,
    find_interface,
    transformed_frame,
    constraint_vector,
    constraint_residual,
    default_interfaces,
)
from .topology import select_face, face_frame, match_edge_ids


JOINT_TYPES = {"rigid", "rotational", "slider", "cylindrical", "planar", "ball"}


def canonical_joint_type(value: str) -> str:
    v = str(value).strip().lower()
    # Legacy files created by the short-lived experimental branch remain loadable.
    if v == "revolute":
        v = "rotational"
    return v


@dataclass
class AssemblyJoint:
    """General kinematic relationship for the standalone assembly backend.

    The public ipt-mcp 58-tool contract is not changed by this class.  It is used only
    by the optional Inventor-style assembly-joint extension.  ``a_intent`` and
    ``b_intent`` are GeometryIntent-like descriptors; old files using only named refs
    remain fully supported.
    """

    name: str
    type: str
    a_occurrence: str | None
    a_ref: str | None
    b_occurrence: str | None
    b_ref: str | None
    linear_position_mm: float | None = None
    linear_start_mm: float | None = None
    linear_end_mm: float | None = None
    angular_position_deg: float | None = None
    angular_start_deg: float | None = None
    angular_end_deg: float | None = None
    health: str = "up_to_date"
    suppressed: bool = False
    locked: bool = False
    a_intent: dict[str, Any] | None = None
    b_intent: dict[str, Any] | None = None
    flip_origin_direction: bool = False
    flip_alignment_direction: bool = False


def _local_secondary(direction) -> np.ndarray:
    """Stable perpendicular used when a geometry intent has no explicit alignment."""
    z = _unit(direction)
    seeds = (
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    )
    seed = min(seeds, key=lambda s: abs(float(np.dot(s, z))))
    x = seed - z * float(np.dot(seed, z))
    return _unit(x)


def _orthonormal_x(z, x_hint=None) -> np.ndarray:
    z = _unit(z)
    if x_hint is not None:
        x = np.asarray(x_hint, float)
        x = x - z * float(np.dot(x, z))
        if np.linalg.norm(x) > 1e-10:
            return _unit(x)
    return _local_secondary(z)


def _transform_local_frame(origin, direction, x_dir, occ: Occurrence | None):
    p = np.asarray(origin, float)
    z = None if direction is None else _unit(direction)
    x = None if z is None else _orthonormal_x(z, x_dir)
    if occ is None:
        return p, z, x
    R = euler_matrix_xyz(occ.rotation_deg_xyz)
    t = np.asarray(occ.position_mm, float)
    p = R @ p + t
    if z is not None:
        z = _unit(R @ z)
        x = _unit(R @ x)
    return p, z, x


def _edge_local_frame(shape, edge_id: str, point: str = "mid"):
    edge = match_edge_ids(shape, [edge_id])[0]
    geom = str(edge.geomType())
    verts = edge.Vertices()
    point = str(point or "mid").lower()
    if geom == "LINE" and len(verts) >= 2:
        a = verts[0].Center(); b = verts[-1].Center()
        va = np.array([a.x, a.y, a.z], float); vb = np.array([b.x, b.y, b.z], float)
        if point == "start": p = va
        elif point == "end": p = vb
        else: p = (va + vb) * 0.5
        z = _unit(vb - va)
        return p, z, _local_secondary(z)
    if geom == "CIRCLE":
        circ = edge._geomAdaptor().Circle(); c = circ.Location(); ax = circ.Axis().Direction()
        p = np.array([c.X(), c.Y(), c.Z()], float)
        z = _unit([ax.X(), ax.Y(), ax.Z()])
        return p, z, _local_secondary(z)
    raise ValueError(f"GeometryIntent edge must be LINE or CIRCLE; got {geom}")


def _intent_local_frame(occ: Occurrence | None, interfaces: dict[str, Any], ref: str | None, intent: dict[str, Any] | None):
    """Resolve an Inventor-style geometry intent to a local LCS.

    Supported intent sources are deliberately general rather than product-specific:
    named work/origin/iMate refs, planar/cylindrical face selectors, and linear/circular
    persistent edge ids.  This mirrors the geometry categories accepted by Inventor's
    CreateGeometryIntent while staying on the standalone OCCT topology model.
    """
    intent = copy.deepcopy(intent) if isinstance(intent, dict) else None
    if intent is None:
        if not ref:
            raise ValueError("Joint side requires ref or intent")
        item = find_interface(interfaces, ref)
        origin = item.get("origin", [0.0, 0.0, 0.0])
        direction = item.get("direction")
        x_dir = item.get("x_dir")
        return np.asarray(origin, float), None if direction is None else _unit(direction), None if direction is None else _orthonormal_x(direction, x_dir), {"kind":"ref","ref":ref}

    kind = str(intent.get("kind") or "ref").lower()
    if kind == "ref":
        r = intent.get("ref") or ref
        if not r:
            raise ValueError("GeometryIntent kind=ref requires ref")
        item = find_interface(interfaces, r)
        direction = item.get("direction")
        return np.asarray(item.get("origin", [0,0,0]), float), None if direction is None else _unit(direction), None if direction is None else _orthonormal_x(direction, item.get("x_dir")), {"kind":"ref","ref":r}

    if occ is None or occ.shape is None:
        raise ValueError(f"GeometryIntent kind={kind} requires an occurrence with geometry")

    if kind == "face":
        selector = intent.get("selector")
        if not isinstance(selector, dict):
            raise ValueError("GeometryIntent kind=face requires selector")
        face = select_face(occ.shape, selector)
        fr = face_frame(face)
        z = _unit(fr["direction"])
        p = np.asarray(fr["origin"], float)
        return p, z, _local_secondary(z), {"kind":"face","selector":selector,"frame_kind":fr.get("kind")}

    if kind == "edge":
        edge_id = intent.get("edge_id")
        if not edge_id:
            raise ValueError("GeometryIntent kind=edge requires edge_id")
        p, z, x = _edge_local_frame(occ.shape, str(edge_id), str(intent.get("point") or "mid"))
        return p, z, x, {"kind":"edge","edge_id":edge_id,"point":str(intent.get("point") or "mid")}

    raise ValueError("GeometryIntent kind must be ref|face|edge")


def _side(occurrences: dict[str, Occurrence], assembly_interfaces: dict, name: str | None, ref: str | None, intent: dict[str, Any] | None):
    if name:
        if name not in occurrences:
            raise ValueError(f"Occurrence not found: {name}")
        occ = occurrences[name]
        local_interfaces = occ.interfaces
    else:
        occ = None
        local_interfaces = assembly_interfaces
    p, z, x, resolved = _intent_local_frame(occ, local_interfaces, ref, intent)
    p, z, x = _transform_local_frame(p, z, x, occ)
    return occ, p, z, x, resolved


def _signed_angle_deg(x_from, x_to, axis) -> float:
    if x_from is None or x_to is None or axis is None:
        return 0.0
    z = _unit(axis)
    a = np.asarray(x_from, float) - z * float(np.dot(x_from, z))
    b = np.asarray(x_to, float) - z * float(np.dot(x_to, z))
    if np.linalg.norm(a) < 1e-10 or np.linalg.norm(b) < 1e-10:
        return 0.0
    a = _unit(a); b = _unit(b)
    s = float(np.dot(np.cross(a, b), z))
    c = float(np.clip(np.dot(a, b), -1.0, 1.0))
    return math.degrees(math.atan2(s, c))


def _clamp(value: float, lo: float | None, hi: float | None) -> float:
    v = float(value)
    if lo is not None: v = max(v, float(lo))
    if hi is not None: v = min(v, float(hi))
    return v


def validate_joint_definition(j: AssemblyJoint) -> None:
    typ = canonical_joint_type(j.type)
    j.type = typ
    if typ not in JOINT_TYPES:
        raise ValueError("joint type must be rigid|rotational|slider|cylindrical|planar|ball")
    if not (j.a_ref or j.a_intent):
        raise ValueError("joint side A requires ref or GeometryIntent")
    if not (j.b_ref or j.b_intent):
        raise ValueError("joint side B requires ref or GeometryIntent")
    if j.linear_start_mm is not None and j.linear_end_mm is not None and float(j.linear_start_mm) > float(j.linear_end_mm):
        raise ValueError("linear_start_mm must be <= linear_end_mm")
    if j.angular_start_deg is not None and j.angular_end_deg is not None and float(j.angular_start_deg) > float(j.angular_end_deg):
        raise ValueError("angular_start_deg must be <= angular_end_deg")
    # Position and travel limits are different concepts.  A zero-DOF relationship can
    # still have a non-zero fixed relative pose (for example a rigidly mounted handle at
    # a chosen clocking angle).  Travel limits, however, only make sense on coordinates
    # that remain free for that joint type.
    if typ == "ball" and any(v is not None for v in (j.linear_position_mm, j.angular_position_deg)):
        raise ValueError("ball joint does not use scalar linear/angular position")
    if typ not in {"slider", "cylindrical"} and any(v is not None for v in (j.linear_start_mm, j.linear_end_mm)):
        raise ValueError(f"{typ} joint has no free axial translation; linear limits are not applicable")
    if typ not in {"rotational", "cylindrical", "planar"} and any(v is not None for v in (j.angular_start_deg, j.angular_end_deg)):
        raise ValueError(f"{typ} joint has no free axial rotation; angular limits are not applicable")


def _frames(occurrences: dict[str, Occurrence], j: AssemblyJoint, assembly_interfaces: dict | None = None):
    interfaces = assembly_interfaces or default_interfaces()
    oa, pa, za, xa, ra = _side(occurrences, interfaces, j.a_occurrence, j.a_ref, j.a_intent)
    ob, pb, zb, xb, rb = _side(occurrences, interfaces, j.b_occurrence, j.b_ref, j.b_intent)
    if j.flip_origin_direction:
        if zb is not None: zb = -zb
    if j.flip_alignment_direction:
        if xb is not None: xb = -xb
    return oa, pa, za, xa, ra, ob, pb, zb, xb, rb


def joint_state(occurrences: dict[str, Occurrence], j: AssemblyJoint, assembly_interfaces: dict | None = None) -> dict[str, Any]:
    _, pa, za, xa, _, _, pb, zb, xb, _ = _frames(occurrences, j, assembly_interfaces)
    linear = None; angular = None
    if za is not None:
        linear = float(np.dot(pb - pa, _unit(za)))
    if za is not None and xa is not None and xb is not None:
        angular = _signed_angle_deg(xa, xb, za)
    axis_alignment_deg = None
    if za is not None and zb is not None:
        axis_alignment_deg = math.degrees(math.acos(float(np.clip(np.dot(_unit(za), _unit(zb)), -1.0, 1.0))))
    return {
        "linear_position_mm": linear,
        "angular_position_deg": angular,
        "axis_alignment_deg": axis_alignment_deg,
    }


def joint_vector(occurrences: dict[str, Occurrence], j: AssemblyJoint, assembly_interfaces: dict | None = None) -> np.ndarray:
    """Pure kinematic equality residual; commanded positions/limits do not remove DOF."""
    _, pa, za, xa, _, _, pb, zb, xb, _ = _frames(occurrences, j, assembly_interfaces)
    typ = canonical_joint_type(j.type)
    v = np.asarray(pb - pa, float)
    if typ == "ball":
        return v
    if za is None or zb is None:
        raise ValueError(f"{typ} joint requires directional GeometryIntents")
    ua = _unit(za); ub = _unit(zb)
    orient = np.cross(ub, ua)
    perp = v - ua * float(np.dot(v, ua))
    axial_value = float(np.dot(v, ua))
    roll_value = math.radians(_signed_angle_deg(xa, xb, ua))

    # For coordinates that the joint constrains, position fields define the fixed
    # relative pose.  For coordinates that the joint intentionally leaves free, the
    # same position fields are handled below by joint_solver_vector as optional drives.
    fixed_linear_target = float(j.linear_position_mm or 0.0)
    fixed_angular_target = math.radians(float(j.angular_position_deg or 0.0))
    axial = np.array([axial_value - fixed_linear_target], float)
    roll = np.array([roll_value - fixed_angular_target], float)
    if typ == "rigid": return np.concatenate([orient, perp, axial, roll])
    if typ == "rotational": return np.concatenate([orient, perp, axial])
    if typ == "slider": return np.concatenate([orient, perp, roll])
    if typ == "cylindrical": return np.concatenate([orient, perp])
    if typ == "planar": return np.concatenate([orient, axial])
    raise ValueError("Unsupported joint type")


def joint_solver_vector(occurrences: dict[str, Occurrence], j: AssemblyJoint, assembly_interfaces: dict | None = None) -> np.ndarray:
    """Residual used by the global solver, including drive positions and unilateral limits."""
    base = list(np.asarray(joint_vector(occurrences, j, assembly_interfaces), float))
    st = joint_state(occurrences, j, assembly_interfaces)
    lp = st.get("linear_position_mm"); ap = st.get("angular_position_deg")
    typ = canonical_joint_type(j.type)
    if lp is not None and typ in {"slider", "cylindrical"}:
        if j.linear_position_mm is not None:
            base.append(float(lp) - float(_clamp(j.linear_position_mm, j.linear_start_mm, j.linear_end_mm)))
        if j.linear_start_mm is not None:
            base.append(min(0.0, float(lp) - float(j.linear_start_mm)))
        if j.linear_end_mm is not None:
            base.append(max(0.0, float(lp) - float(j.linear_end_mm)))
    if ap is not None and typ in {"rotational", "cylindrical", "planar"}:
        if j.angular_position_deg is not None:
            base.append(math.radians(float(ap) - float(_clamp(j.angular_position_deg, j.angular_start_deg, j.angular_end_deg))))
        if j.angular_start_deg is not None:
            base.append(math.radians(min(0.0, float(ap) - float(j.angular_start_deg))))
        if j.angular_end_deg is not None:
            base.append(math.radians(max(0.0, float(ap) - float(j.angular_end_deg))))
    return np.asarray(base, float)


def _limits_healthy(occurrences: dict[str, Occurrence], j: AssemblyJoint, assembly_interfaces: dict | None = None, tol=1e-5) -> bool:
    st = joint_state(occurrences, j, assembly_interfaces)
    lp = st.get("linear_position_mm"); ap = st.get("angular_position_deg")
    if lp is not None:
        if j.linear_start_mm is not None and lp < float(j.linear_start_mm) - tol: return False
        if j.linear_end_mm is not None and lp > float(j.linear_end_mm) + tol: return False
        if j.linear_position_mm is not None and abs(lp - _clamp(j.linear_position_mm, j.linear_start_mm, j.linear_end_mm)) > 1e-4: return False
    if ap is not None:
        if j.angular_start_deg is not None and ap < float(j.angular_start_deg) - 1e-4: return False
        if j.angular_end_deg is not None and ap > float(j.angular_end_deg) + 1e-4: return False
        if j.angular_position_deg is not None and abs(ap - _clamp(j.angular_position_deg, j.angular_start_deg, j.angular_end_deg)) > 1e-3: return False
    return True


def joint_residual(occurrences: dict[str, Occurrence], j: AssemblyJoint, assembly_interfaces: dict | None = None) -> float:
    v = joint_solver_vector(occurrences, j, assembly_interfaces)
    return float(np.linalg.norm(v))


def joint_diagnostics(occurrences: dict[str, Occurrence], j: AssemblyJoint, assembly_interfaces: dict | None = None) -> dict[str, Any]:
    interfaces = assembly_interfaces or default_interfaces()
    out: dict[str, Any] = {"name":j.name,"type":canonical_joint_type(j.type),"health":j.health}
    try:
        _, _, _, _, ra, _, _, _, _, rb = _frames(occurrences, j, interfaces)
        out["origin_one"] = ra; out["origin_two"] = rb
        out["state"] = joint_state(occurrences, j, interfaces)
        out["kinematic_residual"] = float(np.linalg.norm(joint_vector(occurrences, j, interfaces)))
        out["solve_residual"] = float(np.linalg.norm(joint_solver_vector(occurrences, j, interfaces)))
        out["limits_healthy"] = bool(_limits_healthy(occurrences, j, interfaces))
    except Exception as exc:
        out["error"] = str(exc)
        out["kinematic_residual"] = math.inf
        out["solve_residual"] = math.inf
        out["limits_healthy"] = False
    return out


def _rotvec_matrix(v) -> np.ndarray:
    v = np.asarray(v, float); a = float(np.linalg.norm(v))
    if a < 1e-14: return np.eye(3)
    x, y, z = v / a; c = math.cos(a); s = math.sin(a); C = 1.0 - c
    return np.array([
        [x*x*C+c, x*y*C-z*s, x*z*C+y*s],
        [y*x*C+z*s, y*y*C+c, y*z*C-x*s],
        [z*x*C-y*s, z*y*C+x*s, z*z*C+c],
    ], float)


def _relationship_score(occurrences, constraints, joints, interfaces):
    vals=[]
    for c in constraints:
        if c.suppressed: continue
        try: vals.append(float(constraint_residual(occurrences,c,interfaces)))
        except Exception: vals.append(math.inf)
    for j in joints:
        if j.suppressed: continue
        try: vals.append(float(joint_residual(occurrences,j,interfaces)))
        except Exception: vals.append(math.inf)
    if any(not math.isfinite(v) for v in vals): return (math.inf, math.inf)
    return (max(vals, default=0.0), sum(vals))


def solve_assembly_relationships(
    occurrences: dict[str, Occurrence],
    constraints,
    joints: list[AssemblyJoint],
    assembly_interfaces: dict | None = None,
    iterations: int = 64,
) -> dict[str, Any]:
    """Simultaneously solve every active legacy constraint and kinematic joint.

    This is intentionally generic: no component/product names are inspected.  When no
    explicit grounded occurrence exists, the first active occurrence is used only as a
    temporary numerical gauge so global rigid-body drift does not make the system
    singular; its persisted ``grounded`` flag is not changed.
    """
    interfaces = assembly_interfaces or default_interfaces()
    active_constraints=[c for c in (constraints or []) if not c.suppressed]
    active_joints=[j for j in (joints or []) if not j.suppressed]
    for j in active_joints:
        validate_joint_definition(j)
        if j.linear_position_mm is not None:
            j.linear_position_mm=_clamp(j.linear_position_mm,j.linear_start_mm,j.linear_end_mm)
        if j.angular_position_deg is not None:
            j.angular_position_deg=_clamp(j.angular_position_deg,j.angular_start_deg,j.angular_end_deg)
    active_occ={n:o for n,o in occurrences.items() if not o.suppressed}
    if not active_occ:
        return {"converged":True,"score":[0.0,0.0],"iterations":0}

    grounded=[n for n,o in active_occ.items() if o.grounded]
    gauge = None if grounded else next(iter(active_occ))
    free=[n for n,o in active_occ.items() if not o.grounded and n != gauge]
    baseline={n:(list(o.position_mm),list(o.rotation_deg_xyz)) for n,o in occurrences.items()}
    baseline_score=_relationship_score(occurrences,active_constraints,active_joints,interfaces)
    base_R={n:euler_matrix_xyz(active_occ[n].rotation_deg_xyz) for n in free}
    base_t={n:np.asarray(active_occ[n].position_mm,float) for n in free}

    def restore(state):
        for n,(p,r) in state.items():
            if n in occurrences:
                occurrences[n].position_mm=list(p); occurrences[n].rotation_deg_xyz=list(r)

    def put(x):
        for i,n in enumerate(free):
            dt=np.asarray(x[i*6:i*6+3],float); rv=np.asarray(x[i*6+3:i*6+6],float)
            R=_rotvec_matrix(rv) @ base_R[n]
            occurrences[n].position_mm=[float(v) for v in (base_t[n]+dt)]
            occurrences[n].rotation_deg_xyz=[float(v) for v in matrix_to_euler_xyz(R)]

    # characteristic length gives angular residuals a physically useful scale while
    # preserving exact zero at the same solution.
    diags=[]
    for o in active_occ.values():
        try:
            bb=o.shape.BoundingBox(); diags.append(math.sqrt(bb.xlen**2+bb.ylen**2+bb.zlen**2))
        except Exception: pass
    orient_scale=max(1.0, min(100.0, float(np.median(diags))*0.1 if diags else 10.0))

    def relation_chunks():
        chunks=[]
        for c in active_constraints:
            v=np.asarray(constraint_vector(occurrences,c,interfaces),float)
            # orientation rows are the first three for current legacy relation vectors.
            if len(v)>=3 and c.type in {"mate","flush","insert"}:
                v=v.copy(); v[:3]*=orient_scale
            elif c.type=="angle":
                v=v*orient_scale
            chunks.append(v)
        for j in active_joints:
            v=np.asarray(joint_solver_vector(occurrences,j,interfaces),float).copy()
            # Joint vectors begin with axis-orientation residual except Ball.
            if canonical_joint_type(j.type)!="ball" and len(v)>=3:
                v[:3]*=orient_scale
            # angular command/limit terms are radians and are at the tail; scaling every
            # dimensionless small term is unnecessary for convergence, but the primary
            # axis alignment must compete with mm translation residuals.
            chunks.append(v)
        return chunks

    def residual(x, regularize=True):
        put(x)
        try: chunks=relation_chunks()
        except Exception: return None
        r=np.concatenate(chunks) if chunks else np.zeros(0,float)
        if not np.all(np.isfinite(r)): return None
        if regularize and x.size:
            # Weak trust-to-start regularization resolves null-space branch ambiguity but
            # is orders of magnitude below real relationship equations.
            r=np.concatenate([r, x*1e-9])
        return r

    if not free:
        score=_relationship_score(occurrences,active_constraints,active_joints,interfaces)
        for c in active_constraints:
            try:c.health="up_to_date" if constraint_residual(occurrences,c,interfaces)<1e-3 else "sick"
            except Exception:c.health="sick"
        for j in active_joints:
            try:j.health="up_to_date" if joint_residual(occurrences,j,interfaces)<1e-3 and _limits_healthy(occurrences,j,interfaces) else "sick"
            except Exception:j.health="sick"
        return {"converged":score[0]<1e-3,"score":list(score),"iterations":0,"gauge":gauge}

    x=np.zeros(6*len(free),float)
    r=residual(x)
    if r is None:
        restore(baseline)
        return {"converged":False,"score":[math.inf,math.inf],"iterations":0,"gauge":gauge}
    best_x=x.copy(); best_norm=float(np.dot(r,r)); lam=1e-3; used=0
    for it in range(max(1,int(iterations))):
        used=it+1; m=len(r); n=len(x); J=np.zeros((m,n),float)
        for k in range(n):
            h=1e-4 if (k%6)<3 else 1e-6
            xp=x.copy(); xm=x.copy(); xp[k]+=h; xm[k]-=h
            rp=residual(xp); rm=residual(xm)
            if rp is None or rm is None: continue
            J[:,k]=(rp-rm)/(2*h)
        put(x)
        A=J.T@J + lam*np.eye(n); g=J.T@r
        try: dx=np.linalg.solve(A,-g)
        except np.linalg.LinAlgError: dx=np.linalg.lstsq(A,-g,rcond=None)[0]
        if not np.all(np.isfinite(dx)) or float(np.linalg.norm(dx))<1e-10: break
        accepted=False
        for alpha in (1.0,0.5,0.25,0.1):
            cand=x+alpha*dx; rc=residual(cand)
            if rc is None: continue
            norm=float(np.dot(rc,rc))
            if norm < best_norm:
                x=cand; r=rc; best_x=cand.copy(); best_norm=norm; lam=max(1e-10,lam/3); accepted=True; break
        if not accepted:
            lam=min(1e10,lam*10); put(x)
        if best_norm < 1e-12: break

    put(best_x)
    score=_relationship_score(occurrences,active_constraints,active_joints,interfaces)
    for c in active_constraints:
        try:c.health="up_to_date" if constraint_residual(occurrences,c,interfaces)<1e-3 else "sick"
        except Exception:c.health="sick"
    for j in active_joints:
        try:j.health="up_to_date" if joint_residual(occurrences,j,interfaces)<1e-3 and _limits_healthy(occurrences,j,interfaces) else "sick"
        except Exception:j.health="sick"
    # Never make a mixed assembly worse than its starting state.
    final_score=_relationship_score(occurrences,active_constraints,active_joints,interfaces)
    restore_needed = (not math.isfinite(final_score[0])) or (final_score >= baseline_score and baseline_score[0] < math.inf and final_score[0] >= 1e-3)
    if restore_needed:
        restore(baseline)
        final_score=baseline_score
        for c in active_constraints:
            try:c.health="up_to_date" if constraint_residual(occurrences,c,interfaces)<1e-3 else "sick"
            except Exception:c.health="sick"
        for j in active_joints:
            try:j.health="up_to_date" if joint_residual(occurrences,j,interfaces)<1e-3 and _limits_healthy(occurrences,j,interfaces) else "sick"
            except Exception:j.health="sick"
    return {"converged":bool(not restore_needed and final_score[0]<1e-3),"score":list(final_score),"iterations":used,"gauge":gauge}


def solve_joint(occurrences: dict[str, Occurrence], j: AssemblyJoint, assembly_interfaces: dict | None = None) -> None:
    """Backward-compatible single-joint entry point using the general simultaneous solver."""
    solve_assembly_relationships(occurrences, [], [j], assembly_interfaces)


def _matrix_rank(a: np.ndarray, rtol: float = 1e-7) -> int:
    a=np.asarray(a,float)
    if a.size==0:return 0
    s=np.linalg.svd(a,compute_uv=False)
    if s.size==0 or float(s[0])<=1e-12:return 0
    return int(np.sum(s > max(a.shape)*float(s[0])*rtol))


def assembly_kinematic_degrees_of_freedom(occurrences: dict[str, Occurrence], constraints, joints: list[AssemblyJoint], assembly_interfaces: dict | None = None):
    """Numerical null-space DOF of the union of constraints and *kinematic* joint equations."""
    interfaces=assembly_interfaces or default_interfaces()
    active_occ={n:o for n,o in occurrences.items() if not o.suppressed}
    free=[n for n,o in active_occ.items() if not o.grounded]
    out={n:(0,0) if o.grounded or o.suppressed else (3,3) for n,o in occurrences.items()}
    if not free:return out
    residual_fns=[]
    for c in constraints or []:
        if c.suppressed or c.health!="up_to_date":continue
        try: constraint_vector(occurrences,c,interfaces)
        except Exception: continue
        residual_fns.append(lambda c=c: constraint_vector(occurrences,c,interfaces))
    for j in joints or []:
        if j.suppressed or j.health!="up_to_date":continue
        try: joint_vector(occurrences,j,interfaces)
        except Exception: continue
        residual_fns.append(lambda j=j: joint_vector(occurrences,j,interfaces))
    if not residual_fns:return out
    def residual():return np.concatenate([np.asarray(fn(),float) for fn in residual_fns])
    base=residual(); nvar=6*len(free); J=np.zeros((len(base),nvar),float)
    teps=1e-4; reps=1e-5; rdeg=math.degrees(reps)
    snapshots={n:(list(occurrences[n].position_mm),list(occurrences[n].rotation_deg_xyz)) for n in free}
    try:
        for oi,name in enumerate(free):
            occ=occurrences[name]
            for k in range(6):
                p0,r0=snapshots[name]; occ.position_mm=list(p0); occ.rotation_deg_xyz=list(r0)
                if k<3: occ.position_mm[k]+=teps; eps=teps
                else: occ.rotation_deg_xyz[k-3]+=rdeg; eps=reps
                J[:,oi*6+k]=(residual()-base)/eps
    finally:
        for n,(p,r) in snapshots.items(): occurrences[n].position_mm=list(p); occurrences[n].rotation_deg_xyz=list(r)
    _u,svals,vh=np.linalg.svd(J,full_matrices=True)
    rank=int(np.sum(svals > max(J.shape)*float(svals[0])*1e-7)) if svals.size and float(svals[0])>1e-12 else 0
    null=vh[rank:].T if rank<nvar else np.zeros((nvar,0),float)
    for oi,name in enumerate(free):
        block=null[oi*6:(oi+1)*6,:]
        out[name]=(min(3,_matrix_rank(block[:3,:])),min(3,_matrix_rank(block[3:,:])))
    return {k:(int(v[0]),int(v[1])) for k,v in out.items()}


def assembly_relationship_diagnostics(occurrences, constraints, joints, assembly_interfaces: dict | None = None) -> dict[str, Any]:
    interfaces=assembly_interfaces or default_interfaces(); rows=[]
    for c in constraints or []:
        if c.suppressed: continue
        try:r=float(constraint_residual(occurrences,c,interfaces)); err=None
        except Exception as exc:r=math.inf; err=str(exc)
        rows.append({"family":"constraint","name":c.name,"type":c.type,"a_occurrence":c.a_occurrence,"b_occurrence":c.b_occurrence,"health":c.health,"residual":r,"error":err})
    for j in joints or []:
        if j.suppressed: continue
        row={"family":"joint","a_occurrence":j.a_occurrence,"b_occurrence":j.b_occurrence,**joint_diagnostics(occurrences,j,interfaces)}
        rows.append(row)
    sick=[r for r in rows if r.get("health")!="up_to_date" or not math.isfinite(float(r.get("residual",r.get("solve_residual",math.inf))))]
    # Generic conflict candidates: active relationships acting on the same occurrence pair.
    conflicts=[]
    for i,a in enumerate(rows):
        pa=frozenset(x for x in (a.get("a_occurrence"),a.get("b_occurrence")) if x)
        for b in rows[i+1:]:
            pb=frozenset(x for x in (b.get("a_occurrence"),b.get("b_occurrence")) if x)
            if pa and pa==pb and (a.get("health")!="up_to_date" or b.get("health")!="up_to_date"):
                conflicts.append({"relationship_a":a.get("name"),"relationship_b":b.get("name"),"occurrences":sorted(pa)})
    return {"relationships":rows,"sick":sick,"possible_conflicts":conflicts}
