from __future__ import annotations
import json, hashlib, re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from standalonecad.bridge.protocol import APP_DIR

# Exact current upstream BakedToolDispatchAuthorizer allow-list (v0.1.0 +
# assembly-query additions). Baked tools are governed read-only query tools,
# never geometry/document mutations.
BAKED_ALLOWED = frozenset({
    'health','get_document_info','list_open_documents','list_parameters',
    'get_parameter','get_iproperty','get_mass_properties','list_interfaces',
    'check_interference','measure_min_distance','get_assembly_bom','list_constraints',
})

class BakeStore:
    def __init__(self):
        self.path=APP_DIR/'bake_store.json'; self.history=[]; self.data={'tools':{},'suggestions':{},'patterns':{}}; self.load()
    def load(self):
        if self.path.exists():
            try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
            except Exception:pass
    def save(self):self.path.write_text(json.dumps(self.data,indent=2,ensure_ascii=False),encoding='utf-8')
    def observe(self,command,args):
        # Upstream baked tools may dispatch only the governed read-only query set.
        # Do not generate suggestions for geometry/write commands that could never
        # be accepted by the real BakedToolDispatchAuthorizer.
        if command not in BAKED_ALLOWED:return
        self.history.append({'command':command,'params':args}); self.history=self.history[-12:]
        # Detect repeated 3-command workflows. Values are preserved from the latest successful sequence.
        if len(self.history)>=3:
            seq=self.history[-3:]; key='>'.join(x['command'] for x in seq); rec=self.data['patterns'].setdefault(key,{'count':0}); rec['count']+=1
            if rec['count']>=3:
                sid='sug_'+hashlib.sha1(key.encode()).hexdigest()[:10]
                if sid not in self.data['suggestions']:
                    payload={'steps':seq}; self.data['suggestions'][sid]={'id':sid,'title':'Repeated workflow: '+key,'description':'The same three-command CAD workflow has recurred and can be baked into one tool.','source':'runtime_observer','score':min(1.0,rec['count']/5),'payload_json':json.dumps(payload,ensure_ascii=False),'status':'active','created_at':datetime.now(timezone.utc).isoformat()}
                self.save()
    def list_tools(self):return {'tools':[v for v in self.data['tools'].values()]}
    def list_suggestions(self):
        now=datetime.now(timezone.utc); out=[]
        for s in self.data['suggestions'].values():
            if s.get('status')=='active':out.append(s)
            elif s.get('status')=='snoozed' and s.get('snooze_until'):
                try:
                    if datetime.fromisoformat(s['snooze_until'])<=now:s['status']='active';out.append(s)
                except Exception:pass
        self.save(); return {'suggestions':out}
    def issue_draft(self,id):
        s=self.data['suggestions'].get(id)
        if not s:return {'ok':False,'error_code':'not_found','message':'Bake suggestion was not found.'}
        return {'ok':True,'issue':{'title':'[ToolBaker] '+(s.get('title') or id),'body':f"## Summary\n{s.get('description','Repeated workflow detected.')}\n\n## Suggestion\n- id: `{id}`\n- source: `{s.get('source')}`\n- score: `{s.get('score')}`\n\n## Payload\n```json\n{s.get('payload_json','{}')}\n```"}}
    def prepare_accept(self,suggestion_id,desired_name):
        s=self.data['suggestions'].get(suggestion_id)
        if not s:raise ValueError('suggestion not found')
        if desired_name in self.data['tools']:raise ValueError('desiredName already exists')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_\-]{1,63}',desired_name):raise ValueError('desiredName must be a safe identifier')
        try:payload=json.loads(s.get('payload_json') or '{}')
        except Exception as exc:raise ValueError('suggestion payload is invalid JSON') from exc
        steps=payload.get('steps',[])
        if not isinstance(steps,list) or not steps:raise ValueError('suggestion has no executable steps')
        clean=[]
        for i,step in enumerate(steps):
            if not isinstance(step,dict) or not isinstance(step.get('command'),str) or not isinstance(step.get('params',{}),dict):
                raise ValueError(f'invalid baked step {i}')
            cmd=step['command']
            if cmd not in BAKED_ALLOWED:
                raise ValueError('Baked tool target is not allowed: '+cmd)
            clean.append({'command':cmd,'params':dict(step.get('params') or {})})
        return {
            'name':desired_name,
            'description':s.get('description','Baked CAD query workflow'),
            'source':'macro',
            'suggestion_id':suggestion_id,
            'steps':clean,
            'params_schema':{},
        }
    def commit_accept(self,record):
        sid=record['suggestion_id']; s=self.data['suggestions'].get(sid)
        if not s:raise ValueError('suggestion not found')
        now=datetime.now(timezone.utc).isoformat()
        stored={
            'name':record['name'],'description':record.get('description') or record['name'],
            'source':'macro','handler_tool':'run_baked_tool','usage_count':0,
            'created_at':now,'verified':True,'steps':record['steps'],
            'params_schema':record.get('params_schema') or {},
        }
        self.data['tools'][record['name']]=stored; s['status']='accepted'; self.save()
        return {'ok':True,'name':record['name'],'verified':True,'steps':len(record['steps'])}
    def accept(self,suggestion_id,desired_name):
        # Local compatibility helper. The MCP server performs the upstream-style
        # add-in round-trip before calling commit_accept.
        try:return self.commit_accept(self.prepare_accept(suggestion_id,desired_name))
        except Exception as exc:return {'ok':False,'error':{'code':'INVALID_ARGUMENT','message':str(exc)}}
    def wire_record(self,name):
        rec=self.get_tool(name)
        if not rec:return None
        return {
            'Name':rec['name'],'Description':rec.get('description') or rec['name'],
            'Source':'macro','HandlerTool':'', 'FixedArgs':{},
            'Sequence':[{'cmd':x['command'],'params':x.get('params') or {}} for x in rec.get('steps',[])],
            'ParamsSchema':rec.get('params_schema') or {},
            # The standalone host does not compile C#, but apply_bake deliberately
            # requires a non-empty source marker just like the upstream record.
            'SourceCode':'// CADia governed read-only macro',
        }
    def dismiss(self,id):
        s=self.data['suggestions'].get(id)
        if not s:return {'ok':False,'error':{'code':'INVALID_ARGUMENT','message':'suggestion not found'}}
        s['status']='snoozed'; s['snooze_until']=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat(); self.save(); return {'ok':True,'suggestion_id':id,'status':'snoozed_30d'}
    def get_tool(self,name):return self.data['tools'].get(name)
    def mark_used(self,name):
        if name in self.data['tools']:self.data['tools'][name]['usage_count']=int(self.data['tools'][name].get('usage_count',0))+1; self.save()

def substitute(value,params):
    if isinstance(value,str):
        m=re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',value)
        if m and m.group(1) in params:return params[m.group(1)]
        for k,v in params.items():value=value.replace('${'+k+'}',str(v))
        return value
    if isinstance(value,list):return [substitute(x,params) for x in value]
    if isinstance(value,dict):return {k:substitute(v,params) for k,v in value.items()}
    return value
