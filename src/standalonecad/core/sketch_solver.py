from __future__ import annotations
import math
import copy
from typing import Callable
import numpy as np

EPS = 1e-9


def _num(v, value: Callable[[object], float]) -> float:
    return float(value(v))


def _line(e, value):
    d=e.data
    return [_num(d['x1'],value),_num(d['y1'],value)],[_num(d['x2'],value),_num(d['y2'],value)]


def _set_line(e,a,b):
    e.data.update({'x1':float(a[0]),'y1':float(a[1]),'x2':float(b[0]),'y2':float(b[1])})


def _unit(v):
    n=math.hypot(v[0],v[1])
    if n<EPS:return (1.0,0.0)
    return (v[0]/n,v[1]/n)


def _len(e,value):
    a,b=_line(e,value); return math.dist(a,b)


def enforce_constraint(sm, c, value) -> bool:
    ids=c.get('entity_ids') or c.get('tags') or []
    ents={e.tag:e for e in sm.entities}
    if any(x not in ents for x in ids): return False
    es=[ents[x] for x in ids]; typ=str(c.get('type','')).lower()
    if typ=='horizontal' and len(es)==1 and es[0].kind=='line':
        a,b=_line(es[0],value); b[1]=a[1]; _set_line(es[0],a,b); return True
    if typ=='vertical' and len(es)==1 and es[0].kind=='line':
        a,b=_line(es[0],value); b[0]=a[0]; _set_line(es[0],a,b); return True
    if typ=='coincident' and len(es)>=2:
        a,b=es[:2]
        if a.kind=='line' and b.kind=='line':
            p0,p1=_line(a,value); q0,q1=_line(b,value); q0=list(p1); _set_line(b,q0,q1); return True
        if a.kind=='line' and b.kind in ('circle','arc'):
            p0,p1=_line(a,value); b.data['cx'],b.data['cy']=p1; return True
        if b.kind=='line' and a.kind in ('circle','arc'):
            q0,q1=_line(b,value); q0=[_num(a.data['cx'],value),_num(a.data['cy'],value)]; _set_line(b,q0,q1); return True
        if a.kind in ('circle','arc') and b.kind in ('circle','arc'):
            b.data['cx']=_num(a.data['cx'],value); b.data['cy']=_num(a.data['cy'],value); return True
    if typ in ('parallel','perpendicular') and len(es)>=2 and all(e.kind=='line' for e in es[:2]):
        a0,a1=_line(es[0],value); b0,b1=_line(es[1],value); u=_unit((a1[0]-a0[0],a1[1]-a0[1])); L=math.dist(b0,b1)
        if typ=='perpendicular':u=(-u[1],u[0])
        _set_line(es[1],b0,[b0[0]+u[0]*L,b0[1]+u[1]*L]); return True
    if typ=='equal' and len(es)>=2:
        a,b=es[:2]
        if a.kind=='line' and b.kind=='line':
            b0,b1=_line(b,value); u=_unit((b1[0]-b0[0],b1[1]-b0[1])); L=_len(a,value); _set_line(b,b0,[b0[0]+u[0]*L,b0[1]+u[1]*L]); return True
        if a.kind in ('circle','arc') and b.kind in ('circle','arc'):
            b.data['r']=_num(a.data['r'],value); return True
    if typ=='concentric' and len(es)>=2 and all(e.kind in ('circle','arc') for e in es[:2]):
        es[1].data['cx']=_num(es[0].data['cx'],value); es[1].data['cy']=_num(es[0].data['cy'],value); return True
    if typ=='collinear' and len(es)>=2 and all(e.kind=='line' for e in es[:2]):
        a0,a1=_line(es[0],value); b0,b1=_line(es[1],value); u=_unit((a1[0]-a0[0],a1[1]-a0[1])); L=math.dist(b0,b1)
        # Project the first endpoint of B to line A, retaining the along-line location.
        t=(b0[0]-a0[0])*u[0]+(b0[1]-a0[1])*u[1]; q0=[a0[0]+t*u[0],a0[1]+t*u[1]]
        _set_line(es[1],q0,[q0[0]+u[0]*L,q0[1]+u[1]*L]); return True
    if typ=='tangent' and len(es)>=2:
        a,b=es[:2]
        if a.kind=='line' and b.kind in ('circle','arc'):
            p0,p1=_line(a,value); u=_unit((p1[0]-p0[0],p1[1]-p0[1])); n=(-u[1],u[0]); cx,cy=_num(b.data['cx'],value),_num(b.data['cy'],value); r=_num(b.data['r'],value)
            signed=(cx-p0[0])*n[0]+(cy-p0[1])*n[1]; side=1.0 if signed>=0 else -1.0
            t=(cx-p0[0])*u[0]+(cy-p0[1])*u[1]; proj=(p0[0]+t*u[0],p0[1]+t*u[1])
            b.data['cx']=proj[0]+side*r*n[0]; b.data['cy']=proj[1]+side*r*n[1]; return True
        if b.kind=='line' and a.kind in ('circle','arc'):
            return enforce_constraint(sm,{'type':'tangent','entity_ids':[ids[1],ids[0]]},value)
        if a.kind in ('circle','arc') and b.kind in ('circle','arc'):
            ax,ay=_num(a.data['cx'],value),_num(a.data['cy'],value); bx,by=_num(b.data['cx'],value),_num(b.data['cy'],value); ra,rb=_num(a.data['r'],value),_num(b.data['r'],value); u=_unit((bx-ax,by-ay)); b.data['cx']=ax+u[0]*(ra+rb); b.data['cy']=ay+u[1]*(ra+rb); return True
    if typ=='symmetric' and len(es)>=3 and es[2].kind=='line':
        axis=es[2]; a0,a1=_line(axis,value); vx,vy=a1[0]-a0[0],a1[1]-a0[1]; L2=vx*vx+vy*vy
        if L2<EPS:return False
        def mir(p):
            t=((p[0]-a0[0])*vx+(p[1]-a0[1])*vy)/L2; q=(a0[0]+t*vx,a0[1]+t*vy); return (2*q[0]-p[0],2*q[1]-p[1])
        src,dst=es[0],es[1]
        if src.kind=='line' and dst.kind=='line':
            p0,p1=_line(src,value); _set_line(dst,mir(p0),mir(p1)); return True
        if src.kind in ('circle','arc') and dst.kind==src.kind:
            q=mir((_num(src.data['cx'],value),_num(src.data['cy'],value))); dst.data['cx'],dst.data['cy'],dst.data['r']=q[0],q[1],_num(src.data['r'],value); return True
    return False


def solve_constraints(sm, value, passes:int=12):
    """Deterministic iterative solver with best-state retention.

    The legacy solver simply returned the geometry from the final enforcement pass.
    Interdependent constraints can oscillate, so a later pass may actually be worse.
    We now retain the state with the lowest worst residual (then lowest total residual),
    which is conservative: supported constraint semantics are unchanged, but a solve
    cannot end in a state that was measurably worse than an earlier pass.
    """
    if not sm.constraints:
        return []

    def snapshot():
        return [copy.deepcopy(e.data) for e in sm.entities]

    def restore(state):
        for e,data in zip(sm.entities,state):
            e.data=copy.deepcopy(data)

    def score():
        errors=[]
        for c in sm.constraints:
            err,_=constraint_error(sm,c,value)
            errors.append(float(err))
        if not errors:
            return (0.0,0.0)
        if any(not math.isfinite(x) for x in errors):
            return (math.inf,math.inf)
        return (max(errors),sum(errors))

    solved=[]
    best_state=snapshot(); best_score=score()
    stagnant=0
    for _ in range(max(1,int(passes))):
        count=0
        for c in sm.constraints:
            ok=enforce_constraint(sm,c,value)
            c['directly_enforced']=bool(ok)
            if ok:count+=1
        solved.append(count)
        current=score()
        if current < best_score:
            best_score=current; best_state=snapshot(); stagnant=0
        else:
            stagnant+=1
        if best_score[0] <= 1e-7 or count==0 or stagnant>=3:
            break

    restore(best_state)
    # Reflect the final retained geometry, not merely the last attempted pass.
    for c in sm.constraints:
        err,_=constraint_error(sm,c,value)
        c['directly_enforced']=bool(math.isfinite(err) and err <= 1e-6)

    # Monotonic v2 fallback: the legacy deterministic solver remains authoritative.
    # A numerical simultaneous solve is attempted ONLY when legacy leaves a sick
    # constraint/dimension, and its candidate is committed only when it is strictly
    # better while preserving every relation that legacy already had healthy.
    # Therefore an already-successful sketch never changes path or geometry.
    try:
        _try_global_fallback(sm, value)
    except Exception:
        # Fallback is deliberately non-fatal.  Legacy result is already restored.
        pass
    for c in sm.constraints:
        err,_=constraint_error(sm,c,value)
        c['directly_enforced']=bool(math.isfinite(err) and err <= 1e-6)
    return solved


def constraint_error(sm,c,value):
    ents={e.tag:e for e in sm.entities}; ids=c.get('entity_ids') or c.get('tags') or []; typ=str(c.get('type','')).lower()
    if any(x not in ents for x in ids): return math.inf,'entity_not_found'
    es=[ents[x] for x in ids]; tol=1e-6
    try:
        if typ in ('horizontal','vertical') and len(es)==1 and es[0].kind=='line':
            a,b=_line(es[0],value); return (abs(b[1]-a[1]) if typ=='horizontal' else abs(b[0]-a[0])),None
        if typ=='coincident' and len(es)>=2:
            a,b=es[:2]
            if a.kind=='line' and b.kind=='line': return math.dist(_line(a,value)[1],_line(b,value)[0]),None
            if a.kind=='line' and b.kind in ('circle','arc'): return math.dist(_line(a,value)[1],(_num(b.data['cx'],value),_num(b.data['cy'],value))),None
            if b.kind=='line' and a.kind in ('circle','arc'): return math.dist(_line(b,value)[0],(_num(a.data['cx'],value),_num(a.data['cy'],value))),None
            if a.kind in ('circle','arc') and b.kind in ('circle','arc'): return math.dist((_num(a.data['cx'],value),_num(a.data['cy'],value)),(_num(b.data['cx'],value),_num(b.data['cy'],value))),None
        if typ in ('parallel','perpendicular','collinear') and len(es)>=2 and all(e.kind=='line' for e in es[:2]):
            a0,a1=_line(es[0],value); b0,b1=_line(es[1],value); va=(a1[0]-a0[0],a1[1]-a0[1]); vb=(b1[0]-b0[0],b1[1]-b0[1]); na=math.hypot(*va); nb=math.hypot(*vb)
            if na<EPS or nb<EPS:return math.inf,'degenerate_line'
            cross=abs(va[0]*vb[1]-va[1]*vb[0])/(na*nb); dot=abs(va[0]*vb[0]+va[1]*vb[1])/(na*nb)
            if typ=='parallel':return cross,None
            if typ=='perpendicular':return dot,None
            distance=abs((b0[0]-a0[0])*va[1]-(b0[1]-a0[1])*va[0])/na; return cross+distance,None
        if typ=='equal' and len(es)>=2:
            a,b=es[:2]
            if a.kind=='line' and b.kind=='line':return abs(_len(a,value)-_len(b,value)),None
            if a.kind in ('circle','arc') and b.kind in ('circle','arc'):return abs(_num(a.data['r'],value)-_num(b.data['r'],value)),None
        if typ=='concentric' and len(es)>=2 and all(e.kind in ('circle','arc') for e in es[:2]):
            return math.dist((_num(es[0].data['cx'],value),_num(es[0].data['cy'],value)),(_num(es[1].data['cx'],value),_num(es[1].data['cy'],value))),None
        if typ=='tangent' and len(es)>=2:
            a,b=es[:2]
            if a.kind=='line' and b.kind in ('circle','arc'):
                p0,p1=_line(a,value); vx,vy=p1[0]-p0[0],p1[1]-p0[1]; L=math.hypot(vx,vy); cx,cy=_num(b.data['cx'],value),_num(b.data['cy'],value); r=_num(b.data['r'],value); dist=abs((cx-p0[0])*vy-(cy-p0[1])*vx)/max(L,EPS); return abs(dist-r),None
            if b.kind=='line' and a.kind in ('circle','arc'): return constraint_error(sm,{'type':'tangent','entity_ids':[ids[1],ids[0]]},value)
            if a.kind in ('circle','arc') and b.kind in ('circle','arc'):
                ca=(_num(a.data['cx'],value),_num(a.data['cy'],value)); cb=(_num(b.data['cx'],value),_num(b.data['cy'],value)); return abs(math.dist(ca,cb)-(_num(a.data['r'],value)+_num(b.data['r'],value))),None
        if typ=='symmetric' and len(es)>=3 and es[2].kind=='line':
            # Validate by applying a temporary mathematical mirror measurement.
            a0,a1=_line(es[2],value); vx,vy=a1[0]-a0[0],a1[1]-a0[1]; L2=vx*vx+vy*vy
            if L2<EPS:return math.inf,'degenerate_axis'
            def mir(p):
                t=((p[0]-a0[0])*vx+(p[1]-a0[1])*vy)/L2; q=(a0[0]+t*vx,a0[1]+t*vy); return (2*q[0]-p[0],2*q[1]-p[1])
            src,dst=es[0],es[1]
            if src.kind=='line' and dst.kind=='line':
                s0,s1=_line(src,value); d0,d1=_line(dst,value); return max(math.dist(mir(s0),d0),math.dist(mir(s1),d1)),None
            if src.kind in ('circle','arc') and dst.kind==src.kind:
                q=mir((_num(src.data['cx'],value),_num(src.data['cy'],value))); return math.dist(q,(_num(dst.data['cx'],value),_num(dst.data['cy'],value)))+abs(_num(src.data['r'],value)-_num(dst.data['r'],value)),None
        return math.inf,'unsupported_entity_combination'
    except Exception as exc:
        return math.inf,str(exc)

def _dimension_error(sm, dim, value):
    """Residual for the same driving-dimension forms supported by the public tool.

    Keeping this intentionally narrow is part of the monotonic policy: unsupported
    dimension semantics remain on the legacy path instead of being guessed.
    """
    tag=dim.get('entity_id') or dim.get('entityId')
    ent=next((e for e in sm.entities if e.tag==tag),None)
    if ent is None:return math.inf
    target=float(value(dim.get('value_mm',dim.get('value',0))))
    if ent.kind=='line':return abs(_len(ent,value)-target)
    if ent.kind in ('circle','arc'):return abs(_num(ent.data['r'],value)-target)
    return math.inf



def _constraint_residual_components(sm,c,value):
    """Signed/smooth residuals used ONLY by the failure-only global fallback.

    Public/legacy constraint semantics stay in ``constraint_error``.  These components
    give the numerical fallback directional information without changing the accepted
    meaning of a constraint.  Unsupported combinations deliberately fall back to the
    legacy scalar residual instead of guessing new semantics.
    """
    ents={e.tag:e for e in sm.entities}; ids=c.get('entity_ids') or c.get('tags') or []; typ=str(c.get('type','')).lower()
    if any(x not in ents for x in ids):return [math.inf]
    es=[ents[x] for x in ids]
    try:
        if typ in ('horizontal','vertical') and len(es)==1 and es[0].kind=='line':
            a,b=_line(es[0],value); return [b[1]-a[1] if typ=='horizontal' else b[0]-a[0]]
        if typ=='coincident' and len(es)>=2:
            a,b=es[:2]
            if a.kind=='line' and b.kind=='line':
                p=_line(a,value)[1]; q=_line(b,value)[0]; return [q[0]-p[0],q[1]-p[1]]
            if a.kind=='line' and b.kind in ('circle','arc'):
                p=_line(a,value)[1]; q=(_num(b.data['cx'],value),_num(b.data['cy'],value)); return [q[0]-p[0],q[1]-p[1]]
            if b.kind=='line' and a.kind in ('circle','arc'):
                p=(_num(a.data['cx'],value),_num(a.data['cy'],value)); q=_line(b,value)[0]; return [q[0]-p[0],q[1]-p[1]]
            if a.kind in ('circle','arc') and b.kind in ('circle','arc'):
                p=(_num(a.data['cx'],value),_num(a.data['cy'],value)); q=(_num(b.data['cx'],value),_num(b.data['cy'],value)); return [q[0]-p[0],q[1]-p[1]]
        if typ in ('parallel','perpendicular','collinear') and len(es)>=2 and all(e.kind=='line' for e in es[:2]):
            a0,a1=_line(es[0],value); b0,b1=_line(es[1],value)
            va=(a1[0]-a0[0],a1[1]-a0[1]); vb=(b1[0]-b0[0],b1[1]-b0[1]); na=math.hypot(*va); nb=math.hypot(*vb)
            if na<EPS or nb<EPS:return [math.inf]
            cross=(va[0]*vb[1]-va[1]*vb[0])/(na*nb); dot=(va[0]*vb[0]+va[1]*vb[1])/(na*nb)
            if typ=='parallel':return [cross]
            if typ=='perpendicular':return [dot]
            signed_dist=((b0[0]-a0[0])*va[1]-(b0[1]-a0[1])*va[0])/na
            return [cross,signed_dist]
        if typ=='equal' and len(es)>=2:
            a,b=es[:2]
            if a.kind=='line' and b.kind=='line':return [_len(b,value)-_len(a,value)]
            if a.kind in ('circle','arc') and b.kind in ('circle','arc'):return [_num(b.data['r'],value)-_num(a.data['r'],value)]
        if typ=='concentric' and len(es)>=2 and all(e.kind in ('circle','arc') for e in es[:2]):
            return [_num(es[1].data['cx'],value)-_num(es[0].data['cx'],value),_num(es[1].data['cy'],value)-_num(es[0].data['cy'],value)]
        if typ=='tangent' and len(es)>=2:
            a,b=es[:2]
            if a.kind=='line' and b.kind in ('circle','arc'):
                p0,p1=_line(a,value); vx,vy=p1[0]-p0[0],p1[1]-p0[1]; L=math.hypot(vx,vy)
                if L<EPS:return [math.inf]
                cx,cy=_num(b.data['cx'],value),_num(b.data['cy'],value); r=_num(b.data['r'],value)
                signed=((cx-p0[0])*vy-(cy-p0[1])*vx)/L
                # Preserve the side selected by the legacy state, avoiding abs()'s cusp.
                side=1.0 if signed>=0 else -1.0
                return [signed-side*r]
            if b.kind=='line' and a.kind in ('circle','arc'):
                return _constraint_residual_components(sm,{'type':'tangent','entity_ids':[ids[1],ids[0]]},value)
            if a.kind in ('circle','arc') and b.kind in ('circle','arc'):
                ca=(_num(a.data['cx'],value),_num(a.data['cy'],value)); cb=(_num(b.data['cx'],value),_num(b.data['cy'],value))
                return [math.dist(ca,cb)-(_num(a.data['r'],value)+_num(b.data['r'],value))]
        if typ=='symmetric' and len(es)>=3 and es[2].kind=='line':
            a0,a1=_line(es[2],value); vx,vy=a1[0]-a0[0],a1[1]-a0[1]; L2=vx*vx+vy*vy
            if L2<EPS:return [math.inf]
            def mir(p):
                t=((p[0]-a0[0])*vx+(p[1]-a0[1])*vy)/L2; q=(a0[0]+t*vx,a0[1]+t*vy); return (2*q[0]-p[0],2*q[1]-p[1])
            src,dst=es[0],es[1]
            if src.kind=='line' and dst.kind=='line':
                s0,s1=_line(src,value); d0,d1=_line(dst,value); q0,q1=mir(s0),mir(s1)
                return [d0[0]-q0[0],d0[1]-q0[1],d1[0]-q1[0],d1[1]-q1[1]]
            if src.kind in ('circle','arc') and dst.kind==src.kind:
                q=mir((_num(src.data['cx'],value),_num(src.data['cy'],value)))
                return [_num(dst.data['cx'],value)-q[0],_num(dst.data['cy'],value)-q[1],_num(dst.data['r'],value)-_num(src.data['r'],value)]
    except Exception:
        pass
    err,_=constraint_error(sm,c,value)
    return [float(err)]


def _dimension_residual_component(sm,dim,value):
    tag=dim.get('entity_id') or dim.get('entityId'); ent=next((e for e in sm.entities if e.tag==tag),None)
    if ent is None:return math.inf
    target=float(value(dim.get('value_mm',dim.get('value',0))))
    if ent.kind=='line':return _len(ent,value)-target
    if ent.kind in ('circle','arc'):return _num(ent.data['r'],value)-target
    return math.inf

def _health_vector(sm,value):
    vals=[]
    for c in sm.constraints:
        err,_=constraint_error(sm,c,value); vals.append(float(err))
    for d in getattr(sm,'dimensions',[]):
        vals.append(float(_dimension_error(sm,d,value)))
    return vals

def _health_score(sm,value):
    vals=_health_vector(sm,value)
    if not vals:return (0.0,0.0)
    if any(not math.isfinite(x) for x in vals):return (math.inf,math.inf)
    return (max(vals),sum(vals))

def _numeric_dofs(sm):
    refs=[]
    # Projected geometry is a derived reference and must not be moved by the solver.
    for ent in sm.entities:
        if ent.data.get('projected'):continue
        fields=()
        if ent.kind=='line':fields=('x1','y1','x2','y2')
        elif ent.kind in ('circle','arc'):fields=('cx','cy','r')
        for key in fields:
            if isinstance(ent.data.get(key),(int,float)):
                refs.append((ent,key))
    return refs

def _try_global_fallback(sm,value,iterations=24):
    baseline_state=[copy.deepcopy(e.data) for e in sm.entities]
    baseline_errors=_health_vector(sm,value)
    baseline_score=_health_score(sm,value)
    if baseline_score[0] <= 1e-6 or not math.isfinite(baseline_score[0]):
        return False
    refs=_numeric_dofs(sm)
    if not refs:return False
    x0=np.asarray([float(e.data[k]) for e,k in refs],dtype=float)
    # Scale regularization so an under-constrained sketch stays close to the
    # deterministic legacy solution instead of drifting arbitrarily.
    scale=max(1.0,float(np.max(np.abs(x0))) if x0.size else 1.0)

    def put(x):
        for val,(ent,key) in zip(x,refs):
            if key=='r':val=max(1e-9,float(val))
            ent.data[key]=float(val)

    def raw_residual():
        vals=[]
        for c in sm.constraints:
            vals.extend(float(v) for v in _constraint_residual_components(sm,c,value))
        for d in getattr(sm,'dimensions',[]):vals.append(float(_dimension_residual_component(sm,d,value)))
        return np.asarray(vals,dtype=float)

    def residual(x):
        put(x); r=raw_residual()
        if not np.all(np.isfinite(r)):return None
        # Very weak anchor; only resolves null-space drift.
        reg=(x-x0)*(1e-7/scale)
        return np.concatenate([r,reg])

    best_x=x0.copy(); best_score=baseline_score; lam=1e-3
    r=residual(best_x)
    if r is None:
        for e,data in zip(sm.entities,baseline_state):e.data=copy.deepcopy(data)
        return False
    for _ in range(max(1,int(iterations))):
        m=len(r); n=len(best_x); J=np.zeros((m,n),dtype=float)
        for j in range(n):
            h=max(1e-6,abs(best_x[j])*1e-6)
            trial=best_x.copy(); trial[j]+=h; rp=residual(trial)
            trial[j]-=2*h; rm=residual(trial)
            if rp is None or rm is None:
                put(best_x); continue
            J[:,j]=(rp-rm)/(2*h)
        put(best_x)
        A=J.T@J + lam*np.eye(n); g=J.T@r
        try:dx=np.linalg.solve(A,-g)
        except np.linalg.LinAlgError:dx=np.linalg.lstsq(A,-g,rcond=None)[0]
        if not np.all(np.isfinite(dx)) or float(np.linalg.norm(dx))<1e-9:break
        candidate=best_x+dx; rc=residual(candidate)
        if rc is None:
            lam=min(1e8,lam*10.0); put(best_x); continue
        score=_health_score(sm,value)
        if score < best_score:
            best_x=candidate; best_score=score; r=rc; lam=max(1e-9,lam/3.0)
            if best_score[0] <= 1e-6:break
        else:
            lam=min(1e8,lam*10.0); put(best_x)

    put(best_x)
    candidate_errors=_health_vector(sm,value)
    # Hard non-regression gate: every relation that legacy had healthy remains healthy.
    preserved=True
    for before,after in zip(baseline_errors,candidate_errors):
        if math.isfinite(before) and before<=1e-6 and (not math.isfinite(after) or after>1e-6):
            preserved=False; break
    improved=preserved and best_score < baseline_score
    if not improved:
        for e,data in zip(sm.entities,baseline_state):e.data=copy.deepcopy(data)
        return False
    return True

