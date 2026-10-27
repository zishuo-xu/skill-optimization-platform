"""Finish protocol must not override independently observed business results."""
import pytest
from app import services, trial
from app.evaluation import score_case
from app.execution import run_case
from app.errors import ValidationError
from tests.test_a13_capabilities import (
    package, ScriptedAdapter, NATIVE_CAPS, make_request,
    select_response, finish_response,
)
from tests.conftest import environment_payload

CALL = {'name': 'list_tasks', 'arguments': {'project_id': 'P123', 'status': 'in_progress'}}
EXPECTED = {'action': 'tool_call', 'routes': [['task_query']], 'tool_calls': [CALL]}


def execute(package, ending=None, strict=False):
    root, manifest = package
    request = make_request(root, manifest, loader_config={'mode': 'on_demand', 'strict_finish': strict})
    adapter = ScriptedAdapter([
        select_response('task_query'),
        {'tool_calls': [ScriptedAdapter.tool('call_tool', {'name': CALL['name'], 'arguments': CALL['arguments']}, 'business')]},
        ending or {'content': '查询完成'},
    ], capabilities=NATIVE_CAPS)
    return run_case(request, adapter)


def test_business_scoring_and_both_api_sides_preserve_warning(package):
    actual = execute(package)
    score = score_case(EXPECTED, actual)
    assert score['passed'] and score['finish_protocol'] == 'missing'
    wrong = dict(EXPECTED, tool_calls=[dict(CALL, arguments={'project_id': 'OTHER', 'status': 'in_progress'})])
    assert not score_case(wrong, actual)['passed']
    assert trial._side(actual, score)['finish_protocol'] == 'missing'
    assert services._side(dict(actual, actual=actual, scores=score))['finish_protocol'] == 'missing'
    assert trial._side(actual, None)['finish_protocol'] == 'missing'


@pytest.mark.parametrize('action', ['no_action', 'clarify'])
def test_ambiguous_end_cannot_become_no_action(package, action):
    root, manifest = package
    actual = run_case(make_request(root, manifest), ScriptedAdapter([{'content': '好的'}], capabilities=NATIVE_CAPS))
    score = score_case({'action': action, 'routes': [[]], 'tool_calls': []}, actual)
    assert actual['action'] == 'undetermined'
    assert not score['passed'] and not score['action_ok']


@pytest.mark.parametrize('strict,ending,code', [
    (True, {'content': '完成'}, 'protocol_error'),
    (False, {'content': '未完', 'finish_reason': 'length'}, 'truncated'),
    (False, dict(finish_response(), finish_reason='length'), 'truncated'),
    (False, finish_response(selected=()), 'protocol_error'),
])
def test_hard_failures_remain_failures_with_partial_evidence(package, strict, ending, code):
    actual = execute(package, ending, strict)
    assert actual['error']['code'] == code
    assert actual['action'] == 'tool_call'
    assert not score_case(EXPECTED, actual)['passed']


def test_environment_freezes_boolean_finish_policy(demo):
    env = services.create_environment(demo['project']['id'], environment_payload())
    assert env['loader_config']['strict_finish'] is False
    payload = environment_payload()
    payload['loader_config']['strict_finish'] = 'false'
    with pytest.raises(ValidationError):
        services.create_environment(demo['project']['id'], payload)


def test_legacy_baseline_fingerprint_is_incompatible():
    old = services.hashing.hash_json({'skill_version_id': 'sv', 'environment_id': 'env',
                               'dataset_id': 'ds', 'scorer_version': 'score-v1', 'repeats': 1})
    assert old != services.run_fingerprint('sv', 'env', 'ds', 'score-v1', 1)
