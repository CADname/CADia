from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
import copy, json, threading, math, re, base64, tempfile, os
import numpy as np
import cadquery as cq
from cadquery import importers
from .expressions import eval_expr
from .topology import face_records, edge_records, vertex_records, match_edge_ids, match_edge, match_face, match_vertex_id, make_edge_ref, make_face_ref, make_vertex_ref, select_face, face_frame
from .assembly import Occurrence, AssemblyConstraint, default_interfaces, load_component, transform_shape, solve_constraint, find_interface, transformed_frame, constraint_residual, assembly_degrees_of_freedom, try_global_constraint_fallback
from .joints import canonical_joint_type, AssemblyJoint, joint_residual, joint_state, validate_joint_definition, assembly_kinematic_degrees_of_freedom, solve_assembly_relationships, assembly_relationship_diagnostics
from .gears import involute_spur_gear
from .mechanical_accelerator import stepped_shaft, parallel_key, rolling_bearing, compression_spring, belleville_spring, v_pulley, advanced_gear, flange_coupling, lead_screw_nut
from .standard_parts import metric_hex_bolt, metric_hex_nut, metric_washer, external_metric_thread, internal_metric_thread_cut, metric_pitch
from .materials import density_for, normalize_material
from .sketch_solver import solve_constraints, constraint_error as sketch_constraint_error
from .sheet_metal import make_base as make_sheet_base, make_flange as make_sheet_flange, validate_rule as validate_sheet_rule, flat_pattern_sketch, flat_pattern_metadata
from .inventor_origin import ORIGIN_PLANES, ORIGIN_AXES, CENTER_POINT_NAME, canonical_plane_name, canonical_axis_name, canonical_point_name

@dataclass
class Parameter:
    name:str; expression:str; unit:str='mm'; kind:str='user'

@dataclass
class SketchEntity:
    kind:str; tag:str; data:dict[str,Any]

@dataclass
class SketchModel:
    name:str; plane:str='XY'; entities:list[SketchEntity]=field(default_factory=list); constraints:list[dict[str,Any]]=field(default_factory=list); dimensions:list[dict[str,Any]]=field(default_factory=list); closed:bool=False; plane_ref:Any=None

@dataclass
class FeatureRecord:
    name:str; kind:str; params:dict[str,Any]; operation:str='join'; suppressed:bool=False

class CadDocument:
    def __init__(self):
        self.lock=threading.RLock(); self.reset()

    def reset(self,doc_type='part'):
        self.title='Untitled'; self.path=None; self.doc_type=doc_type; self.units='mm'; self.material='Generic'
        self.parameters:dict[str,Parameter]={}; self.properties:dict[str,Any]={'Part Number':'','Description':'','Author':''}
        self.property_sets:dict[str,dict[str,Any]]={'Design Tracking Properties':self.properties,'Inventor User Defined Properties':{}}
        self.sketches:dict[str,SketchModel]={}; self.sketches3d:dict[str,dict[str,Any]]={}; self.features:list[FeatureRecord]=[]; self.work_planes:dict[str,dict]={}; self.work_axes:dict[str,dict]={}; self.work_points:dict[str,dict]={}; self.imates:dict[str,dict]={}
        self.shape=None; self.imported_shape=None; self.import_source=None; self._imported_brep_b64=None
        self.occurrences:dict[str,Occurrence]={}; self.constraints:list[AssemblyConstraint]=[]; self.joints:list[AssemblyJoint]=[]
        # Isolated native sheet-metal subsystem.  Normal part modeling never reads this
        # state, so adding sheet-metal support cannot alter an existing successful part.
        self.sheet_metal:dict[str,Any]|None=None
        self.view_orientation='iso_top_right'; self.view_fit=True
        # Runtime-only Inventor-style object façade caches.  These wrappers delegate to
        # the existing backend and are intentionally excluded from serialization.
        self._component_definition_facade=None; self._reference_manager_facade=None
        # Pure runtime caches. They are never serialized and never change model semantics.
        self._numeric_param_cache_key=None; self._numeric_param_cache_vals=None
        self._feature_shape_cache=[]
        self._association_refresh_enabled=False
        self.dirty=False

    @property
    def component_definition(self):
        if self._component_definition_facade is None:
            from .inventor_facade import component_definition_for
            self._component_definition_facade=component_definition_for(self)
        return self._component_definition_facade

    @property
    def reference_manager(self):
        if self._reference_manager_facade is None:
            from .inventor_facade import ReferenceManager
            self._reference_manager_facade=ReferenceManager(self)
        return self._reference_manager_facade

    def _parameter_cache_key(self):
        # Parameter objects are intentionally mutable for ipt-mcp compatibility.  Derive
        # the key from their actual content so even direct expression edits invalidate
        # the cache safely without relying on every caller to remember an invalidation API.
        return tuple((n,p.expression,p.unit,p.kind) for n,p in self.parameters.items())

    def invalidate_parameter_cache(self):
        self._numeric_param_cache_key=None; self._numeric_param_cache_vals=None

    def mark_dirty(self, value=True):
        self.dirty=bool(value)
        return self.dirty

    def next_model_parameter_name(self):
        used={n for n in self.parameters if re.fullmatch(r"d\d+",str(n))}
        i=0
        while f"d{i}" in used:i+=1
        return f"d{i}"

    def create_model_parameter(self, value, unit='mm'):
        name=self.next_model_parameter_name()
        expr=str(value)
        # Preserve explicit expressions unchanged.  Plain numeric values inherit the
        # declared unit so model parameters are canonicalized into mm/degrees by eval_expr.
        if isinstance(value,(int,float)) and unit:
            expr=f"{value} {unit}"
        self.parameters[name]=Parameter(name,expr,unit,'model')
        self.invalidate_parameter_cache()
        return name

    def get_iproperty(self,set_name,prop_name):
        if set_name in ('Design Tracking Properties','Summary Information','Document Summary Information'):
            bucket=self.property_sets.setdefault(set_name, self.properties if set_name=='Design Tracking Properties' else {})
        else:
            bucket=self.property_sets.setdefault(set_name,{})
        return bucket.get(prop_name)

    def set_iproperty(self,set_name,prop_name,value):
        bucket=self.property_sets.setdefault(set_name,{})
        bucket[prop_name]=value
        if set_name=='Design Tracking Properties':
            self.properties=bucket
        return value

    @staticmethod
    def _shape_to_brep_b64(shape):
        if shape is None:return None
        fd,tmp=tempfile.mkstemp(prefix='standalonecad-embed-',suffix='.brep'); os.close(fd)
        try:
            if not shape.exportBrep(tmp):raise ValueError('Failed to serialize B-Rep')
            return base64.b64encode(Path(tmp).read_bytes()).decode('ascii')
        finally:
            try:Path(tmp).unlink(missing_ok=True)
            except Exception:pass

    @staticmethod
    def _shape_from_brep_b64(payload):
        if not payload:return None
        fd,tmp=tempfile.mkstemp(prefix='standalonecad-embed-',suffix='.brep'); os.close(fd)
        try:
            Path(tmp).write_bytes(base64.b64decode(payload.encode('ascii')))
            return cq.Shape.importBrep(tmp)
        finally:
            try:Path(tmp).unlink(missing_ok=True)
            except Exception:pass

    def numeric_params(self):
        key=self._parameter_cache_key()
        if key==self._numeric_param_cache_key and self._numeric_param_cache_vals is not None:
            return dict(self._numeric_param_cache_vals)
        vals={}; pending=dict(self.parameters)
        for _ in range(len(pending)+3):
            changed=False
            for n,p in list(pending.items()):
                try: vals[n]=eval_expr(p.expression,vals); del pending[n]; changed=True
                except ValueError: pass
            if not changed: break
        if pending: raise ValueError(f"Unresolved/cyclic parameters: {', '.join(pending)}")
        self._numeric_param_cache_key=key; self._numeric_param_cache_vals=dict(vals)
        return vals

    def value(self,x): return eval_expr(x,self.numeric_params())

    def _plane(self,name,fallback_ref=None):
        if isinstance(name,str):name=canonical_plane_name(name,allow_compat=True)
        n=str(name).upper() if not isinstance(name,dict) else ''
        if name in ORIGIN_PLANES:
            # CadQuery's named planes are the kernel representation for Inventor's
            # three fixed origin WorkPlanes.
            return name[:2]
        wp=self.work_planes.get(name) if isinstance(name,str) else None
        if wp:
            # Work features are parametric: when their source geometry changes during a
            # history rebuild, resolve the stored references against the current body.
            try: wp=self._refresh_work_plane(name)
            except Exception: wp=self.work_planes[name]
            return cq.Plane(origin=tuple(wp['origin']),xDir=tuple(wp.get('x_dir',[1,0,0])),normal=tuple(wp['direction']))
        # ipt-mcp accepts a work-plane or planar face reference. Resolve stable/semantic face refs here.
        if self.shape is not None:
            legacy_exc=None
            try:
                face=match_face(self.shape,name); fr=face_frame(face)
                if fr['kind']!='plane': raise ValueError(f'Face is not planar: {name}')
                normal=fr['direction']
                xdir=(1,0,0) if abs(float(normal[0])) < 0.9 else (0,1,0)
                return cq.Plane(origin=tuple(fr['origin']),xDir=xdir,normal=tuple(normal))
            except ValueError as exc:
                legacy_exc=exc
            # Failure-only persistent topology fallback.  Exact/named references always
            # run first; this path is invisible to an already-successful sketch.
            if fallback_ref is not None:
                try:
                    face=match_face(self.shape,fallback_ref); fr=face_frame(face)
                    if fr['kind']!='plane': raise ValueError(f'Face is not planar: {fallback_ref}')
                    normal=fr['direction']; xdir=(1,0,0) if abs(float(normal[0])) < 0.9 else (0,1,0)
                    return cq.Plane(origin=tuple(fr['origin']),xDir=xdir,normal=tuple(normal))
                except Exception:
                    pass
            if legacy_exc is not None: raise legacy_exc
        raise ValueError(f'Unknown plane/planar face reference: {name}')

    def plane_object(self,name,fallback_ref=None):
        p=self._plane(name,fallback_ref)
        return cq.Plane.named(p) if isinstance(p,str) else p

    def build_sketch(self,sm:SketchModel):
        # Associative projection is a monotonic fallback only.  Normal successful
        # rebuilds keep the exact legacy copied-projection behavior.  If a legacy
        # rebuild fails, rebuild() temporarily enables this refresh path and retries.
        if getattr(self,'_association_refresh_enabled',False):
            self._refresh_projected_geometry(sm)
        # Re-apply document-level constraints before every feature rebuild. This keeps
        # downstream features parametric after dimensions/parameters are edited instead
        # of treating constraints as one-shot UI gestures.
        solve_constraints(sm, self.value)
        s=cq.Sketch(); vals=self.numeric_params()
        for ent in sm.entities:
            d=ent.data; k=ent.kind
            if k=='line': s=s.segment((eval_expr(d['x1'],vals),eval_expr(d['y1'],vals)),(eval_expr(d['x2'],vals),eval_expr(d['y2'],vals)),tag=ent.tag)
            elif k=='circle':
                x,y,r=[eval_expr(d[q],vals) for q in ('cx','cy','r')]; s=s.push([(x,y)]).circle(r,tag=ent.tag).reset()
            elif k=='arc':
                cx,cy,r,a1,a2=[eval_expr(d[q],vals) for q in ('cx','cy','r','start_deg','end_deg')]
                # Follow the requested CCW sweep even when it crosses 360 degrees.
                sweep=(a2-a1)%360.0
                if abs(sweep)<1e-12 and abs(a2-a1)>1e-12:sweep=360.0
                am=a1+sweep/2.0
                p1=(cx+r*math.cos(math.radians(a1)),cy+r*math.sin(math.radians(a1))); pm=(cx+r*math.cos(math.radians(am)),cy+r*math.sin(math.radians(am))); p2=(cx+r*math.cos(math.radians(a1+sweep)),cy+r*math.sin(math.radians(a1+sweep)))
                s=s.arc(p1,pm,p2,tag=ent.tag)
            elif k=='spline':
                pts=[(eval_expr(q[0],vals),eval_expr(q[1],vals)) for q in d.get('points',[])]
                if len(pts)>=2:s=s.spline(pts,tangents=None,periodic=bool(d.get('periodic',False)),tag=ent.tag)
        # Apply the subset CadQuery's solver supports directly; preserve every requested constraint in metadata.
        for c in sm.constraints:
            try:
                typ=c['type'].lower(); tags=c.get('entity_ids') or c.get('tags') or []
                if typ=='horizontal' and len(tags)==1: s=s.constrain(tags[0],'Orientation',(1,0))
                elif typ=='vertical' and len(tags)==1: s=s.constrain(tags[0],'Orientation',(0,1))
                elif typ in ('coincident','parallel','perpendicular','tangent','concentric','equal','collinear','symmetric'):
                    # CadQuery does not expose these Inventor-style names as stable public constraints across all entity pairs.
                    # They remain explicit document constraints and are validated geometrically by sketch_constraint_health().
                    pass
            except Exception: pass
        try:s=s.solve()
        except Exception:pass
        # Convert connected closed edges into profile faces/wires consumable by Workplane.extrude/revolve.
        try:s=s.assemble()
        except Exception:pass
        return s

    def profile_face_world(self, sm:SketchModel):
        sk=self.build_sketch(sm)
        raw=getattr(sk,'_faces',None)
        if raw is None: raise ValueError(f'Sketch has no closed profile: {sm.name}')
        faces=raw.Faces() if hasattr(raw,'Faces') and not isinstance(raw,cq.Face) else [raw]
        if len(faces)!=1: raise ValueError(f'{sm.name}: sweep/loft currently requires exactly one closed profile')
        return faces[0].transformShape(self.plane_object(sm.plane,sm.plane_ref).rG)

    def sketch_constraint_health(self,sm:SketchModel):
        solve_constraints(sm, self.value)
        out=[]
        for c in sm.constraints:
            err,reason=sketch_constraint_error(sm,c,self.value)
            ok=math.isfinite(err) and err < 1e-5
            out.append({
                'type':str(c.get('type','')).lower(),
                'entity_ids':c.get('entity_ids') or c.get('tags') or [],
                'health':'up_to_date' if ok else 'sick',
                'error':None if not math.isfinite(err) else float(err),
                'reason':reason,
            })
        return out

    def _rebuild_once(self, old, start_index=0, *, associative_projection=False):
        start=max(0,min(int(start_index or 0),len(old)))
        can_resume=(start>0 and len(self._feature_shape_cache)>=start and not associative_projection)
        if not can_resume:
            start=0; self.shape=self.imported_shape; self.features=[]; self._feature_shape_cache=[]; self.sheet_metal=None
        else:
            self.shape=self._feature_shape_cache[start-1]
            self.features=old[:start]
            self._feature_shape_cache=self._feature_shape_cache[:start]
        prior_flag=getattr(self,'_association_refresh_enabled',False)
        self._association_refresh_enabled=bool(associative_projection)
        try:
            for f in old[start:]:
                if f.suppressed:
                    self.features.append(f)
                else:
                    self._apply_feature(f,record=True)
                self._feature_shape_cache.append(self.shape)
        finally:
            self._association_refresh_enabled=prior_flag

    def rebuild(self,start_index=0):
        """Rebuild with a strict legacy-first, fallback-only non-regression policy.

        The established path always runs first.  Only if that path raises does
        StandaloneCAD retry a full rebuild with associative projected-geometry refresh.
        A successful legacy rebuild is never replaced, so existing success behavior and
        performance are preserved while previously failing parametric cases can recover.
        """
        with self.lock:
            if self.doc_type=='assembly': self.rebuild_assembly(); return
            old=list(self.features)
            try:
                self._rebuild_once(old,start_index,associative_projection=False)
                return
            except Exception as legacy_exc:
                # The failed legacy attempt may have partially replayed history. Reset and
                # try the strictly additive associative projection fallback.
                try:
                    self.shape=self.imported_shape; self.features=[]; self._feature_shape_cache=[]
                    self._rebuild_once(old,0,associative_projection=True)
                    return
                except Exception:
                    # Preserve the original legacy error contract; the engine transaction
                    # layer restores the complete pre-command document snapshot.
                    raise legacy_exc

    def add_feature(self,kind,name,params,operation='join'):
        if self.doc_type!='part':raise ValueError('Part feature tools require an active part document')
        rec=FeatureRecord(name or f'{kind.capitalize()}{len(self.features)+1}',kind,copy.deepcopy(params),operation); self._apply_feature(rec,record=True); self._feature_shape_cache.append(self.shape); return rec

    def find_feature(self,name):
        f=next((x for x in self.features if x.name==name),None)
        if f is None:raise ValueError(f'Feature not found: {name}')
        return f

    def edit_feature(self,name,updates):
        f=self.find_feature(name); idx=self.features.index(f); changes=copy.deepcopy(updates or {})
        # Inventor Feature.Definition-style behavior: when a feature field is backed by
        # an existing model parameter (d#), editing the field changes that driving
        # parameter rather than replacing the history link with a literal.  This keeps
        # downstream expressions and subsequent edits parametric.
        for key,value in changes.items():
            if key=='operation':
                f.operation=str(value).lower(); continue
            current=f.params.get(key)
            if isinstance(current,str) and current in self.parameters and isinstance(value,(int,float,str)):
                unit=str(self.parameters[current].unit or '')
                raw=str(value).strip()
                try:
                    float(raw); raw=f'{raw} {unit}' if unit else raw
                except Exception:
                    pass
                self.parameters[current].expression=raw; self.invalidate_parameter_cache()
            else:
                f.params[key]=value
        # Sheet-metal state is intentionally isolated and derived by replaying its own
        # features; editing one of them uses a full replay. Normal part features keep
        # the established incremental rebuild path unchanged.
        self.rebuild(0 if f.kind.startswith('sheet_metal_') else idx); return f

    def _set_feature_numeric_param(self, feature, key, value, unit='mm'):
        """Update one driving feature value while preserving its d# parameter binding."""
        value=float(value)
        current=feature.params.get(key)
        if isinstance(current,str) and current in self.parameters:
            suffix=f' {unit}' if unit else ''
            self.parameters[current].expression=f'{value:g}{suffix}'
            self.invalidate_parameter_cache()
        else:
            feature.params[key]=value
        return value

    def _selection_provenance(self, ref, kind='face'):
        """Inventor CreatedByFeature-like best-effort provenance for final topology.

        The final face/edge is captured as a persistent descriptor and rebound against
        every recorded feature result from oldest to newest.  The first stage where it
        exists is its creation feature for ordinary parametric history.  If topology was
        radically replaced, callers simply fall back to direct editing instead of
        guessing a feature.
        """
        if self.doc_type!='part' or self.shape is None:return None
        maker=make_face_ref if kind=='face' else make_edge_ref
        matcher=match_face if kind=='face' else match_edge
        try:persistent=maker(self.shape,ref)
        except Exception:return None
        for idx,stage in enumerate(self._feature_shape_cache):
            if stage is None or idx>=len(self.features):continue
            try:
                matcher(stage,persistent)
                f=self.features[idx]
                return {'feature_name':f.name,'feature_kind':f.kind,'feature_index':idx,'reference':persistent}
            except Exception:
                continue
        return None

    def selection_provenance(self, ref, kind='face'):
        return copy.deepcopy(self._selection_provenance(ref,kind))

    def _try_history_move_face(self, face_ref, distance_mm):
        """Prefer parameter/history editing for simple terminal faces.

        Positive distance always means movement along the selected face's outward
        normal.  Only cases whose driving parameter can be determined unambiguously are
        handled here; every other case returns None and uses the direct-edit fallback.
        """
        prov=self._selection_provenance(face_ref,'face')
        if not prov:return None
        idx=int(prov['feature_index']); f=self.features[idx]
        try:
            face=match_face(self.shape,face_ref); fr=face_frame(face)
        except Exception:return None
        if str(fr.get('kind','')).lower() not in ('plane','planar'):return None
        n=np.asarray(fr.get('direction') or [0,0,0],float)
        if np.linalg.norm(n)<1e-9:return None
        n=n/np.linalg.norm(n); delta=float(distance_mm)

        def commit_dimension(key,new_value,origin_key=None,origin_value=None):
            if float(new_value)<=1e-6:raise ValueError(f'{f.name}.{key} would become non-positive')
            self._set_feature_numeric_param(f,key,float(new_value),'mm')
            if origin_key is not None and origin_value is not None:f.params[origin_key]=[float(x) for x in origin_value]
            self.rebuild(idx)
            return {'mode':'history','feature_name':f.name,'feature_kind':f.kind,'feature_index':idx,'parameter':key}

        if f.kind=='box':
            axes=[np.asarray([1,0,0],float),np.asarray([0,1,0],float),np.asarray([0,0,1],float)]
            keys=['length_mm','width_mm','height_mm']
            dots=[float(np.dot(n,a)) for a in axes]; axis=int(np.argmax(np.abs(dots)))
            if abs(dots[axis])<0.995:return None
            key=keys[axis]; old=float(self.value(f.params[key])); new=old+delta
            origin=list(f.params.get('origin_mm',[0,0,0])); centered=bool(f.params.get('centered',False))
            if centered:
                origin[axis]=float(origin[axis])+float(n[axis])*delta/2.0
            elif dots[axis]<0:
                origin[axis]=float(origin[axis])-delta
            return commit_dimension(key,new,'origin_mm',origin)

        if f.kind in ('cylinder','cone'):
            axis=np.asarray(f.params.get('axis',[0,0,1]),float)
            if np.linalg.norm(axis)<1e-9:return None
            axis=axis/np.linalg.norm(axis); dot=float(np.dot(n,axis))
            if abs(dot)<0.995:return None
            old=float(self.value(f.params['height_mm'])); new=old+delta
            origin=list(f.params.get('origin_mm',[0,0,0]))
            if dot<0:origin=(np.asarray(origin,float)-axis*delta).tolist()
            return commit_dimension('height_mm',new,'origin_mm',origin)

        if f.kind=='slot':
            dot=float(np.dot(n,np.asarray([0,0,1],float)))
            if abs(dot)<0.995:return None
            old=float(self.value(f.params['depth_mm'])); new=old+delta
            z0=float(self.value(f.params.get('z0_mm',0)))
            if dot<0:z0-=delta
            self._set_feature_numeric_param(f,'depth_mm',new,'mm'); f.params['z0_mm']=z0; self.rebuild(idx)
            return {'mode':'history','feature_name':f.name,'feature_kind':f.kind,'feature_index':idx,'parameter':'depth_mm'}

        if f.kind=='extrude':
            direction=str(f.params.get('direction','positive')).lower()
            if direction=='symmetric':return None
            sm=self.sketches.get(f.params.get('sketch_name'))
            if sm is None:return None
            try:
                plane=self._plane(sm.plane,sm.plane_ref)
                if isinstance(plane,str):
                    axis=np.asarray({'XY':[0,0,1],'XZ':[0,1,0],'YZ':[1,0,0]}[plane.upper()],float)
                else:
                    axis=np.asarray(plane.zDir.toTuple(),float)
                axis=axis/np.linalg.norm(axis)
            except Exception:return None
            terminal=axis if direction!='negative' else -axis
            if float(np.dot(n,terminal))<0.995:return None
            key='distance_mm' if 'distance_mm' in f.params else 'distance'
            old=abs(float(self.value(f.params.get(key,10)))); new=old+delta
            return commit_dimension(key,new)

        return None

    def move_face(self, face_ref, distance_mm, prefer_history=True, name=None):
        if self.doc_type!='part':raise ValueError('move_face requires an active part document')
        if self.shape is None:raise ValueError('No body for move_face')
        persistent=make_face_ref(self.shape,face_ref)
        if abs(float(distance_mm))<1e-9:raise ValueError('distance_mm must be non-zero')
        if prefer_history:
            result=self._try_history_move_face(persistent,float(distance_mm))
            if result is not None:return result
        rec=self.add_feature('move_face',name or f'MoveFace{len(self.features)+1}',{'face_ref':persistent,'distance_mm':float(distance_mm)},'join')
        return {'mode':'direct','feature_name':rec.name,'feature_kind':rec.kind,'feature_index':len(self.features)-1}

    def set_face_diameter(self, face_ref, diameter_mm):
        if self.doc_type!='part':raise ValueError('set_face_diameter requires an active part document')
        if self.shape is None:raise ValueError('No body')
        target=float(diameter_mm)
        if target<=1e-6:raise ValueError('diameter_mm must be > 0')
        persistent=make_face_ref(self.shape,face_ref)
        face=match_face(self.shape,persistent)
        if str(face.geomType()).upper()!='CYLINDER':raise ValueError('set_face_diameter requires a cylindrical face')
        prov=self._selection_provenance(persistent,'face')
        if not prov:raise ValueError('Cylindrical face is not traceable to editable feature history')
        idx=int(prov['feature_index']); f=self.features[idx]
        # Prefer the exact Inventor-style feature definition rather than a destructive
        # direct B-Rep rewrite whenever the diameter is a recorded feature parameter.
        candidate_keys=[]
        if f.kind in ('cylinder','hole','metric_hex_bolt','metric_hex_nut','metric_washer'):candidate_keys=['diameter_mm']
        elif f.kind=='external_thread':candidate_keys=['major_diameter_mm']
        for key in candidate_keys:
            if key in f.params:
                self._set_feature_numeric_param(f,key,target,'mm'); self.rebuild(idx)
                return {'mode':'history','feature_name':f.name,'feature_kind':f.kind,'feature_index':idx,'parameter':key,'diameter_mm':target}
        raise ValueError(f'Selected cylindrical face belongs to {f.kind}, which has no unambiguous editable diameter parameter')

    def _sketch_for_edit(self, name=None):
        if self.doc_type!='part':raise ValueError('Sketch editing requires an active part document')
        if name is None:
            if len(self.sketches)==1:return next(iter(self.sketches.values()))
            raise ValueError('sketch_name is required when more than one sketch exists')
        sm=self.sketches.get(str(name))
        if sm is None:raise ValueError(f'Sketch not found: {name}')
        return sm

    def _earliest_feature_using_sketch(self, sketch_name):
        for i,f in enumerate(self.features):
            p=f.params
            if p.get('sketch_name')==sketch_name or p.get('profile_sketch_name')==sketch_name or sketch_name in (p.get('sketch_names') or []):
                return i
        return None

    def _rebuild_after_sketch_edit(self, sm):
        # Inventor solves the sketch first and then updates dependent features.  Keep the
        # same order here: reject an invalid constraint system before touching B-Rep
        # history, then replay only from the first feature consuming this sketch.
        solve_constraints(sm,self.value)
        sick=[x for x in self.sketch_constraint_health(sm) if x.get('health') not in ('up_to_date','healthy')]
        if sick:
            raise ValueError('Sketch edit produced an unhealthy constraint system: '+ '; '.join(f"{x.get('type')}:{x.get('reason') or x.get('error') or x.get('name','')}" for x in sick[:4]))
        idx=self._earliest_feature_using_sketch(sm.name)
        if idx is not None:self.rebuild(idx)
        return idx

    def _set_sketch_field(self, entity, key, value, unit='mm'):
        current=entity.data.get(key)
        if isinstance(current,str) and current in self.parameters:
            raw=str(value).strip(); p=self.parameters[current]
            try:
                float(raw); raw=f'{raw} {unit or p.unit}' if (unit or p.unit) else raw
            except Exception:pass
            p.expression=raw; self.invalidate_parameter_cache()
        else:
            entity.data[key]=value

    def edit_sketch_entity(self, sketch_name, entity_id, updates):
        sm=self._sketch_for_edit(sketch_name)
        ent=next((e for e in sm.entities if e.tag==entity_id),None)
        if ent is None:raise ValueError(f'Sketch entity not found: {entity_id}')
        changes=copy.deepcopy(updates or {})
        aliases={'radius':'r','radius_mm':'r','startDeg':'start_deg','endDeg':'end_deg'}
        changes={aliases.get(k,k):v for k,v in changes.items()}
        allowed={
            'line':{'x1','y1','x2','y2'},
            'circle':{'cx','cy','r'},
            'arc':{'cx','cy','r','start_deg','end_deg'},
        }.get(ent.kind,set())
        bad=sorted(set(changes)-allowed)
        if bad:raise ValueError(f'Unsupported {ent.kind} sketch edit fields: {bad}; allowed={sorted(allowed)}')
        if not changes:raise ValueError('updates must contain at least one editable sketch field')
        for key,value in changes.items():
            if key=='r' and float(self.value(value) if isinstance(value,str) else value)<=0:raise ValueError('Sketch radius must be > 0')
            self._set_sketch_field(ent,key,value,'deg' if key.endswith('_deg') else 'mm')
        idx=self._rebuild_after_sketch_edit(sm)
        return {'sketch_name':sm.name,'entity_id':ent.tag,'entity_kind':ent.kind,'data':copy.deepcopy(ent.data),'first_dependent_feature_index':idx}

    def delete_sketch_entity(self, sketch_name, entity_id):
        sm=self._sketch_for_edit(sketch_name)
        ent=next((e for e in sm.entities if e.tag==entity_id),None)
        if ent is None:raise ValueError(f'Sketch entity not found: {entity_id}')
        sm.entities=[e for e in sm.entities if e.tag!=entity_id]
        removed_constraints=[c.get('name') for c in sm.constraints if entity_id in (c.get('entity_ids') or c.get('tags') or [])]
        removed_dimensions=[d.get('name') for d in sm.dimensions if entity_id in (d.get('entity_id'),d.get('entityId'))]
        # Inventor removes constraints owned by a deleted sketch entity.  Keep their model
        # parameters as harmless orphan parameters instead of risking dependent expression
        # breakage elsewhere in the document.
        sm.constraints=[c for c in sm.constraints if entity_id not in (c.get('entity_ids') or c.get('tags') or [])]
        sm.dimensions=[d for d in sm.dimensions if entity_id not in (d.get('entity_id'),d.get('entityId'))]
        idx=self._rebuild_after_sketch_edit(sm)
        return {'sketch_name':sm.name,'deleted_entity':entity_id,'removed_constraints':[x for x in removed_constraints if x], 'removed_dimensions':[x for x in removed_dimensions if x], 'first_dependent_feature_index':idx}

    def edit_sketch_dimension(self, sketch_name, dimension_name, value_mm):
        sm=self._sketch_for_edit(sketch_name)
        dim=next((x for x in sm.dimensions if x.get('name')==dimension_name),None)
        if dim is None:raise ValueError(f'Sketch dimension not found: {dimension_name}')
        target=float(value_mm)
        if target<=0:raise ValueError('Sketch driving dimension value must be > 0')
        pname=dim.get('value_mm') if isinstance(dim.get('value_mm'),str) and dim.get('value_mm') in self.parameters else dim.get('name')
        if not pname or pname not in self.parameters:raise ValueError(f'Sketch dimension has no editable driving parameter: {dimension_name}')
        unit=str(self.parameters[pname].unit or 'mm')
        self.parameters[pname].expression=f'{target:g} {unit}' if unit else f'{target:g}'
        self.invalidate_parameter_cache()
        ent_id=dim.get('entity_id') or dim.get('entityId')
        ent=next((e for e in sm.entities if e.tag==ent_id),None)
        if ent is None:raise ValueError(f'Sketch dimension references missing entity: {ent_id}')
        # Match the dimension semantics used by inventor_add_sketch_dimension.  Radius
        # dimensions are already parameter-bound; line dimensions retain their start
        # point and direction while the endpoint moves to the new driving length.
        if ent.kind=='line':
            x1,y1=float(self.value(ent.data['x1'])),float(self.value(ent.data['y1']))
            x2,y2=float(self.value(ent.data['x2'])),float(self.value(ent.data['y2']))
            dx,dy=x2-x1,y2-y1; length=math.hypot(dx,dy)
            if length<1e-12:raise ValueError('Cannot edit dimension of a zero-length sketch line')
            ent.data['x2']=x1+dx/length*target; ent.data['y2']=y1+dy/length*target
        elif ent.kind in ('circle','arc'):
            ent.data['r']=pname
        idx=self._rebuild_after_sketch_edit(sm)
        return {'sketch_name':sm.name,'dimension_name':dimension_name,'parameter_name':pname,'value_mm':target,'first_dependent_feature_index':idx}

    def delete_sketch_dimension(self, sketch_name, dimension_name):
        sm=self._sketch_for_edit(sketch_name)
        dim=next((x for x in sm.dimensions if x.get('name')==dimension_name),None)
        if dim is None:raise ValueError(f'Sketch dimension not found: {dimension_name}')
        pname=dim.get('value_mm') if isinstance(dim.get('value_mm'),str) else dim.get('name')
        ent_id=dim.get('entity_id') or dim.get('entityId')
        ent=next((e for e in sm.entities if e.tag==ent_id),None)
        if ent is not None and pname in self.parameters:
            # Removing an Inventor driving dimension frees the sketch geometry at its
            # current solved position.  Detach direct radius bindings before retaining
            # the old d# only as an orphan safety parameter for external expressions.
            if ent.kind in ('circle','arc') and ent.data.get('r')==pname:
                ent.data['r']=float(self.value(pname))
        sm.dimensions=[x for x in sm.dimensions if x is not dim]
        idx=self._rebuild_after_sketch_edit(sm)
        return {'sketch_name':sm.name,'deleted_dimension':dimension_name,'retained_parameter':pname,'first_dependent_feature_index':idx}

    def edit_sketch_constraint(self, sketch_name, constraint_name, constraint_type=None, entity_ids=None):
        sm=self._sketch_for_edit(sketch_name)
        c=next((x for x in sm.constraints if x.get('name')==constraint_name),None)
        if c is None:raise ValueError(f'Sketch constraint not found: {constraint_name}')
        allowed={'coincident','parallel','perpendicular','horizontal','vertical','tangent','concentric','equal','collinear','symmetric'}
        if constraint_type is not None:
            typ=str(constraint_type).lower()
            if typ not in allowed:raise ValueError(f'constraint_type must be one of {sorted(allowed)}')
            c['type']=typ
        if entity_ids is not None:
            ids=[str(x) for x in entity_ids]
            known={e.tag for e in sm.entities}
            if any(x not in known for x in ids):raise ValueError('Sketch constraint references unknown entity id(s)')
            c['entity_ids']=ids
        idx=self._rebuild_after_sketch_edit(sm)
        return {'sketch_name':sm.name,'constraint':copy.deepcopy(c),'first_dependent_feature_index':idx}

    def delete_sketch_constraint(self, sketch_name, constraint_name):
        sm=self._sketch_for_edit(sketch_name)
        c=next((x for x in sm.constraints if x.get('name')==constraint_name),None)
        if c is None:raise ValueError(f'Sketch constraint not found: {constraint_name}')
        sm.constraints=[x for x in sm.constraints if x is not c]
        idx=self._rebuild_after_sketch_edit(sm)
        return {'sketch_name':sm.name,'deleted_constraint':constraint_name,'first_dependent_feature_index':idx}

    def edit_feature_definition(self, name, updates, new_name=None):
        """Inventor Feature.Definition-style validated history edit.

        ``edit_feature`` remains as the backward-compatible generic extension.  This
        typed path accepts only fields that are meaningful for the recorded feature,
        preserves d# parameter bindings, and rebuilds from that feature atomically.
        """
        f=self.find_feature(name); idx=self.features.index(f); changes=copy.deepcopy(updates or {})
        rules={
            'extrude':{'distance_mm','distance','direction','operation'},
            'extrude_advanced':{'distance_mm','taper_deg','direction','operation'},
            'revolve':{'angle_deg','angle','axis_id','operation'},
            'hole':{'diameter_mm','kind','through','depth_mm','cbore_diameter_mm','cbore_depth_mm','csink_diameter_mm','csink_angle_deg','tapped_designation','tapped_class','tapped_right_handed','tapped_thread_depth_mm'},
            'fillet':{'radius_mm','edge_ids'},
            'chamfer':{'distance_mm','edge_ids'},
            'circular_pattern':{'feature_names','axis','count','angle_deg','natural_direction'},
            'rectangular_pattern':{'feature_names','dir1','count1','spacing_mm1','dir2','count2','spacing_mm2','natural_direction1','natural_direction2'},
            'shell':{'thickness_mm','kind','remove_face_ids'},
            'box':{'length_mm','width_mm','height_mm','origin_mm','centered','operation'},
            'cylinder':{'diameter_mm','height_mm','origin_mm','axis','operation'},
            'cone':{'diameter1_mm','diameter2_mm','height_mm','origin_mm','axis','operation'},
            'sphere':{'diameter_mm','center_mm','operation'},
            'torus':{'major_radius_mm','minor_radius_mm','center_mm','axis','operation'},
            'slot':{'length_mm','width_mm','depth_mm','cx_mm','cy_mm','z0_mm','operation'},
            'coil':{'pitch_mm','height_mm','helix_radius_mm','section_diameter_mm','z0_mm','right_handed','operation'},
            'loft':{'sketch_names','ruled','operation'},
            'sweep':{'profile_sketch_name','path_points_mm','path_sketch3d_name','smooth','is_frenet','transition','operation'},
            'draft_face':{'face_ref','angle_deg','pull_direction','neutral_plane'},
            'replace_face':{'face_ref','target_plane'},
            'offset_body':{'distance_mm','join','remove_internal_edges'},
            'delete_faces':{'face_refs'},
            'thread_face':{'major_diameter_mm','pitch_mm','length_mm','offset_mm','right_handed','internal','reverse_direction'},
            'split_body':{'plane','keep'},
            'combine_bodies':{'base_index','tool_indices','operation','keep_tools'},
        }
        allowed=rules.get(f.kind)
        if allowed is None:raise ValueError(f'Feature Definition editing is not yet typed for feature kind: {f.kind}; use edit_feature for expert/raw history edits')
        bad=sorted(set(changes)-allowed)
        if bad:raise ValueError(f'Unsupported {f.kind} definition fields: {bad}; allowed={sorted(allowed)}')
        numeric_units={
            'distance_mm':'mm','distance':'mm','taper_deg':'deg','angle_deg':'deg','angle':'deg','diameter_mm':'mm','depth_mm':'mm',
            'cbore_diameter_mm':'mm','cbore_depth_mm':'mm','csink_diameter_mm':'mm','csink_angle_deg':'deg','tapped_thread_depth_mm':'mm',
            'radius_mm':'mm','spacing_mm1':'mm','spacing_mm2':'mm','thickness_mm':'mm','length_mm':'mm','width_mm':'mm','height_mm':'mm',
            'diameter1_mm':'mm','diameter2_mm':'mm','major_radius_mm':'mm','minor_radius_mm':'mm','depth_mm':'mm','cx_mm':'mm','cy_mm':'mm','z0_mm':'mm',
            'pitch_mm':'mm','helix_radius_mm':'mm','section_diameter_mm':'mm','major_diameter_mm':'mm','length_mm':'mm','offset_mm':'mm','angle_deg':'deg',
        }
        for key,value in changes.items():
            if key=='operation':
                op=str(value).lower()
                if op not in {'new','join','add','cut','subtract','intersect','common'}:raise ValueError('operation must be new|join|cut|intersect')
                f.operation=op; continue
            if key in numeric_units and value is not None and not isinstance(value,(list,dict,bool)):
                v=float(self.value(value) if isinstance(value,str) else value)
                positive_keys={'distance_mm','distance','diameter_mm','depth_mm','cbore_diameter_mm','cbore_depth_mm','csink_diameter_mm','radius_mm','spacing_mm1','spacing_mm2','thickness_mm','length_mm','width_mm','height_mm','diameter1_mm','major_radius_mm','minor_radius_mm','pitch_mm','helix_radius_mm','section_diameter_mm'}
                if key in positive_keys and v<=0:raise ValueError(f'{key} must be > 0')
                self._set_feature_numeric_param(f,key,v,numeric_units[key]); continue
            if key in ('count','count1','count2') and value is not None:
                iv=int(value)
                if iv<1:raise ValueError(f'{key} must be >= 1')
                f.params[key]=iv; continue
            f.params[key]=copy.deepcopy(value)
        if f.kind=='extrude' and str(f.params.get('direction','positive')).lower() not in {'positive','negative','symmetric'}:raise ValueError('extrude direction must be positive|negative|symmetric')
        if f.kind=='hole' and str(f.params.get('kind','drilled')).lower() not in {'drilled','counterbore','countersink'}:raise ValueError('hole kind must be drilled|counterbore|countersink')
        if new_name is not None:
            nn=str(new_name).strip()
            if not nn:raise ValueError('new_name must not be blank')
            if any(x is not f and x.name==nn for x in self.features):raise ValueError(f'Feature name already exists: {nn}')
            old=f.name; f.name=nn
            # Pattern source references are name-based in this standalone backend; mirror
            # Inventor's object-reference rename semantics by updating those references.
            for later in self.features[idx+1:]:
                names=later.params.get('feature_names')
                if isinstance(names,list):later.params['feature_names']=[nn if x==old else x for x in names]
        self.rebuild(idx)
        return {'feature_name':f.name,'feature_kind':f.kind,'operation':f.operation,'params':copy.deepcopy(f.params),'feature_index':idx}

    def rename_parameter(self, name, new_name):
        if name not in self.parameters:raise ValueError(f'Parameter not found: {name}')
        new_name=str(new_name).strip()
        if not new_name:raise ValueError('new_name must not be blank')
        if new_name in self.parameters:raise ValueError(f'Parameter already exists: {new_name}')
        pat=re.compile(rf'(?<![A-Za-z0-9_]){re.escape(str(name))}(?![A-Za-z0-9_])')
        par=self.parameters.pop(name); par.name=new_name; self.parameters[new_name]=par
        for q in self.parameters.values():q.expression=pat.sub(new_name,str(q.expression))
        def repl(v):
            if isinstance(v,str):return new_name if v==name else pat.sub(new_name,v)
            if isinstance(v,list):return [repl(x) for x in v]
            if isinstance(v,dict):return {k:repl(x) for k,x in v.items()}
            return v
        for sm in self.sketches.values():
            for e in sm.entities:e.data=repl(e.data)
            sm.dimensions=repl(sm.dimensions); sm.constraints=repl(sm.constraints)
        for f in self.features:f.params=repl(f.params)
        for rec in self.work_planes.values():
            for k,v in list(rec.items()):rec[k]=repl(v)
        for rec in self.work_axes.values():
            for k,v in list(rec.items()):rec[k]=repl(v)
        for rec in self.work_points.values():
            for k,v in list(rec.items()):rec[k]=repl(v)
        self.invalidate_parameter_cache(); self.rebuild(0)
        return {'old_name':name,'new_name':new_name}

    def delete_parameter(self, name):
        if name not in self.parameters:raise ValueError(f'Parameter not found: {name}')
        pat=re.compile(rf'(?<![A-Za-z0-9_]){re.escape(str(name))}(?![A-Za-z0-9_])')
        uses=[]
        for n,q in self.parameters.items():
            if n!=name and pat.search(str(q.expression)):uses.append(f'parameter:{n}')
        def scan(v,path):
            if isinstance(v,str) and (v==name or pat.search(v)):uses.append(path)
            elif isinstance(v,list):
                for i,x in enumerate(v):scan(x,f'{path}[{i}]')
            elif isinstance(v,dict):
                for k,x in v.items():scan(x,f'{path}.{k}')
        for sn,sm in self.sketches.items():
            for e in sm.entities:scan(e.data,f'sketch:{sn}:{e.tag}')
            scan(sm.dimensions,f'sketch:{sn}:dimensions')
        for f in self.features:scan(f.params,f'feature:{f.name}')
        if uses:raise ValueError('Parameter is still referenced; remove/rebind dependencies first: '+', '.join(uses[:12]))
        del self.parameters[name]; self.invalidate_parameter_cache(); return {'deleted_parameter':name}

    def transform_sketch_entities(self, sketch_name, entity_ids, translate_mm=None, rotate_deg=0.0, pivot_mm=None, copy_entities=False):
        sm=self._sketch_for_edit(sketch_name); ids=[str(x) for x in entity_ids]
        ents=[next((e for e in sm.entities if e.tag==x),None) for x in ids]
        if any(e is None for e in ents):raise ValueError('Unknown sketch entity id(s)')
        dx,dy=[float(x) for x in (translate_mm or [0,0])]; px,py=[float(x) for x in (pivot_mm or [0,0])]
        a=math.radians(float(rotate_deg)); ca,sa=math.cos(a),math.sin(a)
        def pt(x,y):
            x=float(self.value(x)); y=float(self.value(y)); X=x-px; Y=y-py
            return px+ca*X-sa*Y+dx, py+sa*X+ca*Y+dy
        new=[]
        for ent in ents:
            target=copy.deepcopy(ent) if copy_entities else ent
            if copy_entities:
                target.tag=f'e{len(sm.entities)+len(new)+1}'
            d=target.data
            if target.kind=='line':d['x1'],d['y1']=pt(d['x1'],d['y1']); d['x2'],d['y2']=pt(d['x2'],d['y2'])
            elif target.kind in ('circle','arc'):d['cx'],d['cy']=pt(d['cx'],d['cy']);
            elif target.kind=='arc':d['start_deg']=float(self.value(d['start_deg']))+float(rotate_deg); d['end_deg']=float(self.value(d['end_deg']))+float(rotate_deg)
            elif target.kind=='spline':d['points']=[list(pt(q[0],q[1])) for q in d.get('points',[])]
            else:raise ValueError(f'Sketch transform does not support entity kind: {target.kind}')
            if copy_entities:new.append(target)
        if copy_entities:sm.entities.extend(new)
        idx=self._rebuild_after_sketch_edit(sm)
        return {'sketch_name':sm.name,'entity_ids':[e.tag for e in (new if copy_entities else ents)],'copied':bool(copy_entities),'first_dependent_feature_index':idx}

    def rename_sketch(self, name, new_name):
        if name not in self.sketches:raise ValueError(f'Sketch not found: {name}')
        nn=str(new_name).strip()
        if not nn:raise ValueError('new_name must not be blank')
        if nn in self.sketches:raise ValueError(f'Sketch already exists: {nn}')
        sm=self.sketches.pop(name); sm.name=nn; self.sketches[nn]=sm
        for f in self.features:
            for key in ('sketch_name','profile_sketch_name'):
                if f.params.get(key)==name:f.params[key]=nn
            if isinstance(f.params.get('sketch_names'),list):f.params['sketch_names']=[nn if x==name else x for x in f.params['sketch_names']]
        return {'old_name':name,'new_name':nn}

    def delete_sketch(self, name, delete_dependents=False):
        if name not in self.sketches:raise ValueError(f'Sketch not found: {name}')
        deps=[i for i,f in enumerate(self.features) if f.params.get('sketch_name')==name or f.params.get('profile_sketch_name')==name or name in (f.params.get('sketch_names') or [])]
        if deps and not delete_dependents:raise ValueError('Sketch is consumed by feature history; set delete_dependents=true for explicit destructive deletion')
        removed=[]
        if deps:
            first=min(deps); removed=[f.name for f in self.features[first:]]; self.features=self.features[:first]
        del self.sketches[name]; self.rebuild(0 if not deps else min(deps))
        return {'deleted_sketch':name,'deleted_features':removed}

    def edit_work_feature(self, kind, name, type_=None, refs=None, offset=None, new_name=None):
        kind=str(kind).lower(); bucket=self.work_planes if kind=='plane' else self.work_axes if kind=='axis' else self.work_points if kind=='point' else None
        if bucket is None:raise ValueError('kind must be plane|axis|point')
        if name not in bucket:raise ValueError(f'Work {kind} not found: {name}')
        old=copy.deepcopy(bucket[name]); del bucket[name]
        try:
            if kind=='plane': rec=self.create_work_plane(type_ or old.get('type','offset'), list(refs if refs is not None else old.get('refs') or []), old.get('offset_mm') if offset is None else offset, name=name)
            elif kind=='axis': rec=self.create_work_axis(type_ or old.get('type','edge'), list(refs if refs is not None else old.get('refs') or []), name=name)
            else: rec=self.create_work_point(type_ or old.get('type','fixed'), list(refs if refs is not None else old.get('refs') or []), point_mm=old.get('origin'), name=name)
        except Exception:
            bucket[name]=old; raise
        if new_name is not None:
            nn=str(new_name).strip()
            if not nn:raise ValueError('new_name must not be blank')
            if nn in bucket and nn!=name:raise ValueError(f'Work feature already exists: {nn}')
            bucket.pop(name); rec['name']=nn; bucket[nn]=rec
            def replace_ref(v):
                if isinstance(v,str):return nn if v==name else v
                if isinstance(v,list):return [replace_ref(x) for x in v]
                if isinstance(v,dict):return {k:replace_ref(x) for k,x in v.items()}
                return v
            for f in self.features:f.params=replace_ref(f.params)
            for r in self.work_planes.values():r.update(replace_ref(dict(r)))
            for r in self.work_axes.values():r.update(replace_ref(dict(r)))
            for r in self.work_points.values():r.update(replace_ref(dict(r)))
            name=nn
        self.rebuild(0); return {'kind':kind,'name':name,'definition':copy.deepcopy(rec)}

    def delete_work_feature(self, kind, name):
        kind=str(kind).lower(); bucket=self.work_planes if kind=='plane' else self.work_axes if kind=='axis' else self.work_points if kind=='point' else None
        if bucket is None:raise ValueError('kind must be plane|axis|point')
        if name not in bucket:raise ValueError(f'Work {kind} not found: {name}')
        uses=[]
        def scan(v,path):
            if isinstance(v,str) and v==name:uses.append(path)
            elif isinstance(v,list):
                for i,x in enumerate(v):scan(x,f'{path}[{i}]')
            elif isinstance(v,dict):
                for k,x in v.items():scan(x,f'{path}.{k}')
        for f in self.features:scan(f.params,f'feature:{f.name}')
        for n,r in self.work_planes.items():
            if not (kind=='plane' and n==name):scan(r,f'work_plane:{n}')
        for n,r in self.work_axes.items():
            if not (kind=='axis' and n==name):scan(r,f'work_axis:{n}')
        for n,r in self.work_points.items():
            if not (kind=='point' and n==name):scan(r,f'work_point:{n}')
        if uses:raise ValueError('Work feature is still referenced; rebind/delete dependents first: '+', '.join(uses[:12]))
        del bucket[name]; return {'deleted_work_feature':name,'kind':kind}

    def thread_face(self, face_ref, designation=None, pitch_mm=None, major_diameter_mm=None, full_depth=True, thread_depth_mm=None, offset_mm=0.0, right_handed=True, internal=None, reverse_direction=False, name=None):
        if self.doc_type!='part' or self.shape is None:raise ValueError('thread_face requires an active part with geometry')
        persistent=make_face_ref(self.shape,face_ref); persistent.pop('radius_mm',None); face=match_face(self.shape,persistent)
        if str(face.geomType()).upper()!='CYLINDER':raise ValueError('ThreadFeature currently requires a cylindrical face')
        cyl=face._geomAdaptor().Cylinder(); ax=cyl.Axis(); loc=ax.Location(); av=np.array([ax.Direction().X(),ax.Direction().Y(),ax.Direction().Z()],float); av=av/np.linalg.norm(av); a0=np.array([loc.X(),loc.Y(),loc.Z()],float)
        verts=face.Vertices(); vals=[float(np.dot(np.array(v.Center().toTuple(),float)-a0,av)) for v in verts]
        if not vals:raise ValueError('Unable to determine cylindrical face axial extent')
        lo,hi=min(vals),max(vals); available=hi-lo
        off=float(offset_mm or 0); L=(available-off) if bool(full_depth) else float(thread_depth_mm or 0)
        if L<=1e-6 or off<0 or off+L>available+1e-6:raise ValueError('Thread depth/offset is outside the selected face extent')
        start=(hi-off-L) if reverse_direction else (lo+off); origin=a0+av*start
        face_dia=2.0*float(cyl.Radius()); major=float(major_diameter_mm or face_dia)
        if designation:
            m=re.fullmatch(r'(?i)M\s*(\d+(?:\.\d+)?)\s*(?:[x×]\s*(\d+(?:\.\d+)?))?',str(designation).strip())
            if not m:raise ValueError('designation must look like M10 or M10x1.5')
            major=float(m.group(1)); pitch=metric_pitch(major,float(m.group(2)) if m.group(2) else pitch_mm)
        else:pitch=metric_pitch(major,pitch_mm)
        if internal is None:
            v=verts[0].Center(); vp=np.array(v.toTuple(),float); axispt=a0+av*float(np.dot(vp-a0,av)); radial=vp-axispt
            try:n=np.array(face.normalAt(v).toTuple(),float); internal=bool(float(np.dot(n,radial))<0)
            except Exception:internal=False
        rec=self.add_feature('thread_face',name or f'Thread{len(self.features)+1}',{'face_ref':persistent,'major_diameter_mm':major,'pitch_mm':pitch,'length_mm':L,'offset_mm':off,'right_handed':bool(right_handed),'internal':bool(internal),'reverse_direction':bool(reverse_direction)},'cut' if internal else 'join')
        return {'feature_name':rec.name,'designation':f'M{major:g}x{pitch:g}','internal':bool(internal),'thread_length_mm':L}

    def replace_occurrence(self, name, path, replace_all=False):
        if self.doc_type!='assembly':raise ValueError('replace_occurrence requires an active assembly')
        if name not in self.occurrences:raise ValueError(f'Occurrence not found: {name}')
        target=self.occurrences[name]; old_path=target.path
        names=[n for n,o in self.occurrences.items() if replace_all and o.path==old_path] or [name]
        from .assembly import load_component
        resolved=str(Path(path).expanduser().resolve()); shape,interfaces,pn,mass,ctype,children=load_component(resolved,{str(Path(self.path).resolve())} if self.path else None)
        for n in names:
            o=self.occurrences[n]; o.path=resolved; o.shape=shape; o.interfaces=copy.deepcopy(interfaces); o.part_number=pn; o.unit_mass_g=mass; o.component_type=ctype; o.children=copy.deepcopy(children); o.source_session_id=None; o.reference_status='resolved'; o.load_error=None
        self.solve_assembly_constraints(); self.rebuild_assembly(solve=False)
        return {'replaced_occurrences':names,'path':resolved,'component_type':ctype}
    def delete_face_heal(self, face_ref, name=None):
        if self.doc_type!='part':raise ValueError('delete_face requires an active part document')
        if self.shape is None:raise ValueError('No body for delete_face')
        persistent=make_face_ref(self.shape,face_ref)
        rec=self.add_feature('delete_face',name or f'DeleteFace{len(self.features)+1}',{'face_ref':persistent},'join')
        return {'feature_name':rec.name,'feature_kind':rec.kind,'feature_index':len(self.features)-1,'heal':True}

    def suppress_feature(self,name,suppressed=True):
        f=self.find_feature(name); idx=self.features.index(f); f.suppressed=bool(suppressed); self.rebuild(idx); return f

    def delete_feature(self,name):
        idx=next((i for i,f in enumerate(self.features) if f.name==name),None)
        if idx is None:raise ValueError(f'Feature not found: {name}')
        del self.features[idx]
        self.rebuild(idx); return name

    def _combine(self,tool,operation):
        if self.shape is None:
            if operation in ('join','new','add'):self.shape=tool
            else:raise ValueError(f'Operation {operation} requires an existing body')
            return
        base=cq.Workplane(obj=self.shape)
        if operation in ('join','new','add'):self.shape=base.union(tool).val()
        elif operation in ('cut','subtract'):self.shape=base.cut(tool).val()
        elif operation in ('intersect','common'):self.shape=base.intersect(tool).val()
        else:raise ValueError(f'Unknown operation: {operation}')

    def _axis_for_revolve(self,sm,axis_id):
        if isinstance(axis_id,str):axis_id=canonical_axis_name(axis_id,allow_compat=True)
        if axis_id=='X Axis':return (0,0),(1,0)
        if axis_id=='Y Axis':return (0,0),(0,1)
        if axis_id=='Z Axis':
            # In sketch coordinates Z has no direct 2D representation; use local Y as deterministic equivalent for origin-axis projection.
            return (0,0),(0,1)
        ent=next((e for e in sm.entities if e.tag==axis_id),None)
        if not ent or ent.kind!='line':raise ValueError(f'axis_id must be a sketch line entity id or origin axis: {axis_id}')
        return (self.value(ent.data['x1']),self.value(ent.data['y1'])),(self.value(ent.data['x2']),self.value(ent.data['y2']))

    def _axis_vector(self,ref,fallback_ref=None):
        q=str(ref or 'Z Axis') if not isinstance(ref,dict) else ''
        if q:q=canonical_axis_name(q,allow_compat=True)
        base={k:(list(v[0]),list(v[1])) for k,v in ORIGIN_AXES.items()}
        if q in base:return base[q]
        if isinstance(ref,str) and q in self.work_axes:
            wa=self._refresh_work_axis(q); return list(wa['origin']),list(wa['direction'])
        legacy_exc=None
        if self.shape is not None:
            try:
                e=match_edge_ids(self.shape,[ref])[0]
                if str(e.geomType())!='LINE':raise ValueError('axis edge is not linear')
                vs=e.Vertices(); a=vs[0].Center(); b=vs[-1].Center(); v=np.array([b.x-a.x,b.y-a.y,b.z-a.z],float); v=v/np.linalg.norm(v); return [a.x,a.y,a.z],v.tolist()
            except Exception as exc:legacy_exc=exc
            if fallback_ref is not None:
                try:
                    e=match_edge(self.shape,fallback_ref)
                    if str(e.geomType())!='LINE':raise ValueError('axis edge is not linear')
                    vs=e.Vertices(); a=vs[0].Center(); b=vs[-1].Center(); v=np.array([b.x-a.x,b.y-a.y,b.z-a.z],float); v=v/np.linalg.norm(v); return [a.x,a.y,a.z],v.tolist()
                except Exception:pass
        if legacy_exc is not None:raise legacy_exc
        raise ValueError(f'Unknown axis reference: {ref}')

    def _apply_feature(self,rec,record=True):
        p=rec.params; kind=rec.kind
        if kind in ('extrude','revolve'):
            sm=self.sketches[p['sketch_name']]; sk=self.build_sketch(sm); wp=cq.Workplane(self._plane(sm.plane,sm.plane_ref)).placeSketch(sk)
            if kind=='extrude':
                dist=self.value(p.get('distance_mm',p.get('distance',10))); direction=str(p.get('direction','positive')).lower(); both=direction=='symmetric';
                if direction=='negative':dist=-abs(dist)
                tool=wp.extrude(dist,combine=False,both=both).val()
            else:
                angle=float(self.value(p.get('angle_deg',p.get('angle',360))))
                a0,a1=self._axis_for_revolve(sm,p.get('axis_id','YAxis'))
                # Inventor RevolveHandler semantics: 0 and ±360 use AddFull.
                # CadQuery interprets a negative partial angle as 360-|angle|,
                # so emulate Inventor's kNegativeExtentDirection by reversing
                # the axis and revolving through the positive magnitude.
                if abs(abs(angle)-360.0) < 1e-6 or abs(angle) < 1e-12:
                    cq_angle=360.0
                elif angle < 0:
                    a0,a1=a1,a0; cq_angle=abs(angle)
                else:
                    cq_angle=angle
                tool=wp.revolve(angleDegrees=cq_angle,axisStart=a0,axisEnd=a1,combine=False).val()
            self._combine(tool,rec.operation)
        elif kind=='extrude_advanced':
            sm=self.sketches[p['sketch_name']]; sk=self.build_sketch(sm); wp=cq.Workplane(self._plane(sm.plane,sm.plane_ref)).placeSketch(sk)
            dist=self.value(p['distance_mm']); direction=str(p.get('direction','positive')).lower(); both=direction=='symmetric'
            if direction=='negative': dist=-abs(dist)
            tool=wp.extrude(dist,combine=False,both=both,taper=self.value(p.get('taper_deg',0))).val(); self._combine(tool,rec.operation)
        elif kind=='loft':
            names=p.get('sketch_names') or []
            if len(names)<2: raise ValueError('loft requires at least two sketch_names')
            wires=[]
            for name in names:
                if name not in self.sketches: raise ValueError(f'Unknown sketch: {name}')
                wires.append(self.profile_face_world(self.sketches[name]).outerWire())
            tool=cq.Solid.makeLoft(wires,ruled=bool(p.get('ruled',False))); self._combine(tool,rec.operation)
        elif kind=='sweep':
            name=p['profile_sketch_name']
            if name not in self.sketches: raise ValueError(f'Unknown profile sketch: {name}')
            wire=self.profile_face_world(self.sketches[name]).outerWire(); raw_pts=self.sketch3d_path_points(p['path_sketch3d_name']) if p.get('path_sketch3d_name') else (p.get('path_points_mm') or []); pts=[cq.Vector(*[float(v) for v in q]) for q in raw_pts]
            if len(pts)<2: raise ValueError('sweep path requires at least two 3D points')
            if bool(p.get('smooth',False)) and len(pts)>=3: path=cq.Wire.assembleEdges([cq.Edge.makeSpline(pts)])
            else: path=cq.Wire.assembleEdges([cq.Edge.makeLine(a,b) for a,b in zip(pts,pts[1:])])
            tool=cq.Solid.sweep(wire,[],path,makeSolid=True,isFrenet=bool(p.get('is_frenet',False)),transitionMode=p.get('transition','transformed')); self._combine(tool,rec.operation)
        elif kind=='spur_gear':
            tool, derived = involute_spur_gear(
                module=self.value(p['module']),
                teeth=int(p['teeth']),
                thickness=self.value(p['thickness_mm']),
                bore_diameter=self.value(p.get('bore_diameter_mm', 0)),
                pressure_angle_deg=self.value(p.get('pressure_angle_deg', 20)),
                backlash=self.value(p.get('backlash_mm', 0)),
            )
            p['derived'] = derived
            self._combine(tool, rec.operation)
        elif kind=='accelerator_gear':
            gear_kind=str(p.get('kind','')).lower()
            if gear_kind=='spur':
                if p.get('teeth') is None or p.get('width_mm') is None:
                    raise ValueError('spur gear requires teeth and width_mm')
                tool, derived = involute_spur_gear(
                    module=self.value(p['module']), teeth=int(p['teeth']), thickness=self.value(p['width_mm']),
                    bore_diameter=self.value(p.get('bore_diameter_mm',0)), pressure_angle_deg=self.value(p.get('pressure_angle_deg',20)),
                    backlash=self.value(p.get('backlash_mm',0)),
                )
                derived=dict(derived); derived.update({'generator':'accelerator_gear','gear_kind':'spur','width_mm':self.value(p['width_mm'])})
            else:
                resolved={}
                for key,val in p.items():
                    if key=='derived': continue
                    if key=='kind': continue
                    elif val is None: resolved[key]=None
                    elif isinstance(val,(int,bool,list,dict,str)): resolved[key]=val
                    else: resolved[key]=self.value(val)
                tool, derived = advanced_gear(gear_kind, **resolved)
            p['derived']=derived
            self._combine(tool,rec.operation)
        elif kind=='accelerator_coupling':
            tool,derived=flange_coupling(
                self.value(p['bore_diameter_mm']),self.value(p['hub_diameter_mm']),self.value(p['flange_diameter_mm']),
                self.value(p['hub_length_mm']),self.value(p['flange_thickness_mm']),
                None if p.get('bolt_circle_diameter_mm') is None else self.value(p['bolt_circle_diameter_mm']),int(p.get('bolt_count',4)),
                None if p.get('bolt_hole_diameter_mm') is None else self.value(p['bolt_hole_diameter_mm']),
                None if p.get('keyway_width_mm') is None else self.value(p['keyway_width_mm']),
                None if p.get('keyway_depth_mm') is None else self.value(p['keyway_depth_mm']),
            ); p['derived']=derived; self._combine(tool,rec.operation)
        elif kind=='lead_screw_nut':
            tool,derived=lead_screw_nut(
                self.value(p['major_diameter_mm']),self.value(p['pitch_mm']),self.value(p['screw_length_mm']),self.value(p['nut_length_mm']),
                int(p.get('starts',1)),self.value(p.get('thread_angle_deg',30.0)),
                None if p.get('nut_outer_diameter_mm') is None else self.value(p['nut_outer_diameter_mm']),
                self.value(p.get('clearance_mm',0.15)),bool(p.get('right_handed',True)),
            ); p['derived']=derived; self._combine(tool,rec.operation)
        elif kind=='accelerator_shaft':
            sections=[{'length_mm':self.value(x['length_mm']),'diameter_mm':self.value(x['diameter_mm'])} for x in (p.get('sections') or [])]
            tool,derived=stepped_shaft(
                sections,
                self.value(p.get('bore_diameter_mm',0)),
                None if p.get('keyway_width_mm') is None else self.value(p['keyway_width_mm']),
                None if p.get('keyway_depth_mm') is None else self.value(p['keyway_depth_mm']),
                None if p.get('keyway_length_mm') is None else self.value(p['keyway_length_mm']),
                self.value(p.get('keyway_start_mm',0)),
            ); p['derived']=derived; self._combine(tool,rec.operation)
        elif kind=='parallel_key':
            tool,derived=parallel_key(self.value(p['width_mm']),self.value(p['height_mm']),self.value(p['length_mm']),p.get('end_style','square'))
            p['derived']=derived; self._combine(tool,rec.operation)
        elif kind=='accelerator_bearing':
            tool,derived=rolling_bearing(
                p.get('kind','deep_groove_ball'),self.value(p['bore_diameter_mm']),self.value(p['outer_diameter_mm']),self.value(p['width_mm']),
                int(p.get('rolling_elements',8)),bool(p.get('detailed',True)),
            ); p['derived']=derived; self._combine(tool,rec.operation)
        elif kind=='accelerator_spring':
            spring_kind=str(p.get('kind','compression')).lower()
            if spring_kind=='compression':
                required=('wire_diameter_mm','mean_diameter_mm','free_length_mm','active_turns')
                missing=[k for k in required if p.get(k) is None]
                if missing: raise ValueError('compression spring requires '+', '.join(missing))
                tool,derived=compression_spring(self.value(p['wire_diameter_mm']),self.value(p['mean_diameter_mm']),self.value(p['free_length_mm']),self.value(p['active_turns']),bool(p.get('right_handed',True)))
            elif spring_kind=='belleville':
                required=('outer_diameter_mm','inner_diameter_mm','thickness_mm','free_height_mm')
                missing=[k for k in required if p.get(k) is None]
                if missing: raise ValueError('belleville spring requires '+', '.join(missing))
                tool,derived=belleville_spring(self.value(p['outer_diameter_mm']),self.value(p['inner_diameter_mm']),self.value(p['thickness_mm']),self.value(p['free_height_mm']))
            else: raise ValueError('spring kind must be compression|belleville')
            p['derived']=derived; self._combine(tool,rec.operation)
        elif kind=='v_pulley':
            tool,derived=v_pulley(
                self.value(p['pitch_diameter_mm']),self.value(p['width_mm']),int(p.get('groove_count',1)),self.value(p.get('groove_angle_deg',40)),
                None if p.get('groove_depth_mm') is None else self.value(p['groove_depth_mm']),
                None if p.get('groove_pitch_mm') is None else self.value(p['groove_pitch_mm']),self.value(p.get('bore_diameter_mm',0)),
            ); p['derived']=derived; self._combine(tool,rec.operation)
        elif kind=='sheet_metal_base':
            w=self.value(p['width_mm']); h=self.value(p['height_mm']); t=self.value(p['thickness_mm']); r=self.value(p.get('bend_radius_mm',t)); k=float(p.get('k_factor',0.44))
            t,r,k=validate_sheet_rule(t,r,k)
            tool=make_sheet_base(w,h,t)
            # Base is a dedicated isolated sheet-metal body. It never alters normal part
            # feature semantics because this feature is reachable only through cad_*
            # sheet-metal extensions.
            self._combine(tool,rec.operation)
            self.sheet_metal={'base':{'width_mm':float(w),'height_mm':float(h)},'rule':{'thickness_mm':float(t),'bend_radius_mm':float(r),'k_factor':float(k)},'flanges':[]}
        elif kind=='sheet_metal_flange':
            if not self.sheet_metal:raise ValueError('sheet metal flange requires a sheet_metal_base feature')
            edge=str(p['edge']).lower(); L=self.value(p['length_mm']); angle=float(self.value(p.get('angle_deg',90.0))); r=self.value(p.get('bend_radius_mm',self.sheet_metal['rule']['bend_radius_mm']))
            if any(str(f.get('edge')).lower()==edge for f in self.sheet_metal.get('flanges',[])):
                raise ValueError(f'one base-edge flange per edge is supported; {edge} already has a flange')
            b=self.sheet_metal['base']; rule=self.sheet_metal['rule']; validate_sheet_rule(rule['thickness_mm'],r,rule['k_factor'])
            tool=make_sheet_flange(b['width_mm'],b['height_mm'],rule['thickness_mm'],edge,L,angle)
            self._combine(tool,'join')
            self.sheet_metal['flanges'].append({'edge':edge,'length_mm':float(L),'angle_deg':float(angle),'bend_radius_mm':float(r)})
        elif kind=='box':
            lx=self.value(p['length_mm']); ly=self.value(p['width_mm']); lz=self.value(p['height_mm'])
            origin=[float(x) for x in p.get('origin_mm',[0,0,0])]
            centered=bool(p.get('centered',False))
            if centered: origin=[origin[0]-lx/2,origin[1]-ly/2,origin[2]-lz/2]
            tool=cq.Solid.makeBox(lx,ly,lz,tuple(origin)); self._combine(tool,rec.operation)
        elif kind=='cylinder':
            dia=self.value(p['diameter_mm']); h=self.value(p['height_mm']); origin=tuple(float(x) for x in p.get('origin_mm',[0,0,0])); axis=tuple(float(x) for x in p.get('axis',[0,0,1]));
            tool=cq.Solid.makeCylinder(dia/2.0,h,origin,axis); self._combine(tool,rec.operation)
        elif kind=='cone':
            d1=self.value(p['diameter1_mm']); d2=self.value(p.get('diameter2_mm',0)); h=self.value(p['height_mm']); origin=tuple(float(x) for x in p.get('origin_mm',[0,0,0])); axis=tuple(float(x) for x in p.get('axis',[0,0,1]));
            tool=cq.Solid.makeCone(d1/2.0,d2/2.0,h,origin,axis); self._combine(tool,rec.operation)
        elif kind=='sphere':
            r=self.value(p['diameter_mm'])/2.0; origin=tuple(float(x) for x in p.get('center_mm',[0,0,0])); tool=cq.Solid.makeSphere(r,origin); self._combine(tool,rec.operation)
        elif kind=='torus':
            major=self.value(p['major_radius_mm']); minor=self.value(p['minor_radius_mm']); origin=tuple(float(x) for x in p.get('center_mm',[0,0,0])); axis=tuple(float(x) for x in p.get('axis',[0,0,1])); tool=cq.Solid.makeTorus(major,minor,origin,axis); self._combine(tool,rec.operation)
        elif kind=='metric_hex_bolt':
            tool,meta=metric_hex_bolt(
                self.value(p['diameter_mm']),self.value(p['length_mm']),
                None if p.get('pitch_mm') is None else self.value(p['pitch_mm']),
                None if p.get('head_across_flats_mm') is None else self.value(p['head_across_flats_mm']),
                None if p.get('head_height_mm') is None else self.value(p['head_height_mm']),
                None if p.get('thread_length_mm') is None else self.value(p['thread_length_mm']),
                bool(p.get('right_handed',True)),bool(p.get('modeled_thread',True)),
            ); p['derived']=meta; self._combine(tool,rec.operation)
        elif kind=='metric_hex_nut':
            tool,meta=metric_hex_nut(
                self.value(p['diameter_mm']),None if p.get('pitch_mm') is None else self.value(p['pitch_mm']),
                None if p.get('across_flats_mm') is None else self.value(p['across_flats_mm']),
                None if p.get('height_mm') is None else self.value(p['height_mm']),
                bool(p.get('right_handed',True)),bool(p.get('modeled_thread',True)),
            ); p['derived']=meta; self._combine(tool,rec.operation)
        elif kind=='metric_washer':
            tool,meta=metric_washer(
                self.value(p['diameter_mm']),None if p.get('inner_diameter_mm') is None else self.value(p['inner_diameter_mm']),
                None if p.get('outer_diameter_mm') is None else self.value(p['outer_diameter_mm']),
                None if p.get('thickness_mm') is None else self.value(p['thickness_mm']),
            ); p['derived']=meta; self._combine(tool,rec.operation)
        elif kind=='external_thread':
            tool,meta=external_metric_thread(self.value(p['major_diameter_mm']),self.value(p['pitch_mm']),self.value(p['length_mm']),self.value(p.get('z0_mm',0)),bool(p.get('right_handed',True))); p['derived']=meta; self._combine(tool,rec.operation)
        elif kind=='slot':
            length=self.value(p['length_mm']); width=self.value(p['width_mm']); depth=self.value(p['depth_mm']); z0=self.value(p.get('z0_mm',0)); cx=self.value(p.get('cx_mm',0)); cy=self.value(p.get('cy_mm',0))
            if length < width: raise ValueError('slot length must be >= width')
            straight=max(0.0,length-width)
            wp=cq.Workplane('XY',origin=(0,0,z0)).center(cx,cy)
            if straight<1e-9: tool=wp.circle(width/2).extrude(depth,combine=False).val()
            else: tool=wp.slot2D(length,width,angle=0).extrude(depth,combine=False).val()
            self._combine(tool,rec.operation)
        elif kind=='coil':
            pitch=self.value(p['pitch_mm']); height=self.value(p['height_mm']); helix_radius=self.value(p['helix_radius_mm']); section_radius=self.value(p['section_diameter_mm'])/2.0; z0=self.value(p.get('z0_mm',0))
            if pitch<=0 or height<=0 or helix_radius<=0 or section_radius<=0: raise ValueError('coil dimensions must be > 0')
            helix=cq.Wire.makeHelix(pitch,height,helix_radius,center=(0,0,z0),dir=(0,0,1),lefthand=not bool(p.get('right_handed',True)))
            profile=cq.Workplane('XZ',origin=(0,0,z0)).center(helix_radius,0).circle(section_radius).wire().val()
            tool=cq.Solid.sweep(profile,[],helix,makeSolid=True,isFrenet=True); self._combine(tool,rec.operation)
        elif kind=='shell':
            if self.shape is None: raise ValueError('No body for shell')
            legacy_ids=p.get('remove_face_ids') or []; refs=p.get('remove_face_refs') or []
            faces=[]
            if legacy_ids:
                try:faces=[match_face(self.shape,x) for x in legacy_ids]
                except Exception as legacy_exc:
                    if not refs:raise
                    try:faces=[match_face(self.shape,x) for x in refs]
                    except Exception:raise legacy_exc
            elif refs:
                faces=[match_face(self.shape,x) for x in refs]
            wp=cq.Workplane(obj=self.shape).newObject(faces)
            self.shape=wp.shell(self.value(p['thickness_mm']),kind=p.get('kind','arc')).val()
        elif kind=='mirror_body':
            if self.shape is None: raise ValueError('No body to mirror')
            plane=str(p.get('plane','YZ')).upper(); base=tuple(float(x) for x in p.get('base_point_mm',[0,0,0])); mirrored=self.shape.mirror(plane,base)
            if bool(p.get('keep_original',True)): self.shape=cq.Workplane(obj=self.shape).union(mirrored).val()
            else: self.shape=mirrored
        elif kind=='transform_body':
            if self.shape is None: raise ValueError('No body to transform')
            out=self.shape
            tr=p.get('translate_mm') or [0,0,0]
            if any(abs(float(x))>1e-12 for x in tr): out=out.translate(tuple(float(x) for x in tr))
            axis=p.get('rotate_axis') or [0,0,1]; ang=float(p.get('rotate_deg',0) or 0)
            if abs(ang)>1e-12: out=out.rotate((0,0,0),tuple(float(x) for x in axis),ang)
            scale=float(p.get('scale',1.0) or 1.0)
            if abs(scale-1.0)>1e-12: out=out.scale(scale)
            self.shape=out
        elif kind=='move_face':
            if self.shape is None:raise ValueError('No body for move_face')
            ref=p.get('face_ref'); face=match_face(self.shape,ref); fr=face_frame(face)
            if str(fr.get('kind','')).lower() not in ('plane','planar'):
                raise ValueError('Direct move_face currently requires a planar face; use history/diameter editing for cylindrical faces')
            n=np.asarray(fr.get('direction') or [0,0,0],float)
            if np.linalg.norm(n)<1e-9:raise ValueError('Selected planar face has no stable normal')
            n=n/np.linalg.norm(n); distance=float(self.value(p['distance_mm']))
            if abs(distance)<1e-9:raise ValueError('distance_mm must be non-zero')
            # Inventor Move Face semantics: positive distance follows the selected
            # outward normal.  A face prism is fused for outward motion and cut for
            # inward motion.  This direct-edit feature is history-recorded and therefore
            # participates in normal rebuild/reference-rebinding/transaction rollback.
            from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
            from OCP.gp import gp_Vec
            prism=cq.Shape.cast(BRepPrimAPI_MakePrism(face.wrapped,gp_Vec(*(n*distance).tolist()),True,True).Shape())
            if prism is None or prism.isNull():raise ValueError('move_face failed to build a valid prism')
            if distance>0:self.shape=cq.Workplane(obj=self.shape).union(prism).val()
            else:self.shape=cq.Workplane(obj=self.shape).cut(prism).val()
            if self.shape is None or self.shape.isNull():raise ValueError('move_face produced invalid geometry')
        elif kind=='delete_face':
            if self.shape is None:raise ValueError('No body for delete_face')
            face=match_face(self.shape,p.get('face_ref'))
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
            op=BRepAlgoAPI_Defeaturing(); op.SetShape(self.shape.wrapped); op.AddFaceToRemove(face.wrapped); op.Build()
            if not op.IsDone():raise ValueError('Delete Face healing failed')
            out=cq.Shape.cast(op.Shape())
            if out is None or out.isNull():raise ValueError('Delete Face produced invalid geometry')
            self.shape=out
        elif kind=='draft_face':
            if self.shape is None:raise ValueError('No body for draft_face')
            face=match_face(self.shape,p.get('face_ref')); neutral=self._plane_record(p.get('neutral_plane'))
            pull=p.get('pull_direction',[0,0,1])
            if isinstance(pull,str):_,pull=self._axis_vector(pull)
            d=np.asarray(pull,float); nn=np.linalg.norm(d)
            if nn<1e-12:raise ValueError('pull_direction must be non-zero')
            from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
            from OCP.gp import gp_Dir,gp_Pln,gp_Pnt
            op=BRepOffsetAPI_DraftAngle(self.shape.wrapped)
            op.Add(face.wrapped,gp_Dir(*[float(x) for x in d/nn]),math.radians(float(self.value(p['angle_deg']))),gp_Pln(gp_Pnt(*[float(x) for x in neutral['origin']]),gp_Dir(*[float(x) for x in neutral['direction']])),True)
            if not op.AddDone():raise ValueError('FaceDraft Add failed for the selected face/neutral plane')
            op.Build()
            if not op.IsDone():raise ValueError('FaceDraft build failed')
            out=cq.Shape.cast(op.Shape())
            if out is None or out.isNull() or not out.isValid():raise ValueError('FaceDraft produced invalid geometry')
            self.shape=out
        elif kind=='replace_face':
            if self.shape is None:raise ValueError('No body for replace_face')
            face=match_face(self.shape,p.get('face_ref')); fr=face_frame(face)
            if str(fr.get('kind','')).lower() not in ('plane','planar'):raise ValueError('Standalone ReplaceFace currently supports a planar source face')
            target=self._plane_record(p.get('target_plane')); n=np.asarray(fr['direction'],float); n=n/np.linalg.norm(n); tn=np.asarray(target['direction'],float); tn=tn/np.linalg.norm(tn)
            if abs(float(np.dot(n,tn)))<0.999999:raise ValueError('Planar ReplaceFace requires a parallel target plane in CADia')
            distance=float(np.dot(np.asarray(target['origin'],float)-np.asarray(fr['origin'],float),n))
            if abs(distance)>=1e-10:
                from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
                from OCP.gp import gp_Vec
                prism=cq.Shape.cast(BRepPrimAPI_MakePrism(face.wrapped,gp_Vec(*(n*distance).tolist()),True,True).Shape())
                if prism is None or prism.isNull():raise ValueError('ReplaceFace failed to build transition prism')
                self.shape=(cq.Workplane(obj=self.shape).union(prism).val() if distance>0 else cq.Workplane(obj=self.shape).cut(prism).val())
                if self.shape is None or self.shape.isNull() or not self.shape.isValid():raise ValueError('ReplaceFace produced invalid geometry')
        elif kind=='offset_body':
            if self.shape is None:raise ValueError('No body for offset_body')
            from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
            from OCP.BRepOffset import BRepOffset_Skin
            from OCP.GeomAbs import GeomAbs_Arc,GeomAbs_Intersection
            distance=float(self.value(p['distance_mm'])); join=str(p.get('join','arc')).lower(); jt=GeomAbs_Arc if join=='arc' else GeomAbs_Intersection
            op=BRepOffsetAPI_MakeOffsetShape(); op.PerformByJoin(self.shape.wrapped,distance,1e-5,BRepOffset_Skin,False,False,jt,bool(p.get('remove_internal_edges',True)))
            if not op.IsDone():raise ValueError('Offset/Thicken operation failed')
            out=cq.Shape.cast(op.Shape())
            if out is None or out.isNull() or not out.isValid():raise ValueError('Offset/Thicken produced invalid geometry')
            self.shape=out
        elif kind=='delete_faces':
            if self.shape is None:raise ValueError('No body for delete_faces')
            refs=p.get('face_refs') or []
            if not refs:raise ValueError('delete_faces requires face_refs')
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
            op=BRepAlgoAPI_Defeaturing(); op.SetShape(self.shape.wrapped)
            for ref in refs:op.AddFaceToRemove(match_face(self.shape,ref).wrapped)
            op.Build()
            if not op.IsDone():raise ValueError('Delete Faces healing failed')
            out=cq.Shape.cast(op.Shape())
            if out is None or out.isNull() or not out.isValid():raise ValueError('Delete Faces produced invalid geometry')
            self.shape=out
        elif kind=='thread_face':
            if self.shape is None:raise ValueError('No body for thread_face')
            face=match_face(self.shape,p.get('face_ref'))
            if str(face.geomType()).upper()!='CYLINDER':raise ValueError('ThreadFeature requires a cylindrical face')
            cyl=face._geomAdaptor().Cylinder(); ax=cyl.Axis(); loc=ax.Location(); av=np.array([ax.Direction().X(),ax.Direction().Y(),ax.Direction().Z()],float); av=av/np.linalg.norm(av); a0=np.array([loc.X(),loc.Y(),loc.Z()],float)
            verts=face.Vertices(); vals=[float(np.dot(np.array(v.Center().toTuple(),float)-a0,av)) for v in verts]; lo,hi=min(vals),max(vals)
            L=float(self.value(p['length_mm'])); off=float(self.value(p.get('offset_mm',0))); start=(hi-off-L) if p.get('reverse_direction') else (lo+off); origin=a0+av*start
            major=float(self.value(p['major_diameter_mm'])); pitch=float(self.value(p['pitch_mm'])); internal=bool(p.get('internal',False))
            def orient(tool):
                z=np.array([0.,0.,1.]); d=av; dot=float(np.clip(np.dot(z,d),-1,1)); out=tool
                if dot < 1-1e-10:
                    if dot < -1+1e-10:out=out.rotate((0,0,0),(1,0,0),180.0)
                    else:
                        axis=np.cross(z,d); axis=axis/np.linalg.norm(axis); out=out.rotate((0,0,0),tuple(float(x) for x in axis),math.degrees(math.acos(dot)))
                return out.translate(tuple(float(x) for x in origin))
            if internal:
                cutter,_=internal_metric_thread_cut(major,pitch,L,0.0,bool(p.get('right_handed',True)))
                out=self.shape
                for solid in cutter.Solids():out=cq.Workplane(obj=out).cut(orient(solid)).val()
                self.shape=out
            else:
                envelope=orient(cq.Solid.makeCylinder(major/2.0,L,(0,0,0),(0,0,1)))
                base=cq.Workplane(obj=self.shape).cut(envelope).val(); threaded,_=external_metric_thread(major,pitch,L,0.0,bool(p.get('right_handed',True)))
                self.shape=cq.Workplane(obj=base).union(orient(threaded)).val()
        elif kind=='split_body':
            if self.shape is None:raise ValueError('No body for split_body')
            recp=self._plane_record(p['plane']); plane=cq.Plane(origin=tuple(recp['origin']),xDir=tuple(recp.get('x_dir',[1,0,0])),normal=tuple(recp['direction'])); keep=str(p.get('keep','both')).lower()
            result=cq.Workplane(plane,obj=self.shape).split(keepTop=keep in ('both','positive','top'),keepBottom=keep in ('both','negative','bottom'))
            vals=[v for v in result.vals() if v is not None]
            if not vals:raise ValueError('split_body produced no geometry')
            self.shape=vals[0] if len(vals)==1 else cq.Compound.makeCompound(vals)
        elif kind=='combine_bodies':
            solids=list(self.shape.Solids()) if self.shape is not None else []
            if len(solids)<2:raise ValueError('combine_bodies requires at least two solids')
            bi=int(p.get('base_index',1))-1; tis=[int(x)-1 for x in p.get('tool_indices',[])]
            if bi<0 or bi>=len(solids) or not tis or any(i<0 or i>=len(solids) or i==bi for i in tis):raise ValueError('Invalid body indices')
            op=str(p.get('operation','join')).lower(); out=solids[bi]
            for i in tis:
                if op=='join':out=cq.Workplane(obj=out).union(solids[i]).val()
                elif op=='cut':out=cq.Workplane(obj=out).cut(solids[i]).val()
                elif op=='intersect':out=cq.Workplane(obj=out).intersect(solids[i]).val()
                else:raise ValueError('operation must be join|cut|intersect')
            consumed={bi,*tis}; remain=[s for i,s in enumerate(solids) if i not in consumed]
            if p.get('keep_tools'):remain.extend(solids[i] for i in tis)
            self.shape=out if not remain else cq.Compound.makeCompound([out,*remain])
        elif kind=='hole':
            if self.shape is None:raise ValueError('No body for hole')
            dia=self.value(p['diameter_mm']); spec=p.get('face') or {'kind':'planar','normal':'+Z','extreme':'max'}
            legacy_exc=None
            try:face=select_face(self.shape,spec)
            except Exception as exc:
                legacy_exc=exc; fallback=p.get('_face_ref')
                if fallback is None:raise
                try:face=select_face(self.shape,fallback)
                except Exception:raise legacy_exc
            ff=face_frame(face)
            # Workplane axes are deterministic; points are supplied in world coordinates by ipt-mcp contract.
            origin=ff['origin']; normal=np.asarray(ff['direction'],float); xdir=(1,0,0) if abs(normal[0])<0.9 else (0,1,0); plane_obj=cq.Plane(origin=tuple(origin),xDir=xdir,normal=tuple(normal)); plane=cq.Workplane(plane_obj,obj=self.shape)
            world_pts=[]; pts=[]
            for q in p.get('points_mm',[]):
                world=[float(v) for v in q]; world_pts.append(world)
                local=plane_obj.toLocalCoords(cq.Vector(*world)); pts.append((local.x,local.y))
            if not pts:raise ValueError('points_mm must contain at least one point')
            htype=str(p.get('kind','drilled')).lower(); through=bool(p.get('through',True)); depth=None if through else self.value(p.get('depth_mm'))
            tapped=str(p.get('tapped_designation') or '').strip()
            if tapped:
                # Inventor-style tapped hole: generate the actual internal helical B-Rep instead of
                # storing thread metadata only.  The designation controls nominal major diameter/pitch.
                m=re.fullmatch(r'(?i)M\s*(\d+(?:\.\d+)?)\s*(?:[x×]\s*(\d+(?:\.\d+)?))?',tapped)
                if not m: raise ValueError(f'Unsupported metric tapped designation: {tapped!r}; expected M10 or M10x1.5')
                major=float(m.group(1)); pitch=metric_pitch(major,float(m.group(2)) if m.group(2) else None)
                bb=self.shape.BoundingBox(); through_len=max(1.0,math.sqrt(bb.xlen**2+bb.ylen**2+bb.zlen**2)*2.0)
                base_depth=through_len if through else float(depth)
                requested=p.get('tapped_thread_depth_mm')
                thread_len=base_depth if requested is None else min(base_depth,float(self.value(requested)))
                inward=(-normal/np.linalg.norm(normal)).tolist()
                def orient_from_z(tool):
                    z=np.array([0.0,0.0,1.0]); d=np.asarray(inward,float); dot=float(np.clip(np.dot(z,d),-1,1))
                    if dot>1-1e-10:return tool
                    if dot<-1+1e-10:return tool.rotate((0,0,0),(1,0,0),180.0)
                    axis=np.cross(z,d); axis=axis/np.linalg.norm(axis); angle=math.degrees(math.acos(dot)); return tool.rotate((0,0,0),tuple(float(x) for x in axis),angle)
                out=self.shape
                thread_meta=None
                for world in world_pts:
                    cut,thread_meta=internal_metric_thread_cut(major,pitch,thread_len,0.0,bool(p.get('tapped_right_handed',True)))
                    # Apply each cutter solid separately. This avoids an OCC multi-solid
                    # Compound boolean quirk that can otherwise omit the helical groove.
                    for tool_solid in cut.Solids():
                        tool_solid=orient_from_z(tool_solid).translate(tuple(world))
                        out=cq.Workplane(obj=out).cut(tool_solid).val()
                    # Counterbore/countersink are independent entrance cuts so they do not erase the thread.
                    if htype=='counterbore':
                        extra=cq.Solid.makeCylinder(float(self.value(p['cbore_diameter_mm']))/2.0,float(self.value(p['cbore_depth_mm'])),tuple(world),tuple(inward)); out=cq.Workplane(obj=out).cut(extra).val()
                    elif htype=='countersink':
                        rb=float(self.value(p['csink_diameter_mm']))/2.0; rs=float(thread_meta['minor_diameter_mm'])/2.0; ang=math.radians(float(self.value(p.get('csink_angle_deg',82)))/2.0); h=max(1e-6,(rb-rs)/math.tan(ang)); extra=cq.Solid.makeCone(rb,rs,h,tuple(world),tuple(inward)); out=cq.Workplane(obj=out).cut(extra).val()
                p['tapped_derived']={'designation':f'M{major:g}x{pitch:g}','class':p.get('tapped_class','6H'),'right_handed':bool(p.get('tapped_right_handed',True)),**(thread_meta or {})}
                self.shape=out
            else:
                plane=plane.pushPoints(pts)
                if htype=='counterbore': result=plane.cboreHole(dia,self.value(p['cbore_diameter_mm']),self.value(p['cbore_depth_mm']),depth=depth)
                elif htype=='countersink': result=plane.cskHole(dia,self.value(p['csink_diameter_mm']),self.value(p.get('csink_angle_deg',82)),depth=depth)
                else:result=plane.hole(dia,depth=depth)
                self.shape=result.val()
        elif kind in ('fillet','chamfer'):
            if self.shape is None:raise ValueError('No body')
            legacy_ids=p.get('edge_ids') or []; refs=p.get('edge_refs') or []
            try:
                if not legacy_ids:raise ValueError('no legacy edge ids')
                edges=match_edge_ids(self.shape,legacy_ids)
            except Exception as legacy_exc:
                if not refs:
                    if legacy_ids:raise
                    raise ValueError('edge_ids is required')
                try:edges=match_edge_ids(self.shape,refs)
                except Exception:
                    if legacy_ids:raise legacy_exc
                    raise
            ew=cq.Workplane(obj=self.shape).newObject(edges)
            size=self.value(p.get('radius_mm',p.get('distance_mm'))); self.shape=(ew.fillet(size) if kind=='fillet' else ew.chamfer(size)).val()
        elif kind in ('circular_pattern','rectangular_pattern'):
            names=p.get('feature_names') or []
            if not names:raise ValueError('feature_names is required')
            sources=[]
            for name in names:
                f=next((x for x in self.features if x.name==name),None)
                if not f:raise ValueError(f'Unknown source feature: {name}')
                sources.append(f)
            effects=[self._feature_effect(f) for f in sources]
            if kind=='circular_pattern':
                count=int(p['count']); angle=self.value(p.get('angle_deg',360)); axis_origin,av=self._axis_vector(p.get('axis','Z Axis'),p.get('_axis_ref')); av=tuple(float(x) for x in av); axis_origin=tuple(float(x) for x in axis_origin); natural=bool(p.get('natural_direction',True)); sign=1 if natural else -1
                for i in range(1,count):
                    a=sign*angle*i/count
                    for add,cut in effects:
                        self._apply_pattern_effect(add,cut,lambda sh,aa=a:cq.Workplane(obj=sh).rotate(axis_origin,tuple(axis_origin[k]+av[k] for k in range(3)),aa).val())
            else:
                _,d1=self._axis_vector(p.get('dir1','X Axis'),p.get('_dir1_ref')); d1=tuple(float(x) for x in d1); n1=int(p['count1']); s1=self.value(p['spacing_mm1'])*(1 if p.get('natural_direction1',True) else -1)
                dir2=p.get('dir2'); n2=int(p.get('count2') or 1); s2=self.value(p.get('spacing_mm2') or 0)*(1 if p.get('natural_direction2',True) else -1); d2=(0.0,0.0,0.0)
                if dir2:
                    _,d2=self._axis_vector(dir2,p.get('_dir2_ref')); d2=tuple(float(x) for x in d2)
                for i in range(n1):
                    for j in range(n2):
                        if i==0 and j==0:continue
                        vec=tuple(d1[k]*i*s1+d2[k]*j*s2 for k in range(3))
                        for add,cut in effects:
                            self._apply_pattern_effect(add,cut,lambda sh,v=vec:cq.Workplane(obj=sh).translate(v).val())
        else:raise ValueError(f'Unsupported feature: {kind}')
        if record:self.features.append(rec)

    def _clone_for_history_replay(self):
        tmp=CadDocument(); tmp.reset('part')
        tmp.title=self.title; tmp.units=self.units; tmp.material=self.material
        tmp.parameters=copy.deepcopy(self.parameters); tmp.properties=copy.deepcopy(self.properties)
        tmp.sketches=copy.deepcopy(self.sketches); tmp.sketches3d=copy.deepcopy(self.sketches3d); tmp.work_planes=copy.deepcopy(self.work_planes); tmp.work_axes=copy.deepcopy(self.work_axes); tmp.work_points=copy.deepcopy(self.work_points); tmp.imates=copy.deepcopy(self.imates)
        tmp.imported_shape=self.imported_shape; tmp.import_source=self.import_source; tmp.shape=self.imported_shape
        return tmp

    def _feature_effect(self,source):
        """Return additive and subtractive B-Rep deltas contributed by one history feature.

        This allows patterns to reproduce arbitrary prior features (including fillets,
        chamfers and custom native extensions) instead of whitelisting only three kinds.
        """
        try: idx=next(i for i,f in enumerate(self.features) if f is source or f.name==source.name)
        except StopIteration: raise ValueError(f'Pattern source must precede the pattern feature: {source.name}')
        tmp=self._clone_for_history_replay()
        for f in self.features[:idx]:
            if not f.suppressed: tmp._apply_feature(copy.deepcopy(f),record=True)
            else: tmp.features.append(copy.deepcopy(f))
        before=tmp.shape
        tmp._apply_feature(copy.deepcopy(source),record=True)
        after=tmp.shape
        if after is None: raise ValueError(f'Pattern source produced no geometry: {source.name}')
        if before is None:return after,None
        add=cut=None
        try:
            a=cq.Workplane(obj=after).cut(before).val()
            if a is not None and a.Volume()>1e-9:add=a
        except Exception:pass
        try:
            c=cq.Workplane(obj=before).cut(after).val()
            if c is not None and c.Volume()>1e-9:cut=c
        except Exception:pass
        if add is None and cut is None:
            # Non-volume-changing feature: fall back to legacy feature tool if possible,
            # otherwise refuse rather than patterning the wrong geometry.
            try:return self._feature_tool(source),None
            except Exception as exc:raise ValueError(f'Cannot derive a repeatable B-Rep delta for feature {source.name}: {exc}')
        return add,cut

    def _apply_pattern_effect(self,add,cut,transform):
        if add is not None:self._combine(transform(add),'join')
        if cut is not None:self._combine(transform(cut),'cut')

    def _feature_tool(self,source):
        if source.kind in ('extrude','revolve'):
            sm=self.sketches[source.params['sketch_name']]; wp=cq.Workplane(self._plane(sm.plane,sm.plane_ref)).placeSketch(self.build_sketch(sm))
            if source.kind=='extrude':
                dist=self.value(source.params.get('distance_mm',10)); direction=source.params.get('direction','positive'); dist=-abs(dist) if direction=='negative' else dist; return wp.extrude(dist,combine=False,both=direction=='symmetric').val()
            a0,a1=self._axis_for_revolve(sm,source.params.get('axis_id','YAxis'))
            angle=float(self.value(source.params.get('angle_deg',360)))
            if abs(abs(angle)-360.0) < 1e-6 or abs(angle) < 1e-12:
                cq_angle=360.0
            elif angle < 0:
                a0,a1=a1,a0; cq_angle=abs(angle)
            else:
                cq_angle=angle
            return wp.revolve(cq_angle,axisStart=a0,axisEnd=a1,combine=False).val()
        if source.kind=='hole':
            p=source.params; dia=float(self.value(p['diameter_mm'])); spec=p.get('face') or {'kind':'planar','normal':'+Z','extreme':'max'}
            try:face=select_face(self.shape,spec)
            except Exception as legacy_exc:
                fallback=p.get('_face_ref')
                if fallback is None:raise
                try:face=select_face(self.shape,fallback)
                except Exception:raise legacy_exc
            fr=face_frame(face); normal=np.asarray(fr['direction'],float); inward=(-normal).tolist(); bb=self.shape.BoundingBox(); through_len=max(1.0,math.sqrt(bb.xlen**2+bb.ylen**2+bb.zlen**2)*2.0)
            depth=through_len if p.get('through',True) else float(self.value(p['depth_mm'])); solids=[]
            for q in p.get('points_mm') or []:
                pt=[float(v) for v in q]; solids.append(cq.Solid.makeCylinder(dia/2.0,depth,pt,inward))
                kind=str(p.get('kind','drilled')).lower()
                if kind=='counterbore':
                    solids.append(cq.Solid.makeCylinder(float(self.value(p['cbore_diameter_mm']))/2.0,float(self.value(p['cbore_depth_mm'])),pt,inward))
                elif kind=='countersink':
                    rb=float(self.value(p['csink_diameter_mm']))/2.0; rs=dia/2.0; ang=math.radians(float(self.value(p.get('csink_angle_deg',82)))/2.0); h=max(1e-6,(rb-rs)/math.tan(ang)); solids.append(cq.Solid.makeCone(rb,rs,h,pt,inward))
            if not solids:raise ValueError('Hole pattern source has no points_mm')
            return solids[0] if len(solids)==1 else cq.Compound.makeCompound(solids)
        raise ValueError('Pattern source must be an extrude, revolve, or hole feature in CADia')

    def has_flat_pattern(self):
        return bool(self.doc_type=='part' and self.sheet_metal and self.sheet_metal.get('base'))

    def flat_pattern(self):
        if not self.has_flat_pattern():
            raise ValueError('WRONG_DOCUMENT_TYPE: source=flat_pattern requires a real sheet-metal flat pattern; use cad_create_sheet_metal_base first')
        return flat_pattern_sketch(self.sheet_metal), flat_pattern_metadata(self.sheet_metal)

    def topology(self):
        # Internal topology records are cached on the immutable body wrapper for speed.
        # Return copies at the public document boundary so callers cannot mutate that cache.
        return {'faces':copy.deepcopy(face_records(self.shape)),'edges':copy.deepcopy(edge_records(self.shape)),'vertices':copy.deepcopy(vertex_records(self.shape))}

    def _project_one_edge_data(self, sm, e, *, source_ref=None):
        plane=self.plane_object(sm.plane,sm.plane_ref); geom=str(e.geomType()); verts=e.Vertices()
        common={'projected':True,'source_geom':geom}
        if source_ref is not None: common['source_edge_ref']=copy.deepcopy(source_ref)
        if geom=='LINE' and len(verts)>=2:
            a=plane.toLocalCoords(verts[0].Center()); b=plane.toLocalCoords(verts[-1].Center())
            return 'line',{'x1':a.x,'y1':a.y,'x2':b.x,'y2':b.y,**common}
        if geom=='CIRCLE' and bool(e.IsClosed()):
            try:
                circ=e._geomAdaptor().Circle(); center=circ.Location(); axis=circ.Axis().Direction(); n=np.asarray([axis.X(),axis.Y(),axis.Z()],float); pn=np.asarray(plane.zDir.toTuple(),float)
                if abs(float(np.dot(n/np.linalg.norm(n),pn/np.linalg.norm(pn))))>1-1e-7:
                    c=plane.toLocalCoords(cq.Vector(center.X(),center.Y(),center.Z()))
                    return 'circle',{'cx':c.x,'cy':c.y,'r':float(circ.Radius()),**common}
            except Exception:pass
        count=max(12,min(96,int(max(12.0,float(e.Length())/1.5))))
        pts=[]
        try:samples=e.discretize(count)
        except TypeError:samples=e.discretize(tolerance=max(0.02,float(e.Length())/200.0))
        for q in samples:
            loc=plane.toLocalCoords(q); xy=(float(loc.x),float(loc.y))
            if not pts or math.dist(pts[-1],xy)>1e-9:pts.append(xy)
        periodic=bool(e.IsClosed())
        if periodic and len(pts)>2 and math.dist(pts[0],pts[-1])<1e-8:pts.pop()
        if len(pts)<2:raise ValueError(f'Could not project edge {geom}')
        return 'spline',{'points':pts,'periodic':periodic,**common}

    def _refresh_projected_geometry(self,sm):
        if self.shape is None:return False
        changed=False
        for ent in sm.entities:
            ref=ent.data.get('source_edge_ref') if ent.data.get('projected') else None
            if not ref:continue
            # Each entity is independent.  A failed rebind keeps the exact legacy copy.
            try:
                edge=match_edge(self.shape,ref); kind,data=self._project_one_edge_data(sm,edge,source_ref=ref)
            except Exception:
                continue
            ent.kind=kind; ent.data=data; changed=True
        return changed

    def project_edges_to_sketch(self,sm,edge_ids):
        if self.shape is None:raise ValueError('No model geometry to project')
        edges=match_edge_ids(self.shape,edge_ids); new=[]
        for requested,e in zip(edge_ids,edges):
            tag=f'e{len(sm.entities)+1}'
            try:ref=make_edge_ref(self.shape,requested)
            except Exception:ref=None
            kind,data=self._project_one_edge_data(sm,e,source_ref=ref)
            sm.entities.append(SketchEntity(kind,tag,data)); new.append(tag)
        return new

    def _plane_record(self,ref):
        if isinstance(ref,str):
            ref=canonical_plane_name(ref,allow_compat=True)
        if isinstance(ref,str) and ref in ORIGIN_PLANES:
            origin,direction=ORIGIN_PLANES[ref]
            xdir={'XY Plane':[1.0,0.0,0.0],'XZ Plane':[1.0,0.0,0.0],'YZ Plane':[0.0,1.0,0.0]}[ref]
            return {'origin':list(origin),'direction':list(direction),'x_dir':xdir}
        if isinstance(ref,str) and ref in self.work_planes:return self.work_planes[ref]
        if self.shape is not None:
            f=match_face(self.shape,ref); fr=face_frame(f)
            if fr['kind']!='plane':raise ValueError(f'Plane reference must be planar: {ref}')
            n=np.asarray(fr['direction'],float); trial=np.array([1.0,0.0,0.0]) if abs(n[0])<0.9 else np.array([0.0,1.0,0.0]); x=trial-n*np.dot(trial,n); x=x/np.linalg.norm(x)
            return {'origin':list(fr['origin']),'direction':list(fr['direction']),'x_dir':x.tolist()}
        raise ValueError(f'Unknown plane reference: {ref}')

    def _point_record(self,ref):
        if isinstance(ref,(list,tuple)) and len(ref)==3:return {'origin':[float(self.value(x)) for x in ref],'kind':'point','direction':None}
        if isinstance(ref,str):ref=canonical_point_name(ref,allow_compat=True)
        if isinstance(ref,str) and ref==CENTER_POINT_NAME:return {'origin':[0.0,0.0,0.0],'kind':'point','direction':None}
        if isinstance(ref,str) and ref in self.work_points:
            try:return self._refresh_work_point(ref)
            except Exception:return self.work_points[ref]
        if self.shape is not None:
            v=match_vertex_id(self.shape,ref); c=v.Center(); return {'origin':[c.x,c.y,c.z],'kind':'point','direction':None}
        raise ValueError(f'Unknown point reference: {ref}')

    def _refresh_work_plane(self,name):
        wp=self.work_planes[name]; refs=wp.get('refs') or []
        if not refs:return wp
        t=wp.get('type','offset'); saved=dict(wp); persistent=saved.get('persistent_refs') or []
        del self.work_planes[name]
        try:
            fresh=self.create_work_plane(t,refs,saved.get('offset_mm'),name=name)
            fresh['offset_mm']=saved.get('offset_mm'); return fresh
        except Exception as legacy_exc:
            # Only a failed legacy reference enters persistent-topology rebinding.
            self.work_planes.pop(name,None)
            if persistent:
                try:
                    fresh=self.create_work_plane(t,persistent,saved.get('offset_mm'),name=name)
                    fresh['refs']=list(refs); fresh['persistent_refs']=copy.deepcopy(persistent); fresh['offset_mm']=saved.get('offset_mm'); return fresh
                except Exception:pass
            self.work_planes[name]=saved; raise legacy_exc

    def _refresh_work_axis(self,name):
        wa=self.work_axes[name]; refs=wa.get('refs') or []
        if not refs:return wa
        t=wa.get('type','edge'); saved=dict(wa); persistent=saved.get('persistent_refs') or []
        del self.work_axes[name]
        try:return self.create_work_axis(t,refs,name=name)
        except Exception as legacy_exc:
            self.work_axes.pop(name,None)
            if persistent:
                try:
                    fresh=self.create_work_axis(t,persistent,name=name); fresh['refs']=list(refs); fresh['persistent_refs']=copy.deepcopy(persistent); return fresh
                except Exception:pass
            self.work_axes[name]=saved; raise legacy_exc

    def _refresh_work_point(self,name):
        wp=self.work_points[name]; refs=wp.get('refs') or []
        if not refs or wp.get('type')=='fixed':return wp
        t=wp.get('type','vertex'); saved=dict(wp); persistent=saved.get('persistent_refs') or []
        del self.work_points[name]
        try:return self.create_work_point(t,refs,point_mm=saved.get('origin'),name=name)
        except Exception as legacy_exc:
            self.work_points.pop(name,None)
            if persistent:
                try:
                    fresh=self.create_work_point(t,persistent,point_mm=saved.get('origin'),name=name); fresh['refs']=list(refs); fresh['persistent_refs']=copy.deepcopy(persistent); return fresh
                except Exception:pass
            self.work_points[name]=saved; raise legacy_exc

    def _persistent_plane_ref(self,ref):
        if isinstance(ref,dict):return copy.deepcopy(ref)
        if isinstance(ref,str):ref=canonical_plane_name(ref,allow_compat=True)
        if isinstance(ref,str) and (ref in ORIGIN_PLANES or ref in self.work_planes):return ref
        if self.shape is not None:
            try:return make_face_ref(self.shape,ref)
            except Exception:pass
        return ref

    def _persistent_vertex_ref(self,ref):
        if isinstance(ref,dict):return copy.deepcopy(ref)
        if isinstance(ref,str) and ref in self.work_points:return ref
        if self.shape is not None:
            try:return make_vertex_ref(self.shape,ref)
            except Exception:pass
        return ref

    def _persistent_edge_ref(self,ref):
        if isinstance(ref,dict):return copy.deepcopy(ref)
        if self.shape is not None:
            try:return make_edge_ref(self.shape,ref)
            except Exception:pass
        return ref

    def create_work_plane(self,type_,refs,offset=None,name=None):
        name=name or f'Work Plane {len(self.work_planes)+1}'; t=str(type_).lower(); origin=[0,0,0]; direction=[0,0,1]; xdir=[1,0,0]
        if t=='offset':
            if not refs:raise ValueError('offset work plane requires refs=[plane_or_face_id]')
            base=self._plane_record(refs[0]); direction=list(base['direction']); n=np.asarray(direction,float); n=n/np.linalg.norm(n); origin=(np.asarray(base['origin'],float)+n*float(self.value(offset or 0))).tolist(); xdir=list(base.get('x_dir',[1,0,0]))
        elif t=='three_points':
            if len(refs)!=3:raise ValueError('three_points requires refs=[3 vertex ids]')
            pts=[self._point_record(r)['origin'] for r in refs]; a=np.array(pts[0],float); b=np.array(pts[1],float); c=np.array(pts[2],float); n=np.cross(b-a,c-a); nn=np.linalg.norm(n)
            if nn<1e-10:raise ValueError('three_points work plane requires three non-collinear points')
            n=n/nn; origin=a.tolist(); direction=n.tolist(); xd=b-a; xd=xd/np.linalg.norm(xd); xdir=xd.tolist()
        elif t=='tangent':
            if len(refs)!=2:raise ValueError('tangent requires refs=[surface_face_id, plane_id]')
            if self.shape is None:raise ValueError('tangent work plane requires model geometry')
            f=match_face(self.shape,refs[0]); base=self._plane_record(refs[1]); geom=str(f.geomType()); n=np.asarray(base['direction'],float); n=n/np.linalg.norm(n); b0=np.asarray(base['origin'],float); xdir=list(base.get('x_dir',[1,0,0]))
            if geom=='CYLINDER':
                cyl=f._geomAdaptor().Cylinder(); ax=cyl.Axis(); a0=np.array([ax.Location().X(),ax.Location().Y(),ax.Location().Z()],float); av=np.array([ax.Direction().X(),ax.Direction().Y(),ax.Direction().Z()],float); av=av/np.linalg.norm(av)
                if abs(float(np.dot(n,av)))>1e-6:raise ValueError('For a cylinder, the reference plane must be parallel to the cylinder axis')
                r=float(cyl.Radius()); signed=float(np.dot(b0-a0,n)); sign=1.0 if signed>=0 else -1.0; origin=(a0+n*r*sign).tolist(); direction=n.tolist()
            elif geom=='SPHERE':
                sph=f._geomAdaptor().Sphere(); c=sph.Location(); c0=np.array([c.X(),c.Y(),c.Z()],float); r=float(sph.Radius()); signed=float(np.dot(b0-c0,n)); sign=1.0 if signed>=0 else -1.0; origin=(c0+n*r*sign).tolist(); direction=n.tolist()
            else:
                raise ValueError(f'tangent work plane supports cylindrical or spherical faces in CADia; got {geom}')
        else:raise ValueError('type must be offset|three_points|tangent')
        if t=='offset':persistent=[self._persistent_plane_ref(refs[0])] if refs else []
        elif t=='three_points':persistent=[self._persistent_vertex_ref(r) for r in refs]
        else:persistent=[self._persistent_plane_ref(refs[0]),self._persistent_plane_ref(refs[1])] if len(refs)>=2 else list(refs)
        self.work_planes[name]={'name':name,'kind':'plane','origin':[float(x) for x in origin],'direction':[float(x) for x in direction],'x_dir':[float(x) for x in xdir],'type':t,'refs':list(refs),'persistent_refs':persistent,'offset_mm':None if offset is None else offset}; return self.work_planes[name]

    def create_work_axis(self,type_,refs,name=None):
        name=name or f'Work Axis {len(self.work_axes)+1}'; t=str(type_).lower(); origin=[0,0,0]; direction=[0,0,1]
        if t=='two_points':
            if len(refs)!=2:raise ValueError('two_points requires two vertex refs')
            a=np.array(self._point_record(refs[0])['origin'],float); b=np.array(self._point_record(refs[1])['origin'],float); v=b-a; n=np.linalg.norm(v)
            if n<1e-10:raise ValueError('two_points work axis requires distinct points')
            origin=a.tolist(); direction=(v/n).tolist()
        elif t=='edge':
            if len(refs)!=1:raise ValueError('edge requires one edge ref')
            e=match_edge_ids(self.shape,refs)[0]
            if str(e.geomType())!='LINE':raise ValueError(f'edge work axis requires a linear edge; got {e.geomType()}')
            vs=e.Vertices();
            if len(vs)<2:raise ValueError('edge must have two distinct vertices')
            a=vs[0].Center(); b=vs[-1].Center(); v=np.array([b.x-a.x,b.y-a.y,b.z-a.z],float); n=np.linalg.norm(v)
            if n<1e-10:raise ValueError('edge work axis requires non-zero length')
            origin=[a.x,a.y,a.z]; direction=(v/n).tolist()
        elif t=='plane_intersection':
            if len(refs)!=2:raise ValueError('plane_intersection requires 2 plane refs')
            p1,p2=self._plane_record(refs[0]),self._plane_record(refs[1]); n1=np.asarray(p1['direction'],float); n2=np.asarray(p2['direction'],float); n1=n1/np.linalg.norm(n1); n2=n2/np.linalg.norm(n2); v=np.cross(n1,n2); vv=float(np.dot(v,v))
            if vv<1e-12:raise ValueError('plane_intersection requires two non-parallel planes')
            d1=float(np.dot(n1,np.asarray(p1['origin'],float))); d2=float(np.dot(n2,np.asarray(p2['origin'],float)))
            point=(d1*np.cross(n2,v)+d2*np.cross(v,n1))/vv; origin=point.tolist(); direction=(v/math.sqrt(vv)).tolist()
        elif t=='normal_to_face_through_point':
            if len(refs)!=2:raise ValueError('normal_to_face_through_point requires [plane_or_face_id, point_id]')
            base=self._plane_record(refs[0]); p=self._point_record(refs[1]); origin=list(p['origin']); direction=list(base['direction'])
        else:raise ValueError('type must be two_points|edge|plane_intersection|normal_to_face_through_point')
        if t=='two_points':persistent=[self._persistent_vertex_ref(r) for r in refs]
        elif t=='edge':persistent=[self._persistent_edge_ref(refs[0])] if refs else []
        elif t=='plane_intersection':persistent=[self._persistent_plane_ref(r) for r in refs]
        else:persistent=[self._persistent_plane_ref(refs[0]),self._persistent_vertex_ref(refs[1])] if len(refs)>=2 else list(refs)
        self.work_axes[name]={'name':name,'kind':'axis','origin':[float(x) for x in origin],'direction':[float(x) for x in direction],'type':t,'refs':list(refs),'persistent_refs':persistent}; return self.work_axes[name]

    def create_work_point(self,type_,refs=None,point_mm=None,name=None):
        """Inventor WorkPoints-style construction point.

        Supported definitions intentionally map to stable Inventor concepts: fixed point,
        model vertex, intersection of two linear edges, and center of a cylindrical/
        spherical face.  The record stays parametric by retaining persistent references.
        """
        name=name or f'Work Point {len(self.work_points)+1}'; t=str(type_).lower(); refs=list(refs or [])
        origin=None; persistent=[]
        if t=='fixed':
            if point_mm is None or len(point_mm)!=3:raise ValueError('fixed work point requires point_mm=[x,y,z]')
            origin=[float(self.value(x)) for x in point_mm]
        elif t=='vertex':
            if len(refs)!=1:raise ValueError('vertex work point requires refs=[vertex_ref]')
            v=match_vertex_id(self.shape,refs[0]); c=v.Center(); origin=[c.x,c.y,c.z]; persistent=[self._persistent_vertex_ref(refs[0])]
        elif t=='two_lines':
            if len(refs)!=2:raise ValueError('two_lines work point requires refs=[linear_edge1,linear_edge2]')
            es=[match_edge_ids(self.shape,[r])[0] for r in refs]
            data=[]
            for e in es:
                if str(e.geomType())!='LINE':raise ValueError('two_lines work point requires two linear edges')
                vs=e.Vertices();
                if len(vs)<2:raise ValueError('linear edge requires two vertices')
                a=np.array(vs[0].Center().toTuple(),float); b=np.array(vs[-1].Center().toTuple(),float); data.append((a,b-a))
            p1,d1=data[0]; p2,d2=data[1]; A=np.column_stack((d1,-d2)); rhs=p2-p1
            sol=np.linalg.lstsq(A,rhs,rcond=None)[0]; q1=p1+d1*sol[0]; q2=p2+d2*sol[1]
            if np.linalg.norm(q1-q2)>1e-5:raise ValueError('two_lines work point requires intersecting lines')
            origin=((q1+q2)/2.0).tolist(); persistent=[self._persistent_edge_ref(r) for r in refs]
        elif t=='center':
            if len(refs)!=1:raise ValueError('center work point requires refs=[cylindrical_or_spherical_face]')
            f=match_face(self.shape,refs[0]); typ=str(f.geomType()).upper()
            if typ=='SPHERE':
                g=f._geomAdaptor().Sphere(); c=g.Location(); origin=[c.X(),c.Y(),c.Z()]
            elif typ=='CYLINDER':
                g=f._geomAdaptor().Cylinder(); ax=g.Axis(); c=ax.Location(); origin=[c.X(),c.Y(),c.Z()]
            elif typ=='TORUS':
                g=f._geomAdaptor().Torus(); c=g.Location(); origin=[c.X(),c.Y(),c.Z()]
            else:raise ValueError('center work point supports cylindrical, spherical, or toroidal faces')
            persistent=[self._persistent_plane_ref(refs[0])]
        else:raise ValueError('type must be fixed|vertex|two_lines|center')
        rec={'name':name,'kind':'point','origin':[float(x) for x in origin],'direction':None,'type':t,'refs':refs,'persistent_refs':persistent}
        self.work_points[name]=rec; return rec

    def create_sketch3d(self,name=None):
        name=name or f'3D Sketch{len(self.sketches3d)+1}'
        if name in self.sketches3d:raise ValueError(f'3D sketch already exists: {name}')
        rec={'name':name,'entities':[]}; self.sketches3d[name]=rec; return rec

    def edit_sketch3d(self,action,name=None,entity_ids=None,start_mm=None,end_mm=None,points_mm=None,translate_mm=None,rotate_axis=None,rotate_deg=0.0,pivot_mm=None,new_name=None):
        action=str(action).lower()
        if action=='create':return {'sketch3d_name':self.create_sketch3d(name)['name']}
        if not name or name not in self.sketches3d:raise ValueError(f'3D sketch not found: {name}')
        sm=self.sketches3d[name]; ents=sm['entities']
        if action in ('add_line','add_spline'):
            if action=='add_line':
                if start_mm is None or end_mm is None or len(start_mm)!=3 or len(end_mm)!=3:raise ValueError('add_line requires start_mm/end_mm 3D points')
                data={'start_mm':[float(self.value(x)) for x in start_mm],'end_mm':[float(self.value(x)) for x in end_mm]}; kind='line'
            else:
                pts=points_mm or []
                if len(pts)<2 or any(len(q)!=3 for q in pts):raise ValueError('add_spline requires at least two 3D points')
                data={'points_mm':[[float(self.value(x)) for x in q] for q in pts]}; kind='spline'
            tag=f'e{len(ents)+1}'; ents.append({'kind':kind,'tag':tag,'data':data}); return {'sketch3d_name':name,'entity_id':tag,'kind':kind}
        if action=='transform':
            ids=set(str(x) for x in (entity_ids or [])); targets=[e for e in ents if e['tag'] in ids]
            if not targets:raise ValueError('transform requires valid entity_ids')
            tr=np.array(translate_mm or [0,0,0],float); pivot=np.array(pivot_mm or [0,0,0],float); axis=np.array(rotate_axis or [0,0,1],float); nn=np.linalg.norm(axis)
            if nn<1e-12:raise ValueError('rotate_axis must be non-zero'); axis=axis/nn; a=math.radians(float(rotate_deg)); c=math.cos(a); si=math.sin(a)
            K=np.array([[0,-axis[2],axis[1]],[axis[2],0,-axis[0]],[-axis[1],axis[0],0]],float); R=np.eye(3)*c+(1-c)*np.outer(axis,axis)+si*K
            def xf(q):return (pivot+R@(np.array(q,float)-pivot)+tr).tolist()
            for e in targets:
                if e['kind']=='line':e['data']['start_mm']=xf(e['data']['start_mm']);e['data']['end_mm']=xf(e['data']['end_mm'])
                else:e['data']['points_mm']=[xf(q) for q in e['data']['points_mm']]
            return {'sketch3d_name':name,'entity_ids':[e['tag'] for e in targets]}
        if action=='rename':
            nn=str(new_name or '').strip()
            if not nn:raise ValueError('rename requires new_name')
            if nn in self.sketches3d:raise ValueError(f'3D sketch already exists: {nn}')
            self.sketches3d.pop(name); sm['name']=nn; self.sketches3d[nn]=sm
            for f in self.features:
                if f.params.get('path_sketch3d_name')==name:f.params['path_sketch3d_name']=nn
            return {'old_name':name,'new_name':nn}
        if action=='delete':
            deps=[f.name for f in self.features if f.params.get('path_sketch3d_name')==name]
            if deps:raise ValueError('3D sketch is consumed by features: '+', '.join(deps))
            del self.sketches3d[name]; return {'deleted_sketch3d':name}
        raise ValueError('action must be create|add_line|add_spline|transform|rename|delete')

    def sketch3d_path_points(self,name):
        if name not in self.sketches3d:raise ValueError(f'3D sketch not found: {name}')
        pts=[]
        for e in self.sketches3d[name]['entities']:
            seq=[e['data']['start_mm'],e['data']['end_mm']] if e['kind']=='line' else e['data']['points_mm']
            for q in seq:
                q=[float(x) for x in q]
                if not pts or np.linalg.norm(np.asarray(q)-np.asarray(pts[-1]))>1e-9:pts.append(q)
        if len(pts)<2:raise ValueError('3D sketch path requires at least two points')
        return pts

    def reposition_feature(self,feature_name,before=None,after=None):
        if bool(before)==bool(after):raise ValueError('Specify exactly one of before or after')
        idx=next((i for i,f in enumerate(self.features) if f.name==feature_name),None)
        if idx is None:raise ValueError(f'Feature not found: {feature_name}')
        target_name=before or after; tidx=next((i for i,f in enumerate(self.features) if f.name==target_name),None)
        if tidx is None:raise ValueError(f'Target feature not found: {target_name}')
        if target_name==feature_name:return {'feature_name':feature_name,'feature_index':idx}
        old=copy.deepcopy(self.features); rec=self.features.pop(idx)
        tidx=next(i for i,f in enumerate(self.features) if f.name==target_name); dest=tidx if before else tidx+1; self.features.insert(dest,rec)
        try:self.rebuild(0)
        except Exception:
            self.features=old; self.rebuild(0); raise
        return {'feature_name':feature_name,'feature_index':self.features.index(rec),'before':before,'after':after}

    def create_imate(self,name,type_,selector,offset_mm=0,insert_opposed=True,distance_mm=0):
        if self.doc_type!='part' or self.shape is None:raise ValueError('iMate requires an active part with geometry')
        face=select_face(self.shape,selector); fr=face_frame(face)
        try:face_ref=make_face_ref(self.shape,selector)
        except Exception:face_ref=None
        rec={'name':name,'type':type_,'kind':fr['kind'],'origin':fr['origin'],'direction':fr['direction'],'offset_mm':float(offset_mm),'insert_opposed':bool(insert_opposed),'distance_mm':float(distance_mm),'selector':selector}
        if face_ref is not None:rec['face_ref']=face_ref
        self.imates[name]=rec; return rec

    def list_interfaces_local(self):
        base=default_interfaces(); imates=[]
        for stored in self.imates.values():
            rec=dict(stored); ref=rec.get('face_ref')
            if ref and self.shape is not None:
                # Soft associativity: refresh when persistent topology can rebind; if it
                # cannot, retain the last known interface instead of creating a new failure.
                try:
                    fr=face_frame(match_face(self.shape,ref)); rec['kind']=fr['kind']; rec['origin']=fr['origin']; rec['direction']=fr['direction']
                except Exception:pass
            imates.append(rec)
        base['imates']=imates; base['work_planes'].extend([dict(v) for v in self.work_planes.values()]); base['work_axes'].extend([dict(v) for v in self.work_axes.values()]); base.setdefault('work_points',[]).extend([dict(v) for v in self.work_points.values()]); return base

    def place_occurrence(self,path,grounded=False,position_mm=None,rotation_deg_xyz=None):
        if self.doc_type!='assembly':raise ValueError('place_occurrence requires an active assembly')
        resolved=str(Path(path).expanduser().resolve())
        if self.path and Path(self.path).expanduser().resolve()==Path(resolved):raise ValueError('Assembly cannot contain itself')
        shape,interfaces,pn,mass,component_type,children=load_component(resolved,{str(Path(self.path).resolve())} if self.path else None); stem=Path(path).stem; i=1; name=stem
        while name in self.occurrences:i+=1; name=f'{stem}:{i}'
        occ=Occurrence(name,resolved,bool(grounded),list(position_mm or [0,0,0]),list(rotation_deg_xyz or [0,0,0]),False,pn,mass,interfaces,shape,None,component_type,children); self.occurrences[name]=occ; self.rebuild_assembly(); bb=transform_shape(shape,occ.position_mm,occ.rotation_deg_xyz).BoundingBox(); return {'occurrence_name':name,'component_type':component_type,'bbox_mm':{'min':[bb.xmin,bb.ymin,bb.zmin],'max':[bb.xmax,bb.ymax,bb.zmax]}}

    def _solve_legacy_assembly_constraints(self,passes=12):
        if self.doc_type!='assembly':return
        active=[c for c in self.constraints if not c.suppressed]
        if not active:return
        interfaces=default_interfaces()

        def snapshot():
            return {n:(list(o.position_mm),list(o.rotation_deg_xyz)) for n,o in self.occurrences.items()}
        def restore(state):
            for n,(pos,rot) in state.items():
                if n in self.occurrences:
                    self.occurrences[n].position_mm=list(pos); self.occurrences[n].rotation_deg_xyz=list(rot)
        def score():
            vals=[]
            for c in active:
                try: vals.append(constraint_residual(self.occurrences,c,interfaces))
                except Exception: vals.append(math.inf)
            if any(not math.isfinite(v) for v in vals):return (math.inf,math.inf)
            return (max(vals,default=0.0),sum(vals))

        best=snapshot(); best_score=score(); stagnant=0
        for _ in range(max(1,int(passes))):
            before=snapshot()
            for c in active:
                try:solve_constraint(self.occurrences,c,interfaces)
                except Exception:c.health='sick'
            current=score()
            if current < best_score:
                best_score=current; best=snapshot(); stagnant=0
            else:
                stagnant+=1
            after=snapshot(); delta=0.0
            for n in before:
                delta=max(delta,max(abs(a-b) for a,b in zip(before[n][0],after[n][0])),max(abs(a-b) for a,b in zip(before[n][1],after[n][1])))
            if best_score[0] < 1e-3 or delta<1e-8 or stagnant>=3:break

        restore(best)
        for c in active:
            try:c.health='up_to_date' if constraint_residual(self.occurrences,c,interfaces) < 1e-3 else 'sick'
            except Exception:c.health='sick'

        # Inventor-like simultaneous solve is an additive fallback only.  Existing
        # assemblies that the legacy deterministic solver already satisfies never enter
        # this path.  A candidate is committed only on strict residual improvement.
        if any(c.health!='up_to_date' for c in active):
            try:
                improved=try_global_constraint_fallback(self.occurrences,active,interfaces)
            except Exception:
                improved=False
            if improved:
                for c in active:
                    try:c.health='up_to_date' if constraint_residual(self.occurrences,c,interfaces) < 1e-3 else 'sick'
                    except Exception:c.health='sick'

    def solve_assembly_constraints(self,passes=12):
        """Solve assembly relationships without changing the legacy no-joint path.

        Static assemblies containing only the original ipt-mcp mate/flush/insert/angle
        relationships execute the established solver path byte-for-byte as before.  As
        soon as a kinematic joint is present, every active legacy constraint and joint
        is solved simultaneously.  This avoids the former alternating-solver failure
        mode where one relationship could undo another.
        """
        if self.doc_type!='assembly':return
        active_joints=[j for j in self.joints if not j.suppressed]
        if not active_joints:
            self._solve_legacy_assembly_constraints(passes)
            return
        try:
            self._last_assembly_solve=solve_assembly_relationships(
                self.occurrences,self.constraints,active_joints,default_interfaces(),
                iterations=max(32,int(passes)*5),
            )
        except Exception as exc:
            self._last_assembly_solve={'converged':False,'error':str(exc)}
            for j in active_joints:j.health='sick'

    def rebuild_assembly(self,solve=True):
        if self.doc_type!='assembly':return
        if solve and (self.constraints or self.joints):self.solve_assembly_constraints()
        solids=[]
        for occ in self.occurrences.values():
            if occ.suppressed or occ.shape is None:continue
            solids.append(transform_shape(occ.shape,occ.position_mm,occ.rotation_deg_xyz))
        if not solids:self.shape=None
        elif len(solids)==1:self.shape=solids[0]
        else:self.shape=cq.Compound.makeCompound(solids)

    def add_assembly_constraint(self,type_,a_occ,a_ref,b_occ,b_ref,offset_mm=0,angle_deg=None,insert_opposed=True):
        if self.doc_type!='assembly':raise ValueError('add_constraint requires an active assembly')
        # Keep Inventor-like stable names even after earlier relationships were deleted.
        i=1; existing={x.name for x in self.constraints}
        while f'Constraint{i}' in existing:i+=1
        c=AssemblyConstraint(f'Constraint{i}',type_,a_occ,a_ref,b_occ,b_ref,float(offset_mm),None if angle_deg is None else float(angle_deg),bool(insert_opposed)); self.constraints.append(c); self.solve_assembly_constraints(); self.rebuild_assembly(solve=False); return asdict(c)

    def delete_assembly_constraint(self,name):
        """Inventor AssemblyConstraint.Delete()-style relationship removal."""
        if self.doc_type!='assembly':raise ValueError('delete_constraint requires an active assembly')
        idx=next((i for i,x in enumerate(self.constraints) if x.name==name),None)
        if idx is None:raise ValueError(f'Constraint not found: {name}')
        removed=self.constraints.pop(idx)
        self.solve_assembly_constraints(); self.rebuild_assembly(solve=False)
        return {'deleted':removed.name,'type':removed.type}

    def delete_occurrence(self,name):
        """Inventor ComponentOccurrence.Delete()-style component removal.

        Deleting an occurrence removes assembly relationships that directly depend on
        that occurrence, but never deletes the referenced part/subassembly document.
        """
        if self.doc_type!='assembly':raise ValueError('delete_occurrence requires an active assembly')
        if name not in self.occurrences:raise ValueError(f'Occurrence not found: {name}')
        removed=self.occurrences.pop(name)
        deleted_constraints=[x.name for x in self.constraints if x.a_occurrence==name or x.b_occurrence==name]
        deleted_joints=[x.name for x in self.joints if x.a_occurrence==name or x.b_occurrence==name]
        self.constraints=[x for x in self.constraints if x.a_occurrence!=name and x.b_occurrence!=name]
        self.joints=[x for x in self.joints if x.a_occurrence!=name and x.b_occurrence!=name]
        self.solve_assembly_constraints(); self.rebuild_assembly(solve=False)
        return {
            'deleted_occurrence':name,'component_type':getattr(removed,'component_type','part'),
            'referenced_path':removed.path,'deleted_constraints':deleted_constraints,
            'deleted_joints':deleted_joints,
        }

    def add_assembly_joint(self,type_,a_occ,a_ref,b_occ,b_ref,linear_position_mm=None,linear_start_mm=None,linear_end_mm=None,angular_position_deg=None,angular_start_deg=None,angular_end_deg=None,name=None,a_intent=None,b_intent=None,flip_origin_direction=False,flip_alignment_direction=False):
        if self.doc_type!='assembly':raise ValueError('add_joint requires an active assembly')
        j=AssemblyJoint(
            name or f'Joint{len(self.joints)+1}',canonical_joint_type(type_),a_occ,a_ref,b_occ,b_ref,
            None if linear_position_mm is None else float(linear_position_mm),
            None if linear_start_mm is None else float(linear_start_mm),
            None if linear_end_mm is None else float(linear_end_mm),
            None if angular_position_deg is None else float(angular_position_deg),
            None if angular_start_deg is None else float(angular_start_deg),
            None if angular_end_deg is None else float(angular_end_deg),
            'up_to_date',False,False,
            copy.deepcopy(a_intent) if isinstance(a_intent,dict) else None,
            copy.deepcopy(b_intent) if isinstance(b_intent,dict) else None,
            bool(flip_origin_direction),bool(flip_alignment_direction),
        )
        validate_joint_definition(j)
        if any(x.name==j.name for x in self.joints):raise ValueError(f'Joint already exists: {j.name}')
        self.joints.append(j); self.solve_assembly_constraints(); self.rebuild_assembly(solve=False)
        return {**asdict(j),'state':joint_state(self.occurrences,j,default_interfaces())}

    def edit_assembly_joint(self,name,**updates):
        if self.doc_type!='assembly':raise ValueError('edit_joint requires an active assembly')
        j=next((x for x in self.joints if x.name==name),None)
        if j is None:raise ValueError(f'Joint not found: {name}')
        allowed={'type','a_occurrence','a_ref','b_occurrence','b_ref','a_intent','b_intent','flip_origin_direction','flip_alignment_direction','linear_position_mm','linear_start_mm','linear_end_mm','angular_position_deg','angular_start_deg','angular_end_deg','suppressed'}
        extra=set(updates)-allowed
        if extra:raise ValueError(f'Unsupported joint field(s): {sorted(extra)}')
        for k,v in updates.items():
            if v is None:continue
            if k=='type':setattr(j,k,canonical_joint_type(v))
            elif k in {'suppressed','flip_origin_direction','flip_alignment_direction'}:setattr(j,k,bool(v))
            elif k in {'a_occurrence','a_ref','b_occurrence','b_ref'}:setattr(j,k,str(v))
            elif k in {'a_intent','b_intent'}:setattr(j,k,copy.deepcopy(v))
            else:setattr(j,k,float(v))
        validate_joint_definition(j); self.solve_assembly_constraints(); self.rebuild_assembly(solve=False)
        return {**asdict(j),'state':joint_state(self.occurrences,j,default_interfaces())}

    def set_joint_limits(self,name,linear_start_mm=None,linear_end_mm=None,angular_start_deg=None,angular_end_deg=None):
        if self.doc_type!='assembly':raise ValueError('set_joint_limits requires an active assembly')
        j=next((x for x in self.joints if x.name==name),None)
        if j is None:raise ValueError(f'Joint not found: {name}')
        if linear_start_mm is not None:j.linear_start_mm=float(linear_start_mm)
        if linear_end_mm is not None:j.linear_end_mm=float(linear_end_mm)
        if angular_start_deg is not None:j.angular_start_deg=float(angular_start_deg)
        if angular_end_deg is not None:j.angular_end_deg=float(angular_end_deg)
        validate_joint_definition(j); self.solve_assembly_constraints(); self.rebuild_assembly(solve=False)
        return {**asdict(j),'state':joint_state(self.occurrences,j,default_interfaces())}

    def drive_joint(self,name,linear_position_mm=None,angular_position_deg=None):
        if self.doc_type!='assembly':raise ValueError('drive_joint requires an active assembly')
        j=next((x for x in self.joints if x.name==name),None)
        if j is None:raise ValueError(f'Joint not found: {name}')
        if linear_position_mm is None and angular_position_deg is None:raise ValueError('drive_joint requires linear_position_mm and/or angular_position_deg')
        if linear_position_mm is not None:j.linear_position_mm=float(linear_position_mm)
        if angular_position_deg is not None:j.angular_position_deg=float(angular_position_deg)
        validate_joint_definition(j); self.solve_assembly_constraints(); self.rebuild_assembly(solve=False)
        return {**asdict(j),'state':joint_state(self.occurrences,j,default_interfaces())}

    def delete_joint(self,name):
        if self.doc_type!='assembly':raise ValueError('delete_joint requires an active assembly')
        idx=next((i for i,x in enumerate(self.joints) if x.name==name),None)
        if idx is None:raise ValueError(f'Joint not found: {name}')
        removed=self.joints.pop(idx); self.solve_assembly_constraints(); self.rebuild_assembly(solve=False)
        return {'deleted':name,'type':removed.type}

    def list_joints(self):
        if self.doc_type!='assembly':raise ValueError('list_joints requires an active assembly')
        rows=[]
        for j in self.joints:
            row=asdict(j)
            try:row['state']=joint_state(self.occurrences,j,default_interfaces())
            except Exception as exc:row['state']={'error':str(exc)}
            rows.append(row)
        return {'joints':rows}

    def assembly_relationship_health(self):
        if self.doc_type!='assembly':raise ValueError('assembly_relationship_health requires an active assembly')
        return assembly_relationship_diagnostics(self.occurrences,self.constraints,self.joints,default_interfaces())

    def list_interfaces(self,occurrence=None):
        if occurrence:
            if occurrence not in self.occurrences:raise ValueError(f'Occurrence not found: {occurrence}')
            return self.occurrences[occurrence].interfaces
        return self.list_interfaces_local() if self.doc_type=='part' else default_interfaces()

    def check_interference(self,names=None):
        if self.doc_type!='assembly':raise ValueError('check_interference requires an assembly')
        requested=names or list(self.occurrences)
        missing=[n for n in requested if n not in self.occurrences]
        if missing:raise ValueError('Occurrence not found: '+', '.join(missing))
        use=[self.occurrences[n] for n in requested if not self.occurrences[n].suppressed]; pairs=[]; total=0.0; errors=[]
        for i,a in enumerate(use):
            sa=transform_shape(a.shape,a.position_mm,a.rotation_deg_xyz)
            for b in use[i+1:]:
                sb=transform_shape(b.shape,b.position_mm,b.rotation_deg_xyz)
                try:
                    common=cq.Workplane(obj=sa).intersect(sb).val(); vol=float(common.Volume()) if common is not None else 0.0
                except Exception as exc:
                    errors.append({'a':a.name,'b':b.name,'error':str(exc)}); continue
                if vol>1e-7:pairs.append({'a':a.name,'b':b.name,'volume_mm3':vol}); total+=vol
        return {'ok':not errors,'analysis_complete':not errors,'count':len(pairs),'total_volume_mm3':total,'bodies':len(pairs),'pairs':pairs,'errors':errors}

    def measure_min_distance(self,a_occ,a_ref,b_occ,b_ref):
        if self.doc_type!='assembly':raise ValueError('measure_min_distance requires an assembly')
        def target(name,ref):
            if name not in self.occurrences:raise ValueError(f'Occurrence not found: {name}')
            o=self.occurrences[name]
            if ref:
                item=find_interface(o.interfaces,ref); p,d=transformed_frame(item['origin'],item.get('direction'),o); return cq.Vertex.makeVertex(*p)
            return transform_shape(o.shape,o.position_mm,o.rotation_deg_xyz)
        A=target(a_occ,a_ref); B=target(b_occ,b_ref); errors=[]
        try:dist=A.distance(B)
        except Exception as exc1:
            errors.append(str(exc1))
            try:dist=A.distToShape(B)[0]
            except Exception as exc2:
                errors.append(str(exc2)); raise RuntimeError('Minimum-distance calculation failed; result is unknown. '+' | '.join(errors)) from exc2
        return {'ok':True,'distance_mm':float(dist),'a':{'occurrence':a_occ,'ref':a_ref},'b':{'occurrence':b_occ,'ref':b_ref}}

    def assembly_bom(self,max_rows=500):
        rows=[]; grouped={}; dof=(assembly_kinematic_degrees_of_freedom(self.occurrences,self.constraints,self.joints,default_interfaces()) if self.joints else assembly_degrees_of_freedom(self.occurrences,self.constraints,default_interfaces()))
        truncated=False
        def visit(name,path,part_number,unit_mass,grounded,suppressed,children,depth,parent=None,top_dof=None):
            nonlocal truncated
            if len(rows)>=max_rows:truncated=True; return
            dt,dr=top_dof if top_dof is not None else ((0,0) if grounded or suppressed else (0,0))
            rows.append({'name':name,'path':path,'depth':depth,'parent':parent,'grounded':grounded,'dof_translation':int(dt),'dof_rotation':int(dr),'suppressed':suppressed,'reference_status':'resolved' if Path(path).exists() else 'snapshot_fallback'})
            key=(part_number,path); g=grouped.setdefault(key,{'part_number':part_number,'description':None,'path':path,'qty':0,'unit_mass_g':unit_mass}); g['qty']+=1
            for child in children or []:
                visit(child.get('name','Component'),child.get('path',''),child.get('part_number') or Path(child.get('path','Component')).stem,float(child.get('unit_mass_g',0) or 0),bool(child.get('grounded',False)),bool(child.get('suppressed',False)),child.get('children') or [],depth+1,name,(0,0))
        for o in self.occurrences.values():
            if len(rows)>=max_rows:truncated=True; break
            visit(o.name,o.path,o.part_number,o.unit_mass_g,o.grounded,o.suppressed,getattr(o,'children',[]),0,None,dof.get(o.name,(0,0) if o.grounded or o.suppressed else (3,3)))
        return {'occurrences':rows,'bom':list(grouped.values()),'truncated':truncated}

    def mass_properties(self,density_g_cm3=None):
        if self.shape is None:return {'mass_g':0,'volume_mm3':0,'surface_area_mm2':0,'center_of_mass_mm':[0,0,0],'bounding_box_mm':None,'material':self.material,'density_g_cm3':density_for(self.material)}
        vol=float(self.shape.Volume()); area=float(self.shape.Area()); c=self.shape.Center(); bb=self.shape.BoundingBox(); density=density_for(self.material) if density_g_cm3 is None else float(density_g_cm3)
        mass_known=density is not None; mass=None if density is None else vol/1000*density
        if self.doc_type=='assembly' and self.occurrences and all(o.unit_mass_g>0 for o in self.occurrences.values() if not o.suppressed):mass=sum(o.unit_mass_g for o in self.occurrences.values() if not o.suppressed); mass_known=True
        return {'mass_g':mass,'mass_known':mass_known,'material':self.material,'density_g_cm3':density,'volume_mm3':vol,'surface_area_mm2':area,'center_of_mass_mm':[c.x,c.y,c.z],'bounding_box_mm':{'min':[bb.xmin,bb.ymin,bb.zmin],'max':[bb.xmax,bb.ymax,bb.zmax],'size':[bb.xlen,bb.ylen,bb.zlen]}}

    def serialize(self,base_path=None):
        base_dir=Path(base_path or self.path).expanduser().resolve().parent if (base_path or self.path) else None
        def portable_path(raw):
            if not base_dir:return str(raw)
            try:return os.path.relpath(str(Path(raw).expanduser().resolve()),str(base_dir))
            except Exception:return str(raw)
        def portable_children(items):
            out=[]
            for child in items or []:
                row=copy.deepcopy(child); row['path']=portable_path(row.get('path','')); row['children']=portable_children(row.get('children') or []); out.append(row)
            return out
        occurrences={}
        for k,o in self.occurrences.items():
            row={q:v for q,v in asdict(o).items() if q not in {'shape','source_session_id','children'}}
            row['path']=portable_path(o.path); row['path_relative_to']='assembly' if base_dir else None
            row['children']=portable_children(getattr(o,'children',[]))
            try:row['snapshot_brep_b64']=self._shape_to_brep_b64(o.shape) if o.shape is not None else None
            except Exception:row['snapshot_brep_b64']=None
            occurrences[k]=row
        embedded=self._imported_brep_b64
        if self.imported_shape is not None and embedded is None:
            embedded=self._shape_to_brep_b64(self.imported_shape); self._imported_brep_b64=embedded
        return {'format':'StandaloneCAD-MCP','version':3,'title':self.title,'doc_type':self.doc_type,'units':self.units,'material':self.material,'parameters':{k:asdict(v) for k,v in self.parameters.items()},'properties':self.properties,'property_sets':self.property_sets,'sketches':{k:{'name':v.name,'plane':v.plane,'entities':[asdict(e) for e in v.entities],'constraints':v.constraints,'dimensions':v.dimensions,'closed':v.closed,'plane_ref':v.plane_ref} for k,v in self.sketches.items()},'sketches3d':copy.deepcopy(self.sketches3d),'work_planes':self.work_planes,'work_axes':self.work_axes,'work_points':self.work_points,'imates':self.imates,'features':[asdict(f) for f in self.features],'occurrences':occurrences,'constraints':[asdict(c) for c in self.constraints],'joints':[asdict(j) for j in self.joints],'sheet_metal':copy.deepcopy(self.sheet_metal),'view_orientation':self.view_orientation,'import_source':self.import_source,'imported_brep_b64':embedded}

    def save(self,path):
        path=str(Path(path).expanduser().resolve()); Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.path=path; self.title=Path(path).name[:-10] if Path(path).name.lower().endswith('.scad.json') else Path(path).stem
        Path(path).write_text(json.dumps(self.serialize(base_path=path),indent=2),encoding='utf-8'); self.dirty=False; return path

    def load(self,path,_visited=None):
        p=Path(path).expanduser().resolve(); ext=p.suffix.lower(); visited=set(_visited or set())
        key=str(p)
        if key in visited:raise ValueError(f'Assembly reference cycle detected at {p}')
        visited.add(key)
        if p.name.lower().endswith('.scad.json'):
            d=json.loads(p.read_text(encoding='utf-8')); self.reset(d.get('doc_type','part')); self.title=d.get('title','Untitled'); self.units=d.get('units','mm'); self.material=d.get('material','Generic')
            legacy_props=d.get('properties',{}) or {}; sets=copy.deepcopy(d.get('property_sets') or {})
            if 'Design Tracking Properties' not in sets:sets['Design Tracking Properties']=legacy_props
            sets.setdefault('Inventor User Defined Properties',{})
            self.property_sets=sets; self.properties=self.property_sets['Design Tracking Properties']
            self.parameters={k:Parameter(**v) for k,v in d.get('parameters',{}).items()}
            for k,v in d.get('sketches',{}).items():self.sketches[k]=SketchModel(v['name'],v.get('plane','XY'),[SketchEntity(**e) for e in v.get('entities',[])],v.get('constraints',[]),v.get('dimensions',[]),v.get('closed',False),v.get('plane_ref'))
            self.sketches3d=copy.deepcopy(d.get('sketches3d',{})); self.work_planes=d.get('work_planes',{}); self.work_axes=d.get('work_axes',{}); self.work_points=d.get('work_points',{}); self.imates=d.get('imates',{}); self.sheet_metal=copy.deepcopy(d.get('sheet_metal')); self.view_orientation=d.get('view_orientation','iso_top_right')
            if self.doc_type=='part':
                self.import_source=d.get('import_source'); self._imported_brep_b64=d.get('imported_brep_b64'); self.imported_shape=None
                if self._imported_brep_b64:
                    self.imported_shape=self._shape_from_brep_b64(self._imported_brep_b64)
                elif self.import_source:
                    src=Path(self.import_source).expanduser()
                    if not src.is_absolute():src=(p.parent/src).resolve()
                    if src.exists():
                        iext=src.suffix.lower()
                        if iext in ('.step','.stp'): self.imported_shape=importers.importStep(str(src)).val()
                        elif iext in ('.brep','.brp'): self.imported_shape=importers.importBrep(str(src)).val()
                old=[FeatureRecord(**f) for f in d.get('features',[])]; self.features=[]; self.shape=self.imported_shape; self._feature_shape_cache=[]
                for f in old:
                    if f.suppressed:self.features.append(f)
                    else:self._apply_feature(f,record=True)
                    self._feature_shape_cache.append(self.shape)
            else:
                self.occurrences={}
                def resolve_children(items):
                    out=[]
                    for child in items or []:
                        row=copy.deepcopy(child); cp=Path(row.get('path','')).expanduser()
                        if row.get('path') and not cp.is_absolute():row['path']=str((p.parent/cp).resolve())
                        row['children']=resolve_children(row.get('children') or []); out.append(row)
                    return out
                for k,v in d.get('occurrences',{}).items():
                    stored=v['path']; cp=Path(stored).expanduser()
                    if not cp.is_absolute():cp=(p.parent/cp).resolve()
                    status='resolved'; load_error=None
                    try:
                        shape,interfaces,pn,mass,component_type,children=load_component(str(cp),visited)
                    except Exception as exc:
                        snap=v.get('snapshot_brep_b64')
                        if not snap:raise
                        shape=self._shape_from_brep_b64(snap); interfaces=copy.deepcopy(v.get('interfaces') or default_interfaces()); pn=v.get('part_number') or cp.stem; mass=float(v.get('unit_mass_g',0) or 0); component_type=v.get('component_type','part'); children=resolve_children(v.get('children') or []); status='snapshot_fallback'; load_error=str(exc)
                    self.occurrences[k]=Occurrence(k,str(cp),v.get('grounded',False),v.get('position_mm',[0,0,0]),v.get('rotation_deg_xyz',[0,0,0]),v.get('suppressed',False),v.get('part_number',pn),v.get('unit_mass_g',mass),interfaces,shape,None,v.get('component_type',component_type),children,status,load_error)
                self.constraints=[AssemblyConstraint(**c) for c in d.get('constraints',[])]; self.joints=[AssemblyJoint(**j) for j in d.get('joints',[])]; self.rebuild_assembly()
            self.path=str(p); self.dirty=False; return self.path
        if ext in ('.step','.stp'):
            self.reset('part'); self.import_source=str(p); self.imported_shape=importers.importStep(str(p)).val(); self._imported_brep_b64=self._shape_to_brep_b64(self.imported_shape); self.shape=self.imported_shape; self.title=p.stem; self.path=str(p); self.dirty=False; return self.path
        if ext in ('.brep','.brp'):
            self.reset('part'); self.import_source=str(p); self.imported_shape=importers.importBrep(str(p)).val(); self._imported_brep_b64=self._shape_to_brep_b64(self.imported_shape); self.shape=self.imported_shape; self.title=p.stem; self.path=str(p); self.dirty=False; return self.path
        raise ValueError('Open supports .scad.json, .step/.stp, .brep/.brp')
