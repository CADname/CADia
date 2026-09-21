from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
import math
import numpy as np
import cadquery as cq
from cadquery import importers
from .inventor_origin import (
    ORIGIN_PLANES, ORIGIN_AXES, CENTER_POINT, CENTER_POINT_NAME,
    canonical_origin_name,
)

@dataclass
class Occurrence:
    name: str
    path: str
    grounded: bool = False
    position_mm: list[float] = field(default_factory=lambda:[0.0,0.0,0.0])
    rotation_deg_xyz: list[float] = field(default_factory=lambda:[0.0,0.0,0.0])
    suppressed: bool = False
    part_number: str = ""
    unit_mass_g: float = 0.0
    interfaces: dict[str, Any] = field(default_factory=dict)
    shape: Any = field(default=None, repr=False, compare=False)
    # Runtime-only live link to an open part document.  Assemblies still persist the
    # component path, but while the source part is open this lets an occurrence follow
    # unsaved in-memory edits exactly like a native CAD assembly occurrence.
    source_session_id: str | None = field(default=None, repr=False, compare=False)
    component_type: str = 'part'
    children: list[dict[str, Any]] = field(default_factory=list)
    reference_status: str = 'resolved'
    load_error: str | None = None

@dataclass
class AssemblyConstraint:
    name: str
    type: str
    a_occurrence: str | None
    a_ref: str
    b_occurrence: str | None
    b_ref: str
    offset_mm: float = 0.0
    angle_deg: float | None = None
    insert_opposed: bool = True
    health: str = "up_to_date"
    suppressed: bool = False


def _unit(v):
    a=np.asarray(v,dtype=float); n=np.linalg.norm(a)
    if n < 1e-12: return a
    return a/n

def euler_matrix_xyz(deg):
    rx,ry,rz=[math.radians(float(x)) for x in deg]
    cx,sx=math.cos(rx),math.sin(rx); cy,sy=math.cos(ry),math.sin(ry); cz,sz=math.cos(rz),math.sin(rz)
    Rx=np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]],float)
    Ry=np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]],float)
    Rz=np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]],float)
    return Rz@Ry@Rx

def matrix_to_euler_xyz(R):
    # R = Rz*Ry*Rx
    sy=-R[2,0]
    cy=math.sqrt(max(0.0,1-sy*sy))
    if cy > 1e-8:
        rx=math.atan2(R[2,1],R[2,2]); ry=math.asin(sy); rz=math.atan2(R[1,0],R[0,0])
    else:
        rx=math.atan2(-R[1,2],R[1,1]); ry=math.asin(sy); rz=0.0
    return [math.degrees(rx),math.degrees(ry),math.degrees(rz)]

def rotation_from_to(a,b):
    a=_unit(a); b=_unit(b); dot=float(np.clip(np.dot(a,b),-1,1))
    if dot > 1-1e-10: return np.eye(3)
    if dot < -1+1e-10:
        axis=np.cross(a,[1,0,0])
        if np.linalg.norm(axis)<1e-8: axis=np.cross(a,[0,1,0])
        axis=_unit(axis); return axis_angle_matrix(axis,180)
    axis=_unit(np.cross(a,b)); angle=math.degrees(math.acos(dot)); return axis_angle_matrix(axis,angle)

def axis_angle_matrix(axis,deg):
    x,y,z=_unit(axis); a=math.radians(deg); c,s=math.cos(a),math.sin(a); C=1-c
    return np.array([[x*x*C+c,x*y*C-z*s,x*z*C+y*s],[y*x*C+z*s,y*y*C+c,y*z*C-x*s],[z*x*C-y*s,z*y*C+x*s,z*z*C+c]],float)

def transform_shape(shape, position, rotation):
    wp=cq.Workplane(obj=shape)
    rx,ry,rz=rotation
    if rx: wp=wp.rotate((0,0,0),(1,0,0),rx)
    if ry: wp=wp.rotate((0,0,0),(0,1,0),ry)
    if rz: wp=wp.rotate((0,0,0),(0,0,1),rz)
    if any(abs(float(v))>1e-12 for v in position): wp=wp.translate(tuple(float(v) for v in position))
    return wp.val()

def transformed_frame(local_origin, local_dir, occ:Occurrence):
    R=euler_matrix_xyz(occ.rotation_deg_xyz); t=np.asarray(occ.position_mm,float)
    p=R@np.asarray(local_origin,float)+t
    d=None if local_dir is None else R@np.asarray(local_dir,float)
    return p,d

def load_component(path:str, _visited=None):
    p=Path(path).expanduser().resolve()
    if not p.exists(): raise FileNotFoundError(str(p))
    ext=p.suffix.lower(); visited=set(_visited or set())
    if str(p) in visited:raise ValueError(f'Assembly reference cycle detected at {p}')
    if p.name.lower().endswith('.scad.json'):
        from .document import CadDocument
        d=CadDocument(); d.load(str(p),visited)
        if d.shape is None: raise ValueError('Occurrence source has no geometry')
        if d.doc_type=='part':
            interfaces=d.list_interfaces_local(); children=[]
        elif d.doc_type=='assembly':
            interfaces=default_interfaces(); children=[]
            for o in d.occurrences.values():
                children.append({'name':o.name,'path':o.path,'part_number':o.part_number,'unit_mass_g':o.unit_mass_g,'grounded':o.grounded,'suppressed':o.suppressed,'component_type':getattr(o,'component_type','part'),'children':getattr(o,'children',[])})
        else:raise ValueError(f'Unsupported occurrence document type: {d.doc_type}')
        props=d.properties; mp=d.mass_properties(); mass=mp.get('mass_g')
        return d.shape, interfaces, props.get('Part Number') or d.title or p.stem, (0.0 if mass is None else float(mass)), d.doc_type, children
    if ext in ('.step','.stp'):
        wp=importers.importStep(str(p)); shape=wp.val()
        return shape, default_interfaces(), p.stem, 0.0, 'part', []
    if ext in ('.brep','.brp'):
        wp=importers.importBrep(str(p)); shape=wp.val()
        return shape, default_interfaces(), p.stem, 0.0, 'part', []
    raise ValueError('Supported occurrence files: .scad.json, .step/.stp, .brep/.brp')

def default_interfaces():
    return {
        'imates':[],
        'work_planes':[{'name':k,'kind':'plane','origin':list(v[0]),'direction':list(v[1])} for k,v in ORIGIN_PLANES.items()],
        'work_axes':[{'name':k,'kind':'axis','origin':list(v[0]),'direction':list(v[1])} for k,v in ORIGIN_AXES.items()],
        'work_points':[],
        'origin_geometry':[
            *[{'name':k,'kind':'plane','origin':list(v[0]),'direction':list(v[1])} for k,v in ORIGIN_PLANES.items()],
            *[{'name':k,'kind':'axis','origin':list(v[0]),'direction':list(v[1])} for k,v in ORIGIN_AXES.items()],
            {'name':CENTER_POINT_NAME,'kind':'point','origin':[0,0,0],'direction':None},
        ]
    }

def find_interface(interfaces:dict, ref:str):
    # ipt-mcp expects callers to use names returned by list_interfaces.  Keep those
    # canonical Inventor names public, while accepting hidden compatibility aliases
    # solely so a planner spelling variant cannot sink an otherwise valid plan.
    requested=ref
    ref=canonical_origin_name(ref,allow_compat=True)
    for group in ('imates','work_planes','work_axes','work_points','origin_geometry'):
        for item in interfaces.get(group,[]):
            if item.get('name')==ref: return item
    raise ValueError(f"Unknown reference '{requested}'. Available: {available_interface_names(interfaces)}")

def available_interface_names(interfaces):
    out=[]
    for g in ('imates','work_planes','work_axes','work_points','origin_geometry'):
        out.extend(x.get('name') for x in interfaces.get(g,[]) if x.get('name'))
    return sorted(set(out))

def solve_constraint(occurrences:dict[str,Occurrence], constraint:AssemblyConstraint, assembly_interfaces:dict):
    def side(name,ref):
        if not name:
            item=find_interface(assembly_interfaces,ref); return None,item,np.asarray(item['origin'],float), None if item.get('direction') is None else _unit(item['direction'])
        if name not in occurrences: raise ValueError(f'Occurrence not found: {name}')
        occ=occurrences[name]; item=find_interface(occ.interfaces,ref); p,d=transformed_frame(item['origin'],item.get('direction'),occ)
        return occ,item,p,None if d is None else _unit(d)
    oa,ia,pa,da=side(constraint.a_occurrence,constraint.a_ref)
    ob,ib,pb,db=side(constraint.b_occurrence,constraint.b_ref)
    movable=ob if ob is not None and not ob.grounded else (oa if oa is not None and not oa.grounded else None)
    invert=movable is oa
    if movable is None:
        constraint.health='up_to_date' if constraint_error(constraint,pa,da,pb,db) < 1e-4 else 'sick'
        return
    # Normalize so target A is fixed and B moves. If A moves, solve swapped relation.
    if invert:
        pa,pb=pb,pa; da,db=db,da
        target_occ=oa
    else: target_occ=ob
    Rcur=euler_matrix_xyz(target_occ.rotation_deg_xyz)
    tcur=np.asarray(target_occ.position_mm,float)
    typ=constraint.type
    if typ in ('mate','flush'):
        if da is None or db is None: raise ValueError('mate/flush require directional refs')
        wanted = -da if typ=='mate' else da
        Rot=rotation_from_to(db,wanted); Rnew=Rot@Rcur
        # Rotate around current ref point, then shift to target plane offset.
        local=find_interface(target_occ.interfaces, constraint.a_ref if invert else constraint.b_ref)
        lp=np.asarray(local['origin'],float)
        p_rot=Rnew@lp+tcur
        eff_offset=float(constraint.offset_mm) * (-1.0 if invert else 1.0)
        target=pa + da*eff_offset
        # Plane relation only constrains normal translation; retain tangential position.
        delta=da*np.dot(target-p_rot,da)
        tnew=tcur+delta
        target_occ.rotation_deg_xyz=matrix_to_euler_xyz(Rnew); target_occ.position_mm=tnew.tolist()
    elif typ=='insert':
        if da is None or db is None: raise ValueError('insert requires axis-like directional refs')
        wanted=(-da if constraint.insert_opposed else da)
        Rot=rotation_from_to(db,wanted); Rnew=Rot@Rcur
        local=find_interface(target_occ.interfaces, constraint.a_ref if invert else constraint.b_ref); lp=np.asarray(local['origin'],float)
        p_rot=Rnew@lp+tcur
        # Bring axis origins together and apply the requested axial offset.
        # This enforces both the concentric and planar parts of an Inventor Insert relation.
        eff_offset=float(constraint.offset_mm) * (-1.0 if invert else 1.0)
        target=pa + da*eff_offset
        tnew=tcur+(target-p_rot)
        target_occ.rotation_deg_xyz=matrix_to_euler_xyz(Rnew); target_occ.position_mm=tnew.tolist()
    elif typ=='angle':
        if da is None or db is None or constraint.angle_deg is None: raise ValueError('angle requires directional refs and angle_deg')
        current=math.degrees(math.acos(float(np.clip(np.dot(_unit(da),_unit(db)),-1,1))))
        axis=np.cross(db,da)
        if np.linalg.norm(axis)<1e-8: axis=np.cross(db,[1,0,0])
        if np.linalg.norm(axis)<1e-8: axis=np.cross(db,[0,1,0])
        Rot=axis_angle_matrix(axis,float(constraint.angle_deg)-current); Rnew=Rot@Rcur
        target_occ.rotation_deg_xyz=matrix_to_euler_xyz(Rnew)
    else: raise ValueError('type must be mate|flush|insert|angle')
    # Evaluate after update.
    oa,ia,pa,da=side(constraint.a_occurrence,constraint.a_ref); ob,ib,pb,db=side(constraint.b_occurrence,constraint.b_ref)
    constraint.health='up_to_date' if constraint_error(constraint,pa,da,pb,db) < 1e-3 else 'sick'

def constraint_error(c,pa,da,pb,db):
    if c.type in ('mate','flush') and da is not None and db is not None:
        want=-_unit(da) if c.type=='mate' else _unit(da)
        angular=1-float(np.clip(np.dot(want,_unit(db)),-1,1))
        plane=abs(float(np.dot(pb-pa,_unit(da)))-float(c.offset_mm))
        return angular*1000+plane
    if c.type=='insert' and da is not None and db is not None:
        uda=_unit(da); want=-uda if c.insert_opposed else uda
        v=pb-pa; perp=np.linalg.norm(v-uda*np.dot(v,uda)); axial=abs(float(np.dot(v,uda))-float(c.offset_mm)); angular=1-float(np.clip(np.dot(want,_unit(db)),-1,1))
        return float(perp)+axial+angular*1000
    if c.type=='angle' and da is not None and db is not None and c.angle_deg is not None:
        a=math.degrees(math.acos(float(np.clip(np.dot(_unit(da),_unit(db)),-1,1))))
        return abs(a-float(c.angle_deg))
    return 1e9


def constraint_residual(occurrences:dict[str,Occurrence], c:AssemblyConstraint, assembly_interfaces:dict) -> float:
    """Evaluate one assembly relation without mutating occurrence transforms."""
    def side(name, ref):
        if not name:
            item=find_interface(assembly_interfaces,ref)
            return np.asarray(item['origin'],float), None if item.get('direction') is None else _unit(item['direction'])
        if name not in occurrences:
            raise ValueError(f'Occurrence not found: {name}')
        occ=occurrences[name]; item=find_interface(occ.interfaces,ref)
        p,d=transformed_frame(item['origin'],item.get('direction'),occ)
        return p, None if d is None else _unit(d)
    pa,da=side(c.a_occurrence,c.a_ref); pb,db=side(c.b_occurrence,c.b_ref)
    return float(constraint_error(c,pa,da,pb,db))


def constraint_vector(occurrences:dict[str,Occurrence], c:AssemblyConstraint, assembly_interfaces:dict) -> np.ndarray:
    """Vector residual used for local/global assembly-DOF rank analysis.

    The scalar health score is intentionally not used for rank: mate/flush/insert
    each encode several independent geometric equations.  A vector residual lets
    a numerical Jacobian recover the independent translational/rotational freedoms
    of the solved constraint graph rather than subtracting hard-coded counts.
    """
    def side(name, ref):
        if not name:
            item=find_interface(assembly_interfaces,ref)
            return np.asarray(item['origin'],float), None if item.get('direction') is None else _unit(item['direction'])
        if name not in occurrences:
            raise ValueError(f'Occurrence not found: {name}')
        occ=occurrences[name]; item=find_interface(occ.interfaces,ref)
        p,d=transformed_frame(item['origin'],item.get('direction'),occ)
        return p, None if d is None else _unit(d)
    pa,da=side(c.a_occurrence,c.a_ref); pb,db=side(c.b_occurrence,c.b_ref)
    if c.type in ('mate','flush'):
        if da is None or db is None: raise ValueError('mate/flush require directional refs')
        ua=_unit(da); ub=_unit(db); want=-ua if c.type=='mate' else ua
        orient=np.cross(ub,want)
        plane=np.array([float(np.dot(pb-pa,ua))-float(c.offset_mm)],float)
        return np.concatenate([orient,plane])
    if c.type=='insert':
        if da is None or db is None: raise ValueError('insert requires axis-like directional refs')
        ua=_unit(da); ub=_unit(db); want=-ua if c.insert_opposed else ua
        orient=np.cross(ub,want)
        v=np.asarray(pb-pa,float); perp=v-ua*float(np.dot(v,ua))
        axial=np.array([float(np.dot(v,ua))-float(c.offset_mm)],float)
        return np.concatenate([orient,perp,axial])
    if c.type=='angle':
        if da is None or db is None or c.angle_deg is None: raise ValueError('angle requires directional refs and angle_deg')
        ang=math.acos(float(np.clip(np.dot(_unit(da),_unit(db)),-1.0,1.0)))
        return np.array([ang-math.radians(float(c.angle_deg))],float)
    raise ValueError('type must be mate|flush|insert|angle')

def _matrix_rank(a:np.ndarray, rtol:float=1e-7) -> int:
    a=np.asarray(a,float)
    if a.size==0:return 0
    s=np.linalg.svd(a,compute_uv=False)
    if s.size==0 or float(s[0])<=1e-12:return 0
    return int(np.sum(s > max(a.shape)*float(s[0])*rtol))

def assembly_degrees_of_freedom(occurrences:dict[str,Occurrence], constraints:list[AssemblyConstraint], assembly_interfaces:dict | None=None):
    """Return per-occurrence translation/rotation DOF from the solved constraint graph.

    This is the standalone analogue of Inventor ComponentOccurrence.GetDegreesOfFreedom.
    It forms the numerical Jacobian of all healthy active constraint equations and
    measures each occurrence's projection into the global null-space.  It therefore
    preserves coupled rigid-body freedoms instead of simply subtracting a fixed count
    per mate/insert constraint.
    """
    assembly_interfaces=assembly_interfaces or default_interfaces()
    active_occ={n:o for n,o in occurrences.items() if not o.suppressed}
    free=[n for n,o in active_occ.items() if not o.grounded]
    out={n:(0,0) if o.grounded or o.suppressed else (3,3) for n,o in occurrences.items()}
    if not free:return out
    active=[]
    for c in constraints:
        if c.suppressed or c.health!='up_to_date':continue
        if c.a_occurrence and (c.a_occurrence not in active_occ):continue
        if c.b_occurrence and (c.b_occurrence not in active_occ):continue
        try:
            constraint_vector(occurrences,c,assembly_interfaces)
        except Exception:
            continue
        active.append(c)
    if not active:return out

    def residual():
        return np.concatenate([constraint_vector(occurrences,c,assembly_interfaces) for c in active])

    base=residual(); nvar=6*len(free); J=np.zeros((len(base),nvar),float)
    trans_eps=1e-4
    rot_eps_rad=1e-5; rot_eps_deg=math.degrees(rot_eps_rad)
    snapshots={n:(list(occurrences[n].position_mm),list(occurrences[n].rotation_deg_xyz)) for n in free}
    try:
        for oi,name in enumerate(free):
            occ=occurrences[name]
            for j in range(6):
                pos0,rot0=snapshots[name]
                occ.position_mm=list(pos0); occ.rotation_deg_xyz=list(rot0)
                if j<3:
                    occ.position_mm[j]+=trans_eps; eps=trans_eps
                else:
                    occ.rotation_deg_xyz[j-3]+=rot_eps_deg; eps=rot_eps_rad
                J[:,oi*6+j]=(residual()-base)/eps
    finally:
        for n,(pos,rot) in snapshots.items():
            occurrences[n].position_mm=list(pos); occurrences[n].rotation_deg_xyz=list(rot)

    # Null-space of the full assembly constraint Jacobian.
    _u,svals,vh=np.linalg.svd(J,full_matrices=True)
    if svals.size and float(svals[0])>1e-12:
        tol=max(J.shape)*float(svals[0])*1e-7
        rank=int(np.sum(svals>tol))
    else:
        rank=0
    null=vh[rank:].T if rank < nvar else np.zeros((nvar,0),float)
    for oi,name in enumerate(free):
        block=null[oi*6:(oi+1)*6,:]
        t=min(3,_matrix_rank(block[:3,:])); r=min(3,_matrix_rank(block[3:,:]))
        out[name]=(int(t),int(r))
    return out


def try_global_constraint_fallback(occurrences:dict[str,Occurrence], constraints:list[AssemblyConstraint], assembly_interfaces:dict | None=None, iterations:int=24) -> bool:
    """Fallback-only simultaneous 6DOF assembly solve.

    The sequential legacy solver remains primary.  This routine is called only when
    legacy leaves at least one active relation sick.  It mutates a candidate pose set
    and commits it only when aggregate residual strictly improves while every relation
    that was already healthy stays healthy.  Otherwise all occurrence poses are restored.
    """
    assembly_interfaces=assembly_interfaces or default_interfaces()
    active=[c for c in constraints if not c.suppressed]
    free=[n for n,o in occurrences.items() if not o.suppressed and not o.grounded]
    if not active or not free:return False

    def snap():
        return {n:(list(o.position_mm),list(o.rotation_deg_xyz)) for n,o in occurrences.items()}
    baseline=snap()
    def restore(state):
        for n,(p,r) in state.items():
            if n in occurrences:
                occurrences[n].position_mm=list(p); occurrences[n].rotation_deg_xyz=list(r)
    def scalar_errors():
        vals=[]
        for c in active:
            try:vals.append(float(constraint_residual(occurrences,c,assembly_interfaces)))
            except Exception:vals.append(math.inf)
        return vals
    def score(vals):
        if any(not math.isfinite(v) for v in vals):return (math.inf,math.inf)
        return (max(vals,default=0.0),sum(vals))
    base_err=scalar_errors(); base_score=score(base_err)
    if base_score[0] < 1e-3:return False

    x0=[]
    for n in free:
        x0.extend(occurrences[n].position_mm); x0.extend(occurrences[n].rotation_deg_xyz)
    x0=np.asarray(x0,float); best_x=x0.copy(); best_score=base_score
    pos_scale=max(1.0,float(np.max(np.abs(x0))) if x0.size else 1.0)

    def put(x):
        for i,n in enumerate(free):
            occurrences[n].position_mm=[float(v) for v in x[i*6:i*6+3]]
            occurrences[n].rotation_deg_xyz=[float(v) for v in x[i*6+3:i*6+6]]
    def residual(x):
        put(x); chunks=[]
        try:
            for c in active:chunks.append(np.asarray(constraint_vector(occurrences,c,assembly_interfaces),float))
        except Exception:return None
        r=np.concatenate(chunks) if chunks else np.zeros(0,float)
        if not np.all(np.isfinite(r)):return None
        # Weak anchor only resolves null-space drift; it cannot dominate real constraints.
        reg=(x-x0)*(1e-8/pos_scale)
        return np.concatenate([r,reg])

    r=residual(best_x)
    if r is None:restore(baseline); return False
    lam=1e-3
    for _ in range(max(1,int(iterations))):
        m=len(r); nvar=len(best_x); J=np.zeros((m,nvar),float)
        for j in range(nvar):
            # position variables are mm; rotation variables are degrees.
            h=1e-4 if (j%6)<3 else 1e-3
            xp=best_x.copy(); xm=best_x.copy(); xp[j]+=h; xm[j]-=h
            rp=residual(xp); rm=residual(xm)
            if rp is None or rm is None:
                put(best_x); continue
            J[:,j]=(rp-rm)/(2*h)
        put(best_x)
        A=J.T@J + lam*np.eye(nvar); g=J.T@r
        try:dx=np.linalg.solve(A,-g)
        except np.linalg.LinAlgError:dx=np.linalg.lstsq(A,-g,rcond=None)[0]
        if not np.all(np.isfinite(dx)) or float(np.linalg.norm(dx))<1e-8:break
        cand=best_x+dx; rc=residual(cand)
        if rc is None:
            lam=min(1e8,lam*10); put(best_x); continue
        cur_err=scalar_errors(); cur_score=score(cur_err)
        if cur_score < best_score:
            best_x=cand; best_score=cur_score; r=rc; lam=max(1e-9,lam/3)
            if best_score[0] < 1e-3:break
        else:
            lam=min(1e8,lam*10); put(best_x)

    put(best_x); cand_err=scalar_errors()
    preserved=True
    for before,after in zip(base_err,cand_err):
        if math.isfinite(before) and before<1e-3 and (not math.isfinite(after) or after>=1e-3):
            preserved=False; break
    if not (preserved and score(cand_err) < base_score):
        restore(baseline); return False
    return True
