from __future__ import annotations

from types import SimpleNamespace

from standalonecad.planning import PlanResult
from standalonecad.recovery import (
    classify_failure,
    deterministic_recovery_plan,
    execute_with_failure_recovery,
)


class FakeExecutor:
    def __init__(self, results, schemas=None):
        self._results = list(results)
        self.calls = []
        self.engine = SimpleNamespace(revision=7)
        self.schemas = schemas or {}

    def execute(self, plan, on_event=None, progress_span=(25, 90)):
        self.calls.append(plan)
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeAgent:
    def __init__(self, repairs=None):
        self.repairs = list(repairs or [])
        self.calls = []

    def repair(self, user_prompt, current_state, failed_plan, error, on_event=None, recovery_context=None, progress_span=(72, 78)):
        self.calls.append({
            'user_prompt': user_prompt,
            'state': current_state,
            'failed_plan': failed_plan,
            'error': error,
            'context': recovery_context,
        })
        return self.repairs.pop(0)


def _ok(plan):
    return PlanResult(True, 'ok', plan.get('calls', []), [], None, 1, 2)


def _fail(plan, error='invalid intersection', completed=0):
    calls = plan.get('calls', [])
    results = [{'tool': c['tool'], 'result': {'ok': True}} for c in calls[:completed]]
    return PlanResult(False, '', calls, results, error, 1, 1)


def test_success_path_never_enters_recovery():
    plan = {'calls': [{'tool': 'inventor_health', 'arguments': {}}]}
    executor = FakeExecutor([_ok(plan)])
    agent = FakeAgent()
    out = execute_with_failure_recovery(
        executor=executor,
        agent=agent,
        user_prompt='health',
        state_provider=lambda: {'selection': {}},
        plan=plan,
    )
    assert out.ok
    assert len(executor.calls) == 1
    assert agent.calls == []


def test_selected_edge_reference_is_rebound_only_after_failure():
    plan = {'calls': [{'tool': 'inventor_fillet', 'arguments': {'edgeIds': ['E_bad'], 'radius': 5.0}}]}
    first = _fail(plan, 'Edge reference not found: E_bad')
    recovered = {'calls': [{'tool': 'inventor_fillet', 'arguments': {'edgeIds': ['E_selected'], 'radius': 5.0}}]}
    executor = FakeExecutor([first, _ok(recovered)])
    agent = FakeAgent()
    out = execute_with_failure_recovery(
        executor=executor,
        agent=agent,
        user_prompt='Apply R5 fillet to the selected edge',
        state_provider=lambda: {'selection': {'type': 'edge', 'edge_ref': 'E_selected'}},
        plan=plan,
    )
    assert out.ok
    assert len(executor.calls) == 2
    assert executor.calls[1]['calls'][0]['arguments']['edgeIds'] == ['E_selected']
    assert executor.calls[1]['calls'][0]['arguments']['radius'] == 5.0
    assert agent.calls == []


def test_hole_face_ambiguity_uses_requested_points_without_changing_hole_dimensions():
    plan = {'calls': [{'tool': 'inventor_hole', 'arguments': {
        'face': {'kind': 'planar', 'normal': '+Z', 'extreme': 'max'},
        'points_mm': [[10, 20, 8], [30, 20, 8]],
        'diameter_mm': 6.0,
        'through': True,
    }}]}
    result = _fail(plan, 'Face selector matched 2 faces; refine with near_mm. candidates=[]')
    executor = FakeExecutor([], schemas={})
    candidate, reasons = deterministic_recovery_plan(executor, plan, result, {'selection': {}})
    assert candidate is not None
    args = candidate['calls'][0]['arguments']
    assert args['face']['near_mm'] == [20.0, 20.0, 8.0]
    assert args['diameter_mm'] == 6.0
    assert args['points_mm'] == [[10, 20, 8], [30, 20, 8]]
    assert any('hole coordinates' in x for x in reasons)


def test_schema_type_normalization_is_failure_only_and_lossless():
    plan = {'calls': [{'tool': 'inventor_fillet', 'arguments': {'edgeIds': ['E1'], 'radius': '5'}}]}
    schema = {
        'type': 'object',
        'properties': {
            'edgeIds': {'type': 'array', 'items': {'type': 'string'}},
            'radius': {'type': 'number'},
        },
        'required': ['edgeIds', 'radius'],
        'additionalProperties': False,
    }
    result = _fail(plan, 'inventor_fillet.arguments.radius: expected number')
    executor = FakeExecutor([], schemas={'inventor_fillet': schema})
    candidate, _ = deterministic_recovery_plan(executor, plan, result, {'selection': {}})
    assert candidate['calls'][0]['arguments']['radius'] == 5.0
    assert candidate['calls'][0]['arguments']['edgeIds'] == ['E1']


def test_bounded_ai_repair_uses_failure_context_and_second_attempt_can_recover():
    original = {'calls': [{'tool': 'inventor_extrude', 'arguments': {'sketchName': 'Sketch1', 'distance': 10, 'operation': 'cut', 'direction': 'positive'}}]}
    repair1 = {'calls': [{'tool': 'inventor_extrude', 'arguments': {'sketchName': 'Sketch1', 'distance': 10, 'operation': 'cut', 'direction': 'negative'}}]}
    repair2 = {'calls': [{'tool': 'inventor_extrude', 'arguments': {'sketchName': 'Sketch1', 'distance': 10, 'operation': 'cut', 'direction': 'symmetric'}}]}
    executor = FakeExecutor([
        _fail(original, 'invalid intersection'),
        _fail(repair1, 'empty boolean result'),
        _ok(repair2),
    ])
    agent = FakeAgent([repair1, repair2])
    out = execute_with_failure_recovery(
        executor=executor,
        agent=agent,
        user_prompt='Cut 10 mm from the existing sketch',
        state_provider=lambda: {'selection': {}, 'document': {'has_geometry': True}},
        plan=original,
        max_ai_repairs=2,
    )
    assert out.ok
    assert len(agent.calls) == 2
    assert agent.calls[0]['context']['failure_class'] == 'boolean_intersection'
    assert agent.calls[0]['context']['rules'][0].startswith('The failed plan was rolled back atomically')
    assert agent.calls[1]['context']['recovery_history']


def test_recovery_is_bounded_and_reports_exhaustion():
    original = {'calls': [{'tool': 'inventor_extrude', 'arguments': {'sketchName': 'Sketch1', 'distance': 10}}]}
    r1 = {'calls': [{'tool': 'inventor_extrude', 'arguments': {'sketchName': 'Sketch2', 'distance': 10}}]}
    r2 = {'calls': [{'tool': 'inventor_extrude', 'arguments': {'sketchName': 'Sketch3', 'distance': 10}}]}
    executor = FakeExecutor([
        _fail(original, 'Sketch1 not found'),
        _fail(r1, 'Sketch2 not found'),
        _fail(r2, 'Sketch3 not found'),
    ])
    agent = FakeAgent([r1, r2])
    out = execute_with_failure_recovery(
        executor=executor,
        agent=agent,
        user_prompt='extrude',
        state_provider=lambda: {'selection': {}},
        plan=original,
        max_ai_repairs=2,
    )
    assert not out.ok
    assert len(agent.calls) == 2
    assert len(executor.calls) == 3
    assert 'failure-only recovery exhausted' in (out.error or '')


def test_failure_classifier_common_categories():
    assert classify_failure('Face reference could not be rebound after topology update') == 'topology_reference'
    assert classify_failure('invalid intersection') == 'boolean_intersection'
    assert classify_failure('sketch is not closed') == 'sketch'
    assert classify_failure('fillet failed') == 'fillet_chamfer'
