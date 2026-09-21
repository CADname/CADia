from __future__ import annotations
import hashlib, json, math
from typing import Any
import numpy as np


def _r(v,n=6):
    try:return round(float(v),n)
    except Exception:return 0.0


def _hash(prefix,data):
    raw=json.dumps(data,sort_keys=True,separators=(',',':'))
    return f"{prefix}_{hashlib.sha1(raw.encode()).hexdigest()[:14]}"


def _direction_name(v):
    a=np.asarray(v,float)
    if np.linalg.norm(a)<1e-12:return None
    i=int(np.argmax(np.abs(a))); sign='+' if a[i]>=0 else '-'; return sign+'XYZ'[i]


def _bbox_normalized_point(shape, point, bb=None):
    """Return a dimensionless point in [-1, 1]^3 relative to the current body bbox.

    ``bb`` is accepted so topology indexing can compute the body bounding box once per
    shape instead of once per face/edge.  This is a pure performance optimization; the
    descriptor semantics are unchanged.
    """
    try:
        bb=bb or shape.BoundingBox(); vals=[]
        for value,lo,span in ((point.x,bb.xmin,bb.xlen),(point.y,bb.ymin,bb.ylen),(point.z,bb.zmin,bb.zlen)):
            vals.append(0.0 if abs(span)<1e-12 else 2.0*(float(value)-float(lo))/float(span)-1.0)
        return [_r(x,5) for x in vals]
    except Exception:
        return [0.0,0.0,0.0]


def _shape_token(shape, faces, edges, vertices):
    """Cheap guard against an unexpectedly replaced wrapped shape on the same wrapper."""
    try:h=int(shape.hashCode())
    except Exception:h=id(getattr(shape,'wrapped',shape))
    return (h,len(faces),len(edges),len(vertices))


def _topology_index(shape):
    """Build/cache the expensive body topology index once for a CadQuery shape.

    v9.2 used an edge -> every face -> every face-edge scan.  Threaded fasteners and
    gears can contain thousands of edges, making that O(E*F*Ef) path dominate selection
    and persistent-reference work.  Here each face edge is bucketed by OCCT hashCode and
    verified with ``isSame`` before adjacency is recorded.  Hash collisions therefore do
    not change correctness, while typical work becomes close to O(total face-edges).

    The cache is attached to the immutable-ish CadQuery Shape wrapper.  Boolean/modeling
    operations in this engine return a new wrapper, so a new body gets a new cache.  The
    token check is an additional safety guard.
    """
    if shape is None:return None
    cached=getattr(shape,'_standalonecad_topology_index',None)
    try: current_hash=int(shape.hashCode())
    except Exception: current_hash=id(getattr(shape,'wrapped',shape))
    # CadQuery/OCCT modeling operations return a new Shape wrapper.  If a cache is
    # attached to this wrapper and its OCCT hash is unchanged, no enumeration is needed.
    if isinstance(cached,dict) and cached.get('shape_hash')==current_hash:
        return cached
    faces=list(shape.Faces()); edges=list(shape.Edges()); vertices=list(shape.Vertices())
    token=_shape_token(shape,faces,edges,vertices)
    bb=shape.BoundingBox()
    edge_buckets={}
    for i,e in enumerate(edges):
        try:key=int(e.hashCode())
        except Exception:key=hash(e)
        edge_buckets.setdefault(key,[]).append(i)

    edge_adj=[[] for _ in edges]
    face_edge_geoms=[]
    face_edge_counts=[]
    for f in faces:
        fedges=list(f.Edges()); face_edge_counts.append(len(fedges)); face_edge_geoms.append(sorted(str(e.geomType()) for e in fedges))
        fgeom=str(f.geomType())
        for fe in fedges:
            try:key=int(fe.hashCode())
            except Exception:key=hash(fe)
            found=None
            for i in edge_buckets.get(key,()):
                try:
                    if edges[i].isSame(fe):found=i; break
                except Exception:pass
            # Defensive fallback for an unusual OCCT hash mismatch.  It is intentionally
            # only used for the exceptional case, never as the normal adjacency path.
            if found is None:
                for i,e in enumerate(edges):
                    try:
                        if e.isSame(fe):found=i; break
                    except Exception:pass
            if found is not None:edge_adj[found].append(fgeom)

    index={
        'token':token,'shape_hash':current_hash,'bb':bb,'faces':faces,'edges':edges,'vertices':vertices,
        'edge_adj':edge_adj,'face_edge_geoms':face_edge_geoms,'face_edge_counts':face_edge_counts,
        'face_records':None,'edge_records':None,'vertex_records':None,
    }
    try:shape._standalonecad_topology_index=index
    except Exception:pass
    return index


def face_records(shape):
    if shape is None:return []
    idx=_topology_index(shape)
    if idx.get('face_records') is not None:return idx['face_records']
    faces=idx['faces']; bb=idx['bb']; out=[]
    centers=[f.Center() for f in faces]
    extremes={
        '+X':max((c.x for c in centers),default=0), '-X':min((c.x for c in centers),default=0),
        '+Y':max((c.y for c in centers),default=0), '-Y':min((c.y for c in centers),default=0),
        '+Z':max((c.z for c in centers),default=0), '-Z':min((c.z for c in centers),default=0),
    }
    alias_map={'+Z':['top_face','max_z_face'],'-Z':['bottom_face','min_z_face'], '+X':['max_x_face'], '-X':['min_x_face'], '+Y':['max_y_face'], '-Y':['min_y_face']}
    for i,f in enumerate(faces):
        c=centers[i]; geom=str(f.geomType()); area=float(f.Area())
        edge_geoms=idx['face_edge_geoms'][i]
        rec={'index':i,'geom':geom,'center':[_r(c.x),_r(c.y),_r(c.z)],'center_norm':_bbox_normalized_point(shape,c,bb),'area':_r(area,5),'edge_count':idx['face_edge_counts'][i],'edge_geom_signature':edge_geoms}
        try:
            n=f.normalAt(c); nv=[_r(n.x),_r(n.y),_r(n.z)]; rec['normal']=nv; dn=_direction_name(nv)
        except Exception: rec['normal']=[0,0,0]; dn=None
        rec['direction']=dn
        if geom=='CYLINDER':
            try:
                surf=f._geomAdaptor(); cyl=surf.Cylinder(); rec['radius_mm']=_r(cyl.Radius(),6)
                ax=cyl.Axis().Direction(); rec['axis']=[_r(ax.X()),_r(ax.Y()),_r(ax.Z())]; rec['axis_direction']=_direction_name(rec['axis'])
            except Exception: pass
        semantics=[]
        if geom=='PLANE' and dn in extremes:
            coord={'X':c.x,'Y':c.y,'Z':c.z}[dn[1]]
            if abs(coord-extremes[dn])<1e-5: semantics=list(alias_map.get(dn,[]))
        rec['semantic']=semantics[0] if semantics else None
        rec['semantics']=semantics
        sig={k:rec[k] for k in ('geom','center','area','normal','edge_count','edge_geom_signature')}
        if 'radius_mm' in rec:sig['radius_mm']=rec['radius_mm']; sig['axis']=rec.get('axis')
        rec['id']=_hash('F',sig)
        out.append(rec)
    idx['face_records']=out
    return out


def edge_records(shape):
    if shape is None:return []
    idx=_topology_index(shape)
    if idx.get('edge_records') is not None:return idx['edge_records']
    out=[]; bb=idx['bb']
    for i,e in enumerate(idx['edges']):
        c=e.Center(); geom=str(e.geomType()); adj=sorted(idx['edge_adj'][i])
        rec={'index':i,'geom':geom,'center':[_r(c.x),_r(c.y),_r(c.z)],'center_norm':_bbox_normalized_point(shape,c,bb),'length':_r(e.Length(),5),'adjacent_face_geoms':adj,'adjacent_face_count':len(adj)}
        verts=e.Vertices(); rec['vertices']=[[_r(v.Center().x),_r(v.Center().y),_r(v.Center().z)] for v in verts]
        if geom=='LINE' and len(verts)>=2:
            a=np.asarray(rec['vertices'][0],float); b=np.asarray(rec['vertices'][-1],float); rec['direction']=_direction_name(b-a)
        else: rec['direction']=None
        try:
            if geom=='CIRCLE': rec['radius_mm']=_r(e.radius(),6)
        except Exception: pass
        sig={k:rec[k] for k in ('geom','center','length','vertices','adjacent_face_geoms','adjacent_face_count')}; rec['id']=_hash('E',sig); out.append(rec)
    idx['edge_records']=out
    return out


def vertex_records(shape):
    if shape is None:return []
    idx=_topology_index(shape)
    if idx.get('vertex_records') is not None:return idx['vertex_records']
    out=[]
    for i,v in enumerate(idx['vertices']):
        c=v.Center(); sig={'point':[_r(c.x),_r(c.y),_r(c.z)]}; out.append({'id':_hash('V',sig),'index':i,**sig})
    idx['vertex_records']=out
    return out


def _ref_semantics(ref):
    if isinstance(ref,str): return [ref]
    if not isinstance(ref,dict): return []
    vals=[]
    if ref.get('semantic'): vals.append(str(ref['semantic']))
    vals.extend(str(x) for x in (ref.get('semantics') or []))
    return vals


def make_face_ref(shape, ref):
    """Capture a persistent descriptor for a current face id/semantic/selector."""
    face=match_face(shape,ref)
    idx=_topology_index(shape)
    for r,f in zip(face_records(shape),idx['faces']):
        if f.isSame(face): return {k:r.get(k) for k in ('id','semantic','semantics','geom','direction','center','center_norm','area','normal','radius_mm','axis_direction','edge_count','edge_geom_signature') if r.get(k) is not None}
    raise ValueError(f'Face reference not found: {ref}')


def make_edge_ref(shape, ref):
    edge=match_edge(shape,ref)
    idx=_topology_index(shape)
    for r,e in zip(edge_records(shape),idx['edges']):
        if e.isSame(edge): return {k:r.get(k) for k in ('id','geom','direction','center','center_norm','length','vertices','radius_mm','adjacent_face_geoms','adjacent_face_count') if r.get(k) is not None}
    raise ValueError(f'Edge reference not found: {ref}')


def make_vertex_ref(shape, ref):
    """Capture a persistent descriptor for a current vertex id.

    The exact id remains the primary lookup key.  ``point_norm`` is only used as a
    fallback after upstream topology changes, so already-valid vertex references keep
    their legacy path and cost.
    """
    v=match_vertex_id(shape,ref)
    bb=shape.BoundingBox(); c=v.Center()
    point=[_r(c.x),_r(c.y),_r(c.z)]
    return {
        'id': next((r['id'] for r,vv in zip(vertex_records(shape),_topology_index(shape)['vertices']) if vv.isSame(v)), str(ref)),
        'point': point,
        'point_norm': _bbox_normalized_point(shape,c,bb),
    }


def match_face(shape,ref):
    records=face_records(shape); faces=_topology_index(shape)['faces']
    # 1. Exact hash or semantic aliases.
    rid=ref.get('id') if isinstance(ref,dict) else ref
    sems=_ref_semantics(ref)
    for r,f in zip(records,faces):
        if r['id']==rid or any(x==r.get('semantic') or x in (r.get('semantics') or []) for x in sems): return f
    if not isinstance(ref,dict): raise ValueError(f'Face reference not found: {ref}')
    # 2. Persistent-topology fallback.  Match topology class/orientation first, then
    # choose the closest relative bbox location.  Absolute dimensions are only a weak tie-breaker.
    candidates=[]
    for r,f in zip(records,faces):
        if ref.get('geom') and r.get('geom')!=ref.get('geom'): continue
        if ref.get('direction') and r.get('direction')!=ref.get('direction'): continue
        if ref.get('axis_direction') and r.get('axis_direction')!=ref.get('axis_direction'): continue
        if ref.get('radius_mm') is not None and r.get('radius_mm') is not None:
            rr=float(ref['radius_mm']); tol=max(0.05,abs(rr)*0.08)
            if abs(float(r['radius_mm'])-rr)>tol: continue
        if ref.get('edge_count') is not None and r.get('edge_count')!=ref.get('edge_count'):continue
        if ref.get('edge_geom_signature') and r.get('edge_geom_signature')!=ref.get('edge_geom_signature'):continue
        score=0.0
        if ref.get('center_norm') is not None:
            score += float(np.linalg.norm(np.asarray(r.get('center_norm',[0,0,0]),float)-np.asarray(ref['center_norm'],float)))*20.0
        if ref.get('normal') is not None:
            a=np.asarray(r.get('normal',[0,0,0]),float); b=np.asarray(ref['normal'],float)
            if np.linalg.norm(a)>0 and np.linalg.norm(b)>0: score += (1.0-abs(float(np.dot(a/np.linalg.norm(a),b/np.linalg.norm(b)))))*10.0
        if ref.get('area') not in (None,0): score += abs(math.log(max(float(r.get('area',1e-12)),1e-12)/max(float(ref['area']),1e-12)))
        candidates.append((score,r,f))
    if not candidates: raise ValueError(f'Face reference could not be rebound after topology update: {ref}')
    candidates.sort(key=lambda x:x[0])
    if len(candidates)>1 and abs(candidates[1][0]-candidates[0][0])<1e-8:
        raise ValueError(f'Ambiguous face reference after topology update; refine/reselect. candidates={[x[1].get("id") for x in candidates[:4]]}')
    return candidates[0][2]


def match_edge(shape,ref):
    records=edge_records(shape); edges=_topology_index(shape)['edges']
    rid=ref.get('id') if isinstance(ref,dict) else ref
    for r,e in zip(records,edges):
        if r['id']==rid:return e
    if not isinstance(ref,dict): raise ValueError(f'Edge reference not found: {ref}')
    candidates=[]
    for r,e in zip(records,edges):
        if ref.get('geom') and r.get('geom')!=ref.get('geom'): continue
        if ref.get('direction') and r.get('direction')!=ref.get('direction'): continue
        if ref.get('radius_mm') is not None and r.get('radius_mm') is not None:
            rr=float(ref['radius_mm']); tol=max(0.05,abs(rr)*0.08)
            if abs(float(r['radius_mm'])-rr)>tol: continue
        if ref.get('adjacent_face_count') is not None and r.get('adjacent_face_count')!=ref.get('adjacent_face_count'):continue
        if ref.get('adjacent_face_geoms') and r.get('adjacent_face_geoms')!=ref.get('adjacent_face_geoms'):continue
        score=0.0
        if ref.get('center_norm') is not None:
            score += float(np.linalg.norm(np.asarray(r.get('center_norm',[0,0,0]),float)-np.asarray(ref['center_norm'],float)))*20.0
        if ref.get('length') not in (None,0): score += abs(math.log(max(float(r.get('length',1e-12)),1e-12)/max(float(ref['length']),1e-12)))
        candidates.append((score,r,e))
    if not candidates: raise ValueError(f'Edge reference could not be rebound after topology update: {ref}')
    candidates.sort(key=lambda x:x[0])
    if len(candidates)>1 and abs(candidates[1][0]-candidates[0][0])<1e-8:
        raise ValueError(f'Ambiguous edge reference after topology update; refine/reselect. candidates={[x[1].get("id") for x in candidates[:4]]}')
    return candidates[0][2]


def match_edge_ids(shape,ids):
    return [match_edge(shape,x) for x in ids]


def match_vertex_id(shape,ref):
    recs=vertex_records(shape); verts=_topology_index(shape)['vertices']
    rid=ref.get('id') if isinstance(ref,dict) else ref
    for r,v in zip(recs,verts):
        if r['id']==rid:return v
    if not isinstance(ref,dict):
        raise ValueError(f'Vertex reference not found: {ref}')
    # Persistent fallback: relative bbox location survives uniform/parametric size
    # changes substantially better than an absolute point.  Ambiguous ties are
    # rejected rather than silently selecting the wrong topology.
    target_norm=ref.get('point_norm')
    target_abs=ref.get('point')
    candidates=[]
    bb=shape.BoundingBox()
    for r,v in zip(recs,verts):
        c=v.Center(); score=0.0
        if target_norm is not None:
            score += float(np.linalg.norm(np.asarray(_bbox_normalized_point(shape,c,bb),float)-np.asarray(target_norm,float)))*20.0
        if target_abs is not None:
            diag=max(1e-9,math.sqrt(bb.xlen**2+bb.ylen**2+bb.zlen**2))
            score += float(np.linalg.norm(np.asarray([c.x,c.y,c.z],float)-np.asarray(target_abs,float)))/diag
        candidates.append((score,r,v))
    if not candidates:raise ValueError(f'Vertex reference could not be rebound after topology update: {ref}')
    candidates.sort(key=lambda x:x[0])
    if len(candidates)>1 and abs(candidates[1][0]-candidates[0][0])<1e-8:
        raise ValueError(f'Ambiguous vertex reference after topology update; refine/reselect. candidates={[x[1].get("id") for x in candidates[:4]]}')
    return candidates[0][2]


def select_face(shape,spec):
    if shape is None: raise ValueError('No body')
    if isinstance(spec,str): return match_face(shape,spec)
    if not isinstance(spec,dict): raise ValueError('Face selector must be an object')
    # Descriptor refs from persisted feature history take precedence over selector dictionaries.
    if spec.get('id') or spec.get('semantic') or spec.get('semantics'):
        try:return match_face(shape,spec)
        except ValueError: pass
    kind=str(spec.get('kind','planar')).lower(); records=face_records(shape); faces=_topology_index(shape)['faces']; candidates=[]
    if kind=='planar':
        want=spec.get('normal'); extreme=str(spec.get('extreme','max')).lower(); tol=math.cos(math.radians(float(spec.get('tolerance_deg',5.0))))
        dmap={'+X':np.array([1,0,0]),'-X':np.array([-1,0,0]),'+Y':np.array([0,1,0]),'-Y':np.array([0,-1,0]),'+Z':np.array([0,0,1]),'-Z':np.array([0,0,-1])}
        if want not in dmap: raise ValueError('planar selector normal must be +X|-X|+Y|-Y|+Z|-Z')
        w=dmap[want]
        for r,f in zip(records,faces):
            if r['geom']!='PLANE':continue
            n=np.asarray(r.get('normal',[0,0,0]),float)
            if np.linalg.norm(n)>0 and np.dot(n/np.linalg.norm(n),w)>=tol:candidates.append((r,f))
        if candidates:
            vals=[float(np.dot(np.asarray(r['center']),w)) for r,_ in candidates]; target=(max(vals) if extreme=='max' else min(vals)); eps=max(1e-6,1e-6*max(1,abs(target)))
            candidates=[rf for rf,v in zip(candidates,vals) if abs(v-target)<=eps]
    elif kind=='cylindrical':
        rad=spec.get('radius_mm'); axis=spec.get('axis'); rtol=float(spec.get('radius_tol_mm',0.01)); dmap={'+X':np.array([1,0,0]),'-X':np.array([-1,0,0]),'+Y':np.array([0,1,0]),'-Y':np.array([0,-1,0]),'+Z':np.array([0,0,1]),'-Z':np.array([0,0,-1])}
        for r,f in zip(records,faces):
            if r['geom']!='CYLINDER':continue
            if rad is not None and abs(float(r.get('radius_mm',1e99))-float(rad))>rtol:continue
            if axis in dmap:
                av=np.asarray(r.get('axis',[0,0,0]),float)
                if np.linalg.norm(av)==0 or abs(float(np.dot(av/np.linalg.norm(av),dmap[axis])))<math.cos(math.radians(float(spec.get('tolerance_deg',5)))):continue
            candidates.append((r,f))
    else: raise ValueError('selector kind must be planar or cylindrical')
    near=spec.get('near_mm')
    if len(candidates)>1 and near is not None:
        q=np.asarray(near,float); candidates.sort(key=lambda rf:float(np.linalg.norm(np.asarray(rf[0]['center'])-q)))
        if len(candidates)>=2:
            d0=np.linalg.norm(np.asarray(candidates[0][0]['center'])-q); d1=np.linalg.norm(np.asarray(candidates[1][0]['center'])-q)
            if abs(d0-d1)>1e-8:return candidates[0][1]
    if len(candidates)!=1:
        summary=[{'id':r['id'],'center':r['center'],'normal':r.get('normal'),'radius_mm':r.get('radius_mm'),'axis':r.get('axis')} for r,_ in candidates]
        raise ValueError(f'Face selector matched {len(candidates)} faces; refine with near_mm. candidates={summary}')
    return candidates[0][1]


def face_frame(face):
    c=face.Center(); geom=str(face.geomType())
    if geom=='PLANE':
        n=face.normalAt(c); return {'kind':'plane','origin':[c.x,c.y,c.z],'direction':[n.x,n.y,n.z]}
    if geom=='CYLINDER':
        cyl=face._geomAdaptor().Cylinder(); ax=cyl.Axis(); loc=ax.Location(); d=ax.Direction(); return {'kind':'axis','origin':[loc.X(),loc.Y(),loc.Z()],'direction':[d.X(),d.Y(),d.Z()]}
    raise ValueError(f'Unsupported iMate face geometry: {geom}')
