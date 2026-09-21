import json, math
from pathlib import Path

from standalonecad.core.engine import CadEngine
from standalonecad.bridge.host import CadHost
from standalonecad.mcp.server import McpServer
from standalonecad.mcp.tools import make_tools

EXPECTED = {
'inventor_list_available_targets','inventor_get_current_target','inventor_switch_target',
'inventor_health','inventor_list_open_documents','inventor_get_document_info',
'inventor_new_part','inventor_new_assembly','inventor_open_document','inventor_save_document','inventor_close_document','inventor_set_units','inventor_set_material',
'inventor_list_parameters','inventor_get_parameter','inventor_set_parameter','inventor_create_parameter',
'inventor_get_iproperty','inventor_set_iproperty','inventor_get_mass_properties',
'inventor_create_sketch','inventor_project_geometry','inventor_draw_line','inventor_draw_circle','inventor_draw_rectangle','inventor_draw_arc','inventor_add_sketch_dimension','inventor_add_sketch_constraint','inventor_close_sketch',
'inventor_extrude','inventor_revolve','inventor_fillet','inventor_chamfer','inventor_create_work_plane','inventor_create_work_axis','inventor_hole','inventor_circular_pattern','inventor_rectangular_pattern',
'inventor_capture_view','inventor_export_step','inventor_export_stl','inventor_export_dxf','inventor_view_fit','inventor_set_view_orientation',
'inventor_place_occurrence','inventor_add_constraint','inventor_create_imate',
'inventor_list_interfaces','inventor_check_interference','inventor_measure_min_distance','inventor_get_assembly_bom','inventor_list_constraints',
'inventor_list_baked_tools','inventor_list_bake_suggestions','inventor_create_bake_issue_draft','inventor_run_baked_tool','inventor_accept_bake_suggestion','inventor_dismiss_bake_suggestion'
}

def result(server,name,args=None):
    r=server.dispatch({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args or {}}})
    assert 'error' not in r, r
    out=r['result']
    assert not out.get('isError'), out
    return out['structuredContent']

def test_exact_default_surface_and_original_parameter_casing():
    tools=make_tools('inventor')
    assert len(tools)==58
    assert {t['name'] for t in tools}==EXPECTED
    assert len(make_tools('inventor',enable_code=True))==59
    assert len(make_tools('inventor',extensions=True))==108
    assert len(make_tools('inventor',read_only=True))==14
    schemas={t['name']:t['inputSchema']['properties'] for t in tools}
    assert set(schemas['inventor_extrude'])=={'sketchName','distance','operation','direction'}
    assert set(schemas['inventor_revolve'])=={'sketchName','axisId','angle','operation'}
    assert set(schemas['inventor_project_geometry'])=={'edgeIds'}
    assert set(schemas['inventor_draw_arc'])=={'cx','cy','radius','startDeg','endDeg'}
    assert set(schemas['inventor_add_sketch_dimension'])=={'entityId','value'}
    assert set(schemas['inventor_export_step'])=={'outputPath'}
    assert set(schemas['inventor_set_units'])=={'lengthUnit'}
    assert set(schemas['inventor_set_material'])=={'materialName'}
    assert set(schemas['inventor_get_iproperty'])=={'setName','propName'}


def test_all_58_tools_runtime(tmp_path):
    engine=CadEngine(); host=CadHost(engine); host.start()
    s=McpServer('inventor'); s.host.target=host.info
    called=set()
    def c(short,args=None):
        name='inventor_'+short; called.add(name); return result(s,name,args)
    try:
        # meta/query
        targets=c('list_available_targets'); assert targets['targets']
        cur=c('get_current_target'); assert cur['host_app'] in {'CADia','StandaloneCAD'}
        assert c('switch_target',{'target':host.info['target_id']})['ok']
        assert c('health')['host_app'] in {'CADia','StandaloneCAD'}
        assert c('list_open_documents')['documents']
        assert c('get_document_info')['document_type']=='part'

        # document lifecycle + metadata
        c('new_part')
        c('set_units',{'lengthUnit':'mm'})
        c('set_material',{'materialName':'Aluminum 6061'})
        c('set_iproperty',{'setName':'Design Tracking Properties','propName':'Part Number','value':'TEST-001'})
        assert c('get_iproperty',{'setName':'Design Tracking Properties','propName':'Part Number'})['value']=='TEST-001'

        # parameters
        c('create_parameter',{'name':'T','expression':'5','unit':'mm'})
        assert c('get_parameter',{'name':'T'})['value']==5
        c('set_parameter',{'name':'T','value':'6'})
        assert len(c('list_parameters')['parameters'])==1

        # sketch toolset: disposable sketch exercises every primitive/constraint/dimension
        sk1=c('create_sketch',{'plane':'XY'})['sketch_name']
        line=c('draw_line',{'x1':0,'y1':0,'x2':10,'y2':0})['entity_id']
        c('draw_circle',{'cx':20,'cy':0,'radius':2})
        c('draw_arc',{'cx':0,'cy':10,'radius':3,'startDeg':0,'endDeg':90})
        rect=c('draw_rectangle',{'x1':-5,'y1':-5,'x2':5,'y2':5})['entity_ids']
        c('add_sketch_dimension',{'entityId':line,'value':10})
        c('add_sketch_constraint',{'type':'horizontal','entityIds':[line]})
        c('close_sketch',{'sketchName':sk1})

        # clean profile + extrude
        sk2=c('create_sketch',{'plane':'XY'})['sketch_name']
        c('draw_rectangle',{'x1':-30,'y1':-20,'x2':30,'y2':20})
        c('close_sketch',{'sketchName':sk2})
        ext=c('extrude',{'sketchName':sk2,'distance':6,'operation':'join','direction':'positive'})['feature_name']
        assert abs(engine.doc.shape.Volume()-60*40*6)<1e-5

        # project geometry onto actual planar face reference
        topo=engine.doc.topology(); top=next(f for f in topo['faces'] if f.get('semantic')=='top_face'); edge=topo['edges'][0]
        sk3=c('create_sketch',{'plane':top['id']})['sketch_name']
        assert c('project_geometry',{'edgeIds':[edge['id']]})['projected_count']==1
        c('close_sketch',{'sketchName':sk3})

        # work features
        c('create_work_plane',{'type':'offset','refs':['XY Plane'],'offset':12})
        c('create_work_axis',{'type':'plane_intersection','refs':['XY Plane','YZ Plane']})

        # holes + both patterns (subtractive source)
        hole=c('hole',{'face':{'kind':'planar','normal':'+Z','extreme':'max'},'points_mm':[[15,0,6]],'diameter_mm':4,'kind':'drilled','through':True})['feature_names'][0]
        c('circular_pattern',{'feature_names':[hole],'axis':'Z Axis','count':4,'angle_deg':360,'natural_direction':True})
        # fresh part for rectangular pattern of a join feature
        c('new_part')
        psk=c('create_sketch',{'plane':'XY'})['sketch_name']; c('draw_circle',{'cx':0,'cy':0,'radius':1}); c('close_sketch',{'sketchName':psk})
        pf=c('extrude',{'sketchName':psk,'distance':2})['feature_name']
        c('rectangular_pattern',{'feature_names':[pf],'dir1':'X Axis','count1':3,'spacing_mm1':5,'dir2':'Y Axis','count2':2,'spacing_mm2':5,'natural_direction1':True,'natural_direction2':True})

        # revolve on a fresh part
        c('new_part'); rsk=c('create_sketch',{'plane':'XY'})['sketch_name']; c('draw_rectangle',{'x1':5,'y1':-2,'x2':10,'y2':2}); c('close_sketch',{'sketchName':rsk})
        c('revolve',{'sketchName':rsk,'axisId':'YAxis','angle':360,'operation':'join'})

        # fillet/chamfer are each exercised on clean boxes so references are current
        c('new_part'); bsk=c('create_sketch',{'plane':'XY'})['sketch_name']; c('draw_rectangle',{'x1':0,'y1':0,'x2':20,'y2':10}); c('close_sketch',{'sketchName':bsk}); c('extrude',{'sketchName':bsk,'distance':5})
        eid=engine.doc.topology()['edges'][0]['id']; c('fillet',{'edgeIds':[eid],'radius':1})
        c('new_part'); bsk=c('create_sketch',{'plane':'XY'})['sketch_name']; c('draw_rectangle',{'x1':0,'y1':0,'x2':20,'y2':10}); c('close_sketch',{'sketchName':bsk}); c('extrude',{'sketchName':bsk,'distance':5})
        eid=engine.doc.topology()['edges'][0]['id']; c('chamfer',{'edgeIds':[eid],'distance':1})

        # view/export + mass props
        assert c('get_mass_properties')['volume_mm3']>0
        c('view_fit'); c('set_view_orientation',{'orientation':'iso_top_right','fit':True})
        png=tmp_path/'view.png'; step=tmp_path/'part.step'; stl=tmp_path/'part.stl'; dxf=tmp_path/'sketch.dxf'
        c('capture_view',{'width':96,'height':72,'outputPath':str(png)})
        c('export_step',{'outputPath':str(step)}); c('export_stl',{'outputPath':str(stl)}); c('export_dxf',{'outputPath':str(dxf),'source':'sketch','sketchName':bsk})
        assert all(p.exists() and p.stat().st_size>100 for p in [png,step,stl,dxf])

        # save/open/close. Save a reusable component before assembly.
        part=tmp_path/'component.scad.json'; c('save_document',{'path':str(part)}); c('close_document',{'save':False}); c('open_document',{'path':str(part)})
        # author iMate on active part and save it
        c('create_imate',{'name':'IF_TOP','type':'mate','selector':{'kind':'planar','normal':'+Z','extreme':'max'},'offset_mm':0,'insert_opposed':True,'distance_mm':0})
        c('save_document',{'path':str(part)})

        # assembly + all assembly query tools
        c('new_assembly')
        a=c('place_occurrence',{'path':str(part),'grounded':True})['occurrence_name']
        b=c('place_occurrence',{'path':str(part),'grounded':False,'position_mm':[30,0,0],'rotation_deg_xyz':[0,0,0]})['occurrence_name']
        assert c('list_interfaces',{'occurrence':a})['imates']
        assert c('check_interference',{'occurrences':[a,b]})['count']==0
        assert c('measure_min_distance',{'a':{'occurrence':a},'b':{'occurrence':b}})['distance_mm']>=0
        con=c('add_constraint',{'type':'flush','a':{'occurrence':None,'ref':'YZ Plane'},'b':{'occurrence':b,'ref':'YZ Plane'},'offset_mm':30,'insert_opposed':True})
        assert con['health'] in ('up_to_date','sick')
        assert c('list_constraints')['constraints']
        assert len(c('get_assembly_bom',{'max_rows':500})['occurrences'])==2

        # ToolBaker: seed deterministic suggestions, then exercise lifecycle and execution.
        s.bake.data={'tools':{},'patterns':{},'suggestions':{
            'sug_accept':{'id':'sug_accept','title':'inspect','description':'read document info','source':'test','score':1.0,'payload_json':json.dumps({'steps':[{'command':'get_document_info','params':{}}]}),'status':'active'},
            'sug_dismiss':{'id':'sug_dismiss','title':'dismiss','description':'dismiss me','source':'test','score':0.5,'payload_json':json.dumps({'steps':[]}),'status':'active'}
        }}; s.bake.save()
        assert c('list_baked_tools')['tools']==[]
        assert len(c('list_bake_suggestions')['suggestions'])>=2
        assert c('create_bake_issue_draft',{'id':'sug_accept'})['ok']
        assert c('accept_bake_suggestion',{'suggestionId':'sug_accept','desiredName':'inspect_doc'})['ok']
        assert c('run_baked_tool',{'name':'inspect_doc','paramsJson':'{}'})['ok']
        assert c('dismiss_bake_suggestion',{'suggestionId':'sug_dismiss'})['ok']

        assert called==EXPECTED, f'missing={sorted(EXPECTED-called)} extra={sorted(called-EXPECTED)}'
    finally:
        host.close()

def test_read_only_and_send_code_modes(tmp_path):
    # read-only mirrors ipt-mcp gating: 14 read/server-side tools, no mutators.
    ro=McpServer('inventor',read_only=True)
    assert len(ro.tools)==14
    assert 'inventor_new_part' not in ro.tool_names
    assert 'inventor_check_interference' in ro.tool_names

    # send_code is opt-in and never silently executes arbitrary Python/C#/OS code in standalone mode.
    engine=CadEngine(); host=CadHost(engine); host.start()
    try:
        s=McpServer('inventor',enable_code=True); s.host.target=host.info
        assert len(s.tools)==59 and 'inventor_send_code' in s.tool_names
        ok=result(s,'inventor_send_code',{'code':json.dumps({'commands':[{'command':'new_part','params':{}},{'command':'set_units','params':{'length_unit':'mm'}}]})})
        assert ok['ok'] and len(ok['results'])==2
        bad=s.dispatch({'jsonrpc':'2.0','id':9,'method':'tools/call','params':{'name':'inventor_send_code','arguments':{'code':'System.IO.File.Delete("x")'}}})['result']
        assert bad['isError']
    finally:
        host.close()
