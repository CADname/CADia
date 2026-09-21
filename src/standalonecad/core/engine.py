from __future__ import annotations
import json, tempfile, math, threading, copy, uuid
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field
import cadquery as cq
from cadquery import exporters
from .document import CadDocument,Parameter,SketchModel,SketchEntity
from .render import render_shape_png
from .topology import select_face, face_frame, make_face_ref, make_edge_ref
from .upstream_contracts import validate_export_path, validate_rectangular_pattern, validate_circular_pattern, validate_face_selector
from .materials import normalize_material, density_for
from .sketch_solver import enforce_constraint, solve_constraints
from .canonical import CanonicalCadCore, CanonicalOperation
from .verifier import verify_after_command
from .parameter_graph import earliest_affected_feature
from ..compat.inventor_semantics import InventorSemanticAdapter, UPSTREAM_HOST_COMMANDS
from .inventor_origin import ORIGIN_PLANES, canonical_plane_name

READ_COMMANDS={'health','list_open_documents','get_document_info','list_parameters','get_parameter','get_iproperty','get_mass_properties','list_interfaces','check_interference','measure_min_distance','get_assembly_bom','list_constraints','list_joints','get_topology','get_selection'}
_TRANSACTION_EXCLUDED={'undo','redo','view_fit','set_view_orientation','capture_view','export_step','export_stl','export_dxf','save_document','new_part','new_assembly','open_document','close_document','delete_document_file'}

@dataclass
class _DocumentSession:
    id: str
    doc: CadDocument
    active_sketch: str | None = None
    selection: dict[str, Any] = field(default_factory=dict)
    undo: list[dict[str, Any]] = field(default_factory=list)
    redo: list[dict[str, Any]] = field(default_factory=list)


class CadEngine:
    def __init__(self,on_change=None):
        self.on_change=on_change
        self.lock=threading.RLock(); self.revision=0
        self._sessions:list[_DocumentSession]=[]; self._active_session_id:str|None=None
        # Keep the historical one-click startup experience: one empty Part is open.
        # Subsequent New Part/New Assembly commands add documents instead of erasing it.
        self._create_document_session('part','Untitled',activate=True,notify=False)
        self.canonical=CanonicalCadCore(self)
        self.inventor=InventorSemanticAdapter(self,self.canonical)

    def _active_session(self):
        if self._active_session_id is None:return None
        return next((x for x in self._sessions if x.id==self._active_session_id),None)

    @property
    def doc(self):
        s=self._active_session(); return s.doc if s is not None else None

    @property
    def active_sketch(self):
        s=self._active_session(); return s.active_sketch if s is not None else None
    @active_sketch.setter
    def active_sketch(self,value):
        s=self._active_session()
        if s is not None:s.active_sketch=value

    @property
    def selection(self):
        s=self._active_session(); return s.selection if s is not None else {}
    @selection.setter
    def selection(self,value):
        s=self._active_session()
        if s is not None:s.selection=dict(value or {})

    @property
    def _undo(self):
        s=self._active_session(); return s.undo if s is not None else []
    @_undo.setter
    def _undo(self,value):
        s=self._active_session()
        if s is not None:s.undo=list(value or [])

    @property
    def _redo(self):
        s=self._active_session(); return s.redo if s is not None else []
    @_redo.setter
    def _redo(self,value):
        s=self._active_session()
        if s is not None:s.redo=list(value or [])

    def _unique_title(self,base):
        base=str(base or 'Untitled')
        existing={s.doc.title for s in self._sessions}
        if base not in existing:return base
        i=2
        while f'{base} {i}' in existing:i+=1
        return f'{base} {i}'

    def _create_document_session(self,doc_type='part',title=None,activate=True,notify=True):
        d=CadDocument(); d.reset(doc_type); d.title=self._unique_title(title or ('Untitled Assembly' if doc_type=='assembly' else 'Untitled'))
        s=_DocumentSession(uuid.uuid4().hex,d); self._sessions.append(s)
        if activate:
            self._active_session_id=s.id; d.view_fit=True
        if notify:self._changed()
        return s

    def document_sessions(self):
        return [{'id':s.id,'title':s.doc.title,'path':s.doc.path,'type':s.doc.doc_type,'active':s.id==self._active_session_id,'dirty':bool(getattr(s.doc,'dirty',False))} for s in self._sessions]

    @staticmethod
    def _normalized_path(path):
        if not path:return None
        try:return str(Path(path).expanduser().resolve()).casefold()
        except Exception:return str(path).casefold()

    def _sync_open_assembly_occurrences(self):
        """Refresh open part/subassembly references transitively and atomically."""
        source_by_id={x.id:x for x in self._sessions}
        source_by_path={self._normalized_path(x.doc.path):x for x in self._sessions if self._normalized_path(x.doc.path)}
        changed=set()
        # Multiple passes make nested references independent of tab/open order.
        for _ in range(max(1,len(self._sessions)+1)):
            pass_changed=False
            for asess in self._sessions:
                adoc=asess.doc
                if adoc.doc_type!='assembly':continue
                touched=False
                for occ in adoc.occurrences.values():
                    source=source_by_id.get(getattr(occ,'source_session_id',None)) or source_by_path.get(self._normalized_path(occ.path))
                    if source is None or source.id==asess.id or source.doc.shape is None:continue
                    src=source.doc
                    try:
                        if src.doc_type=='part':interfaces=src.list_interfaces_local()
                        else:
                            from .assembly import default_interfaces
                            interfaces=default_interfaces()
                    except Exception:interfaces=occ.interfaces
                    part_number=src.properties.get('Part Number') or src.title or Path(occ.path).stem
                    try:
                        mass=src.mass_properties().get('mass_g'); mass=0.0 if mass is None else float(mass)
                    except Exception:mass=occ.unit_mass_g
                    geometry_changed=(occ.shape is not src.shape); metadata_changed=(occ.part_number!=part_number or occ.interfaces!=interfaces or abs(float(occ.unit_mass_g)-float(mass))>1e-12 or getattr(occ,'component_type','part')!=src.doc_type); link_changed=(getattr(occ,'source_session_id',None)!=source.id)
                    if geometry_changed or metadata_changed or link_changed or getattr(occ,'reference_status','resolved')!='resolved':
                        occ.source_session_id=source.id; occ.shape=src.shape; occ.interfaces=copy.deepcopy(interfaces); occ.part_number=part_number; occ.unit_mass_g=mass; occ.component_type=src.doc_type; occ.reference_status='resolved'; occ.load_error=None
                        if src.doc_type=='assembly':occ.children=[{'name':x.name,'path':x.path,'part_number':x.part_number,'unit_mass_g':x.unit_mass_g,'grounded':x.grounded,'suppressed':x.suppressed,'component_type':getattr(x,'component_type','part'),'children':copy.deepcopy(getattr(x,'children',[]))} for x in src.occurrences.values()]
                        else:occ.children=[]
                        touched=True
                if touched:
                    adoc.rebuild_assembly(solve=True); pass_changed=True; changed.add(asess.id)
                    if any(getattr(source_by_id.get(getattr(o,'source_session_id',None)).doc,'dirty',False) for o in adoc.occurrences.values() if source_by_id.get(getattr(o,'source_session_id',None)) is not None):adoc.dirty=True
            if not pass_changed:break
        return list(changed)

    def _bind_occurrence_to_open_source(self,assembly_doc,occurrence_name):
        occ=assembly_doc.occurrences.get(occurrence_name)
        if occ is None:return False
        key=self._normalized_path(occ.path)
        source=next((x for x in self._sessions if self._normalized_path(x.doc.path)==key),None)
        if source is None:return False
        occ.source_session_id=source.id
        # Use the current in-memory B-Rep, not a stale file snapshot.
        occ.shape=source.doc.shape
        try:
            if source.doc.doc_type=='part':occ.interfaces=copy.deepcopy(source.doc.list_interfaces_local())
            else:
                from .assembly import default_interfaces
                occ.interfaces=copy.deepcopy(default_interfaces())
        except Exception:pass
        occ.component_type=source.doc.doc_type; occ.reference_status='resolved'; occ.load_error=None
        if source.doc.doc_type=='assembly':occ.children=[{'name':x.name,'path':x.path,'part_number':x.part_number,'unit_mass_g':x.unit_mass_g,'grounded':x.grounded,'suppressed':x.suppressed,'component_type':getattr(x,'component_type','part'),'children':copy.deepcopy(getattr(x,'children',[]))} for x in source.doc.occurrences.values()]
        else:occ.children=[]
        occ.part_number=source.doc.properties.get('Part Number') or Path(occ.path).stem
        try:
            m=source.doc.mass_properties().get('mass_g'); occ.unit_mass_g=0.0 if m is None else float(m)
        except Exception:pass
        assembly_doc.rebuild_assembly(solve=True)
        return True

    def activate_document(self,session_id):
        with self.lock:
            s=next((x for x in self._sessions if x.id==session_id),None)
            if s is None:raise ValueError(f'Open document not found: {session_id}')
            if self._active_session_id!=s.id:
                self._active_session_id=s.id; s.doc.view_fit=True; self._sync_open_assembly_occurrences(); self._changed()
            return {'ok':True,'document_id':s.id,'title':s.doc.title,'document_type':s.doc.doc_type}

    @staticmethod
    def _document_snapshot(doc):
        snap=copy.deepcopy(doc.serialize()); snap['_runtime_path']=doc.path; snap['_runtime_dirty']=bool(getattr(doc,'dirty',False)); return snap

    @staticmethod
    def _document_from_snapshot(snap):
        fd,tmp=tempfile.mkstemp(prefix='standalonecad-restore-',suffix='.scad.json')
        __import__('os').close(fd); p=Path(tmp); d=CadDocument()
        try:
            payload={k:v for k,v in snap.items() if k!='_runtime_path'}
            p.write_text(json.dumps(payload),encoding='utf-8'); d.load(p); d.path=snap.get('_runtime_path'); d.dirty=bool(snap.get('_runtime_dirty',False)); return d
        finally:
            try:p.unlink(missing_ok=True)
            except Exception:pass

    def _snapshot(self):
        if self.doc is None:return None
        return self._document_snapshot(self.doc)

    def _restore(self,snap):
        if snap is None:return
        s=self._active_session()
        if s is None:raise ValueError('No active document to restore')
        s.doc=self._document_from_snapshot(snap); s.active_sketch=None; s.selection={}

    def _workspace_snapshot(self):
        return {
            'sessions':[{'id':s.id,'doc':self._document_snapshot(s.doc),'active_sketch':s.active_sketch,'selection':copy.deepcopy(s.selection),'undo':copy.deepcopy(s.undo),'redo':copy.deepcopy(s.redo)} for s in self._sessions],
            'active_session_id':self._active_session_id,
            'revision':int(self.revision),
        }

    def _restore_workspace(self,snap,bump_revision=False):
        sessions=[]
        for row in snap.get('sessions',[]):
            sessions.append(_DocumentSession(str(row['id']),self._document_from_snapshot(row['doc']),row.get('active_sketch'),copy.deepcopy(row.get('selection') or {}),copy.deepcopy(row.get('undo') or []),copy.deepcopy(row.get('redo') or [])))
        self._sessions=sessions; self._active_session_id=snap.get('active_session_id')
        self.revision=int(snap.get('revision',0))+(1 if bump_revision else 0)
        if self.on_change:self.on_change()

    def _mut(self):
        if self.doc is not None:self._undo.append(self._snapshot()); self._redo.clear()
    def _changed(self):
        self.revision += 1
        if self.on_change:self.on_change()
    def _view_changed(self):
        # Camera/view state is not B-Rep geometry; notify the UI without invalidating
        # the geometry revision/mesh cache.
        if self.on_change:self.on_change()
    def undo(self):
        if self.doc is None or not self._undo:return {'ok':False,'message':'Nothing to undo'}
        ws=self._workspace_snapshot()
        try:
            self._redo.append(self._snapshot()); self._restore(self._undo.pop()); self._sync_open_assembly_occurrences(); self._changed(); return {'ok':True}
        except Exception:
            self._restore_workspace(ws); raise
    def redo(self):
        if self.doc is None or not self._redo:return {'ok':False,'message':'Nothing to redo'}
        ws=self._workspace_snapshot()
        try:
            self._undo.append(self._snapshot()); self._restore(self._redo.pop()); self._sync_open_assembly_occurrences(); self._changed(); return {'ok':True}
        except Exception:
            self._restore_workspace(ws); raise

    def _save_session_with_dependencies(self,sess,target_path=None,visiting=None):
        visiting=set(visiting or set())
        if sess.id in visiting:raise ValueError(f'Document dependency cycle while saving: {sess.doc.title}')
        visiting.add(sess.id)
        if sess.doc.doc_type=='assembly':
            for occ in sess.doc.occurrences.values():
                src=next((x for x in self._sessions if x.id==getattr(occ,'source_session_id',None)),None)
                if src is not None and src.doc.dirty:self._save_session_with_dependencies(src,None,visiting)
        path=target_path or sess.doc.path
        if not path or not str(path).lower().endswith('.scad.json'):raise ValueError(f'Unsaved document requires a .scad.json path: {sess.doc.title}')
        return sess.doc.save(path)

    def save_all(self):
        saved=[]
        for sess in list(self._sessions):
            if getattr(sess.doc,'dirty',False):
                path=self._save_session_with_dependencies(sess)
                if path not in saved:saved.append(path)
        self._sync_open_assembly_occurrences(); self._changed(); return {'ok':True,'saved':saved}

    def _delete_active_document_file(self, *, confirm=False, discard_unsaved_changes=False):
        """Delete only the active saved CADia document file.

        Autodesk Inventor's Document API closes/saves documents but does not expose a
        general disk-file Delete method.  This is therefore a deliberately separate
        CADia extension with stronger safeguards than ordinary CAD mutations.
        """
        if not bool(confirm):raise ValueError('delete_document_file requires confirm=true')
        sess=self._active_session()
        if sess is None:raise ValueError('NO_ACTIVE_DOCUMENT: create or open a document first')
        doc=sess.doc
        if not doc.path:raise ValueError('Cannot delete an unsaved document; it has no file on disk')
        path=Path(doc.path).expanduser().resolve()
        if not path.name.lower().endswith('.scad.json'):
            raise ValueError('delete_document_file only deletes CADia *.scad.json documents')
        if not path.exists():raise FileNotFoundError(f'Document file not found: {path}')
        if doc.dirty and not bool(discard_unsaved_changes):
            raise ValueError('Active document has unsaved changes; set discard_unsaved_changes=true to delete it')

        # Match CAD reference safety: an open parent assembly must stop referencing the
        # document before the underlying file can be removed.  We intentionally do not
        # pretend to discover references in closed files elsewhere on disk.
        refs=[]
        target=self._normalized_path(str(path))
        for parent in self._sessions:
            if parent.id==sess.id or parent.doc.doc_type!='assembly':continue
            for occ in parent.doc.occurrences.values():
                if getattr(occ,'source_session_id',None)==sess.id or self._normalized_path(occ.path)==target:
                    refs.append({'assembly':parent.doc.title,'occurrence':occ.name})
        if refs:
            detail=', '.join(f"{x['assembly']}::{x['occurrence']}" for x in refs)
            raise ValueError('Document is still referenced by an open assembly; delete those occurrences first: '+detail)

        backup=path.read_bytes(); ws=self._workspace_snapshot(); closing_id=sess.id; idx=self._sessions.index(sess)
        try:
            # Close the in-memory document first, then remove exactly that one file.
            self._sessions.pop(idx)
            if self._sessions:
                self._active_session_id=self._sessions[min(idx,len(self._sessions)-1)].id
                self.doc.view_fit=True
            else:self._active_session_id=None
            path.unlink()
            self._changed()
            return {'ok':True,'deleted_path':str(path),'deleted_document_id':closing_id,'document_type':doc.doc_type,'document_open':self.doc is not None}
        except Exception:
            try:
                if not path.exists():path.write_bytes(backup)
            finally:
                self._restore_workspace(ws)
            raise

    def _sketch(self,name=None):
        n=name or self.active_sketch
        if not n or n not in self.doc.sketches:raise ValueError('No active/valid sketch')
        return self.doc.sketches[n]
    def _entity(self,sm,eid):
        e=next((x for x in sm.entities if x.tag==eid),None)
        if e is None:raise ValueError(f'Sketch entity not found: {eid}')
        return e
    def _next_tag(self,sm):return f'e{len(sm.entities)+1}'
    def _enforce_constraint(self,sm,typ,ids):
        c={'type':str(typ).lower(),'entity_ids':list(ids)}
        ok=enforce_constraint(sm,c,self.doc.value)
        if ok:
            # Re-apply the whole system so a newly inserted relation cannot quietly
            # invalidate an earlier relation.
            solve_constraints(sm,self.doc.value)
        return ok

    def execute(self,command:str,p:dict[str,Any] | None,read_only=False):
        """Execute one atomic CAD operation across the whole open-document workspace."""
        params=copy.deepcopy(p or {})
        write=command not in READ_COMMANDS
        if read_only and write:raise PermissionError('READ_ONLY')
        tracked=write and command not in _TRANSACTION_EXCLUDED
        dirty_excluded={'save_document','new_part','new_assembly','open_document','close_document','delete_document_file','export_step','export_stl','export_dxf','capture_view','view_fit','set_view_orientation','undo','redo'}
        with self.lock:
            pre_workspace=self._workspace_snapshot() if tracked else None
            pre_doc=self._snapshot() if tracked else None
            try:
                if command in UPSTREAM_HOST_COMMANDS:
                    result=self.inventor.dispatch(command,params,read_only=read_only)
                else:
                    result=self.canonical.dispatch(CanonicalOperation(command,params,source='native'),read_only=read_only)
                verify_after_command(self,command)
                if write and command not in dirty_excluded and self.doc is not None:self.doc.dirty=True
                if write:self._sync_open_assembly_occurrences()
                if tracked:
                    self._undo.append(pre_doc); self._redo.clear()
                return result
            except Exception:
                if tracked and pre_workspace is not None:self._restore_workspace(pre_workspace)
                raise

    def _execute_impl(self,command:str,p:dict[str,Any],read_only=False):
        d=self.doc; write=command not in READ_COMMANDS
        if read_only and write:raise PermissionError('READ_ONLY')
        try:
            if command=='health':
                return {'ok':True,'inventor_year':2024,'compatibility_emulation_year':2024,'autodesk_inventor_attached':False,'process_id':__import__('os').getpid(),'host_app':'CADia','kernel':'CadQuery/OCP (Open CASCADE)','compatibility_contract':'ipt-mcp-58-handler-semantics+cadia-canonical-core+ux-reliability','document_open':d is not None,'active_document_type':d.doc_type if d is not None else None,'title':d.title if d is not None else None}
            if command=='list_open_documents':
                return {'documents':[{'title':x['title'],'path':x['path'],'type':x['type'],'active':x['active']} for x in self.document_sessions()]}
            if command=='new_part':
                template=p.get('template')
                if template:
                    td=CadDocument(); td.load(template)
                    if td.doc_type!='part':raise ValueError('Part template must be a part document')
                    td.path=None; td.title=self._unique_title(p.get('name') or 'Untitled'); td.dirty=False; s=_DocumentSession(uuid.uuid4().hex,td); self._sessions.append(s); self._active_session_id=s.id; self._changed()
                else:s=self._create_document_session('part',p.get('name') or 'Untitled',activate=True,notify=True)
                return {'ok':True,'document_type':'part','template':template,'title':s.doc.title}
            if command=='new_assembly':
                template=p.get('template')
                if template:
                    td=CadDocument(); td.load(template)
                    if td.doc_type!='assembly':raise ValueError('Assembly template must be an assembly document')
                    td.path=None; td.title=self._unique_title(p.get('name') or 'Untitled Assembly'); td.dirty=False; s=_DocumentSession(uuid.uuid4().hex,td); self._sessions.append(s); self._active_session_id=s.id; self._changed()
                else:s=self._create_document_session('assembly',p.get('name') or 'Untitled Assembly',activate=True,notify=True)
                return {'ok':True,'document_type':'assembly','template':template,'title':s.doc.title}
            if command=='open_document':
                requested=Path(p['path']).expanduser().resolve()
                for s in self._sessions:
                    if s.doc.path:
                        try:
                            if Path(s.doc.path).expanduser().resolve()==requested:
                                self._active_session_id=s.id; s.doc.view_fit=True; self._sync_open_assembly_occurrences(); self._changed(); return {'ok':True,'path':s.doc.path,'document_type':s.doc.doc_type,'title':s.doc.title,'already_open':True}
                        except Exception:pass
                nd=CadDocument(); nd.load(str(requested)); sess=_DocumentSession(uuid.uuid4().hex,nd); self._sessions.append(sess); self._active_session_id=sess.id; nd.view_fit=True; self._sync_open_assembly_occurrences(); self._changed(); return {'ok':True,'path':nd.path,'document_type':nd.doc_type,'title':nd.title,'already_open':False}
            if d is None:
                raise ValueError('NO_ACTIVE_DOCUMENT: create or open a document first')
            if command=='get_document_info':return {'title':d.title,'path':d.path,'document_type':d.doc_type,'dirty':bool(d.dirty)}
            if command=='save_document':
                path=p.get('path') or d.path
                if not path:raise ValueError('path is required for first save')
                if Path(path).suffix.lower() in ('.step','.stp','.brep','.brp'):raise ValueError('Save standalone editable documents as *.scad.json; use export_step for STEP')
                active=self._active_session(); old_path=d.path; new_path=str(Path(path).expanduser().resolve())
                saved=self._save_session_with_dependencies(active,new_path)
                # Save-As updates every open parent occurrence bound to this document.
                if active is not None and self._normalized_path(old_path)!=self._normalized_path(new_path):
                    for sess in self._sessions:
                        if sess.doc.doc_type!='assembly':continue
                        touched=False
                        for occ in sess.doc.occurrences.values():
                            if getattr(occ,'source_session_id',None)==active.id or (old_path and self._normalized_path(occ.path)==self._normalized_path(old_path)):
                                occ.path=new_path; occ.source_session_id=active.id; touched=True
                        if touched:sess.doc.rebuild_assembly(); sess.doc.dirty=True
                return {'ok':True,'path':saved}
            if command=='close_document':
                closing=self._active_session(); closing_id=closing.id
                if p.get('save'):
                    if not d.path:raise ValueError('Cannot save unsaved document without path')
                    d.save(d.path)
                elif d.dirty and d.path and str(d.path).lower().endswith('.scad.json'):
                    # Explicit save=false means discard: parents must return to the disk version too.
                    disk=CadDocument(); disk.load(d.path)
                    for sess in self._sessions:
                        if sess.id==closing_id or sess.doc.doc_type!='assembly':continue
                        touched=False
                        for occ in sess.doc.occurrences.values():
                            if getattr(occ,'source_session_id',None)==closing_id or self._normalized_path(occ.path)==self._normalized_path(d.path):
                                occ.source_session_id=None; occ.shape=disk.shape; occ.component_type=disk.doc_type; occ.reference_status='resolved'; occ.load_error=None
                                if disk.doc_type=='assembly':occ.children=[{'name':x.name,'path':x.path,'part_number':x.part_number,'unit_mass_g':x.unit_mass_g,'grounded':x.grounded,'suppressed':x.suppressed,'component_type':getattr(x,'component_type','part'),'children':copy.deepcopy(getattr(x,'children',[]))} for x in disk.occurrences.values()]
                                else:occ.children=[]
                                try:occ.interfaces=copy.deepcopy(disk.list_interfaces_local()) if disk.doc_type=='part' else occ.interfaces
                                except Exception:pass
                                touched=True
                        if touched:sess.doc.rebuild_assembly(); sess.doc.dirty=True
                idx=self._sessions.index(closing); self._sessions.pop(idx)
                if self._sessions:
                    self._active_session_id=self._sessions[min(idx,len(self._sessions)-1)].id; self.doc.view_fit=True
                else:self._active_session_id=None
                self._changed(); return {'ok':True,'closed_document_id':closing_id,'document_open':self.doc is not None}
            if command=='delete_document_file':
                return self._delete_active_document_file(confirm=p.get('confirm',False),discard_unsaved_changes=p.get('discard_unsaved_changes',False))
            if command=='set_units':
                unit=str(p.get('length_unit','mm')).strip().lower(); aliases={'millimeter':'mm','millimeters':'mm','millimetre':'mm','millimetres':'mm','centimeter':'cm','centimeters':'cm','meter':'m','meters':'m','inch':'in','inches':'in','foot':'ft','feet':'ft'}; unit=aliases.get(unit,unit)
                if unit not in {'mm','cm','m','in','ft'}:raise ValueError('length_unit must be mm|cm|m|in|ft')
                d.units=unit; self._changed(); return {'ok':True,'length_unit':d.units,'model_internal_unit':'mm','note':'ipt-mcp geometric command fields remain explicitly millimetres; this setting controls document display metadata'}
            if command=='set_material':
                d.material=normalize_material(p.get('material_name','Generic')); dens=density_for(d.material); self._changed(); return {'ok':True,'material_name':d.material,'density_g_cm3':dens,'density_known':dens is not None}

            if command=='list_parameters':return {'parameters':[{'name':x.name,'expression':x.expression,'value':d.numeric_params().get(x.name),'unit':x.unit,'kind':x.kind} for x in d.parameters.values()]}
            if command=='get_parameter':
                x=d.parameters[p['name']]; return {'name':x.name,'expression':x.expression,'value':d.numeric_params()[x.name],'unit':x.unit,'kind':x.kind}
            if command=='create_parameter':
                expr=str(p['expression']); unit=str(p['unit'] or 'mm')
                try:
                    float(expr); expr=f'{expr} {unit}'
                except Exception:pass
                d.parameters[p['name']]=Parameter(p['name'],expr,unit,'user'); d.invalidate_parameter_cache(); d.rebuild(); self._changed(); return self.execute('get_parameter',{'name':p['name']})
            if command=='set_parameter':
                if p['name'] not in d.parameters:raise ValueError(f"Parameter not found: {p['name']}")
                # Legacy full rebuild always runs first.  Only when it fails do we try a
                # dependency-proven suffix rebuild reusing the exact pre-change prefix.
                # This preserves every successful historical parameter-edit path while
                # rescuing models whose unrelated early history is expensive/brittle.
                baseline={
                    'shape':d.shape,'features':copy.deepcopy(d.features),'feature_cache':list(d._feature_shape_cache),
                    'sketches':copy.deepcopy(d.sketches),'sketches3d':copy.deepcopy(getattr(d,'sketches3d',{})),'work_planes':copy.deepcopy(d.work_planes),
                    'work_axes':copy.deepcopy(d.work_axes),'work_points':copy.deepcopy(getattr(d,'work_points',{})),'imates':copy.deepcopy(d.imates),
                    'sheet_metal':copy.deepcopy(getattr(d,'sheet_metal',None)),
                }
                raw=str(p['value']); unit=str(d.parameters[p['name']].unit or '')
                try:
                    float(raw); raw=f'{raw} {unit}' if unit else raw
                except Exception:pass
                d.parameters[p['name']].expression=raw; d.invalidate_parameter_cache()
                try:
                    d.rebuild()
                except Exception as legacy_exc:
                    idx=earliest_affected_feature(d,p['name'])
                    # Restore the exact pre-rebuild editable model, while intentionally
                    # retaining the new parameter expression.
                    d.shape=baseline['shape']; d.features=copy.deepcopy(baseline['features']); d._feature_shape_cache=list(baseline['feature_cache'])
                    d.sketches=copy.deepcopy(baseline['sketches']); d.sketches3d=copy.deepcopy(baseline['sketches3d']); d.work_planes=copy.deepcopy(baseline['work_planes']); d.work_axes=copy.deepcopy(baseline['work_axes']); d.work_points=copy.deepcopy(baseline['work_points']); d.imates=copy.deepcopy(baseline['imates']); d.sheet_metal=copy.deepcopy(baseline['sheet_metal'])
                    d.invalidate_parameter_cache(); d.numeric_params()  # reject invalid/cyclic expressions before any fallback commit
                    if idx is None:
                        # The changed parameter is not consumed by recorded geometry.
                        # Keeping the verified pre-change B-Rep is semantically exact.
                        pass
                    elif idx>0 and len(d._feature_shape_cache)>=idx:
                        try:d.rebuild(idx)
                        except Exception:raise legacy_exc
                    else:
                        raise legacy_exc
                self._changed(); return self.execute('get_parameter',{'name':p['name']})

            if command=='edit_parameter':
                action=str(p.get('action','')).lower()
                if action=='rename':
                    if not p.get('new_name'):raise ValueError('edit_parameter rename requires new_name')
                    r=d.rename_parameter(p['name'],p['new_name'])
                elif action=='delete':r=d.delete_parameter(p['name'])
                else:raise ValueError('edit_parameter action must be rename|delete')
                self._changed(); return {'ok':True,**r}
            if command=='rename_parameter':
                r=d.rename_parameter(p['name'],p['new_name']); self._changed(); return {'ok':True,**r}
            if command=='delete_parameter':
                r=d.delete_parameter(p['name']); self._changed(); return {'ok':True,**r}
            if command=='get_iproperty':return {'set_name':p['set_name'],'prop_name':p['prop_name'],'value':d.get_iproperty(p['set_name'],p['prop_name'])}
            if command=='set_iproperty':d.set_iproperty(p['set_name'],p['prop_name'],p['value']); self._changed(); return {'ok':True,'set_name':p['set_name'],'prop_name':p['prop_name'],'value':p['value']}
            if command=='get_mass_properties':return d.mass_properties()

            if command=='create_sketch':
                if d.doc_type!='part':raise ValueError('create_sketch requires part document')
                name=p.get('name') or f'Sketch{len(d.sketches)+1}'
                raw_plane=p.get('plane','XY')
                plane=canonical_plane_name(raw_plane,allow_compat=True) if isinstance(raw_plane,str) else raw_plane
                plane_ref=None
                if isinstance(plane,str) and plane not in ORIGIN_PLANES and plane not in d.work_planes:
                    if d.shape is not None:
                        try:plane_ref=make_face_ref(d.shape,plane)
                        except Exception:pass
                    if plane_ref is None:
                        available=[*ORIGIN_PLANES.keys(),*d.work_planes.keys()]
                        raise ValueError(f"Unknown sketch plane reference '{raw_plane}'. Use XY/XZ/YZ, an exact work-plane name, or a planar face reference. Available work planes: {available}")
                d.sketches[name]=SketchModel(name,plane,plane_ref=plane_ref); self.active_sketch=name; self._changed(); return {'ok':True,'sketch_name':name,'plane':plane}
            if command=='project_geometry':
                sm=self._sketch(p.get('sketch_name')); ids=d.project_edges_to_sketch(sm,p['edge_ids']); self._changed(); return {'ok':True,'projected_count':len(ids),'entity_ids':ids}
            if command=='draw_line':
                sm=self._sketch(p.get('sketch_name')); tag=self._next_tag(sm); sm.entities.append(SketchEntity('line',tag,{k:p[k] for k in ('x1','y1','x2','y2')})); self._changed(); return {'ok':True,'entity_id':tag}
            if command=='draw_circle':
                sm=self._sketch(p.get('sketch_name')); tag=self._next_tag(sm); sm.entities.append(SketchEntity('circle',tag,{'cx':p.get('cx',0),'cy':p.get('cy',0),'r':p.get('radius',p.get('radius_mm'))})); self._changed(); return {'ok':True,'entity_id':tag}
            if command=='draw_rectangle':
                sm=self._sketch(p.get('sketch_name')); x1,y1,x2,y2=[p[k] for k in ('x1','y1','x2','y2')]; pts=[(x1,y1,x2,y1),(x2,y1,x2,y2),(x2,y2,x1,y2),(x1,y2,x1,y1)]; ids=[]
                for a,b,c,e in pts:
                    tag=self._next_tag(sm); sm.entities.append(SketchEntity('line',tag,{'x1':a,'y1':b,'x2':c,'y2':e})); ids.append(tag)
                self._changed(); return {'ok':True,'entity_ids':ids}
            if command=='draw_arc':
                sm=self._sketch(p.get('sketch_name')); tag=self._next_tag(sm); sm.entities.append(SketchEntity('arc',tag,{'cx':p.get('cx',0),'cy':p.get('cy',0),'r':p.get('radius',p.get('radius_mm')),'start_deg':p['start_deg'],'end_deg':p['end_deg']})); self._changed(); return {'ok':True,'entity_id':tag}
            if command=='add_sketch_dimension':
                sm=self._sketch(p.get('sketch_name')); e=self._entity(sm,p['entity_id']); v=float(d.value(p['value_mm']))
                pname=d.create_model_parameter(v,'mm')
                if e.kind in ('circle','arc'):e.data['r']=pname
                elif e.kind=='line':
                    x1,y1=float(d.value(e.data['x1'])),float(d.value(e.data['y1'])); x2,y2=float(d.value(e.data['x2'])),float(d.value(e.data['y2'])); dx,dy=x2-x1,y2-y1; L=math.hypot(dx,dy) or 1; e.data['x2']=x1+dx/L*v; e.data['y2']=y1+dy/L*v
                    # Length dimension remains solver-driven, while its target is a real global model parameter.
                dim={'name':pname,'entity_id':p['entity_id'],'value_mm':pname,'driving':True}; sm.dimensions.append(dim); solve_constraints(sm,d.value); self._changed(); return {'ok':True,'dimension_name':dim['name'],'constraint_health':d.sketch_constraint_health(sm)}
            if command=='add_sketch_constraint':
                sm=self._sketch(p.get('sketch_name')); typ=p['type'].lower(); allowed={'coincident','parallel','perpendicular','horizontal','vertical','tangent','concentric','equal','collinear','symmetric'}
                if typ not in allowed:raise ValueError(f"type must be one of {sorted(allowed)}")
                ids=p['entity_ids']; c={'name':f'c{len(sm.constraints)+1}','type':typ,'entity_ids':ids,'directly_enforced':False}; sm.constraints.append(c); solve_constraints(sm,d.value); health=d.sketch_constraint_health(sm)[-1]; c['directly_enforced']=health['health']=='up_to_date'; self._changed(); return {'ok':True,'constraint_name':c['name'],'health':health['health'],'directly_enforced':c['directly_enforced'],'error':health.get('error'),'note':health.get('reason')}
            if command=='close_sketch':
                sm=self._sketch(p.get('sketch_name')); sm.closed=True; self.active_sketch=None; self._changed(); return {'ok':True,'sketch_name':sm.name,'constraint_health':d.sketch_constraint_health(sm)}
            if command=='edit_sketch':
                action=str(p.get('action','')).lower(); sn=p.get('sketch_name') or self.active_sketch
                amap={'create_3d':'create','add_3d_line':'add_line','add_3d_spline':'add_spline','transform_3d':'transform','rename_3d':'rename','delete_3d':'delete'}
                if action in amap:
                    r=d.edit_sketch3d(amap[action],p.get('sketch_name'),p.get('entity_ids'),p.get('start_mm'),p.get('end_mm'),p.get('points_mm'),p.get('translate_mm'),p.get('rotate_axis'),p.get('rotate_deg',0.0),p.get('pivot_mm'),p.get('new_name')); self._changed(); return {'ok':True,**r}
                if not sn:raise ValueError('edit_sketch requires sketch_name or an active sketch')
                if action=='transform':
                    ids=p.get('entity_ids') or []
                    if not ids:raise ValueError('edit_sketch transform requires entity_ids')
                    tr=p.get('translate_mm')
                    if tr is not None and len(tr)!=2:raise ValueError('translate_mm must contain [dx,dy]')
                    piv=p.get('pivot_mm')
                    if piv is not None and len(piv)!=2:raise ValueError('pivot_mm must contain [x,y]')
                    r=d.transform_sketch_entities(sn,ids,tr,p.get('rotate_deg',0),piv,p.get('copy',False))
                elif action=='rename':
                    if not p.get('new_name'):raise ValueError('edit_sketch rename requires new_name')
                    r=d.rename_sketch(sn,p['new_name'])
                elif action=='delete':r=d.delete_sketch(sn,p.get('delete_dependents',False))
                else:raise ValueError('edit_sketch action must be transform|rename|delete|create_3d|add_3d_line|add_3d_spline|transform_3d|rename_3d|delete_3d')
                self._changed(); return {'ok':True,**r}
            if command=='transform_sketch_entities':
                r=d.transform_sketch_entities(p.get('sketch_name') or self.active_sketch,p['entity_ids'],p.get('translate_mm'),p.get('rotate_deg',0),p.get('pivot_mm'),p.get('copy',False)); self._changed(); return {'ok':True,**r}
            if command=='rename_sketch':
                r=d.rename_sketch(p['name'],p['new_name']);
                if self.active_sketch==p['name']:self.active_sketch=p['new_name']
                self._changed(); return {'ok':True,**r}
            if command=='delete_sketch':
                r=d.delete_sketch(p['name'],p.get('delete_dependents',False));
                if self.active_sketch==p['name']:self.active_sketch=None
                self._changed(); return {'ok':True,**r}
            if command=='edit_sketch_entity':
                r=d.edit_sketch_entity(p.get('sketch_name') or self.active_sketch,p['entity_id'],p.get('updates') or {}); self._changed(); return {'ok':True,**r}
            if command=='delete_sketch_entity':
                r=d.delete_sketch_entity(p.get('sketch_name') or self.active_sketch,p['entity_id']); self._changed(); return {'ok':True,**r}
            if command=='edit_sketch_dimension':
                r=d.edit_sketch_dimension(p.get('sketch_name') or self.active_sketch,p['dimension_name'],p['value_mm']); self._changed(); return {'ok':True,**r}
            if command=='delete_sketch_dimension':
                r=d.delete_sketch_dimension(p.get('sketch_name') or self.active_sketch,p['dimension_name']); self._changed(); return {'ok':True,**r}
            if command=='edit_sketch_constraint':
                r=d.edit_sketch_constraint(p.get('sketch_name') or self.active_sketch,p['constraint_name'],p.get('constraint_type'),p.get('entity_ids')); self._changed(); return {'ok':True,**r}
            if command=='delete_sketch_constraint':
                r=d.delete_sketch_constraint(p.get('sketch_name') or self.active_sketch,p['constraint_name']); self._changed(); return {'ok':True,**r}

            if command=='extrude':
                q=copy.deepcopy(p)
                if isinstance(q.get('distance_mm'),(int,float)):q['distance_mm']=d.create_model_parameter(q['distance_mm'],'mm')
                rec=d.add_feature('extrude',None,q,q.get('operation','join')); self._changed(); return {'ok':True,'feature_name':rec.name}
            if command=='revolve':
                q=copy.deepcopy(p)
                if isinstance(q.get('angle_deg'),(int,float)):q['angle_deg']=d.create_model_parameter(q['angle_deg'],'deg')
                rec=d.add_feature('revolve',None,q,q.get('operation','join')); self._changed(); return {'ok':True,'feature_name':rec.name}
            if command in ('fillet','chamfer'):
                q=dict(p)
                # Capture persistent edge descriptors so history survives upstream dimension edits.
                raw=q.get('edge_ids') or []
                if raw and d.shape is not None:
                    q['edge_refs']=[make_edge_ref(d.shape,x) for x in raw]
                key='radius_mm' if command=='fillet' else 'distance_mm'
                if isinstance(q.get(key),(int,float)):q[key]=d.create_model_parameter(q[key],'mm')
                rec=d.add_feature(command,None,q,'join'); self._changed(); return {'ok':True,'feature_name':rec.name}
            if command=='create_work_plane':
                offset=p.get('offset_mm')
                if str(p.get('type','')).lower()=='offset' and isinstance(offset,(int,float)):
                    offset=d.create_model_parameter(offset,'mm')
                rec=d.create_work_plane(p['type'],p['refs'],offset); self._changed(); return {'ok':True,'work_plane_name':rec['name']}
            if command=='create_work_axis':
                rec=d.create_work_axis(p['type'],p['refs']); self._changed(); return {'ok':True,'work_axis_name':rec['name']}
            if command=='edit_work_feature':
                action=str(p.get('action','redefine')).lower(); kind=str(p.get('kind','')).lower()
                if action=='create':
                    if kind=='point':
                        rec=d.create_work_point(p.get('type') or 'fixed',p.get('refs'),p.get('point_mm'),p.get('name') or p.get('new_name')); r={'work_point_name':rec['name'],**rec}
                    elif kind=='plane':
                        rec=d.create_work_plane(p.get('type'),p.get('refs') or [],p.get('offset_mm'),p.get('name') or p.get('new_name')); r={'work_plane_name':rec['name'],**rec}
                    elif kind=='axis':
                        rec=d.create_work_axis(p.get('type'),p.get('refs') or [],p.get('name') or p.get('new_name')); r={'work_axis_name':rec['name'],**rec}
                    else:raise ValueError('edit_work_feature kind must be plane|axis|point')
                elif action=='delete':r=d.delete_work_feature(kind,p['name'])
                elif action=='rename':
                    if not p.get('new_name'):raise ValueError('edit_work_feature rename requires new_name')
                    r=d.edit_work_feature(kind,p['name'],None,None,None,p['new_name'])
                elif action=='redefine':
                    if kind=='point':
                        if p['name'] not in d.work_points:raise ValueError(f"Work point not found: {p['name']}")
                        saved=d.work_points.pop(p['name'])
                        try:
                            rec=d.create_work_point(p.get('type') or saved.get('type','fixed'),p.get('refs') if p.get('refs') is not None else saved.get('refs'),p.get('point_mm') if p.get('point_mm') is not None else saved.get('origin'),p['name'])
                        except Exception:
                            d.work_points[p['name']]=saved; raise
                        r={'name':rec['name'],'kind':'point','action':'redefine'}
                    else:r=d.edit_work_feature(kind,p['name'],p.get('type'),p.get('refs'),p.get('offset_mm'),p.get('new_name'))
                else:raise ValueError('edit_work_feature action must be create|redefine|rename|delete')
                self._changed(); return {'ok':True,**r}
            if command=='delete_work_feature':
                r=d.delete_work_feature(p['kind'],p['name']); self._changed(); return {'ok':True,**r}
            if command=='hole':
                if isinstance(p.get('face'), dict): validate_face_selector(p['face'], allow_cylindrical=False)
                # Backward-compatible aliases stay internal; MCP public schema remains exact ipt-mcp shape.
                if 'face' not in p and p.get('face_selector'):
                    q=str(p['face_selector']); normals={'>X':('+X','max'),'<X':('-X','max'),'>Y':('+Y','max'),'<Y':('-Y','max'),'>Z':('+Z','max'),'<Z':('-Z','max')}
                    if q in normals:p['face']={'kind':'planar','normal':normals[q][0],'extreme':normals[q][1]}
                    else:p['face']=q
                if 'points_mm' not in p and p.get('points') is not None:
                    raw=p.get('points') or []; face=select_face(d.shape,p.get('face') or {'kind':'planar','normal':'+Z','extreme':'max'}); fr=face_frame(face); normal=fr['direction']; xdir=(1,0,0) if abs(float(normal[0]))<0.9 else (0,1,0); po=cq.Plane(origin=tuple(fr['origin']),xDir=xdir,normal=tuple(normal)); out=[]
                    for q in raw:
                        if len(q)==3:out.append(q)
                        elif len(q)==2:
                            v=po.toWorldCoords((float(q[0]),float(q[1]))); out.append([v.x,v.y,v.z])
                    p['points_mm']=out
                through=bool(p.get('through',True)); depth=p.get('depth_mm')
                if through and depth is not None:raise ValueError('through=true and depth_mm are mutually exclusive')
                if not through and depth is None:raise ValueError('either through=true or depth_mm is required')
                q=dict(p)
                # Inventor-style feature-driving model parameters.  Point coordinates stay
                # literal because the upstream hole contract supplies world coordinates,
                # while actual hole dimensions remain editable through global d# parameters.
                for key,unit in (('diameter_mm','mm'),('depth_mm','mm'),('cbore_diameter_mm','mm'),('cbore_depth_mm','mm'),('csink_diameter_mm','mm'),('csink_angle_deg','deg'),('tapped_thread_depth_mm','mm')):
                    if isinstance(q.get(key),(int,float)):q[key]=d.create_model_parameter(q[key],unit)
                # Preserve the exact legacy face selector/reference as primary.  A persistent
                # descriptor is metadata-only and is consulted only if that legacy reference
                # later fails during rebuild after upstream topology changed.
                if isinstance(q.get('face'),str) and d.shape is not None:
                    try:q['_face_ref']=make_face_ref(d.shape,q['face'])
                    except Exception:pass
                rec=d.add_feature('hole',None,q,'cut'); self._changed(); return {'ok':True,'feature_names':[rec.name],'hole_count':len(q.get('points_mm') or [])}
            if command in ('circular_pattern','rectangular_pattern'):
                if command=='circular_pattern': validate_circular_pattern(p)
                else: validate_rectangular_pattern(p)
                q=copy.deepcopy(p)
                if command=='circular_pattern' and isinstance(q.get('angle_deg'),(int,float)):
                    q['angle_deg']=d.create_model_parameter(q['angle_deg'],'deg')
                elif command=='rectangular_pattern':
                    if isinstance(q.get('spacing_mm1'),(int,float)):q['spacing_mm1']=d.create_model_parameter(q['spacing_mm1'],'mm')
                    if isinstance(q.get('spacing_mm2'),(int,float)):q['spacing_mm2']=d.create_model_parameter(q['spacing_mm2'],'mm')
                # Exact/named axis references remain primary. Persistent edge descriptors
                # are metadata-only fallbacks used only after the exact reference fails.
                if d.shape is not None:
                    if command=='circular_pattern':
                        axis=q.get('axis')
                        if axis not in {'X Axis','Y Axis','Z Axis','X','Y','Z'} and axis not in d.work_axes:
                            try:q['_axis_ref']=make_edge_ref(d.shape,axis)
                            except Exception:pass
                    else:
                        for key,outkey in (('dir1','_dir1_ref'),('dir2','_dir2_ref')):
                            axis=q.get(key)
                            if axis and axis not in {'X Axis','Y Axis','Z Axis','X','Y','Z'} and axis not in d.work_axes:
                                try:q[outkey]=make_edge_ref(d.shape,axis)
                                except Exception:pass
                rec=d.add_feature(command,None,q,'join'); self._changed(); return {'ok':True,'feature_name':rec.name}
            if command=='create_spur_gear':
                # Native additive extension. The public ipt-mcp 58-tool surface remains unchanged.
                params={
                    'module':p['module'], 'teeth':int(p['teeth']), 'thickness_mm':p['thickness_mm'],
                    'bore_diameter_mm':p.get('bore_diameter_mm',0),
                    'pressure_angle_deg':p.get('pressure_angle_deg',20),
                    'backlash_mm':p.get('backlash_mm',0),
                }
                if p.get('replace',False):
                    d.reset('part'); d.title=p.get('name') or 'Spur Gear'; self.active_sketch=None
                    rec=d.add_feature('spur_gear',p.get('name') or 'SpurGear1',params,'new')
                else:
                    if d.shape is None and p.get('name') and d.title.startswith('Untitled'):d.title=p.get('name')
                    rec=d.add_feature('spur_gear',p.get('name'),params,p.get('operation','new' if d.shape is None else 'join'))
                self._changed()
                derived=rec.params.get('derived') or {}
                return {'ok':True,'feature_name':rec.name,**derived}
            if command in ('create_gear','create_shaft','create_parallel_key','create_bearing','create_spring','create_v_pulley','create_coupling','create_lead_screw_nut'):
                # StandaloneCAD Design-Accelerator layer.  These are additive native
                # extensions only; the upstream-compatible inventor_* surface above is
                # intentionally unchanged.
                kind_map={
                    'create_gear':'accelerator_gear','create_shaft':'accelerator_shaft',
                    'create_parallel_key':'parallel_key','create_bearing':'accelerator_bearing',
                    'create_spring':'accelerator_spring','create_v_pulley':'v_pulley',
                    'create_coupling':'accelerator_coupling','create_lead_screw_nut':'lead_screw_nut',
                }
                feature_kind=kind_map[command]
                params={k:v for k,v in p.items() if k not in ('name','replace','operation')}
                if command=='create_gear':
                    params.setdefault('bore_diameter_mm',0.0); params.setdefault('pressure_angle_deg',20.0); params.setdefault('helix_angle_deg',0.0); params.setdefault('backlash_mm',0.0); params.setdefault('clearance_mm',0.0)
                elif command=='create_shaft':
                    params.setdefault('bore_diameter_mm',0.0); params.setdefault('keyway_start_mm',0.0)
                elif command=='create_parallel_key': params.setdefault('end_style','square')
                elif command=='create_bearing': params.setdefault('rolling_elements',8); params.setdefault('detailed',True)
                elif command=='create_spring': params.setdefault('right_handed',True)
                elif command=='create_v_pulley':
                    params.setdefault('groove_count',1); params.setdefault('groove_angle_deg',40.0); params.setdefault('bore_diameter_mm',0.0)
                elif command=='create_coupling':
                    params.setdefault('bolt_count',4)
                elif command=='create_lead_screw_nut':
                    params.setdefault('starts',1); params.setdefault('thread_angle_deg',30.0); params.setdefault('clearance_mm',0.15); params.setdefault('right_handed',True)
                if p.get('replace',False):
                    d.reset('part'); d.title=p.get('name') or command.replace('create_','').replace('_',' ').title(); self.active_sketch=None
                elif d.shape is None and p.get('name') and d.title.startswith('Untitled'):
                    d.title=p.get('name')
                op=p.get('operation','new' if d.shape is None else 'join')
                rec=d.add_feature(feature_kind,p.get('name'),params,op)
                self._changed(); return {'ok':True,'feature_name':rec.name,**(rec.params.get('derived') or {})}
            if command=='create_sheet_metal_base':
                if p.get('replace',False):
                    d.reset('part'); d.title=p.get('name') or 'Sheet Metal Part'; self.active_sketch=None
                elif d.shape is not None:
                    raise ValueError('create_sheet_metal_base requires an empty part or replace=true; normal part geometry is never converted implicitly')
                params={
                    'width_mm':p['width_mm'],'height_mm':p['height_mm'],'thickness_mm':p['thickness_mm'],
                    'bend_radius_mm':p.get('bend_radius_mm',p['thickness_mm']),'k_factor':p.get('k_factor',0.44),
                }
                rec=d.add_feature('sheet_metal_base',p.get('name') or 'SheetMetalBase1',params,'new'); self._changed()
                return {'ok':True,'feature_name':rec.name,'sheet_metal':copy.deepcopy(d.sheet_metal)}
            if command=='add_sheet_metal_flange':
                if not d.sheet_metal:raise ValueError('add_sheet_metal_flange requires a sheet-metal base')
                params={'edge':p['edge'],'length_mm':p['length_mm'],'angle_deg':p.get('angle_deg',90.0)}
                if p.get('bend_radius_mm') is not None:params['bend_radius_mm']=p['bend_radius_mm']
                rec=d.add_feature('sheet_metal_flange',p.get('name') or f"Flange{len(d.sheet_metal.get('flanges',[]))+1}",params,'join'); self._changed()
                return {'ok':True,'feature_name':rec.name,'sheet_metal':copy.deepcopy(d.sheet_metal)}
            if command in ('extrude_advanced','loft','sweep'):
                if command=='sweep' and not p.get('path_points_mm') and not p.get('path_sketch3d_name'):
                    raise ValueError('sweep requires path_points_mm or path_sketch3d_name')
                rec=d.add_feature(command,p.get('name'),{k:v for k,v in p.items() if k not in ('name','operation')},p.get('operation','join')); self._changed(); return {'ok':True,'feature_name':rec.name,'kind':command}
            if command in ('create_box','create_cylinder','create_cone','create_sphere','create_torus','create_slot','create_coil'):
                kind=command.replace('create_','')
                if p.get('replace',False):
                    d.reset('part'); d.title=p.get('name') or kind.replace('_',' ').title(); self.active_sketch=None
                elif d.shape is None and p.get('name') and d.title.startswith('Untitled'):
                    d.title=p.get('name')
                rec=d.add_feature(kind,p.get('name'),{k:v for k,v in p.items() if k not in ('name','replace','operation')},p.get('operation','new' if d.shape is None else 'join'))
                self._changed(); return {'ok':True,'feature_name':rec.name,'kind':kind}
            if command=='create_metric_hex_bolt':
                params={k:v for k,v in p.items() if k not in ('name','replace','operation')}
                if p.get('replace',False): d.reset('part'); d.title=p.get('name') or f"M{p['diameter_mm']:g} Hex Bolt"; self.active_sketch=None
                elif d.shape is None and p.get('name') and d.title.startswith('Untitled'): d.title=p.get('name')
                rec=d.add_feature('metric_hex_bolt',p.get('name') or 'HexBolt1',params,p.get('operation','new' if d.shape is None else 'join')); self._changed(); return {'ok':True,'feature_name':rec.name,**(rec.params.get('derived') or {})}
            if command=='create_metric_hex_nut':
                params={k:v for k,v in p.items() if k not in ('name','replace','operation')}
                if p.get('replace',False): d.reset('part'); d.title=p.get('name') or f"M{p['diameter_mm']:g} Hex Nut"; self.active_sketch=None
                elif d.shape is None and p.get('name') and d.title.startswith('Untitled'): d.title=p.get('name')
                rec=d.add_feature('metric_hex_nut',p.get('name') or 'HexNut1',params,p.get('operation','new' if d.shape is None else 'join')); self._changed(); return {'ok':True,'feature_name':rec.name,**(rec.params.get('derived') or {})}
            if command=='create_metric_washer':
                params={k:v for k,v in p.items() if k not in ('name','replace','operation')}
                if p.get('replace',False): d.reset('part'); d.title=p.get('name') or f"M{p['diameter_mm']:g} Washer"; self.active_sketch=None
                elif d.shape is None and p.get('name') and d.title.startswith('Untitled'): d.title=p.get('name')
                rec=d.add_feature('metric_washer',p.get('name') or 'Washer1',params,p.get('operation','new' if d.shape is None else 'join')); self._changed(); return {'ok':True,'feature_name':rec.name,**(rec.params.get('derived') or {})}
            if command=='create_external_thread':
                rec=d.add_feature('external_thread',p.get('name'),{k:v for k,v in p.items() if k not in ('name','operation')},p.get('operation','join' if d.shape is not None else 'new')); self._changed(); return {'ok':True,'feature_name':rec.name,**(rec.params.get('derived') or {})}
            if command=='shell':
                q={k:v for k,v in p.items() if k!='name'}
                raw=q.get('remove_face_ids') or []
                if raw and d.shape is not None:
                    q['remove_face_refs']=[make_face_ref(d.shape,x) for x in raw]
                rec=d.add_feature('shell',p.get('name'),q,'join'); self._changed(); return {'ok':True,'feature_name':rec.name}
            if command=='mirror_body':
                rec=d.add_feature('mirror_body',p.get('name'),{k:v for k,v in p.items() if k!='name'},'join'); self._changed(); return {'ok':True,'feature_name':rec.name}
            if command=='transform_body':
                rec=d.add_feature('transform_body',p.get('name'),{k:v for k,v in p.items() if k!='name'},'join'); self._changed(); return {'ok':True,'feature_name':rec.name}
            if command=='edit_solid':
                action=str(p.get('action','')).lower()
                if action=='thread_face':
                    ref=p.get('face_ref')
                    if ref is None:
                        sel=self.selection if isinstance(self.selection,dict) else {}
                        ref=sel.get('id') if sel.get('type')=='face' else None
                    if ref is None:raise ValueError('edit_solid thread_face requires face_ref or an explicitly selected face')
                    r=d.thread_face(ref,p.get('designation'),p.get('pitch_mm'),p.get('major_diameter_mm'),p.get('full_depth',True),p.get('thread_depth_mm'),p.get('offset_mm',0),p.get('right_handed',True),p.get('internal'),p.get('reverse_direction',False),p.get('name'))
                    self._changed(); return {'ok':True,**r}
                if action=='split_body':
                    if p.get('plane') is None:raise ValueError('edit_solid split_body requires plane')
                    rec=d.add_feature('split_body',p.get('name'),{'plane':p['plane'],'keep':p.get('keep','both')},'join'); self._changed(); return {'ok':True,'feature_name':rec.name,'solid_count':len(d.shape.Solids()) if d.shape is not None else 0}
                if action=='combine_bodies':
                    ids=p.get('tool_indices') or []
                    if not ids:raise ValueError('edit_solid combine_bodies requires tool_indices')
                    rec=d.add_feature('combine_bodies',p.get('name'),{'base_index':int(p.get('base_index',1)),'tool_indices':[int(x) for x in ids],'operation':p.get('operation','join'),'keep_tools':p.get('keep_tools',False)},'join'); self._changed(); return {'ok':True,'feature_name':rec.name,'solid_count':len(d.shape.Solids()) if d.shape is not None else 0}
                if action=='draft_face':
                    ref=p.get('face_ref')
                    if ref is None:
                        sel=self.selection if isinstance(self.selection,dict) else {}; ref=sel.get('face_ref') or (sel.get('id') if sel.get('type')=='face' else None)
                    if ref is None:raise ValueError('edit_solid draft_face requires face_ref or selected face')
                    if d.shape is not None:ref=make_face_ref(d.shape,ref)
                    if p.get('angle_deg') is None or p.get('neutral_plane') is None:raise ValueError('draft_face requires angle_deg and neutral_plane')
                    rec=d.add_feature('draft_face',p.get('name'),{'face_ref':ref,'angle_deg':p['angle_deg'],'pull_direction':p.get('pull_direction') or [0,0,1],'neutral_plane':p['neutral_plane']},'join'); self._changed(); return {'ok':True,'feature_name':rec.name}
                if action=='replace_face':
                    ref=p.get('face_ref')
                    if ref is None:
                        sel=self.selection if isinstance(self.selection,dict) else {}; ref=sel.get('face_ref') or (sel.get('id') if sel.get('type')=='face' else None)
                    if ref is None or p.get('target_plane') is None:raise ValueError('replace_face requires face_ref/selection and target_plane')
                    if d.shape is not None:ref=make_face_ref(d.shape,ref)
                    rec=d.add_feature('replace_face',p.get('name'),{'face_ref':ref,'target_plane':p['target_plane']},'join'); self._changed(); return {'ok':True,'feature_name':rec.name}
                if action=='offset_body':
                    if p.get('distance_mm') is None:raise ValueError('offset_body requires distance_mm')
                    rec=d.add_feature('offset_body',p.get('name'),{'distance_mm':p['distance_mm'],'join':p.get('join','arc'),'remove_internal_edges':p.get('remove_internal_edges',True)},'join'); self._changed(); return {'ok':True,'feature_name':rec.name}
                if action=='delete_faces':
                    refs=list(p.get('face_refs') or [])
                    if not refs:
                        sel=self.selection if isinstance(self.selection,dict) else {}
                        one=sel.get('face_ref') or (sel.get('id') if sel.get('type')=='face' else None)
                        if one:refs=[one]
                    if not refs:raise ValueError('delete_faces requires face_refs or a selected face')
                    if d.shape is not None:refs=[make_face_ref(d.shape,x) for x in refs]
                    rec=d.add_feature('delete_faces',p.get('name'),{'face_refs':refs},'join'); self._changed(); return {'ok':True,'feature_name':rec.name}
                raise ValueError('edit_solid action must be thread_face|split_body|combine_bodies|draft_face|replace_face|offset_body|delete_faces')
            if command=='thread_face':
                ref=p.get('face_ref')
                if ref is None:
                    sel=self.selection if isinstance(self.selection,dict) else {}
                    ref=sel.get('id') if sel.get('type')=='face' else None
                if ref is None:raise ValueError('thread_face requires face_ref or an explicitly selected cylindrical face')
                r=d.thread_face(ref,p.get('designation'),p.get('pitch_mm'),p.get('major_diameter_mm'),p.get('full_depth',True),p.get('thread_depth_mm'),p.get('offset_mm',0),p.get('right_handed',True),p.get('internal'),p.get('reverse_direction',False),p.get('name')); self._changed(); return {'ok':True,**r}
            if command=='split_body':
                rec=d.add_feature('split_body',p.get('name'),{'plane':p['plane'],'keep':p.get('keep','both')},'join'); self._changed(); return {'ok':True,'feature_name':rec.name,'solid_count':len(d.shape.Solids()) if d.shape is not None else 0}
            if command=='combine_bodies':
                rec=d.add_feature('combine_bodies',p.get('name'),{'base_index':p.get('base_index',1),'tool_indices':p['tool_indices'],'operation':p.get('operation','join'),'keep_tools':p.get('keep_tools',False)},'join'); self._changed(); return {'ok':True,'feature_name':rec.name,'solid_count':len(d.shape.Solids()) if d.shape is not None else 0}
            if command=='move_face':
                ref=p.get('face_ref') or p.get('face_id')
                if ref is None:
                    sel=self.selection if isinstance(self.selection,dict) else {}
                    ref=sel.get('face_ref') if sel.get('type')=='face' else None
                if ref is None:raise ValueError('move_face requires face_ref or an explicitly selected face')
                r=d.move_face(ref,p['distance_mm'],bool(p.get('prefer_history',True)),p.get('name')); self._changed(); return {'ok':True,**r}
            if command=='delete_face':
                ref=p.get('face_ref') or p.get('face_id')
                if ref is None:
                    sel=self.selection if isinstance(self.selection,dict) else {}
                    ref=sel.get('face_ref') if sel.get('type')=='face' else None
                if ref is None:raise ValueError('delete_face requires face_ref or an explicitly selected face')
                r=d.delete_face_heal(ref,p.get('name')); self._changed(); return {'ok':True,**r}
            if command=='set_face_diameter':
                ref=p.get('face_ref') or p.get('face_id')
                if ref is None:
                    sel=self.selection if isinstance(self.selection,dict) else {}
                    ref=sel.get('face_ref') if sel.get('type')=='face' else None
                if ref is None:raise ValueError('set_face_diameter requires face_ref or an explicitly selected face')
                r=d.set_face_diameter(ref,p['diameter_mm']); self._changed(); return {'ok':True,**r}
            if command=='edit_feature':
                rec=d.edit_feature(p['feature_name'],p.get('updates') or {}); self._changed(); return {'ok':True,'feature_name':rec.name,'params':rec.params,'operation':rec.operation}
            if command=='edit_feature_definition':
                if p.get('before') is not None or p.get('after') is not None:
                    if p.get('updates'):raise ValueError('Reposition and definition updates must be separate atomic edits')
                    r=d.reposition_feature(p['feature_name'],p.get('before'),p.get('after'))
                else:r=d.edit_feature_definition(p['feature_name'],p.get('updates') or {},p.get('new_name'))
                self._changed(); return {'ok':True,**r}
            if command=='suppress_feature':
                rec=d.suppress_feature(p['feature_name'],p.get('suppressed',True)); self._changed(); return {'ok':True,'feature_name':rec.name,'suppressed':rec.suppressed}
            if command=='delete_feature':
                name=d.delete_feature(p['feature_name']); self._changed(); return {'ok':True,'deleted_feature':name}

            if command=='capture_view':
                out=p.get('output_path')
                if out: out=str(validate_export_path(out))
                return render_shape_png(d.shape,p.get('width',1280),p.get('height',720),d.view_orientation,out)
            if command=='export_step':
                if d.shape is None:raise ValueError('No body')
                path=str(validate_export_path(p['output_path'])); Path(path).parent.mkdir(parents=True,exist_ok=True); exporters.export(d.shape,path,exportType='STEP'); return {'ok':True,'path':path}
            if command=='export_stl':
                if d.shape is None:raise ValueError('No body')
                path=str(validate_export_path(p['output_path'])); Path(path).parent.mkdir(parents=True,exist_ok=True); exporters.export(d.shape,path,exportType='STL',tolerance=0.01,angularTolerance=0.1); return {'ok':True,'path':path}
            if command=='export_dxf':
                path=str(validate_export_path(p['output_path'])); Path(path).parent.mkdir(parents=True,exist_ok=True)
                if p['source']=='flat_pattern':
                    # This succeeds only for an explicit StandaloneCAD sheet-metal model.
                    # Normal extruded thin solids still fail truthfully; there is no
                    # largest-face projection or implicit sheet-metal guess.
                    sketch,meta=d.flat_pattern(); exporters.export(sketch,path,exportType='DXF')
                    return {'ok':True,'path':path,'source':'flat_pattern','flat_pattern':meta}
                name=p.get('sketch_name');
                if not name or name not in d.sketches:raise ValueError('sketch_name is required when source=sketch')
                exporters.export(d.build_sketch(d.sketches[name]),path,exportType='DXF'); return {'ok':True,'path':path,'source':'sketch','sketch_name':name}
            if command=='view_fit':d.view_fit=True; self._view_changed(); return {'ok':True}
            if command=='set_view_orientation':d.view_orientation=p['orientation']; d.view_fit=bool(p.get('fit',True)); self._view_changed(); return {'ok':True,'orientation':d.view_orientation,'fit':d.view_fit}

            if command=='place_occurrence':
                if d.doc_type!='assembly':
                    # Preserve the historical validation/error contract outside assembly context.
                    r=d.place_occurrence(p['path'],p.get('grounded',False),p.get('position_mm'),p.get('rotation_deg_xyz'))
                    self._bind_occurrence_to_open_source(d,r['occurrence_name']); self._changed(); return {'ok':True,**r}
                comp=d.component_definition
                occ=comp.occurrences.add(
                    p['path'],
                    {'position_mm':p.get('position_mm'),'rotation_deg_xyz':p.get('rotation_deg_xyz')},
                    p.get('grounded',False),
                )
                r=occ.creation_result() or {'occurrence_name':occ.name,'component_type':occ.component_type}
                self._bind_occurrence_to_open_source(d,r['occurrence_name'])
                self._changed(); return {'ok':True,**r}
            if command=='add_constraint':
                if d.doc_type!='assembly':
                    r=d.add_assembly_constraint(p['type'],p.get('a_occurrence'),p['a_ref'],p.get('b_occurrence'),p['b_ref'],p.get('offset_mm',0),p.get('angle_deg'),p.get('insert_opposed',True)); self._changed(); return {'ok':True,**r}
                comp=d.component_definition
                a=comp.occurrences.item(p['a_occurrence']) if p.get('a_occurrence') else None
                b=comp.occurrences.item(p['b_occurrence']) if p.get('b_occurrence') else None
                r=comp.constraints.add(p['type'],a,p['a_ref'],b,p['b_ref'],p.get('offset_mm',0),p.get('angle_deg'),p.get('insert_opposed',True)); self._changed(); return {'ok':True,**r}
            if command=='create_imate':
                validate_face_selector(p['selector'], allow_cylindrical=True)
                r=d.create_imate(p['name'],p['type'],p['selector'],p.get('offset_mm',0),p.get('insert_opposed',True),p.get('distance_mm',0)); self._changed(); return {'ok':True,'imate':r}
            if command=='list_interfaces':
                if d.doc_type=='assembly':return d.component_definition.list_interfaces(p.get('occurrence'))
                return d.list_interfaces(p.get('occurrence'))
            if command=='check_interference':
                if d.doc_type!='assembly':return d.check_interference(p.get('occurrences'))
                return d.component_definition.check_interference(p.get('occurrences'))
            if command=='measure_min_distance':
                if d.doc_type!='assembly':return d.measure_min_distance(p['a_occurrence'],p.get('a_ref'),p['b_occurrence'],p.get('b_ref'))
                return d.component_definition.measure_min_distance(p['a_occurrence'],p.get('a_ref'),p['b_occurrence'],p.get('b_ref'))
            if command=='get_assembly_bom':
                if d.doc_type!='assembly':return d.assembly_bom(int(p.get('max_rows',500)))
                return d.component_definition.get_bom(int(p.get('max_rows',500)))
            if command=='list_constraints':
                if d.doc_type!='assembly':return {'constraints':[vars(c) for c in d.constraints]}
                return d.component_definition.list_constraints()
            if command=='add_joint':
                if d.doc_type!='assembly':
                    r=d.add_assembly_joint(p['type'],p.get('a_occurrence'),p.get('a_ref'),p.get('b_occurrence'),p.get('b_ref'),p.get('linear_position_mm'),p.get('linear_start_mm'),p.get('linear_end_mm'),p.get('angular_position_deg'),p.get('angular_start_deg'),p.get('angular_end_deg'),p.get('name'),p.get('a_intent'),p.get('b_intent'),p.get('flip_origin_direction',False),p.get('flip_alignment_direction',False)); return {'ok':True,**r}
                comp=d.component_definition
                a=comp.occurrences.item(p['a_occurrence']) if p.get('a_occurrence') else None
                b=comp.occurrences.item(p['b_occurrence']) if p.get('b_occurrence') else None
                r=comp.joints.add(
                    p['type'],a,p.get('a_ref'),b,p.get('b_ref'),
                    linear_position_mm=p.get('linear_position_mm'),linear_start_mm=p.get('linear_start_mm'),linear_end_mm=p.get('linear_end_mm'),
                    angular_position_deg=p.get('angular_position_deg'),angular_start_deg=p.get('angular_start_deg'),angular_end_deg=p.get('angular_end_deg'),
                    name=p.get('name'),a_intent=p.get('a_intent'),b_intent=p.get('b_intent'),
                    flip_origin_direction=p.get('flip_origin_direction',False),flip_alignment_direction=p.get('flip_alignment_direction',False),
                )
                if r.get('health')!='up_to_date':
                    import json as _json
                    diag=d.assembly_relationship_health()
                    raise ValueError(f"Assembly joint health is {r.get('health')}; diagnostics={_json.dumps(diag,ensure_ascii=False,default=str)}")
                self._changed(); return {'ok':True,**r}
            if command=='edit_joint':
                name=p['name']; updates={k:v for k,v in p.items() if k!='name' and v is not None}
                if d.doc_type!='assembly':r=d.edit_assembly_joint(name,**updates)
                else:r=d.component_definition.joints.edit(name,**updates)
                if r.get('health')!='up_to_date':
                    import json as _json
                    raise ValueError(f"Assembly joint health is {r.get('health')}; diagnostics={_json.dumps(d.assembly_relationship_health(),ensure_ascii=False,default=str)}")
                self._changed(); return {'ok':True,**r}
            if command=='set_joint_limits':
                if d.doc_type!='assembly':r=d.set_joint_limits(p['name'],p.get('linear_start_mm'),p.get('linear_end_mm'),p.get('angular_start_deg'),p.get('angular_end_deg'))
                else:r=d.component_definition.joints.set_limits(p['name'],p.get('linear_start_mm'),p.get('linear_end_mm'),p.get('angular_start_deg'),p.get('angular_end_deg'))
                if r.get('health')!='up_to_date':
                    import json as _json
                    raise ValueError(f"Assembly joint health is {r.get('health')}; diagnostics={_json.dumps(d.assembly_relationship_health(),ensure_ascii=False,default=str)}")
                self._changed(); return {'ok':True,**r}
            if command=='drive_joint':
                if d.doc_type!='assembly':r=d.drive_joint(p['name'],p.get('linear_position_mm'),p.get('angular_position_deg'))
                else:r=d.component_definition.joints.drive(p['name'],p.get('linear_position_mm'),p.get('angular_position_deg'))
                if r.get('health')!='up_to_date':
                    import json as _json
                    raise ValueError(f"Assembly joint health is {r.get('health')}; diagnostics={_json.dumps(d.assembly_relationship_health(),ensure_ascii=False,default=str)}")
                self._changed(); return {'ok':True,**r}
            if command=='list_joints':
                if d.doc_type!='assembly':return d.list_joints()
                return d.component_definition.joints.list()
            if command=='delete_joint':
                if d.doc_type!='assembly':r=d.delete_joint(p['name'])
                else:r=d.component_definition.joints.delete(p['name'])
                self._changed(); return {'ok':True,**r}
            if command=='replace_occurrence':
                if d.doc_type!='assembly':raise ValueError('replace_occurrence requires an active assembly')
                name=p.get('occurrence_name')
                if not name:
                    sel=self.selection if isinstance(self.selection,dict) else {}; name=sel.get('occurrence_name')
                if not name:raise ValueError('replace_occurrence requires occurrence_name or a selected occurrence')
                r=d.replace_occurrence(name,p['path'],p.get('replace_all',False)); self._changed(); return {'ok':True,**r}
            if command=='edit_occurrence_state':
                if d.doc_type!='assembly':raise ValueError('edit_occurrence_state requires an active assembly')
                name=p.get('occurrence_name')
                if not name:
                    sel=self.selection if isinstance(self.selection,dict) else {}; name=sel.get('occurrence_name')
                if not name:raise ValueError('edit_occurrence_state requires occurrence_name or a selected occurrence')
                occ=d.component_definition.occurrences.item(name)
                if p.get('grounded') is not None:occ.grounded=bool(p['grounded'])
                if p.get('suppressed') is not None:occ.suppressed=bool(p['suppressed'])
                self._changed(); return {'ok':True,'occurrence_name':name,'grounded':occ.grounded,'suppressed':occ.suppressed}
            if command=='transform_occurrence':
                if d.doc_type!='assembly':raise ValueError('transform_occurrence requires an active assembly')
                name=p.get('occurrence_name')
                if not name:
                    sel=self.selection if isinstance(self.selection,dict) else {}
                    name=sel.get('occurrence_name') if sel.get('type') in ('occurrence','face','edge') else None
                if not name:raise ValueError('transform_occurrence requires occurrence_name or an explicitly selected occurrence/assembly geometry')
                occ=d.component_definition.occurrences.item(name)
                if p.get('replacement_path'):
                    r=d.replace_occurrence(name,p['replacement_path'],p.get('replace_all',False))
                    occ=d.component_definition.occurrences.item(name)
                if p.get('grounded') is not None:occ.grounded=bool(p['grounded'])
                if p.get('suppressed') is not None:occ.suppressed=bool(p['suppressed'])
                pos=occ.position_mm; rot=occ.rotation_deg_xyz
                if p.get('position_mm') is not None:pos=[float(x) for x in p['position_mm']]
                if p.get('rotation_deg_xyz') is not None:rot=[float(x) for x in p['rotation_deg_xyz']]
                if p.get('translate_mm') is not None:
                    if len(p['translate_mm'])!=3:raise ValueError('translate_mm must contain 3 values')
                    pos=[a+float(b) for a,b in zip(pos,p['translate_mm'])]
                if p.get('rotate_delta_deg_xyz') is not None:
                    if len(p['rotate_delta_deg_xyz'])!=3:raise ValueError('rotate_delta_deg_xyz must contain 3 values')
                    rot=[a+float(b) for a,b in zip(rot,p['rotate_delta_deg_xyz'])]
                occ.set_transform(pos,rot); self._changed(); return {'ok':True,'occurrence_name':occ.name,'position_mm':occ.position_mm,'rotation_deg_xyz':occ.rotation_deg_xyz,'grounded':occ.grounded,'suppressed':occ.suppressed,'path':occ.path}
            if command=='edit_constraint':
                if d.doc_type!='assembly':raise ValueError('edit_constraint requires an active assembly')
                c=next((x for x in d.constraints if x.name==p['name']),None)
                if c is None:raise ValueError(f"Constraint not found: {p['name']}")
                if p.get('type') is not None:
                    typ=str(p['type']).lower()
                    if typ not in {'mate','flush','insert','angle'}:raise ValueError('constraint type must be mate|flush|insert|angle')
                    c.type=typ
                if p.get('a_occurrence') is not None:c.a_occurrence=p['a_occurrence']
                if p.get('a_ref') is not None:c.a_ref=p['a_ref']
                if p.get('b_occurrence') is not None:c.b_occurrence=p['b_occurrence']
                if p.get('b_ref') is not None:c.b_ref=p['b_ref']
                if p.get('offset_mm') is not None:c.offset_mm=float(p['offset_mm'])
                if p.get('angle_deg') is not None:c.angle_deg=float(p['angle_deg'])
                if p.get('suppressed') is not None:c.suppressed=bool(p['suppressed'])
                if p.get('insert_opposed') is not None:c.insert_opposed=bool(p['insert_opposed'])
                d.solve_assembly_constraints(); d.rebuild_assembly(solve=False)
                if not c.suppressed and c.health!='up_to_date':
                    import json as _json
                    raise ValueError(f"Assembly constraint health is {c.health}; diagnostics={_json.dumps(d.assembly_relationship_health(),ensure_ascii=False,default=str)}")
                self._changed(); return {'ok':True,'constraint':vars(c).copy()}
            if command=='delete_constraint':
                if d.doc_type!='assembly':raise ValueError('delete_constraint requires an active assembly')
                r=d.component_definition.constraints.delete(p['name'])
                self._changed(); return {'ok':True,**r}
            if command=='delete_occurrence':
                if d.doc_type!='assembly':raise ValueError('delete_occurrence requires an active assembly')
                name=p.get('occurrence_name')
                if not name:
                    sel=self.selection if isinstance(self.selection,dict) else {}
                    if sel.get('type') in ('occurrence','face','edge'):name=sel.get('occurrence_name')
                if not name:raise ValueError('delete_occurrence requires occurrence_name or an explicitly selected occurrence/assembly geometry')
                r=d.component_definition.occurrences.delete(name)
                sel=self.selection if isinstance(self.selection,dict) else {}
                if sel.get('occurrence_name')==name:self.selection={}
                self._changed(); return {'ok':True,**r}
            if command=='relink_occurrence':
                if d.doc_type!='assembly':raise ValueError('relink_occurrence requires an assembly')
                name=p['occurrence_name']; new_path=str(Path(p['path']).expanduser().resolve())
                if name not in d.occurrences:raise ValueError(f'Occurrence not found: {name}')
                shape,interfaces,pn,mass,ctype,children=__import__('standalonecad.core.assembly',fromlist=['load_component']).load_component(new_path,{str(Path(d.path).resolve())} if d.path else None)
                occ=d.occurrences[name]; occ.path=new_path; occ.shape=shape; occ.interfaces=interfaces; occ.part_number=pn; occ.unit_mass_g=mass; occ.component_type=ctype; occ.children=children; occ.source_session_id=None; occ.reference_status='resolved'; occ.load_error=None
                self._bind_occurrence_to_open_source(d,name); d.rebuild_assembly(); self._changed(); return {'ok':True,'occurrence_name':name,'path':new_path,'component_type':ctype}
            if command=='save_all_documents':return self.save_all()

            if command=='send_code':raise PermissionError('SEND_CODE_DISABLED')

            # Standalone-only optional extension commands (not in strict 58-tool compatibility surface).
            if command=='get_topology':return d.topology()
            if command=='get_selection':return dict(self.selection)
            if command=='undo':return self.undo()
            if command=='redo':return self.redo()
            raise ValueError(f'Unknown command: {command}')
        except Exception:
            raise
