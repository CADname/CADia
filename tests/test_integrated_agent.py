from __future__ import annotations

import json
from pathlib import Path

from standalonecad.bridge.host import CadHost
from standalonecad.codex_agent import CodexAgent, PLANNER_PREFIX
from standalonecad.core.engine import CadEngine
from standalonecad.planning import PlanExecutor, direct_plan


ROOT = Path(__file__).resolve().parents[1]


def _fake_codex(path: Path, payload: dict):
    text = json.dumps(payload, ensure_ascii=False)
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys,pathlib\n"
        "args=sys.argv[1:]\n"
        "out=None\n"
        "for i,x in enumerate(args):\n"
        "    if x in ('--output-last-message','-o') and i+1<len(args): out=args[i+1]\n"
        "_ = sys.stdin.read()\n"
        "print(json.dumps({'type':'turn.started'}),flush=True)\n"
        "print(json.dumps({'type':'turn.completed'}),flush=True)\n"
        f"text={text!r}\n"
        "if out: pathlib.Path(out).write_text(text,encoding='utf-8')\n",
        encoding='utf-8',
    )
    path.chmod(0o755)


def test_spur_gear_direct_plan_needs_no_nested_mcp_or_codex(monkeypatch):
    monkeypatch.delenv('CODEX_CLI_PATH', raising=False)
    plan = direct_plan('Generate a spur gear with module m=2, teeth z=25, face width 20 mm, and center bore diameter 15 mm.')
    assert plan is not None
    call = plan['calls'][0]
    assert call['tool'] == 'cad_create_spur_gear'
    assert call['arguments']['module'] == 2
    assert call['arguments']['teeth'] == 25
    assert call['arguments']['thickness_mm'] == 20
    assert call['arguments']['bore_diameter_mm'] == 15


def test_codex_planner_has_no_mcp_child_configuration(tmp_path, monkeypatch):
    fake = tmp_path / 'fake_codex.py'
    payload = {
        'calls': [
            {'tool':'inventor_new_part','arguments':{'template':None}},
            {'tool':'inventor_create_sketch','arguments':{'plane':'XY'}},
            {'tool':'inventor_draw_rectangle','arguments':{'x1':-40,'y1':-30,'x2':40,'y2':30}},
            {'tool':'inventor_close_sketch','arguments':{'sketchName':'Sketch1'}},
            {'tool':'inventor_extrude','arguments':{'sketchName':'Sketch1','distance':8,'operation':'join','direction':'positive'}},
        ],
        'note':'plate',
    }
    _fake_codex(fake, payload)
    monkeypatch.setenv('CODEX_CLI_PATH', str(fake))
    agent = CodexAgent('standalonecad-1', ROOT)
    plan = agent.plan('make the planner test plate using a sketch workflow', {'document':{'type':'part'}})
    assert plan['calls'][0]['tool'] == 'inventor_new_part'
    cmd = ' '.join(agent._exec_cmd(tmp_path/'out.json'))
    assert 'mcp_servers.' not in cmd
    assert '--ignore-user-config' in cmd
    assert '--sandbox read-only' in cmd
    assert 'dangerously-bypass-approvals-and-sandbox' not in cmd


def test_inprocess_58_tool_health_and_spur_gear_execution():
    engine = CadEngine()
    host = CadHost(engine)
    host.start()
    try:
        x = PlanExecutor(engine, host.info['target_id'], target_info=host.info)
        health = x.health_check()
        assert health['ok'] and health['tools'] == 58
        before = engine.revision
        plan = direct_plan('Generate a spur gear with module 2, 25 teeth, face width 20 mm, and bore diameter 15 mm')
        out = x.execute(plan)
        assert out.ok, out.error
        assert engine.revision > before
        assert engine.doc.shape is not None
        assert engine.doc.features[-1].kind == 'spur_gear'
        derived = engine.doc.features[-1].params['derived']
        assert derived['pitch_diameter_mm'] == 50
        assert derived['outside_diameter_mm'] == 54
    finally:
        host.close()


def test_failed_multi_call_plan_rolls_back_atomically():
    engine = CadEngine()
    host = CadHost(engine)
    host.start()
    try:
        x = PlanExecutor(engine, host.info['target_id'], target_info=host.info)
        x.health_check()
        snap = engine.doc.serialize()
        plan = {
            'calls': [
                {'tool':'inventor_create_sketch','arguments':{'plane':'XY'}},
                {'tool':'inventor_extrude','arguments':{'sketchName':'DOES_NOT_EXIST','distance':5,'operation':'join','direction':'positive'}},
            ]
        }
        out = x.execute(plan)
        assert not out.ok
        restored = engine.doc.serialize()
        assert restored['sketches'] == snap['sketches']
        assert restored['features'] == snap['features']
        assert engine.doc.shape is None
    finally:
        host.close()


def test_ui_has_no_demo_shortcuts_or_product_disclaimer():
    text = (ROOT / 'src/standalonecad/ui/app.py').read_text(encoding='utf-8')
    for unwanted in ('spur gear",', 'plate",', 'fillet",', 'Autodesk Inventor', 'create and edit with natural language'):
        assert unwanted not in text
    assert 'text="AI"' in text


def test_planner_prompt_uses_ipt_contract_and_json_only():
    p = PLANNER_PREFIX.lower()
    assert 'prefer inventor_* tools' in p
    assert 'return json only' in p
    assert 'tool_catalog' in p
    assert 'current_state' in p

def test_fake_codex_plan_executes_plate_end_to_end(tmp_path, monkeypatch):
    fake = tmp_path / 'fake_codex.py'
    payload = {
        'calls': [
            {'tool':'inventor_new_part','arguments':{'template':None}},
            {'tool':'inventor_create_sketch','arguments':{'plane':'XY'}},
            {'tool':'inventor_draw_rectangle','arguments':{'x1':-40,'y1':-30,'x2':40,'y2':30}},
            {'tool':'inventor_close_sketch','arguments':{'sketchName':'Sketch1'}},
            {'tool':'inventor_extrude','arguments':{'sketchName':'Sketch1','distance':8,'operation':'join','direction':'positive'}},
        ],
        'note':'plate',
    }
    _fake_codex(fake, payload)
    monkeypatch.setenv('CODEX_CLI_PATH', str(fake))
    engine = CadEngine()
    host = CadHost(engine)
    host.start()
    try:
        agent = CodexAgent(host.info['target_id'], ROOT)
        executor = PlanExecutor(engine, host.info['target_id'], target_info=host.info)
        executor.health_check()
        plan = agent.plan('make the planner test plate using a sketch workflow', {'document':{'type':'part','revision':0}})
        out = executor.execute(plan)
        assert out.ok, out.error
        assert abs(engine.doc.shape.Volume() - 80*60*8) < 1e-6
        assert engine.revision >= 5
    finally:
        host.close()


def test_inprocess_health_does_not_depend_on_descriptor_rediscovery(monkeypatch):
    engine = CadEngine()
    host = CadHost(engine)
    host.start()
    try:
        # Reproduce the Windows startup failure mode: descriptor discovery cannot
        # return the just-created host.  The UI must still use its already-live host.
        monkeypatch.setattr('standalonecad.bridge.client.list_descriptors', lambda: [])
        x = PlanExecutor(engine, host.info['target_id'], target_info=host.info)
        health = x.health_check()
        assert health['ok'] and health['target_id'] == host.info['target_id']
        listed = x._dispatch('inventor_list_available_targets', {})
        assert [t['target_id'] for t in listed['targets']] == [host.info['target_id']]
        out = x.execute({'calls':[{'tool':'inventor_new_part','arguments':{'template':None}}]})
        assert out.ok, out.error
    finally:
        host.close()
