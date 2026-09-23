from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any

from standalonecad.core.engine import READ_COMMANDS
from standalonecad.core.standard_parts import metric_pitch
from standalonecad.mcp.server import McpServer
from standalonecad.mcp.tools import make_tools


NON_REVISION_COMMANDS = {
    'health','list_available_targets','get_current_target','switch_target','list_open_documents',
    'get_document_info','list_parameters','get_parameter','get_iproperty','get_mass_properties',
    'capture_view','export_step','export_stl','export_dxf','list_interfaces','check_interference',
    'measure_min_distance','get_assembly_bom','list_constraints','list_joints','list_baked_tools',
    'list_bake_suggestions','create_bake_issue_draft','save_document','view_fit','set_view_orientation',
}


@dataclass
class PlanResult:
    ok: bool
    message: str
    calls: list[dict[str, Any]]
    results: list[dict[str, Any]]
    error: str | None = None
    revision_before: int = 0
    revision_after: int = 0


def _unwrap_type(schema: dict[str, Any]) -> list[dict[str, Any]]:
    if 'anyOf' in schema:
        return [x for x in schema['anyOf'] if isinstance(x, dict)]
    return [schema]


def _validate_value(schema: dict[str, Any], value: Any, path: str = '$') -> None:
    branches = _unwrap_type(schema)
    branch_errors: list[str] = []
    for branch in branches:
        try:
            _validate_single(branch, value, path)
            return
        except ValueError as exc:
            branch_errors.append(str(exc))
    raise ValueError(branch_errors[0] if branch_errors else f'{path}: invalid value')


def _validate_single(schema: dict[str, Any], value: Any, path: str) -> None:
    typ = schema.get('type')
    if typ == 'null':
        if value is not None:
            raise ValueError(f'{path}: expected null')
        return
    if typ == 'object':
        if not isinstance(value, dict):
            raise ValueError(f'{path}: expected object')
        props = schema.get('properties') or {}
        for req in schema.get('required') or []:
            if req not in value:
                raise ValueError(f'{path}.{req}: required')
        if schema.get('additionalProperties') is False:
            extra = sorted(set(value) - set(props))
            if extra:
                raise ValueError(f'{path}: unknown argument(s): {extra}')
        for k, v in value.items():
            if k in props:
                _validate_value(props[k], v, f'{path}.{k}')
        return
    if typ == 'array':
        if not isinstance(value, list):
            raise ValueError(f'{path}: expected array')
        if 'minItems' in schema and len(value) < int(schema['minItems']):
            raise ValueError(f'{path}: requires at least {schema["minItems"]} items')
        item_schema = schema.get('items') or {}
        for idx, item in enumerate(value):
            _validate_value(item_schema, item, f'{path}[{idx}]')
        return
    if typ == 'string':
        if not isinstance(value, str):
            raise ValueError(f'{path}: expected string')
    elif typ == 'integer':
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f'{path}: expected integer')
    elif typ == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'{path}: expected number')
    elif typ == 'boolean':
        if not isinstance(value, bool):
            raise ValueError(f'{path}: expected boolean')
    elif typ is not None:
        raise ValueError(f'{path}: unsupported schema type {typ}')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: must be one of {schema["enum"]}')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 'minimum' in schema and value < schema['minimum']:
            raise ValueError(f'{path}: minimum is {schema["minimum"]}')
        if 'maximum' in schema and value > schema['maximum']:
            raise ValueError(f'{path}: maximum is {schema["maximum"]}')


def _extract_number(text: str, patterns: list[str]) -> float | None:
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                pass
    return None


def _explicit_replace_request(text: str) -> bool:
    low=(text or '').lower()
    phrases=(
        '기존 모델 지우', '기존 모델 삭제', '현재 모델 지우', '현재 모델 삭제',
        '기존 거 지우', '기존거 지우', '기존 것 지우', '기존것 지우',
        '전부 지우', '전부 삭제', '완전히 교체', '기존 모델 교체', '현재 모델 교체',
        'clear current model', 'delete current model', 'clear active model', 'delete active model',
        'clear existing model', 'delete existing model', 'clear all', 'delete all', 'fully replace',
        'replace current', 'replace existing', 'delete current', 'erase current',
    )
    return any(x in low for x in phrases)


def _creation_shortcut_allowed(text: str) -> bool:
    """Keep creation shortcuts from hijacking explicit edit/delete requests.

    This guard prevents a creation-looking noun/dimension from bypassing the language planner
    when the user explicitly asks to edit, resize, replace, or delete existing geometry.
    Explicit requests to replace the current model remain valid creation shortcuts.
    """
    if _explicit_replace_request(text):
        return True
    low=(text or '').lower()
    edit_delete_markers=(
        '수정', '변경', '바꿔', '바꾸', '늘려', '늘리', '줄여', '줄이',
        '삭제', '제거', '지워', '지우', '없애', '교체',
        'edit', 'modify', 'change', 'resize', 'increase', 'decrease',
        'delete', 'remove', 'erase', 'replace',
    )
    return not any(marker in low for marker in edit_delete_markers)


def _standalone_creation_plan(tool: str, args: dict[str, Any], note: str, user_prompt: str, state: dict[str, Any], *, supports_replace: bool = True) -> dict[str, Any]:
    """Create a standalone component without silently destroying the active document.

    If the UI supplied document state and the active document already contains geometry
    (or is not a Part), create a new Part first.  ``replace=true`` is reserved for an
    explicit user request to erase/replace the current model.  Calls made by unit tests
    or external code without CURRENT_STATE use a single-call plan.
    """
    out=dict(args)
    explicit_replace=_explicit_replace_request(user_prompt)
    doc_state=state.get('document', '__missing__')
    needs_new=False
    if doc_state != '__missing__':
        if doc_state is None:
            needs_new=True
        elif isinstance(doc_state,dict):
            needs_new=(doc_state.get('type')!='part' or bool(doc_state.get('has_geometry')))
    calls=[]
    if explicit_replace:
        if supports_replace:out['replace']=True
    else:
        if supports_replace:out['replace']=False
        if needs_new:calls.append({'tool':'inventor_new_part','arguments':{}})
    calls.append({'tool':tool,'arguments':out})
    return {'calls':calls,'note':note}


def direct_plan(user_prompt: str, current_state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Deterministic local routing for well-defined standard features.

    This intentionally handles only cases whose parameters can be extracted without
    guessing. Everything else goes to the language planner.
    """
    q = (user_prompt or '').strip()
    low = q.lower()
    state = current_state or {}
    selection = state.get('selection') or {}
    creation_shortcut_allowed = _creation_shortcut_allowed(q)

    # Assembly component deletion is deterministic when the viewport/tree already
    # identifies the occurrence.  Keep file deletion separate: removing an occurrence
    # must never imply deleting its source part/subassembly document.
    if selection.get('type') in ('occurrence','face','edge') and selection.get('occurrence_name'):
        if any(k in low for k in ('부품 삭제','부품 제거','이 부품 삭제','이 부품 제거','어셈블리에서 빼','어셈블리에서 제거','delete component','remove component','delete this component','remove this component','remove from assembly','occurrence delete','delete occurrence')):
            return {'calls':[{'tool':'inventor_delete_occurrence','arguments':{'occurrence_name':str(selection['occurrence_name'])}}], 'note':'selected_occurrence_delete'}

    # Direct manipulation routes for the explicit current selection. These avoid asking
    # the LLM to rediscover a face/edge the user already clicked in the viewport.
    if selection.get('type') == 'edge' and selection.get('edge_ref'):
        edge_id = str(selection['edge_ref'])
        if any(k in low for k in ('필렛','fillet','라운드','round edge')):
            radius = _extract_number(q,[r'(?:필렛|fillet|반지름|radius|r)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?',r'([0-9]+(?:\.[0-9]+)?)\s*mm\s*(?:필렛|fillet)'])
            if radius is not None:
                return {'calls':[{'tool':'inventor_fillet','arguments':{'edgeIds':[edge_id],'radius':radius}}], 'note':'selected_edge_fillet'}
        if any(k in low for k in ('모따기','chamfer')):
            distance = _extract_number(q,[r'(?:모따기|chamfer|거리|distance)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?',r'([0-9]+(?:\.[0-9]+)?)\s*mm\s*(?:모따기|chamfer)'])
            if distance is not None:
                return {'calls':[{'tool':'inventor_chamfer','arguments':{'edgeIds':[edge_id],'distance':distance}}], 'note':'selected_edge_chamfer'}

    if selection.get('type') == 'face' and selection.get('face_ref'):
        face_id=str(selection['face_ref'])
        # Inventor-style selection driven edits.  These routes are intentionally based
        # on the explicit viewport selection, so the planner never guesses a different
        # face when the user says "this face / here".
        if any(k in low for k in ('면 삭제','이 면 삭제','면을 삭제','delete face','delete this face','remove face','remove this face','이 면 없애','면 없애')):
            return {'calls':[{'tool':'cad_delete_face','arguments':{'face_ref':face_id,'name':None}}], 'note':'selected_face_delete_heal'}

        if any(k in low for k in ('쉘','shell','속 비워','속을 비워','hollow')):
            thickness=_extract_number(q,[r'(?:두께|thickness)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?',r'([0-9]+(?:\.[0-9]+)?)\s*mm\s*(?:쉘|shell)'])
            if thickness is not None:
                # Standalone shell follows CadQuery/Inventor convention documented by
                # this extension: negative thickness offsets inward. Natural-language
                # hollow/shell requests default inward unless explicitly saying outward.
                signed=abs(thickness) if any(k in low for k in ('바깥','outward','outside')) else -abs(thickness)
                return {'calls':[{'tool':'cad_shell','arguments':{'thickness_mm':signed,'remove_face_ids':[face_id],'kind':'arc','name':None}}], 'note':'selected_face_shell'}

        if not any(k in low for k in ('구멍','홀','hole','탭','tap')) and any(k in low for k in ('지름','직경','diameter','ø','Ø')):
            diameter=_extract_number(q,[r'(?:지름|직경|diameter)\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)',r'(?:ø|Ø)\s*([0-9]+(?:\.[0-9]+)?)'])
            if diameter is not None:
                return {'calls':[{'tool':'cad_set_face_diameter','arguments':{'face_ref':face_id,'diameter_mm':diameter}}], 'note':'selected_cylindrical_face_diameter'}

        if any(k in low for k in ('밀어','당겨','이동','옮겨','offset face','move face','push','pull','press pull','press-pull')):
            distance=_extract_number(q,[r'(-?[0-9]+(?:\.[0-9]+)?)\s*(?:mm)',r'(?:거리|distance|offset)\s*[:=]?\s*(-?[0-9]+(?:\.[0-9]+)?)'])
            if distance is not None:
                # Positive = selected outward normal. Korean/English inward wording flips
                # an unsigned magnitude; explicit negative values are preserved.
                if distance>=0 and any(k in low for k in ('안쪽','안으로','내부','inward','inside','push in')):distance=-abs(distance)
                elif distance>=0 and any(k in low for k in ('바깥','밖으로','외부','outward','outside','pull out')):distance=abs(distance)
                return {'calls':[{'tool':'cad_move_face','arguments':{'face_ref':face_id,'distance_mm':distance,'prefer_history':True,'name':None}}], 'note':'selected_face_move'}

    if selection.get('type') == 'face' and selection.get('center') and any(k in low for k in ('구멍','홀','hole','탭','tap')):
        direction = selection.get('direction')
        if direction not in ('+X','-X','+Y','-Y','+Z','-Z'):
            # Find the full topology record when an older UI selection omitted direction.
            fid=selection.get('face_ref')
            rec=next((x for x in (state.get('faces') or []) if x.get('id')==fid),None)
            direction=(rec or {}).get('direction')
        dia = _extract_number(q,[r'(?:지름|직경|diameter)\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)',r'(?:ø|Ø)\s*([0-9]+(?:\.[0-9]+)?)'])
        md = _extract_number(q,[r'\bM\s*([0-9]+(?:\.[0-9]+)?)'])
        depth = _extract_number(q,[r'(?:깊이|depth)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?'])
        if dia is None and md is not None: dia=md
        if direction and dia is not None:
            center=[float(v) for v in selection['center']]
            face={'kind':'planar','normal':direction,'extreme':'max','near_mm':center}
            through = depth is None or any(k in low for k in ('관통','through all','through-hole','through hole'))
            args={'face':face,'points_mm':[center],'diameter_mm':dia,'kind':'drilled','through':bool(through),'depth_mm':None if through else depth,'cbore_diameter_mm':None,'cbore_depth_mm':None,'csink_diameter_mm':None,'csink_angle_deg':82.0,'tapped':None}
            if any(k in low for k in ('탭','tap','tapped')) and md is not None:
                pitch=_extract_number(q,[r'(?:피치|pitch)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)',r'\bM\s*[0-9]+(?:\.[0-9]+)?\s*[xX×]\s*([0-9]+(?:\.[0-9]+)?)'])
                designation=f'M{md:g}' + (f'x{pitch:g}' if pitch is not None else '')
                args['tapped']={'designation':designation,'class':'6H','right_handed':True,'full_depth':True,'thread_depth_mm':None if through else depth}
            return {'calls':[{'tool':'inventor_hole','arguments':args}], 'note':'selected_face_hole'}

    # High-frequency primitive routes stay local. These are deliberately conservative:
    # a match is accepted only when all geometry-defining dimensions are explicit.
    triple = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*[xX×*]\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*[xX×*]\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?', q)
    if creation_shortcut_allowed and triple and any(k in low for k in ('직육면체','박스','box','플레이트','plate','블록','block')):
        L,W,H=(float(triple.group(i)) for i in (1,2,3))
        box_args={'length_mm':L,'width_mm':W,'height_mm':H,'origin_mm':[0,0,0],'centered':False,'operation':'new','replace':False,'name':'Box1'}
        plan=_standalone_creation_plan('cad_create_box',box_args,'deterministic_box',q,state)
        if any(k in low for k in ('구멍','홀','holes','hole')):
            count4=bool(re.search(r'\b(?:four|4)\b',low) or any(k in low for k in ('네 개','4개','네개')))
            dia=_extract_number(q,[
                r'(?:four|4)\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)\s*mm\s*(?:holes|hole)',
                r'(?:구멍|홀|holes?|hole\s*diameter|diameter)\D{0,12}(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?',
                r'(?:ø|Ø)\s*([0-9]+(?:\.[0-9]+)?)',
            ])
            offset=_extract_number(q,[
                r'([0-9]+(?:\.[0-9]+)?)\s*mm\s*from\s*(?:each\s*)?corner',
                r'(?:offset|corner\s*offset)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?',
                r'(?:모서리|코너)\D{0,12}([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?',
            ])
            if count4 and dia is not None and offset is not None and 0 < offset < min(L,W)/2:
                z=H
                pts=[[offset,offset,z],[L-offset,offset,z],[offset,W-offset,z],[L-offset,W-offset,z]]
                face={'kind':'planar','normal':'+Z','extreme':'max','near_mm':[L/2,W/2,z]}
                hole_args={'face':face,'points_mm':pts,'diameter_mm':dia,'kind':'drilled','through':True,'depth_mm':None,'cbore_diameter_mm':None,'cbore_depth_mm':None,'csink_diameter_mm':None,'csink_angle_deg':82.0,'tapped':None}
                plan['calls'].append({'tool':'inventor_hole','arguments':hole_args})
                plan['note']='deterministic_mounting_plate_with_holes'
        return plan

    if creation_shortcut_allowed and ('정육면체' in low or 'cube' in low):
        side=_extract_number(q,[r'(?:한\s*변|변\s*길이|side)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?',r'([0-9]+(?:\.[0-9]+)?)\s*mm\s*(?:정육면체|cube)'])
        if side is not None:
            return _standalone_creation_plan('cad_create_box',{'length_mm':side,'width_mm':side,'height_mm':side,'origin_mm':[0,0,0],'centered':False,'operation':'new','replace':False,'name':'Cube1'},'deterministic_cube',q,state)

    if creation_shortcut_allowed and any(k in low for k in ('원기둥','cylinder')):
        dia=_extract_number(q,[r'(?:지름|직경|diameter)\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)',r'(?:ø|Ø)\s*([0-9]+(?:\.[0-9]+)?)'])
        height=_extract_number(q,[r'(?:높이|길이|height)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?'])
        if dia is not None and height is not None:
            return _standalone_creation_plan('cad_create_cylinder',{'diameter_mm':dia,'height_mm':height,'origin_mm':[0,0,0],'axis':[0,0,1],'operation':'new','replace':False,'name':'Cylinder1'},'deterministic_cylinder',q,state)

    if creation_shortcut_allowed and any(k in low for k in ('나사봉','threaded rod','thread rod')):
        md=_extract_number(q,[r'\bM\s*([0-9]+(?:\.[0-9]+)?)'])
        length=_extract_number(q,[r'(?:길이|length)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?'])
        pitch=_extract_number(q,[r'(?:피치|pitch)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)',r'\bM\s*[0-9]+(?:\.[0-9]+)?\s*[xX×]\s*([0-9]+(?:\.[0-9]+)?)'])
        if md is not None and length is not None:
            try:pitch=metric_pitch(md,pitch)
            except Exception:pass
            if pitch is not None:
                return _standalone_creation_plan('cad_create_external_thread',{'major_diameter_mm':md,'pitch_mm':pitch,'length_mm':length,'z0_mm':0.0,'right_handed':True,'operation':'new','name':'ThreadedRod1'},'deterministic_threaded_rod',q,state,supports_replace=False)
    # ISO-style metric hex fasteners are routed locally so ordinary standard-part
    # prompts do not depend on a language-model plan or ad-hoc feature construction.
    if creation_shortcut_allowed and any(k in low for k in ('육각 볼트','육각볼트','hex bolt','hex-head bolt','hex head bolt')):
        md = _extract_number(q, [r'\bM\s*([0-9]+(?:\.[0-9]+)?)', r'호칭(?:\s*지름)?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        length = _extract_number(q, [r'(?:볼트\s*)?(?:전체\s*)?길이\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?', r'under[- ]?head\s*length\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        thread_len = _extract_number(q, [r'나사산\s*길이\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?', r'thread(?:ed)?\s*length\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        hh = _extract_number(q, [r'머리\s*(?:두께|높이)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?', r'head\s*(?:height|thickness)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        af = _extract_number(q, [r'(?:대변\s*거리|육각\s*크기|대변)\s*(?:\([^)]*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?', r'across\s*flats\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        pitch = _extract_number(q, [r'(?:피치|pitch)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?', r'\bM\s*[0-9]+(?:\.[0-9]+)?\s*[xX×]\s*([0-9]+(?:\.[0-9]+)?)'])
        if md is not None and (length is not None or thread_len is not None):
            # If the user supplied only a thread length for a standard hex bolt,
            # interpret it as a fully-threaded under-head length instead of asking
            # a needless follow-up. An explicit overall length always takes priority.
            effective_length = length if length is not None else thread_len
            args = {
                'diameter_mm': md, 'length_mm': effective_length,
                'pitch_mm': pitch, 'head_across_flats_mm': af, 'head_height_mm': hh,
                'thread_length_mm': thread_len if thread_len is not None else effective_length,
                'right_handed': True, 'modeled_thread': True, 'replace': False,
                'operation': 'new', 'name': 'HexBolt1',
            }
            return _standalone_creation_plan('cad_create_metric_hex_bolt',args,'deterministic_metric_hex_bolt',q,state)

    if creation_shortcut_allowed and any(k in low for k in ('육각 너트','육각너트','hex nut')):
        md = _extract_number(q, [r'\bM\s*([0-9]+(?:\.[0-9]+)?)'])
        pitch = _extract_number(q, [r'(?:피치|pitch)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', r'\bM\s*[0-9]+(?:\.[0-9]+)?\s*[xX×]\s*([0-9]+(?:\.[0-9]+)?)'])
        af = _extract_number(q, [r'(?:대변\s*거리|육각\s*크기|대변)\s*(?:\([^)]*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        h = _extract_number(q, [r'(?:너트\s*)?(?:두께|높이)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        if md is not None:
            return _standalone_creation_plan('cad_create_metric_hex_nut',{'diameter_mm':md,'pitch_mm':pitch,'across_flats_mm':af,'height_mm':h,'right_handed':True,'modeled_thread':True,'replace':False,'operation':'new','name':'HexNut1'},'deterministic_metric_hex_nut',q,state)

    if creation_shortcut_allowed and any(k in low for k in ('평와셔','와셔','washer')) and re.search(r'\bM\s*[0-9]',q,re.I):
        md = _extract_number(q, [r'\bM\s*([0-9]+(?:\.[0-9]+)?)'])
        if md is not None:
            return _standalone_creation_plan('cad_create_metric_washer',{'diameter_mm':md,'inner_diameter_mm':None,'outer_diameter_mm':None,'thickness_mm':None,'replace':False,'operation':'new','name':'Washer1'},'deterministic_metric_washer',q,state)

    # Design-Accelerator routes are deliberately high-confidence.  A named mechanical
    # component plus every function-defining dimension is required; phrases such as
    # "기어처럼" or "gear-like" therefore remain with the generic language planner.
    gear_like_phrase = any(k in low for k in ('기어처럼','gear-like','gear like'))
    accel_create_intent = creation_shortcut_allowed and any(k in low for k in ('만들', '생성', '추가', 'create', 'generate', 'model a ', 'model an '))
    if accel_create_intent and not gear_like_phrase and any(k in low for k in ('헬리컬 기어','헬리컬기어','helical gear','helical-gear','헤링본 기어','헤링본기어','herringbone gear')):
        module = _extract_number(q,[r'모듈(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', r'module(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)',r'\bm\s*=\s*([0-9]+(?:\.[0-9]+)?)'])
        teeth = _extract_number(q,[r'(?:이빨\s*수|잇수)(?:\s*\(\s*z\s*\))?\s*[:=]?\s*([0-9]+)', r'(?:teeth|tooth)\s*[:=]?\s*([0-9]+)', r'([0-9]+)\s*(?:teeth|tooth)',r'\bz\s*=\s*([0-9]+)'])
        width = _extract_number(q,[r'(?:치폭|이너비|두께|face\s*width|width)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?'])
        helix = _extract_number(q,[r'(?:헬릭스각|헬리컬각|나선각|helix(?:\s*angle)?)\s*[:=]?\s*([+-]?[0-9]+(?:\.[0-9]+)?)\s*(?:도|deg|°)?'])
        bore = _extract_number(q,[r'(?:중심\s*)?(?:축구멍|축\s*구멍|보어|bore)[^0-9]{0,12}(?:지름|직경|diameter)?\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)'])
        pressure = _extract_number(q,[r'압력각\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)',r'pressure\s*angle\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        if module is not None and teeth is not None and width is not None and helix is not None:
            kind='herringbone' if any(k in low for k in ('헤링본','herringbone')) else 'helical'
            args={'kind':kind,'module':module,'teeth':int(teeth),'width_mm':width,'bore_diameter_mm':0.0 if bore is None else bore,
                  'pressure_angle_deg':20.0 if pressure is None else pressure,'helix_angle_deg':helix,'backlash_mm':0.0,'clearance_mm':0.0,
                  'rim_width_mm':None,'cone_angle_deg':None,'length_mm':None,'height_mm':None,'lead_angle_deg':None,'thread_starts':None,
                  'sun_teeth':None,'planet_teeth':None,'planet_count':None,'replace':False,'operation':'new','name':'HerringboneGear1' if kind=='herringbone' else 'HelicalGear1'}
            return _standalone_creation_plan('cad_create_gear',args,'deterministic_'+kind+'_gear',q,state)

    if accel_create_intent and not gear_like_phrase and any(k in low for k in ('베벨 기어','베벨기어','bevel gear','bevel-gear')):
        module=_extract_number(q,[r'모듈(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', r'module(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)',r'\bm\s*=\s*([0-9]+(?:\.[0-9]+)?)'])
        teeth=_extract_number(q,[r'(?:이빨\s*수|잇수)\s*[:=]?\s*([0-9]+)', r'([0-9]+)\s*(?:teeth|tooth)', r'(?:teeth|tooth)\s*[:=]?\s*([0-9]+)',r'\bz\s*=\s*([0-9]+)'])
        width=_extract_number(q,[r'(?:치폭|페이스폭|face\s*width|width)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        cone=_extract_number(q,[r'(?:피치\s*콘각|콘각|cone\s*angle)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        helix=_extract_number(q,[r'(?:헬릭스각|나선각|helix(?:\s*angle)?)\s*[:=]?\s*([+-]?[0-9]+(?:\.[0-9]+)?)'])
        if None not in (module,teeth,width,cone):
            args={'kind':'bevel','module':module,'teeth':int(teeth),'width_mm':width,'bore_diameter_mm':0.0,'pressure_angle_deg':20.0,
                  'helix_angle_deg':0.0 if helix is None else helix,'backlash_mm':0.0,'clearance_mm':0.0,'rim_width_mm':None,'cone_angle_deg':cone,
                  'length_mm':None,'height_mm':None,'lead_angle_deg':None,'thread_starts':None,'sun_teeth':None,'planet_teeth':None,'planet_count':None,
                  'replace':False,'operation':'new','name':'BevelGear1'}
            return _standalone_creation_plan('cad_create_gear',args,'deterministic_bevel_gear',q,state)

    if accel_create_intent and any(k in low for k in ('웜 기어','웜기어','worm gear pair','worm wheel pair','worm gear set')):
        module=_extract_number(q,[r'모듈(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', r'module(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)',r'\bm\s*=\s*([0-9]+(?:\.[0-9]+)?)'])
        teeth=_extract_number(q,[r'(?:휠\s*)?(?:잇수|이빨\s*수)\s*[:=]?\s*([0-9]+)', r'([0-9]+)\s*(?:wheel\s*)?(?:teeth|tooth)', r'(?:wheel\s*)?(?:teeth|tooth)\s*[:=]?\s*([0-9]+)'])
        width=_extract_number(q,[r'(?:휠\s*)?(?:치폭|폭|width)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        length=_extract_number(q,[r'(?:웜\s*)?(?:길이|length)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        lead=_extract_number(q,[r'(?:리드각|lead\s*angle)\s*[:=]?\s*([+-]?[0-9]+(?:\.[0-9]+)?)'])
        starts=_extract_number(q,[r'(?:줄수|시작수|thread\s*starts?|starts?)\s*[:=]?\s*([0-9]+)'])
        bore=_extract_number(q,[r'(?:휠\s*)?(?:보어|축구멍|bore)[^0-9]{0,12}(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)'])
        if None not in (module,teeth,width,length,lead,starts):
            args={'kind':'worm_pair','module':module,'teeth':int(teeth),'width_mm':width,'bore_diameter_mm':0.0 if bore is None else bore,
                  'pressure_angle_deg':20.0,'helix_angle_deg':0.0,'backlash_mm':0.0,'clearance_mm':0.0,'rim_width_mm':None,'cone_angle_deg':None,
                  'length_mm':length,'height_mm':None,'lead_angle_deg':lead,'thread_starts':int(starts),'sun_teeth':None,'planet_teeth':None,'planet_count':None,
                  'replace':False,'operation':'new','name':'WormGearPair1'}
            return _standalone_creation_plan('cad_create_gear',args,'deterministic_worm_pair',q,state)

    if accel_create_intent and any(k in low for k in ('리드 스크류','리드스크류','lead screw','사다리꼴 나사','trapezoidal screw')):
        diameter=_extract_number(q,[r'(?:지름|직경|major\s*diameter)[^0-9]{0,8}(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)',r'(?:ø|Ø)\s*([0-9]+(?:\.[0-9]+)?)'])
        pitch=_extract_number(q,[r'(?:피치|pitch)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        screw_length=_extract_number(q,[r'(?:스크류|나사|screw)\s*(?:길이|length)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        nut_length=_extract_number(q,[r'(?:너트|nut)\s*(?:길이|length|L)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        starts=_extract_number(q,[r'(?:줄수|시작수|thread\s*starts?|starts?)\s*[:=]?\s*([0-9]+)'])
        angle=_extract_number(q,[r'(?:나사각|thread\s*angle|사다리꼴각)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        if None not in (diameter,pitch,screw_length,nut_length):
            args={'major_diameter_mm':diameter,'pitch_mm':pitch,'screw_length_mm':screw_length,'nut_length_mm':nut_length,'starts':1 if starts is None else int(starts),
                  'thread_angle_deg':30.0 if angle is None else angle,'nut_outer_diameter_mm':None,'clearance_mm':0.15,'right_handed':True,
                  'replace':False,'operation':'new','name':'LeadScrewNut1'}
            return _standalone_creation_plan('cad_create_lead_screw_nut',args,'deterministic_trapezoidal_lead_screw_nut',q,state)

    if accel_create_intent and any(k in low for k in ('플랜지 커플링','플랜지커플링','flange coupling')):
        bore=_extract_number(q,[r'(?:보어|축구멍|bore)[^0-9]{0,10}(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)'])
        hub=_extract_number(q,[r'(?:허브\s*)?(?:지름|직경|diameter)\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)'])
        flange=_extract_number(q,[r'(?:플랜지\s*)(?:지름|직경|diameter)\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)'])
        hub_len=_extract_number(q,[r'(?:허브\s*)(?:길이|length)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        ft=_extract_number(q,[r'(?:플랜지\s*)(?:두께|thickness)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        if None not in (bore,hub,flange,hub_len,ft):
            args={'bore_diameter_mm':bore,'hub_diameter_mm':hub,'flange_diameter_mm':flange,'hub_length_mm':hub_len,'flange_thickness_mm':ft,
                  'bolt_circle_diameter_mm':None,'bolt_count':4,'bolt_hole_diameter_mm':None,'keyway_width_mm':None,'keyway_depth_mm':None,
                  'replace':False,'operation':'new','name':'FlangeCoupling1'}
            return _standalone_creation_plan('cad_create_coupling',args,'deterministic_flange_coupling',q,state)

    if accel_create_intent and any(k in low for k in ('worm screw','worm shaft','웜 스크류','웜스크류','웜 샤프트','웜샤프트')):
        module=_extract_number(q,[r'모듈(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', r'module(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)',r'\bm\s*=\s*([0-9]+(?:\.[0-9]+)?)'])
        lead=_extract_number(q,[r'(?:리드각|lead\s*angle)\s*[:=]?\s*([+-]?[0-9]+(?:\.[0-9]+)?)'])
        starts=_extract_number(q,[r'(?:줄수|시작수|thread\s*starts?|starts?)\s*[:=]?\s*([0-9]+)'])
        length=_extract_number(q,[r'(?:길이|length)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?'])
        if None not in (module,lead,starts,length):
            args={'kind':'worm','module':module,'teeth':None,'width_mm':None,'bore_diameter_mm':0.0,'pressure_angle_deg':20.0,'helix_angle_deg':0.0,
                  'backlash_mm':0.0,'clearance_mm':0.0,'rim_width_mm':None,'cone_angle_deg':None,'length_mm':length,'height_mm':None,'lead_angle_deg':lead,
                  'thread_starts':int(starts),'sun_teeth':None,'planet_teeth':None,'planet_count':None,'replace':False,'operation':'new','name':'Worm1'}
            return _standalone_creation_plan('cad_create_gear',args,'deterministic_worm',q,state)

    if accel_create_intent and any(k in low for k in ('압축 스프링','압축스프링','compression spring')):
        wire=_extract_number(q,[r'(?:선경|와이어\s*지름|wire\s*diameter)\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)'])
        mean=_extract_number(q,[r'(?:평균\s*지름|평균경|mean\s*diameter)\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)'])
        free=_extract_number(q,[r'(?:자유장|자유\s*길이|free\s*length)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        turns=_extract_number(q,[r'(?:유효\s*)?(?:권수|감김수|turns?|coils?)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        if None not in (wire,mean,free,turns):
            args={'kind':'compression','wire_diameter_mm':wire,'mean_diameter_mm':mean,'free_length_mm':free,'active_turns':turns,'right_handed':True,
                  'outer_diameter_mm':None,'inner_diameter_mm':None,'thickness_mm':None,'free_height_mm':None,'replace':False,'operation':'new','name':'CompressionSpring1'}
            return _standalone_creation_plan('cad_create_spring',args,'deterministic_compression_spring',q,state)

    if accel_create_intent and any(k in low for k in ('v풀리','v 풀리','v-pulley','v pulley')):
        pitch_d=_extract_number(q,[r'(?:피치\s*지름|피치경|pitch\s*diameter)\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)'])
        width=_extract_number(q,[r'(?:폭|너비|width)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        grooves=_extract_number(q,[r'(?:홈\s*수|grooves?)\s*[:=]?\s*([0-9]+)'])
        bore=_extract_number(q,[r'(?:보어|축구멍|bore)[^0-9]{0,12}(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)'])
        if pitch_d is not None and width is not None:
            args={'pitch_diameter_mm':pitch_d,'width_mm':width,'groove_count':1 if grooves is None else int(grooves),'groove_angle_deg':40.0,
                  'groove_depth_mm':None,'groove_pitch_mm':None,'bore_diameter_mm':0.0 if bore is None else bore,'replace':False,'operation':'new','name':'VPulley1'}
            return _standalone_creation_plan('cad_create_v_pulley',args,'deterministic_v_pulley',q,state)

    if creation_shortcut_allowed and any(k in low for k in ('스퍼 기어', '스퍼기어', 'spur gear', 'spur-gear')):
        module = _extract_number(q, [r'모듈(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', r'module(?:\s*\(\s*m\s*\))?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', r'\bm\s*=\s*([0-9]+(?:\.[0-9]+)?)'])
        teeth = _extract_number(q, [r'(?:이빨\s*수|잇수)(?:\s*\(\s*z\s*\))?\s*[:=]?\s*([0-9]+)', r'(?:teeth|tooth)\s*[:=]?\s*([0-9]+)', r'([0-9]+)\s*(?:teeth|tooth)', r'([0-9]+)\s*(?:teeth|tooth)', r'(?:teeth|tooth)\s*[:=]?\s*([0-9]+)', r'\bz\s*=\s*([0-9]+)'])
        thickness = _extract_number(q, [r'(?:이너비(?:\s*\(\s*두께\s*\))?|두께|치폭|face\s*width)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?'])
        bore = _extract_number(q, [r'(?:중심\s*)?(?:축구멍|축\s*구멍|보어|bore)[^0-9]{0,12}(?:지름|직경|diameter)?\s*[:=]?\s*(?:ø|Ø)?\s*([0-9]+(?:\.[0-9]+)?)', r'(?:ø|Ø)\s*([0-9]+(?:\.[0-9]+)?)'])
        pressure = _extract_number(q, [r'압력각\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', r'pressure\s*angle\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)'])
        if module is not None and teeth is not None and thickness is not None:
            args: dict[str, Any] = {
                'module': module,
                'teeth': int(teeth),
                'thickness_mm': thickness,
                'bore_diameter_mm': 0.0 if bore is None else bore,
                'pressure_angle_deg': 20.0 if pressure is None else pressure,
                'backlash_mm': 0.0,
                'replace': False,
                'name': 'SpurGear1',
            }
            return _standalone_creation_plan('cad_create_spur_gear',args,'deterministic_spur_gear',q,state)
    return None


class PlanExecutor:
    """Executes ipt-mcp-shaped calls against the exact open document in-process.

    The embedded UI therefore does not depend on a nested stdio MCP child process.
    External MCP compatibility remains available through the normal server entrypoint.
    """

    def __init__(self, engine, target_id: str, target_info: dict[str, Any] | None = None):
        self.engine = engine
        self.target_id = str(target_id)
        # In the desktop UI the execution host is created in this same process.  Pass
        # its live descriptor directly so startup does not depend on rediscovering the
        # current process from ~/.standalonecad-mcp/host-*.json on Windows.
        self.ipt = McpServer('inventor', target_id=self.target_id, target_info=target_info)
        # Strict upstream-compatible surface stays at 58 tools.  Inventor-style joint
        # extensions live in a separate server instance so the compatibility surface is
        # never mutated, while the embedded planner can still use the added assembly API.
        self.ipt_assembly = McpServer('inventor', target_id=self.target_id, target_info=target_info, assembly_extensions=True)
        self.native = McpServer('cad', target_id=self.target_id, extensions=True, target_info=target_info)
        base_rows = make_tools('inventor')
        extended_rows = make_tools('inventor', assembly_extensions=True)
        base_names = {row['name'] for row in base_rows}
        self.assembly_extension_names = {row['name'] for row in extended_rows if row['name'] not in base_names}
        self.schemas: dict[str, dict[str, Any]] = {}
        for row in base_rows + [r for r in extended_rows if r['name'] in self.assembly_extension_names] + make_tools('cad', extensions=True):
            self.schemas[row['name']] = row['inputSchema']

    def health_check(self) -> dict[str, Any]:
        init = self.ipt.dispatch({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'CADia','version':'1'}}})
        if 'error' in init:
            raise RuntimeError(f'CAD control initialize failed: {init["error"]}')
        listing = self.ipt.dispatch({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
        tools = (listing.get('result') or {}).get('tools') or []
        if len(tools) != 58:
            raise RuntimeError(f'CAD control surface mismatch: expected 58 tools, got {len(tools)}')
        health = self._dispatch('inventor_health', {})
        if not health.get('ok', False):
            raise RuntimeError('CAD target health check failed')
        return {'ok': True, 'tools': len(tools), 'target_id': self.target_id, 'health': health}

    _BIND_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,63}$')
    _RESULT_OCC_RE = re.compile(r'^\$result\[(\d+)\]\.occurrence_name$')
    _BIND_OCC_RE = re.compile(r'^\$bind\.([A-Za-z_][A-Za-z0-9_-]{0,63})\.occurrence_name$')

    @staticmethod
    def _iter_runtime_reference_strings(value: Any, path: str):
        if isinstance(value, str):
            if value.strip().startswith('$result[') or value.strip().startswith('$bind.'):
                yield path, value.strip()
            return
        if isinstance(value, list):
            for idx, item in enumerate(value):
                yield from PlanExecutor._iter_runtime_reference_strings(item, f'{path}[{idx}]')
            return
        if isinstance(value, dict):
            for key, item in value.items():
                yield from PlanExecutor._iter_runtime_reference_strings(item, f'{path}.{key}')

    def _validate_runtime_reference_contract(self, calls: list[dict[str, Any]]) -> None:
        """Preflight runtime-result references before any CAD mutation.

        Autodesk's ComponentOccurrences.Add returns a ComponentOccurrence object and callers
        then work with that returned occurrence.  Our planner keeps the same object-result
        semantics through stable named bindings.  Legacy $result[N].occurrence_name remains
        supported for backward compatibility, but it must actually point at an earlier
        inventor_place_occurrence call.
        """
        binds: dict[str, tuple[int, str]] = {}
        for idx, call in enumerate(calls, 1):
            bind = call.get('bind')
            if bind is not None:
                if not isinstance(bind, str) or not self._BIND_NAME_RE.fullmatch(bind):
                    raise ValueError(
                        f'calls[{idx-1}].bind: expected a unique identifier matching '
                        '[A-Za-z_][A-Za-z0-9_-]{0,63}'
                    )
                if bind in binds:
                    prev_idx, _ = binds[bind]
                    raise ValueError(f'calls[{idx-1}].bind: duplicate runtime binding {bind!r}; first defined at call {prev_idx}')
                binds[bind] = (idx, str(call.get('tool') or ''))

            for path, ref in self._iter_runtime_reference_strings(call.get('arguments') or {}, f'calls[{idx-1}].arguments'):
                m = self._RESULT_OCC_RE.fullmatch(ref)
                if m:
                    target = int(m.group(1))
                    if target >= idx:
                        raise ValueError(f'{path}: runtime reference must point to an earlier call: {ref}')
                    if target < 1 or target > len(calls):
                        raise ValueError(f'{path}: runtime result reference is out of range: {ref}')
                    producer = str(calls[target-1].get('tool') or '')
                    if producer != 'inventor_place_occurrence':
                        raise ValueError(
                            f'{path}: {ref} points to call {target} ({producer}), which does not produce occurrence_name. '
                            'Bind the inventor_place_occurrence call and use $bind.<name>.occurrence_name.'
                        )
                    continue
                m = self._BIND_OCC_RE.fullmatch(ref)
                if m:
                    name = m.group(1)
                    row = binds.get(name)
                    if row is None or row[0] >= idx:
                        raise ValueError(f'{path}: unknown or not-yet-available runtime binding: {ref}')
                    producer_idx, producer = row
                    if producer != 'inventor_place_occurrence':
                        raise ValueError(
                            f'{path}: binding {name!r} comes from call {producer_idx} ({producer}), '
                            'which does not produce occurrence_name.'
                        )
                    continue
                raise ValueError(f'{path}: unsupported runtime reference syntax: {ref}')

    def validate_plan(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(plan, dict):
            raise ValueError('Planner output must be a JSON object')
        calls = plan.get('calls')
        if not isinstance(calls, list) or not calls:
            raise ValueError('Planner returned no CAD calls')
        if len(calls) > 80:
            raise ValueError('Planner returned too many CAD calls')
        out: list[dict[str, Any]] = []
        for idx, call in enumerate(calls):
            if not isinstance(call, dict):
                raise ValueError(f'calls[{idx}] must be an object')
            name = str(call.get('tool') or '')
            args = call.get('arguments') or {}
            if name not in self.schemas:
                raise ValueError(f'Unknown CAD tool in plan: {name}')
            if not isinstance(args, dict):
                raise ValueError(f'{name}: arguments must be an object')
            _validate_value(self.schemas[name], args, f'{name}.arguments')
            row={'tool': name, 'arguments': args}
            if 'bind' in call:
                row['bind']=call.get('bind')
            out.append(row)
        self._validate_runtime_reference_contract(out)
        return out

    @staticmethod
    def _short(name: str) -> str:
        if name.startswith('inventor_'):
            return name[len('inventor_'):]
        if name.startswith('cad_'):
            return name[len('cad_'):]
        return name

    def _assembly_occurrence_names(self) -> set[str]:
        # Runtime-only helper for plans generated before occurrence instance names are
        # known.  This does not alter the MCP contract or the stored assembly names.
        with self.engine.lock:
            doc = self.engine.doc
            if doc is None or getattr(doc, 'doc_type', None) != 'assembly':
                return set()
            return set(getattr(doc, 'occurrences', {}) or {})

    @staticmethod
    def _occurrence_identity(name: str) -> tuple[str, int]:
        # Stored component files use `.scad.json`, while Inventor-style plans can refer
        # to an instance as `Component:1`. Normalize these forms only for lookup.
        m = re.fullmatch(r'(.*?)(?::(\d+))?', str(name).strip())
        base = (m.group(1) if m else str(name)).strip()
        index = int(m.group(2)) if m and m.group(2) else 1
        if base.lower().endswith('.scad'):
            base = base[:-5]
        return base.casefold(), index

    def _resolve_occurrence_alias(self, value: str) -> str:
        names = self._assembly_occurrence_names()
        if value in names or not names:
            return value
        ident = self._occurrence_identity(value)
        matches = [name for name in names if self._occurrence_identity(name) == ident]
        if len(matches) == 1:
            return matches[0]
        return value

    def _resolve_runtime_refs(
        self,
        value: Any,
        completed: list[dict[str, Any]],
        bindings: dict[str, dict[str, Any]] | None = None,
        key: str | None = None,
    ) -> Any:
        # Stable named bindings mirror object-return semantics: a place-occurrence call can
        # be tagged with `bind`, and later calls consume the actual returned occurrence
        # name via `$bind.<name>.occurrence_name`.  Legacy positional $result[N] references
        # remain supported without changing their behavior.
        bindings = bindings or {}
        if isinstance(value, str):
            raw=value.strip()
            m = self._BIND_OCC_RE.fullmatch(raw)
            if m:
                name=m.group(1)
                result=bindings.get(name) or {}
                occ=result.get('occurrence_name') if isinstance(result,dict) else None
                if not occ:
                    raise ValueError(f'Runtime binding does not contain occurrence_name: {value}')
                return str(occ)
            m = self._RESULT_OCC_RE.fullmatch(raw)
            if m:
                idx = int(m.group(1)) - 1
                if idx < 0 or idx >= len(completed):
                    raise ValueError(f'Runtime result reference is not available yet: {value}')
                result = completed[idx].get('result') or {}
                occ = result.get('occurrence_name') if isinstance(result, dict) else None
                if not occ:
                    raise ValueError(f'Runtime result does not contain occurrence_name: {value}')
                return str(occ)
            if key in {'occurrence', 'occurrence_name', 'a_occurrence', 'b_occurrence'}:
                return self._resolve_occurrence_alias(value)
            return value
        if isinstance(value, list):
            out = [self._resolve_runtime_refs(v, completed, bindings, None) for v in value]
            if key == 'occurrences':
                return [self._resolve_occurrence_alias(v) if isinstance(v, str) else v for v in out]
            return out
        if isinstance(value, dict):
            return {k: self._resolve_runtime_refs(v, completed, bindings, k) for k, v in value.items()}
        return value

    def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in self.assembly_extension_names:
            server = self.ipt_assembly
        elif name.startswith('inventor_'):
            server = self.ipt
        else:
            server = self.native
        res = server.dispatch({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})
        if 'error' in res:
            raise RuntimeError(str(res['error']))
        payload = res.get('result') or {}
        if payload.get('isError'):
            structured = payload.get('structuredContent') or {}
            err = structured.get('error') if isinstance(structured, dict) else None
            msg = (err or {}).get('message') if isinstance(err, dict) else None
            raise RuntimeError(msg or 'CAD tool failed')
        structured = payload.get('structuredContent')
        return structured if isinstance(structured, dict) else {'ok': True}

    def _snapshot(self) -> dict[str, Any]:
        with self.engine.lock:
            return self.engine._workspace_snapshot()

    def _rollback(self, snap: dict[str, Any]) -> None:
        with self.engine.lock:
            self.engine._restore_workspace(snap,bump_revision=True)

    @staticmethod
    def _emit_progress(on_event, message: str, percent: int | None = None):
        if on_event:
            payload={'message':message}
            if percent is not None:payload['percent']=max(0,min(100,int(percent)))
            on_event('progress',payload)

    def execute(self, plan: dict[str, Any], on_event=None, progress_span: tuple[int,int] = (25,90)) -> PlanResult:
        calls = self.validate_plan(plan)
        snap = self._snapshot()
        before = int(self.engine.revision)
        results: list[dict[str, Any]] = []
        bindings: dict[str, dict[str, Any]] = {}
        expects_revision = any(self._short(c['tool']) not in NON_REVISION_COMMANDS for c in calls)
        try:
            start,end=progress_span
            span=max(1,end-start)
            for idx, call in enumerate(calls, 1):
                pct=start + int(span * (idx-1) / max(1,len(calls)))
                self._emit_progress(on_event,f'Modeling… {idx}/{len(calls)}',pct)
                runtime_args = self._resolve_runtime_refs(call['arguments'], results, bindings)
                result = self._dispatch(call['tool'], runtime_args)
                row={'tool': call['tool'], 'result': result}
                bind=call.get('bind')
                if bind:
                    row['bind']=bind
                    bindings[str(bind)]=result if isinstance(result,dict) else {}
                results.append(row)
                pct_done=start + int(span * idx / max(1,len(calls)))
                self._emit_progress(on_event,f'Modeling step complete · {idx}/{len(calls)}',pct_done)
            self._emit_progress(on_event,'Validating model result…',min(98,end+2))
            after = int(self.engine.revision)
            if expects_revision and after == before:
                raise RuntimeError('CAD command finished without changing the current document')
            msg = self._summary(results, before, after)
            return PlanResult(True, msg, calls, results, None, before, after)
        except Exception as exc:
            self._rollback(snap)
            return PlanResult(False, '', calls, results, str(exc), before, int(self.engine.revision))


    def execute_stepwise(self, plan: dict[str, Any], on_event=None, progress_span: tuple[int,int] = (25,90)) -> PlanResult:
        """Execute a plan with per-call savepoints while preserving successful prefix work.

        Unlike :meth:`execute`, this method does not roll the whole plan back when a later
        call fails. Only the failing call is rolled back, so a controller can inspect the
        real intermediate CAD state and ask the planner for a continuation. The caller is
        responsible for taking an outer snapshot if it needs all-or-nothing fallback.
        """
        calls = self.validate_plan(plan)
        before = int(self.engine.revision)
        results: list[dict[str, Any]] = []
        bindings: dict[str, dict[str, Any]] = {}
        expects_revision = any(self._short(c['tool']) not in NON_REVISION_COMMANDS for c in calls)
        start, end = progress_span
        span = max(1, end - start)

        for idx, call in enumerate(calls, 1):
            step_snapshot = self._snapshot()
            try:
                pct = start + int(span * (idx - 1) / max(1, len(calls)))
                self._emit_progress(on_event, f'Modeling… {idx}/{len(calls)}', pct)
                runtime_args = self._resolve_runtime_refs(call['arguments'], results, bindings)
                result = self._dispatch(call['tool'], runtime_args)
                row = {'tool': call['tool'], 'result': result}
                bind = call.get('bind')
                if bind:
                    row['bind'] = bind
                    bindings[str(bind)] = result if isinstance(result, dict) else {}
                results.append(row)
                pct_done = start + int(span * idx / max(1, len(calls)))
                self._emit_progress(on_event, f'Modeling step complete · {idx}/{len(calls)}', pct_done)
            except Exception as exc:
                try:
                    self._rollback(step_snapshot)
                except Exception:
                    pass
                return PlanResult(
                    False, '', calls, results, str(exc), before, int(self.engine.revision)
                )

        self._emit_progress(on_event, 'Validating model result…', min(98, end + 2))
        after = int(self.engine.revision)
        if expects_revision and after == before:
            return PlanResult(
                False, '', calls, results,
                'CAD command finished without changing the current document', before, after,
            )
        return PlanResult(True, self._summary(results, before, after), calls, results, None, before, after)

    def _summary(self, results: list[dict[str, Any]], before: int, after: int) -> str:
        with self.engine.lock:
            doc = self.engine.doc
            if doc is None:
                return 'The document was closed. No CAD document is currently open.'
            shape = doc.shape
            volume = float(shape.Volume()) if shape is not None else 0.0
            features = len(doc.features)
            title = doc.title
        last = results[-1]['result'] if results else {}
        details: list[str] = []
        for key, label in (
            ('pitch_diameter_mm','Pitch diameter'),('outside_diameter_mm','Outside diameter'),
            ('root_diameter_mm','Root diameter'),('feature_name','Feature'),
        ):
            if key in last:
                value = last[key]
                details.append(f'{label} {value:g} mm' if isinstance(value, (int,float)) and key.endswith('_mm') else f'{label} {value}')
        base = f'{title} modeling complete · {features} feature · {volume:,.3f} mm³'
        if details:
            base += '\n' + ' · '.join(details)
        return base
