from __future__ import annotations

from types import SimpleNamespace

from standalonecad.planning import PlanResult
from standalonecad.recovery import execute_with_failure_recovery


class FakeExecutor:
    def __init__(self, stepwise_results, legacy_result=None):
        self.stepwise_results = list(stepwise_results)
        self.legacy_result = legacy_result or PlanResult(True, "legacy", [], [], None, 1, 2)
        self.engine = SimpleNamespace(revision=1)
        self.state = {"prefix": []}
        self.stepwise_calls = []
        self.legacy_calls = []
        self.rollbacks = 0

    def _snapshot(self):
        return {"state": {"prefix": list(self.state["prefix"])}, "revision": self.engine.revision}

    def _rollback(self, snap):
        self.rollbacks += 1
        self.state = {"prefix": list(snap["state"]["prefix"])}
        self.engine.revision = snap["revision"] + 1

    def execute_stepwise(self, plan, on_event=None, progress_span=(25, 70)):
        self.stepwise_calls.append(plan)
        result, prefix_marker = self.stepwise_results.pop(0)
        if prefix_marker is not None:
            self.state["prefix"].append(prefix_marker)
        self.engine.revision += max(1, len(result.results))
        return result

    def execute(self, plan, on_event=None, progress_span=(25, 70)):
        self.legacy_calls.append(plan)
        return self.legacy_result


class FakeAgent:
    def __init__(self, continuation):
        self.continuation = continuation
        self.continue_calls = []

    def continue_after_failure(self, **kwargs):
        self.continue_calls.append(kwargs)
        return self.continuation

    def repair(self, *args, **kwargs):
        raise AssertionError("legacy repair should not be used when closed-loop succeeds")


def test_closed_loop_continues_from_real_prefix(monkeypatch):
    monkeypatch.setenv("CADIA_KERNEL_CLOSED_LOOP", "1")
    initial = {
        "calls": [
            {"tool": "a", "arguments": {}},
            {"tool": "b", "arguments": {}},
            {"tool": "c", "arguments": {}},
        ]
    }
    first = PlanResult(
        False, "", initial["calls"],
        [{"tool": "a", "result": {"ok": True}}],
        "b failed", 1, 2,
    )
    cont = {"calls": [{"tool": "b2", "arguments": {}}, {"tool": "c2", "arguments": {}}]}
    second = PlanResult(
        True, "done", cont["calls"],
        [{"tool": "b2", "result": {"ok": True}}, {"tool": "c2", "result": {"ok": True}}],
        None, 2, 4,
    )
    ex = FakeExecutor([(first, "a"), (second, "b2c2")])
    ag = FakeAgent(cont)

    out = execute_with_failure_recovery(
        executor=ex, agent=ag, user_prompt="build it",
        state_provider=lambda: {"prefix": list(ex.state["prefix"])}, plan=initial,
    )
    assert out.ok
    assert [c["tool"] for c in out.calls] == ["a", "b2", "c2"]
    assert len(ag.continue_calls) == 1
    call = ag.continue_calls[0]
    assert call["failed_call"]["tool"] == "b"
    assert [c["tool"] for c in call["remaining_calls"]] == ["c"]
    assert call["current_state"]["prefix"] == ["a"]
    assert not ex.legacy_calls


def test_closed_loop_failure_restores_then_uses_legacy(monkeypatch):
    monkeypatch.setenv("CADIA_KERNEL_CLOSED_LOOP", "1")
    initial = {"calls": [{"tool": "a", "arguments": {}}, {"tool": "b", "arguments": {}}]}
    first = PlanResult(False, "", initial["calls"], [{"tool": "a", "result": {"ok": True}}], "b failed", 1, 2)
    cont = {"calls": [{"tool": "b2", "arguments": {}}]}
    second = PlanResult(False, "", cont["calls"], [], "b2 failed", 2, 2)
    legacy = PlanResult(True, "legacy", initial["calls"], [], None, 1, 3)
    ex = FakeExecutor([(first, "a"), (second, None)], legacy_result=legacy)
    ag = FakeAgent(cont)

    out = execute_with_failure_recovery(
        executor=ex, agent=ag, user_prompt="build it",
        state_provider=lambda: {"prefix": list(ex.state["prefix"])}, plan=initial,
        max_ai_repairs=1,
    )
    assert out.ok
    assert out.message == "legacy"
    assert ex.rollbacks >= 1
    assert ex.state["prefix"] == []
    assert len(ex.legacy_calls) == 1
