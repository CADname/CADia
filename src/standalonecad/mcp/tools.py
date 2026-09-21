from __future__ import annotations

def obj(props=None,required=None):
    d={'type':'object','properties':props or {},'additionalProperties':False}
    if required:d['required']=required
    return d

def s(desc='',enum=None):
    d={'type':'string'}
    if desc:d['description']=desc
    if enum:d['enum']=enum
    return d

def n(desc='',minimum=None,maximum=None):
    d={'type':'number'}
    if desc:d['description']=desc
    if minimum is not None:d['minimum']=minimum
    if maximum is not None:d['maximum']=maximum
    return d

def i(desc='',minimum=None):
    d={'type':'integer'}
    if desc:d['description']=desc
    if minimum is not None:d['minimum']=minimum
    return d

def b(desc=''):
    d={'type':'boolean'}
    if desc:d['description']=desc
    return d

def arr(item,desc=''):
    d={'type':'array','items':item}
    if desc:d['description']=desc
    return d

def nullable(schema):return {'anyOf':[schema,{'type':'null'}]}

FACE=obj({'kind':s(enum=['planar']),'normal':s(enum=['+X','-X','+Y','-Y','+Z','-Z']),'extreme':s(enum=['max','min']),'near_mm':arr(n())},['kind','normal'])
FACE_SELECTOR=obj({'kind':s(enum=['planar','cylindrical']),'normal':s(enum=['+X','-X','+Y','-Y','+Z','-Z']),'extreme':s(enum=['max','min']),'radius_mm':n(),'axis':s(enum=['+X','-X','+Y','-Y','+Z','-Z']),'near_mm':arr(n()),'tolerance_deg':n(),'radius_tol_mm':n()},['kind'])
CONSTRAINT_SIDE=obj({'occurrence':nullable(s()),'ref':s()},['ref'])
JOINT_INTENT=obj({
    'kind':s(enum=['ref','face','edge']),
    'ref':nullable(s()),
    'selector':nullable(FACE_SELECTOR),
    'edge_id':nullable(s()),
    'point':nullable(s(enum=['start','mid','end'])),
},['kind'])
JOINT_SIDE=obj({'occurrence':nullable(s()),'ref':nullable(s()),'intent':nullable(JOINT_INTENT)})
MEASURE_SIDE=obj({'occurrence':s(),'ref':nullable(s())},['occurrence'])
TAPPED=obj({'designation':s(),'class':s(),'right_handed':b(),'full_depth':b(),'thread_depth_mm':nullable(n())},['designation'])

TOOLSETS={
'meta':[
('list_available_targets','List detected live CAD host targets.',obj()),
('get_current_target','Report the selected CAD target or NO_TARGET.',obj()),
('switch_target','Select the active target by descriptor id/session.',obj({'target':s()},['target'])),
],
'query':[
('health','Probe the active CAD target and current document.',obj()),
('list_open_documents','List open documents and which is active.',obj()),
('get_document_info','Get active document title, path and type.',obj()),
],
'document':[
('new_part','Create a new part document. template is accepted for ipt-mcp compatibility.',obj({'template':nullable(s())})),
('new_assembly','Create a new assembly document. template is accepted for ipt-mcp compatibility.',obj({'template':nullable(s())})),
('open_document','Open an editable CADia document or supported B-Rep file.',obj({'path':s()},['path'])),
('save_document','Save active editable document; omit path to save in place.',obj({'path':nullable(s())})),
('close_document','Close active document; save=true saves first.',obj({'save':b()})),
('set_units','Set active document length unit.',obj({'lengthUnit':s(enum=['mm','cm','m','in','ft'])},['lengthUnit'])),
('set_material','Assign material metadata by name.',obj({'materialName':s()},['materialName'])),
],
'parameters':[
('list_parameters','List model and user parameters.',obj()),
('get_parameter','Get one parameter by name.',obj({'name':s()},['name'])),
('set_parameter','Set an existing parameter expression/value.',obj({'name':s(),'value':s()},['name','value'])),
('create_parameter','Create a user parameter.',obj({'name':s(),'expression':s(),'unit':s()},['name','expression','unit'])),
],
'properties':[
('get_iproperty','Get an iProperty-compatible document property.',obj({'setName':s(),'propName':s()},['setName','propName'])),
('set_iproperty','Set an iProperty-compatible document property.',obj({'setName':s(),'propName':s(),'value':s()},['setName','propName','value'])),
('get_mass_properties','Get mass, volume, area, centre and bounding box.',obj()),
],
'sketch':[
('create_sketch','Create a new 2D sketch on XY/XZ/YZ (Inventor base planes) or an exact work-plane/planar-face reference returned by the current model. Do not invent origin reference names.',obj({'plane':s()})),
('project_geometry','Project model edges into the active sketch.',obj({'edgeIds':arr(s())},['edgeIds'])),
('draw_line','Draw line in active sketch.',obj({'x1':n(),'y1':n(),'x2':n(),'y2':n()},['x1','y1','x2','y2'])),
('draw_circle','Draw circle in active sketch.',obj({'cx':n(),'cy':n(),'radius':n()},['cx','cy','radius'])),
('draw_rectangle','Draw two-point rectangle and return four line ids.',obj({'x1':n(),'y1':n(),'x2':n(),'y2':n()},['x1','y1','x2','y2'])),
('draw_arc','Draw center-radius arc CCW.',obj({'cx':n(),'cy':n(),'radius':n(),'startDeg':n(),'endDeg':n()},['cx','cy','radius','startDeg','endDeg'])),
('add_sketch_dimension','Add a driving dimension to a sketch entity.',obj({'entityId':s(),'value':n()},['entityId','value'])),
('add_sketch_constraint','Add geometric constraint over entity ids.',obj({'type':s(enum=['coincident','parallel','perpendicular','horizontal','vertical','tangent','concentric','equal','collinear','symmetric']),'entityIds':arr(s())},['type','entityIds'])),
('close_sketch','Finish sketch editing.',obj({'sketchName':nullable(s())})),
],
'feature':[
('extrude','Extrude named sketch; distance mm; join/cut/intersect; positive/negative/symmetric.',obj({'sketchName':s(),'distance':n(),'operation':s(enum=['join','cut','intersect']),'direction':s(enum=['positive','negative','symmetric'])},['sketchName','distance'])),
('revolve','Revolve named sketch about sketch-line id or origin axis.',obj({'sketchName':s(),'axisId':s(),'angle':n(),'operation':s(enum=['join','cut','intersect'])},['sketchName','axisId','angle'])),
('fillet','Add constant-radius edge fillet.',obj({'edgeIds':arr(s()),'radius':n()},['edgeIds','radius'])),
('chamfer','Add equal-distance edge chamfer.',obj({'edgeIds':arr(s()),'distance':n()},['edgeIds','distance'])),
('create_work_plane','Create offset/three_points/tangent work plane.',obj({'type':s(enum=['offset','three_points','tangent']),'refs':arr(s()),'offset':nullable(n())},['type','refs'])),
('create_work_axis','Create two_points/edge/plane_intersection/normal_to_face_through_point work axis.',obj({'type':s(enum=['two_points','edge','plane_intersection','normal_to_face_through_point']),'refs':arr(s())},['type','refs'])),
('hole','Create drilled/counterbore/countersink holes using deterministic face selector and 3D points.',obj({'face':FACE,'points_mm':arr(arr(n())),'diameter_mm':n(),'kind':s(enum=['drilled','counterbore','countersink']),'through':b(),'depth_mm':nullable(n()),'cbore_diameter_mm':nullable(n()),'cbore_depth_mm':nullable(n()),'csink_diameter_mm':nullable(n()),'csink_angle_deg':n(),'tapped':nullable(TAPPED)},['face','points_mm','diameter_mm'])),
('circular_pattern','Circular pattern one or more feature names around axis.',obj({'feature_names':arr(s()),'axis':s(),'count':i(minimum=2),'angle_deg':n(),'natural_direction':b()},['feature_names','axis','count'])),
('rectangular_pattern','Rectangular pattern one or more feature names along one or two axes.',obj({'feature_names':arr(s()),'dir1':s(),'count1':i(minimum=1),'spacing_mm1':n(),'dir2':nullable(s()),'count2':nullable(i(minimum=1)),'spacing_mm2':nullable(n()),'natural_direction1':b(),'natural_direction2':b()},['feature_names','dir1','count1','spacing_mm1'])),
],
'export':[
('capture_view','Capture current CAD view as inline base64 PNG or output file.',obj({'width':i(minimum=16),'height':i(minimum=16),'outputPath':nullable(s())})),
('export_step','Export active part/assembly to STEP.',obj({'outputPath':s()},['outputPath'])),
('export_stl','Export active part/assembly to STL.',obj({'outputPath':s()},['outputPath'])),
('export_dxf','Export 2D DXF from sketch or flat_pattern source.',obj({'outputPath':s(),'source':s(enum=['sketch','flat_pattern']),'sketchName':nullable(s())},['outputPath','source'])),
('view_fit','Fit view to model extents.',obj()),
('set_view_orientation','Set standard camera orientation.',obj({'orientation':s(enum=['iso_top_right','iso_top_left','iso_bottom_right','iso_bottom_left','front','back','top','bottom','left','right']),'fit':b()},['orientation'])),
],
'assembly':[
('place_occurrence','Place a component in active assembly with optional initial pose.',obj({'path':s(),'grounded':b(),'position_mm':nullable(arr(n())),'rotation_deg_xyz':nullable(arr(n()))},['path'])),
('add_constraint','Add mate/flush/insert/angle relationship between named refs.',obj({'type':s(enum=['mate','flush','insert','angle']),'a':CONSTRAINT_SIDE,'b':CONSTRAINT_SIDE,'offset_mm':n(),'angle_deg':nullable(n()),'insert_opposed':b()},['type','a','b'])),
('create_imate','Create named iMate on active part using deterministic face selector.',obj({'name':s(),'type':s(enum=['mate','flush','insert']),'selector':FACE_SELECTOR,'offset_mm':n(),'insert_opposed':b(),'distance_mm':n()},['name','type','selector'])),
],
'assembly_query':[
('list_interfaces','List named iMates, work features and origin geometry.',obj({'occurrence':nullable(s())})),
('check_interference','Run interference analysis over all or selected top-level occurrences.',obj({'occurrences':nullable(arr(s()))})),
('measure_min_distance','Minimum 3D distance between occurrences or named refs.',obj({'a':MEASURE_SIDE,'b':MEASURE_SIDE},['a','b'])),
('get_assembly_bom','Get occurrence tree and grouped BOM with solved constraint-graph DOF.',obj({'max_rows':i(minimum=1)})),
('list_constraints','Read back assembly relationship graph and health.',obj()),
],
'toolbaker':[
('list_baked_tools','List verified registered baked tools.',obj()),
('list_bake_suggestions','List active recurrent-workflow suggestions.',obj()),
('create_bake_issue_draft','Create an issue-style draft for a suggestion without submitting.',obj({'id':s()},['id'])),
],
'toolbaker_write':[
('run_baked_tool','Execute a registered baked tool with JSON parameters.',obj({'name':s(),'paramsJson':s()},['name','paramsJson'])),
('accept_bake_suggestion','Accept suggestion and register it as a baked tool.',obj({'suggestionId':s(),'desiredName':s()},['suggestionId','desiredName'])),
('dismiss_bake_suggestion','Snooze/dismiss a suggestion.',obj({'suggestionId':s()},['suggestionId'])),
],
'code':[
('send_code','Dangerous opt-in escape hatch. In standalone mode accepts a restricted command-sequence JSON DSL, not arbitrary OS code.',obj({'code':s()},['code'])),
],
}

EXTENSIONS=[
('get_topology','Standalone extension: list stable-ish face/edge/vertex references.',obj()),
('get_selection','Standalone extension: get the currently selected face/edge/vertex.',obj()),
('undo','Undo the last mutating CAD command.',obj()),
('redo','Redo the last undone CAD command.',obj()),
('create_spur_gear','Create a deterministic involute external spur gear.',obj({
    'module':n(minimum=0.01),'teeth':i(minimum=6),'thickness_mm':n(minimum=0.01),
    'bore_diameter_mm':n(minimum=0),'pressure_angle_deg':n(minimum=5,maximum=35),
    'backlash_mm':n(minimum=0),'replace':b(),'name':nullable(s()),
},['module','teeth','thickness_mm'])),
('create_gear','Design-Accelerator gear family. Spur stays on the native stable generator; helical/herringbone/ring/bevel/rack/worm-screw/planetary use the pinned Apache-2.0 cq_gears backend. kind=worm means a worm screw; kind=worm_pair creates a native worm + wheel pair. Use only for an explicit matching mechanical-component request.',obj({
    'kind':s(enum=['spur','helical','herringbone','ring','bevel','rack','worm','worm_pair','planetary']),
    'module':n(minimum=0.01),'teeth':nullable(i(minimum=6)),'width_mm':nullable(n(minimum=0.01)),
    'bore_diameter_mm':n(minimum=0),'pressure_angle_deg':n(minimum=5,maximum=35),'helix_angle_deg':n(minimum=-55,maximum=55),
    'backlash_mm':n(minimum=0),'clearance_mm':n(minimum=0),'rim_width_mm':nullable(n(minimum=0.01)),
    'cone_angle_deg':nullable(n(minimum=0.01,maximum=89.99)),'length_mm':nullable(n(minimum=0.01)),'height_mm':nullable(n(minimum=0.01)),
    'lead_angle_deg':nullable(n(minimum=-79.99,maximum=79.99)),'thread_starts':nullable(i(minimum=1)),
    'sun_teeth':nullable(i(minimum=6)),'planet_teeth':nullable(i(minimum=6)),'planet_count':nullable(i(minimum=2)),
    'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['kind','module'])),
('create_shaft','Design-Accelerator stepped shaft along +Z. Sections are ordered diameter/length steps; optional bore and rectangular axial keyway are rebuilt parametrically.',obj({
    'sections':arr(obj({'length_mm':n(minimum=0.001),'diameter_mm':n(minimum=0.001)},['length_mm','diameter_mm'])),
    'bore_diameter_mm':n(minimum=0),'keyway_width_mm':nullable(n(minimum=0.001)),'keyway_depth_mm':nullable(n(minimum=0.001)),
    'keyway_length_mm':nullable(n(minimum=0.001)),'keyway_start_mm':n(minimum=0),
    'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['sections'])),
('create_parallel_key','Create a deterministic parallel key as a parametric mechanical component.',obj({
    'width_mm':n(minimum=0.001),'height_mm':n(minimum=0.001),'length_mm':n(minimum=0.001),'end_style':s(enum=['square','round']),
    'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['width_mm','height_mm','length_mm'])),
('create_bearing','Create a deterministic bearing layout model with exact requested bore/outside/width envelope and simplified internal rolling elements.',obj({
    'kind':s(enum=['deep_groove_ball','cylindrical_roller','envelope']),'bore_diameter_mm':n(minimum=0.001),'outer_diameter_mm':n(minimum=0.001),'width_mm':n(minimum=0.001),
    'rolling_elements':i(minimum=3),'detailed':b(),'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['kind','bore_diameter_mm','outer_diameter_mm','width_mm'])),
('create_spring','Design-Accelerator spring family. compression uses wire/mean diameter/free length/turns; belleville uses OD/ID/thickness/free height.',obj({
    'kind':s(enum=['compression','belleville']),'wire_diameter_mm':nullable(n(minimum=0.001)),'mean_diameter_mm':nullable(n(minimum=0.001)),
    'free_length_mm':nullable(n(minimum=0.001)),'active_turns':nullable(n(minimum=0.01)),'right_handed':b(),
    'outer_diameter_mm':nullable(n(minimum=0.001)),'inner_diameter_mm':nullable(n(minimum=0.001)),'thickness_mm':nullable(n(minimum=0.001)),'free_height_mm':nullable(n(minimum=0.001)),
    'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['kind'])),
('create_v_pulley','Create a parametric V-pulley with real revolved V grooves. Geometry is explicit; no belt-section standard is guessed.',obj({
    'pitch_diameter_mm':n(minimum=0.001),'width_mm':n(minimum=0.001),'groove_count':i(minimum=1),'groove_angle_deg':n(minimum=20,maximum=80),
    'groove_depth_mm':nullable(n(minimum=0.001)),'groove_pitch_mm':nullable(n(minimum=0.001)),'bore_diameter_mm':n(minimum=0),
    'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['pitch_diameter_mm','width_mm'])),
('create_coupling','Create a deterministic rigid flange coupling with two mating halves, bore, bolt circle and optional keyways.',obj({
    'bore_diameter_mm':n(minimum=0.001),'hub_diameter_mm':n(minimum=0.001),'flange_diameter_mm':n(minimum=0.001),
    'hub_length_mm':n(minimum=0.001),'flange_thickness_mm':n(minimum=0.001),'bolt_circle_diameter_mm':nullable(n(minimum=0.001)),
    'bolt_count':i(minimum=0),'bolt_hole_diameter_mm':nullable(n(minimum=0.001)),'keyway_width_mm':nullable(n(minimum=0.001)),'keyway_depth_mm':nullable(n(minimum=0.001)),
    'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['bore_diameter_mm','hub_diameter_mm','flange_diameter_mm','hub_length_mm','flange_thickness_mm'])),
('create_lead_screw_nut','Create a deterministic trapezoidal lead screw and matching nut compound with explicit pitch, starts and thread angle.',obj({
    'major_diameter_mm':n(minimum=0.001),'pitch_mm':n(minimum=0.001),'screw_length_mm':n(minimum=0.001),'nut_length_mm':n(minimum=0.001),
    'starts':i(minimum=1),'thread_angle_deg':n(minimum=20,maximum=60),'nut_outer_diameter_mm':nullable(n(minimum=0.001)),
    'clearance_mm':n(minimum=0),'right_handed':b(),'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['major_diameter_mm','pitch_mm','screw_length_mm','nut_length_mm'])),
('create_sheet_metal_base','Create an isolated rectangular sheet-metal base with explicit thickness, bend radius and K-factor. This native extension never changes normal part modeling paths.',obj({
    'width_mm':n(minimum=0.001),'height_mm':n(minimum=0.001),'thickness_mm':n(minimum=0.001),'bend_radius_mm':n(minimum=0),'k_factor':n(minimum=0,maximum=1),
    'replace':b(),'name':nullable(s()),
},['width_mm','height_mm','thickness_mm'])),
('add_sheet_metal_flange','Add one straight flange to a sheet-metal base edge. Folded B-Rep is deterministic; flat-pattern development uses bend radius + K-factor.',obj({
    'edge':s(enum=['top','bottom','left','right']),'length_mm':n(minimum=0.001),'angle_deg':n(minimum=-179.999,maximum=179.999),'bend_radius_mm':nullable(n(minimum=0)),'name':nullable(s()),
},['edge','length_mm'])),
('extrude_advanced','Advanced extrusion with taper angle, similar to Inventor taper extrusion.',obj({
    'sketch_name':s(),'distance_mm':n(),'taper_deg':n(minimum=-89,maximum=89),'operation':s(enum=['new','join','cut','intersect']),'direction':s(enum=['positive','negative','symmetric']),'name':nullable(s()),
},['sketch_name','distance_mm'])),
('loft','Loft a solid through two or more closed profile sketches on arbitrary planes.',obj({
    'sketch_names':arr(s()),'ruled':b(),'operation':s(enum=['new','join','cut','intersect']),'name':nullable(s()),
},['sketch_names'])),
('sweep','Sweep a closed profile sketch along explicit 3D path points or an existing Inventor-style 3D sketch path.',obj({
    'profile_sketch_name':s(),'path_points_mm':nullable(arr(arr(n()))),'path_sketch3d_name':nullable(s()),'smooth':b(),'is_frenet':b(),'transition':s(enum=['transformed','round','right']),'operation':s(enum=['new','join','cut','intersect']),'name':nullable(s()),
},['profile_sketch_name'])),
('create_box','Inventor-style primitive box / rectangular solid.',obj({
    'length_mm':n(minimum=0.001),'width_mm':n(minimum=0.001),'height_mm':n(minimum=0.001),
    'origin_mm':arr(n()),'centered':b(),'operation':s(enum=['new','join','cut','intersect']),'replace':b(),'name':nullable(s()),
},['length_mm','width_mm','height_mm'])),
('create_cylinder','Inventor-style cylinder primitive.',obj({
    'diameter_mm':n(minimum=0.001),'height_mm':n(minimum=0.001),'origin_mm':arr(n()),'axis':arr(n()),
    'operation':s(enum=['new','join','cut','intersect']),'replace':b(),'name':nullable(s()),
},['diameter_mm','height_mm'])),
('create_cone','Create a cone or frustum primitive.',obj({
    'diameter1_mm':n(minimum=0.001),'diameter2_mm':n(minimum=0),'height_mm':n(minimum=0.001),'origin_mm':arr(n()),'axis':arr(n()),
    'operation':s(enum=['new','join','cut','intersect']),'replace':b(),'name':nullable(s()),
},['diameter1_mm','height_mm'])),
('create_sphere','Create a sphere primitive.',obj({
    'diameter_mm':n(minimum=0.001),'center_mm':arr(n()),'operation':s(enum=['new','join','cut','intersect']),'replace':b(),'name':nullable(s()),
},['diameter_mm'])),
('create_torus','Create a torus primitive.',obj({
    'major_radius_mm':n(minimum=0.001),'minor_radius_mm':n(minimum=0.001),'center_mm':arr(n()),'axis':arr(n()),
    'operation':s(enum=['new','join','cut','intersect']),'replace':b(),'name':nullable(s()),
},['major_radius_mm','minor_radius_mm'])),
('create_slot','Create/extrude a straight slot (racetrack) as new/join/cut/intersect.',obj({
    'length_mm':n(minimum=0.001),'width_mm':n(minimum=0.001),'depth_mm':n(minimum=0.001),'cx_mm':n(),'cy_mm':n(),'z0_mm':n(),
    'operation':s(enum=['new','join','cut','intersect']),'replace':b(),'name':nullable(s()),
},['length_mm','width_mm','depth_mm'])),
('create_coil','Create a true helical coil/spring solid using a circular section.',obj({
    'pitch_mm':n(minimum=0.001),'height_mm':n(minimum=0.001),'helix_radius_mm':n(minimum=0.001),'section_diameter_mm':n(minimum=0.001),
    'z0_mm':n(),'right_handed':b(),'operation':s(enum=['new','join','cut','intersect']),'replace':b(),'name':nullable(s()),
},['pitch_mm','height_mm','helix_radius_mm','section_diameter_mm'])),
('create_external_thread','Create a modeled 60-degree helical metric external thread/rod along +Z.',obj({
    'major_diameter_mm':n(minimum=0.001),'pitch_mm':n(minimum=0.001),'length_mm':n(minimum=0.001),'z0_mm':n(),'right_handed':b(),
    'operation':s(enum=['new','join','cut','intersect']),'name':nullable(s()),
},['major_diameter_mm','pitch_mm','length_mm'])),
('create_metric_hex_bolt','Create an ISO-style metric hex bolt with real modeled helical thread. Omitted pitch/head dimensions use built-in metric standards.',obj({
    'diameter_mm':n(minimum=1),'length_mm':n(minimum=0.001),'pitch_mm':nullable(n(minimum=0.001)),
    'head_across_flats_mm':nullable(n(minimum=0.001)),'head_height_mm':nullable(n(minimum=0.001)),'thread_length_mm':nullable(n(minimum=0)),
    'right_handed':b(),'modeled_thread':b(),'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['diameter_mm','length_mm'])),
('create_metric_hex_nut','Create an ISO-style metric hex nut with real modeled female helical thread.',obj({
    'diameter_mm':n(minimum=1),'pitch_mm':nullable(n(minimum=0.001)),'across_flats_mm':nullable(n(minimum=0.001)),'height_mm':nullable(n(minimum=0.001)),
    'right_handed':b(),'modeled_thread':b(),'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['diameter_mm'])),
('create_metric_washer','Create an ISO-style normal-series metric plain washer.',obj({
    'diameter_mm':n(minimum=1),'inner_diameter_mm':nullable(n(minimum=0.001)),'outer_diameter_mm':nullable(n(minimum=0.001)),'thickness_mm':nullable(n(minimum=0.001)),
    'replace':b(),'operation':s(enum=['new','join']),'name':nullable(s()),
},['diameter_mm'])),
('shell','Inventor-style shell/hollow feature. remove_face_ids are openings; negative thickness offsets inward.',obj({
    'thickness_mm':n(),'remove_face_ids':arr(s()),'kind':s(enum=['arc','intersection']),'name':nullable(s()),
},['thickness_mm'])),
('mirror_body','Mirror the current solid across XY/XZ/YZ and optionally keep the original.',obj({
    'plane':s(enum=['XY','XZ','YZ']),'base_point_mm':arr(n()),'keep_original':b(),'name':nullable(s()),
},['plane'])),
('transform_body','Direct-edit style whole-body translate/rotate/uniform-scale feature.',obj({
    'translate_mm':arr(n()),'rotate_axis':arr(n()),'rotate_deg':n(),'scale':n(minimum=0.000001),'name':nullable(s()),
})),
('move_face','Inventor Move Face-style edit. Moves one selected planar face along its outward normal. It first edits the originating history feature when unambiguous, then falls back to a history-recorded direct B-Rep edit.',obj({
    'face_ref':nullable(s()),'distance_mm':n(),'prefer_history':b(),'name':nullable(s()),
},['distance_mm'])),
('delete_face','Inventor Delete Face (heal)-style edit. Removes the selected face and heals surrounding geometry using the OCCT defeaturing engine.',obj({
    'face_ref':nullable(s()),'name':nullable(s()),
})),
('set_face_diameter','Edit the driving diameter of a selected cylindrical face when it can be traced unambiguously to feature history.',obj({
    'face_ref':nullable(s()),'diameter_mm':n(minimum=0.000001),
},['diameter_mm'])),
('edit_sketch_entity','Inventor-style sketch geometry edit. Edits one existing line/circle/arc entity, preserves driving d# bindings when present, solves constraints, and rebuilds dependent features.',obj({
    'sketch_name':nullable(s()),'entity_id':s(),'updates':{'type':'object','additionalProperties':True},
},['entity_id','updates'])),
('delete_sketch_entity','Delete one existing sketch entity. Directly dependent sketch constraints/dimensions are removed; dependent 3D features are rebuilt atomically.',obj({
    'sketch_name':nullable(s()),'entity_id':s(),
},['entity_id'])),
('edit_sketch_dimension','Edit an existing driving sketch dimension through its model parameter, then solve the sketch and rebuild dependent features.',obj({
    'sketch_name':nullable(s()),'dimension_name':s(),'value_mm':n(minimum=0.000001),
},['dimension_name','value_mm'])),
('delete_sketch_dimension','Delete a sketch dimension constraint while retaining its former model parameter as a safe orphan if other expressions may reference it.',obj({
    'sketch_name':nullable(s()),'dimension_name':s(),
},['dimension_name'])),
('edit_sketch_constraint','Edit the type and/or referenced entities of an existing geometric sketch constraint, then re-solve and rebuild.',obj({
    'sketch_name':nullable(s()),'constraint_name':s(),'constraint_type':nullable(s(enum=['coincident','parallel','perpendicular','horizontal','vertical','tangent','concentric','equal','collinear','symmetric'])),'entity_ids':nullable(arr(s())),
},['constraint_name'])),
('delete_sketch_constraint','Delete an existing geometric sketch constraint and rebuild downstream features.',obj({
    'sketch_name':nullable(s()),'constraint_name':s(),
},['constraint_name'])),
('edit_feature_definition','Validated Inventor Feature.Definition-style history edit for common features. Preserves d# bindings, supports rename, and can reposition a history feature before/after another feature using Inventor RepositionObject-style semantics.',obj({
    'feature_name':s(),'updates':{'type':'object','additionalProperties':True},'new_name':nullable(s()),'before':nullable(s()),'after':nullable(s()),
},['feature_name'])),
('edit_feature','Edit parameters on an existing history feature and rebuild downstream geometry.',obj({
    'feature_name':s(),'updates':{'type':'object','additionalProperties':True},
},['feature_name','updates'])),
('suppress_feature','Suppress or unsuppress an existing history feature and rebuild.',obj({'feature_name':s(),'suppressed':b()},['feature_name'])),
('delete_feature','Delete an existing history feature and rebuild downstream geometry.',obj({'feature_name':s()},['feature_name'])),
('delete_document_file','Standalone-only destructive document deletion. Deletes the currently active saved .scad.json part/assembly file after explicit confirmation. It refuses unsaved/dirty or open-referenced documents unless the caller explicitly permits discarding dirty changes; it never recursively deletes folders or referenced component files.',obj({
    'confirm':b(),'discard_unsaved_changes':b(),
},['confirm'])),
('edit_parameter','Compact Inventor-style parameter edit extension. Rename or delete an existing model/user parameter while preserving/rejecting dependencies explicitly.',obj({
    'action':s(enum=['rename','delete']),'name':s(),'new_name':nullable(s()),
},['action','name'])),
('edit_sketch','Compact Inventor-style 2D/3D sketch edit extension. Move/rotate/copy/rename/delete 2D sketch content and create/edit Inventor-style 3D path sketches without expanding the upstream ipt-mcp core surface.',obj({
    'action':s(enum=['transform','rename','delete','create_3d','add_3d_line','add_3d_spline','transform_3d','rename_3d','delete_3d']),'sketch_name':nullable(s()),'entity_ids':nullable(arr(s())),
    'translate_mm':nullable(arr(n())),'rotate_axis':nullable(arr(n())),'rotate_deg':n(),'pivot_mm':nullable(arr(n())),'copy':b(),
    'new_name':nullable(s()),'delete_dependents':b(),'start_mm':nullable(arr(n())),'end_mm':nullable(arr(n())),'points_mm':nullable(arr(arr(n()))),
},['action'])),
('edit_work_feature','Compact Inventor-style work geometry edit extension. Create/redefine/rename/delete work planes, work axes, and work points without altering the upstream ipt-mcp core contract.',obj({
    'action':s(enum=['create','redefine','rename','delete']),'kind':s(enum=['plane','axis','point']),'name':nullable(s()),
    'type':nullable(s()),'refs':nullable(arr({})),'offset_mm':nullable(n()),'point_mm':nullable(arr(n())),'new_name':nullable(s()),
},['action','kind'])),
('edit_solid','Compact Inventor-style solid editing extension. Supports thread-on-face, Split/Combine, FaceDraft, planar ReplaceFace, body Offset/Thicken-family editing, and multi-face DeleteFace/heal.',obj({
    'action':s(enum=['thread_face','split_body','combine_bodies','draft_face','replace_face','offset_body','delete_faces']),'name':nullable(s()),
    'face_ref':nullable(s()),'face_refs':nullable(arr(s())),'designation':nullable(s()),'pitch_mm':nullable(n(minimum=0.000001)),'major_diameter_mm':nullable(n(minimum=0.000001)),
    'full_depth':b(),'thread_depth_mm':nullable(n(minimum=0.000001)),'offset_mm':n(minimum=0),'right_handed':b(),'internal':nullable(b()),'reverse_direction':b(),
    'plane':nullable({}),'keep':s(enum=['both','positive','negative','top','bottom']),
    'base_index':n(minimum=1),'tool_indices':nullable(arr(n(minimum=1))),'operation':s(enum=['join','cut','intersect']),'keep_tools':b(),
    'angle_deg':nullable(n()),'pull_direction':nullable({'anyOf':[s(),arr(n())]}),'neutral_plane':nullable({}),'target_plane':nullable({}),
    'distance_mm':nullable(n()),'join':s(enum=['arc','intersection']),'remove_internal_edges':b(),
},['action'])),
]

ASSEMBLY_EXTENSIONS=[
('add_joint','Inventor AssemblyJoint extension. Creates a kinematic joint using Inventor-style joint semantics: rigid, rotational, slider, cylindrical, planar, or ball. This extension does not alter the upstream 58-tool contract.',obj({
    'type':s(enum=['rigid','rotational','slider','cylindrical','planar','ball']),
    'a':JOINT_SIDE,'b':JOINT_SIDE,'name':nullable(s()),
    'linear_position_mm':nullable(n()),'linear_start_mm':nullable(n()),'linear_end_mm':nullable(n()),
    'angular_position_deg':nullable(n()),'angular_start_deg':nullable(n()),'angular_end_deg':nullable(n()),
    'flip_origin_direction':b(),'flip_alignment_direction':b(),
},['type','a','b'])),
('edit_joint','Edit an existing Inventor-style assembly joint definition, including type/origins/intents, position, limits, and suppression.',obj({
    'name':s(),'type':nullable(s(enum=['rigid','rotational','slider','cylindrical','planar','ball'])),
    'a_occurrence':nullable(s()),'a_ref':nullable(s()),'b_occurrence':nullable(s()),'b_ref':nullable(s()),
    'a_intent':nullable({}),'b_intent':nullable({}),'flip_origin_direction':nullable(b()),'flip_alignment_direction':nullable(b()),
    'linear_position_mm':nullable(n()),'linear_start_mm':nullable(n()),'linear_end_mm':nullable(n()),
    'angular_position_deg':nullable(n()),'angular_start_deg':nullable(n()),'angular_end_deg':nullable(n()),
    'suppressed':nullable(b()),
},['name'])),
('set_joint_limits','Set linear and/or angular travel limits on an existing assembly joint.',obj({
    'name':s(),'linear_start_mm':nullable(n()),'linear_end_mm':nullable(n()),
    'angular_start_deg':nullable(n()),'angular_end_deg':nullable(n()),
},['name'])),
('drive_joint','Move an existing assembly joint to an in-range linear and/or angular position without changing its joint type.',obj({
    'name':s(),'linear_position_mm':nullable(n()),'angular_position_deg':nullable(n()),
},['name'])),
('list_joints','List Inventor-style assembly joints with type, health, limits, and current state.',obj()),
('delete_joint','Delete an assembly joint by name.',obj({'name':s()},['name'])),
('transform_occurrence','Edit an assembly occurrence pose/state or replace its referenced component while preserving the occurrence relationship context where possible.',obj({
    'occurrence_name':nullable(s()),'position_mm':nullable(arr(n())),'rotation_deg_xyz':nullable(arr(n())),
    'translate_mm':nullable(arr(n())),'rotate_delta_deg_xyz':nullable(arr(n())),
    'replacement_path':nullable(s()),'replace_all':b(),'grounded':nullable(b()),'suppressed':nullable(b()),
})),
('edit_constraint','Edit/rebind an existing mate/flush/insert/angle relationship, optionally converting among those supported constraint types, then re-solve the assembly.',obj({
    'name':s(),'type':nullable(s(enum=['mate','flush','insert','angle'])),
    'a_occurrence':nullable(s()),'a_ref':nullable(s()),'b_occurrence':nullable(s()),'b_ref':nullable(s()),
    'offset_mm':nullable(n()),'angle_deg':nullable(n()),'suppressed':nullable(b()),'insert_opposed':nullable(b()),
},['name'])),
('delete_constraint','Inventor AssemblyConstraint.Delete-style removal of an existing mate/flush/insert/angle relationship.',obj({'name':s()},['name'])),
('delete_occurrence','Inventor ComponentOccurrence.Delete-style removal of a placed part/subassembly. Dependent constraints/joints are removed; the referenced source document/file is not deleted.',obj({
    'occurrence_name':nullable(s()),
})),
]

DEFAULT_TOOLSETS=['meta','query','document','parameters','properties','sketch','feature','export','assembly','assembly_query','toolbaker','toolbaker_write']
READONLY_TOOLSETS={'meta','query','assembly_query','toolbaker'}

def make_tools(prefix='inventor',toolsets=None,read_only=False,enable_code=False,extensions=False,assembly_extensions=False):
    requested=DEFAULT_TOOLSETS if not toolsets or toolsets==['all'] else toolsets
    if read_only:requested=[x for x in requested if x in READONLY_TOOLSETS]
    if enable_code and not read_only and 'code' not in requested:requested=list(requested)+['code']
    tools=[]
    for ts in requested:
        for name,desc,schema in TOOLSETS.get(ts,[]):tools.append({'name':f'{prefix}_{name}','description':desc,'inputSchema':schema,'_toolset':ts})
    if extensions and not read_only:
        for name,desc,schema in EXTENSIONS:tools.append({'name':f'{prefix}_{name}','description':desc,'inputSchema':schema,'_toolset':'extensions'})
    if assembly_extensions and not read_only:
        for name,desc,schema in ASSEMBLY_EXTENSIONS:tools.append({'name':f'{prefix}_{name}','description':desc,'inputSchema':schema,'_toolset':'assembly_joint_extension'})
    # _toolset is internal metadata; strip from wire representation later if desired.
    return tools
