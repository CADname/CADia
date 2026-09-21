from __future__ import annotations
import sys,json,argparse,os
from standalonecad import __version__
from standalonecad.bridge.client import HostClient
from .tools import make_tools,DEFAULT_TOOLSETS
from .bake import BakeStore
MODERN='2026-07-28'; LEGACY='2025-11-25'

_CANONICAL_CODES={'NO_TARGET','TARGET_UNAVAILABLE','NO_DOCUMENT','WRONG_DOCUMENT_TYPE','INVALID_ARGUMENT','UNSUPPORTED_HOST','API_ERROR','TIMEOUT','RESPONSE_TOO_LARGE','READ_ONLY','SEND_CODE_DISABLED','UNAUTHORIZED'}
def _tool_error_code(exc):
    msg=str(exc)
    prefix=msg.split(':',1)[0].strip().upper()
    if prefix in _CANONICAL_CODES:return prefix
    if isinstance(exc,PermissionError):return 'READ_ONLY' if 'READ_ONLY' in msg.upper() else 'UNAUTHORIZED'
    if isinstance(exc,(ValueError,KeyError,TypeError,FileNotFoundError,json.JSONDecodeError)):return 'INVALID_ARGUMENT'
    return type(exc).__name__.upper()

class McpServer:
    def __init__(self,prefix='inventor',toolsets=None,read_only=False,enable_code=False,extensions=False,target_id=None,target_info=None,assembly_extensions=False):
        self.prefix=prefix;self.read_only=read_only;self.enable_code=enable_code;self.extensions=extensions;self.assembly_extensions=assembly_extensions;self.toolsets=toolsets or DEFAULT_TOOLSETS
        raw=make_tools(prefix,self.toolsets,read_only,enable_code,extensions,assembly_extensions);self.tools=[{k:v for k,v in t.items() if k!='_toolset'} for t in raw];self.tool_names={t['name'] for t in self.tools};self.host=HostClient(target_id,target_info=target_info);self.bake=BakeStore();self.target_id=target_id
    def ok(self,id,result):return {'jsonrpc':'2.0','id':id,'result':result}
    def err(self,id,code,msg,data=None):
        e={'code':code,'message':msg}
        if data is not None:e['data']=data
        return {'jsonrpc':'2.0','id':id,'error':e}
    def _to_wire(self,cmd,args):
        # Match the original ipt-mcp MCP-facing method parameter names, then translate to its snake_case wire envelope.
        p=dict(args)
        if cmd=='set_units':p={'length_unit':args.get('lengthUnit',args.get('length_unit'))}
        elif cmd=='set_material':p={'material_name':args.get('materialName',args.get('material_name'))}
        elif cmd in ('get_iproperty','set_iproperty'):
            p={'set_name':args.get('setName',args.get('set_name')),'prop_name':args.get('propName',args.get('prop_name'))}
            if cmd=='set_iproperty':p['value']=args['value']
        elif cmd=='project_geometry':p={'edge_ids':args.get('edgeIds',args.get('edge_ids'))}
        elif cmd=='draw_arc':p={'cx':args['cx'],'cy':args['cy'],'radius':args['radius'],'start_deg':args.get('startDeg',args.get('start_deg')),'end_deg':args.get('endDeg',args.get('end_deg'))}
        elif cmd=='add_sketch_dimension':p={'entity_id':args.get('entityId',args.get('entity_id')),'value_mm':args.get('value',args.get('value_mm'))}
        elif cmd=='add_sketch_constraint':p={'type':args['type'],'entity_ids':args.get('entityIds',args.get('entity_ids'))}
        elif cmd=='close_sketch':p={'sketch_name':args.get('sketchName',args.get('sketch_name'))}
        elif cmd=='extrude':p={'sketch_name':args.get('sketchName',args.get('sketch_name')),'distance_mm':args['distance'],'operation':args.get('operation','join'),'direction':args.get('direction','positive')}
        elif cmd=='revolve':p={'sketch_name':args.get('sketchName',args.get('sketch_name')),'axis_id':args.get('axisId',args.get('axis_id')),'angle_deg':args['angle'],'operation':args.get('operation','join')}
        elif cmd=='fillet':p={'edge_ids':args.get('edgeIds',args.get('edge_ids')),'radius_mm':args['radius']}
        elif cmd=='chamfer':p={'edge_ids':args.get('edgeIds',args.get('edge_ids')),'distance_mm':args['distance']}
        elif cmd=='create_work_plane':p={'type':args['type'],'refs':args['refs'],'offset_mm':args.get('offset')}
        elif cmd=='create_work_axis':p={'type':args['type'],'refs':args['refs']}
        elif cmd=='hole':
            p={k:v for k,v in args.items() if k!='tapped'}
            tap=args.get('tapped')
            if tap:
                p.update({'tapped_designation':tap.get('designation'),'tapped_class':tap.get('class','6H'),'tapped_right_handed':tap.get('right_handed',True),'tapped_full_depth':tap.get('full_depth',True),'tapped_thread_depth_mm':tap.get('thread_depth_mm')})
        elif cmd=='capture_view':p={'width':args.get('width',1280),'height':args.get('height',720),'output_path':args.get('outputPath',args.get('output_path'))}
        elif cmd in ('export_step','export_stl'):p={'output_path':args.get('outputPath',args.get('output_path'))}
        elif cmd=='export_dxf':p={'output_path':args.get('outputPath',args.get('output_path')),'source':args['source'],'sketch_name':args.get('sketchName',args.get('sketch_name'))}
        elif cmd=='add_constraint':
            a=args['a'];b=args['b'];p={'type':args['type'],'a_occurrence':a.get('occurrence'),'a_ref':a['ref'],'b_occurrence':b.get('occurrence'),'b_ref':b['ref'],'offset_mm':args.get('offset_mm',0),'angle_deg':args.get('angle_deg'),'insert_opposed':args.get('insert_opposed',True)}
        elif cmd=='measure_min_distance':
            a=args['a'];b=args['b'];p={'a_occurrence':a['occurrence'],'a_ref':a.get('ref'),'b_occurrence':b['occurrence'],'b_ref':b.get('ref')}
        elif cmd=='add_joint':
            a=args['a']; b=args['b']; p={
                'type':args['type'],'a_occurrence':a.get('occurrence'),'a_ref':a.get('ref'),
                'b_occurrence':b.get('occurrence'),'b_ref':b.get('ref'),'name':args.get('name'),
                'a_intent':a.get('intent'),'b_intent':b.get('intent'),
                'linear_position_mm':args.get('linear_position_mm'),'linear_start_mm':args.get('linear_start_mm'),'linear_end_mm':args.get('linear_end_mm'),
                'angular_position_deg':args.get('angular_position_deg'),'angular_start_deg':args.get('angular_start_deg'),'angular_end_deg':args.get('angular_end_deg'),
                'flip_origin_direction':args.get('flip_origin_direction',False),'flip_alignment_direction':args.get('flip_alignment_direction',False),
            }
        return p
    def _target_public(self,t):return self.host.public(t) if t else None
    def _call_baked(self,name,params):
        rec=self.bake.get_tool(name)
        if not rec:raise ValueError('baked tool not found: '+name)
        wire=self.bake.wire_record(name)
        # Match upstream topology: run_baked_tool round-trips to the add-in/host,
        # whose BakedToolDispatchAuthorizer enforces the read-only allow-list.
        result=self.host.call('run_baked_tool',{'name':name,'tool_record':wire,'params':params},self.read_only)
        self.bake.mark_used(name)
        return result
    def _accept_baked(self,suggestion_id,desired_name):
        rec=self.bake.prepare_accept(suggestion_id,desired_name)
        payload={
            'tool_name':rec['name'],'description':rec.get('description') or rec['name'],
            'source':'macro','handler_tool':'','fixed_args':{},
            'sequence':[{'cmd':x['command'],'params':x.get('params') or {}} for x in rec['steps']],
            'source_code':'// CADia governed read-only macro',
            'params_schema':rec.get('params_schema') or {},
        }
        applied=self.host.call('apply_bake',payload,self.read_only)
        if not isinstance(applied,dict) or not applied.get('success'):
            msg=(applied or {}).get('message') if isinstance(applied,dict) else None
            raise ValueError(msg or 'apply_bake failed')
        return self.bake.commit_accept(rec)
    def _restricted_send_code(self,code):
        if not self.enable_code:raise PermissionError('SEND_CODE_DISABLED')
        # Standalone equivalent deliberately accepts only JSON command sequences; no eval/exec, filesystem, process, or network APIs.
        try:payload=json.loads(code)
        except Exception as e:raise ValueError('Standalone send_code expects JSON: {"commands":[{"command":"...","params":{...}}]}') from e
        cmds=payload.get('commands') if isinstance(payload,dict) else None
        if not isinstance(cmds,list) or not cmds:raise ValueError('commands array is required')
        banned={'send_code','run_baked_tool','accept_bake_suggestion','dismiss_bake_suggestion'};results=[]
        for c in cmds:
            cmd=str(c.get('command',''))
            if cmd in banned or cmd.startswith('_'):raise PermissionError('Command not allowed in restricted send_code: '+cmd)
            results.append({'command':cmd,'result':self.host.call(cmd,c.get('params') or {},False)})
        return {'ok':True,'results':results}
    def dispatch(self,r):
        rid=r.get('id');m=r.get('method');p=r.get('params') or {}
        if m=='server/discover':return self.ok(rid,{'resultType':'complete','supportedVersions':[MODERN,LEGACY],'capabilities':{'tools':{}},'serverInfo':{'name':'CADia-MCP','version':__version__},'instructions':'CADia local Open CASCADE host. The inventor_* tool names and schemas mirror the public ipt-mcp v0.1.0 58-tool compatibility surface.'})
        if m=='initialize':
            pv=p.get('protocolVersion',LEGACY);return self.ok(rid,{'protocolVersion':pv,'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'CADia-MCP','version':__version__},'instructions':'CADia is the active target. Use the provided CAD tools only for this document.'})
        if m in ('notifications/initialized','notifications/cancelled'):return None
        if m=='ping':return self.ok(rid,{})
        if m=='tools/list':return self.ok(rid,{'resultType':'complete','tools':self.tools})
        if m=='tools/call':
            name=p.get('name','');args=p.get('arguments') or {}
            if name not in self.tool_names:return self.err(rid,-32602,'Unknown tool: '+name)
            cmd=name[len(self.prefix)+1:]
            try:
                if cmd=='list_available_targets':
                    targets=self.host.targets()
                    if self.target_id:
                        targets=[t for t in targets if str(t.get('target_id'))==str(self.target_id)]
                    result={'targets':targets}
                elif cmd=='get_current_target':
                    if self.host.target is None:self.host.select()
                    if self.host.target is None:result={'ok':False,'error':{'code':'NO_TARGET','message':'no live target'}}
                    else:result=self._target_public(self.host.target)
                elif cmd=='switch_target':
                    requested=str(args['target'])
                    if self.target_id and requested!=str(self.target_id):
                        raise PermissionError('TARGET_PINNED: embedded CADia MCP is pinned to '+str(self.target_id))
                    result={'ok':bool(self.host.select(requested)),'target':requested}
                elif cmd=='list_baked_tools':result=self.bake.list_tools()
                elif cmd=='list_bake_suggestions':result=self.bake.list_suggestions()
                elif cmd=='create_bake_issue_draft':result=self.bake.issue_draft(args['id'])
                elif cmd=='accept_bake_suggestion':result=self._accept_baked(args['suggestionId'],args['desiredName'])
                elif cmd=='dismiss_bake_suggestion':result=self.bake.dismiss(args['suggestionId'])
                elif cmd=='run_baked_tool':
                    try:bp=json.loads(args.get('paramsJson') or '{}')
                    except Exception as e:raise ValueError('paramsJson must be a JSON object') from e
                    if not isinstance(bp,dict):raise ValueError('paramsJson must be a JSON object')
                    result=self._call_baked(args['name'],bp)
                elif cmd=='send_code':result=self._restricted_send_code(args['code'])
                else:
                    wire=self._to_wire(cmd,args);result=self.host.call(cmd,wire,self.read_only);self.bake.observe(cmd,wire)
                txt=json.dumps(result,ensure_ascii=False,separators=(',',':'))
                return self.ok(rid,{'resultType':'complete','content':[{'type':'text','text':txt}],'structuredContent':result})
            except Exception as e:
                result={'ok':False,'error':{'code':_tool_error_code(e),'message':str(e)}};return self.ok(rid,{'resultType':'complete','content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}],'structuredContent':result,'isError':True})
        return self.err(rid,-32601,'Method not found: '+str(m))
    def run(self):
        print(f'CADia-MCP {__version__} stdio server ready ({len(self.tools)} tools)',file=sys.stderr,flush=True)
        for line in sys.stdin:
            if not line.strip():continue
            try:r=json.loads(line);res=self.dispatch(r)
            except Exception as e:res=self.err(None,-32603,str(e))
            if res is not None:print(json.dumps(res,separators=(',',':')),flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prefix',choices=['inventor','cad'],default='inventor');ap.add_argument('--toolsets',default='all');ap.add_argument('--read-only',action='store_true');ap.add_argument('--enable-send-code',action='store_true');ap.add_argument('--extensions',action='store_true');ap.add_argument('--assembly-extensions',action='store_true',help='Expose CADia Inventor-style assembly joint extensions in addition to the unchanged upstream-compatible surface');ap.add_argument('--target',default=None,help='Pin this MCP server to one CADia target id');a=ap.parse_args();sets=['all'] if a.toolsets.strip().lower()=='all' else [x.strip() for x in a.toolsets.split(',') if x.strip()];McpServer(a.prefix,sets,a.read_only,a.enable_send_code,a.extensions,a.target,assembly_extensions=a.assembly_extensions).run()
if __name__=='__main__':main()
