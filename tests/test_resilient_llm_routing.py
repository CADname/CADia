from __future__ import annotations

import pytest

import standalonecad.codex_agent as ca


def _agent():
    agent = ca.CodexAgent.__new__(ca.CodexAgent)
    agent._catalog = [
        {"name":"cad_create_box","description":"","inputSchema":{}},
        {"name":"cad_create_spur_gear","description":"","inputSchema":{}},
        {"name":"inventor_fillet","description":"","inputSchema":{}},
    ]
    return agent


def test_successful_full_request_plan_is_never_intercepted(monkeypatch):
    agent = _agent()
    expected = {
        "calls":[
            {"tool":"cad_create_box","arguments":{"length_mm":100,"width_mm":60,"height_mm":8}},
            {"tool":"cad_create_box","arguments":{"length_mm":100,"width_mm":8,"height_mm":50}},
            {"tool":"cad_create_box","arguments":{"length_mm":100,"width_mm":8,"height_mm":50}},
        ],
        "note":"compound plan",
    }
    calls = {"planner":0, "direct":0}
    def run(prompt, on_event=None, progress_span=(5,20)):
        calls["planner"] += 1
        assert "U-shaped mounting bracket" in prompt
        return expected
    def direct(*args, **kwargs):
        calls["direct"] += 1
        raise AssertionError("direct_plan must not intercept successful LLM planning")
    agent._run_planner = run
    monkeypatch.setattr(ca, "direct_plan", direct)
    monkeypatch.delenv("CADIA_PLANNER_ROUTING_MODE", raising=False)
    out = ca.CodexAgent.plan(agent, "Create a U-shaped mounting bracket with a 100 x 60 x 8 mm base and two 50 mm walls.", {})
    assert out == expected
    assert calls == {"planner":1, "direct":0}


def test_simple_generator_request_is_also_llm_first(monkeypatch):
    agent = _agent()
    expected = {"calls":[{"tool":"cad_create_spur_gear","arguments":{"module":2,"teeth":25,"width_mm":20}}],"note":"gear"}
    agent._run_planner = lambda *a, **k: expected
    monkeypatch.setattr(ca, "direct_plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must be LLM-first")))
    monkeypatch.delenv("CADIA_PLANNER_ROUTING_MODE", raising=False)
    assert ca.CodexAgent.plan(agent, "Create a spur gear, module 2, 25 teeth, width 20 mm.", {}) == expected


def test_invalid_first_planner_response_retries_before_fallback(monkeypatch):
    agent = _agent()
    good = {"calls":[{"tool":"cad_create_box","arguments":{"length_mm":10,"width_mm":20,"height_mm":30}}],"note":"retry ok"}
    state = {"n":0, "direct":0}
    def run(prompt, on_event=None, progress_span=(5,20)):
        state["n"] += 1
        if state["n"] == 1:
            return {"calls":[{"tool":"hallucinated_tool","arguments":{}}],"note":"bad"}
        assert "PLANNER_RETRY_FEEDBACK" in prompt
        return good
    def direct(*args, **kwargs):
        state["direct"] += 1
        raise AssertionError("fallback must not run if retry succeeds")
    agent._run_planner = run
    monkeypatch.setattr(ca, "direct_plan", direct)
    monkeypatch.delenv("CADIA_PLANNER_ROUTING_MODE", raising=False)
    assert ca.CodexAgent.plan(agent, "Create a block.", {}) == good
    assert state == {"n":2, "direct":0}


def test_transport_failure_retries_then_preserves_legacy_fallback(monkeypatch):
    agent = _agent()
    state = {"n":0}
    def run(*args, **kwargs):
        state["n"] += 1
        raise RuntimeError("AI transport unavailable")
    fallback = {"calls":[{"tool":"cad_create_box","arguments":{"length_mm":100,"width_mm":60,"height_mm":8}}],"note":"legacy fallback"}
    agent._run_planner = run
    monkeypatch.setattr(ca, "direct_plan", lambda *a, **k: fallback)
    monkeypatch.delenv("CADIA_PLANNER_ROUTING_MODE", raising=False)
    assert ca.CodexAgent.plan(agent, "Create a 100 x 60 x 8 mm plate.", {}) == fallback
    assert state["n"] == 2


def test_two_invalid_plans_then_preserves_legacy_fallback(monkeypatch):
    agent = _agent()
    state = {"n":0}
    def run(*args, **kwargs):
        state["n"] += 1
        return {"calls":[{"tool":"not_a_real_tool","arguments":{}}],"note":"invalid"}
    fallback = {"calls":[{"tool":"cad_create_box","arguments":{"length_mm":4,"width_mm":5,"height_mm":6}}],"note":"fallback"}
    agent._run_planner = run
    monkeypatch.setattr(ca, "direct_plan", lambda *a, **k: fallback)
    monkeypatch.delenv("CADIA_PLANNER_ROUTING_MODE", raising=False)
    assert ca.CodexAgent.plan(agent, "Create a block.", {}) == fallback
    assert state["n"] == 2


def test_user_cancel_never_retries_or_falls_back(monkeypatch):
    agent = _agent()
    state = {"n":0}
    def run(*args, **kwargs):
        state["n"] += 1
        raise RuntimeError("The modeling task was canceled by the user")
    agent._run_planner = run
    monkeypatch.setattr(ca, "direct_plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cancel must not fallback")))
    monkeypatch.delenv("CADIA_PLANNER_ROUTING_MODE", raising=False)
    with pytest.raises(RuntimeError, match="canceled"):
        ca.CodexAgent.plan(agent, "Create a cube.", {})
    assert state["n"] == 1


def test_legacy_emergency_switch_still_exists(monkeypatch):
    agent = _agent()
    fallback = {"calls":[{"tool":"cad_create_box","arguments":{"length_mm":1,"width_mm":1,"height_mm":1}}],"note":"legacy"}
    monkeypatch.setenv("CADIA_PLANNER_ROUTING_MODE", "legacy_direct_first")
    monkeypatch.setattr(ca, "direct_plan", lambda *a, **k: fallback)
    agent._run_planner = lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy switch must use direct first"))
    assert ca.CodexAgent.plan(agent, "Create a cube.", {}) == fallback


def test_prompt_contains_full_request_coverage_contract():
    assert "Read the complete USER_REQUEST" in ca.PLANNER_PREFIX
    assert "audit the proposed calls" in ca.PLANNER_PREFIX
    assert "never as keyword routers" in ca.PLANNER_PREFIX
