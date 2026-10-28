from app import execution, services, trial
from tests.test_a13_capabilities import (
    NATIVE_CAPS, TEXT_TOOL_CAPS, ScriptedAdapter, make_request, package, select_response,
)


def test_new_execution_keeps_tool_responses_and_summary(package):
    root, manifest = package
    request = make_request(root, manifest, fixtures=[
        {'name': 'one', 'match': {}, 'response': None},
        {'name': 'two', 'match': {}, 'response': {'password': 'hidden', 'value': 3}},
    ])
    request['model_config'] = {'api_key': 'private-key-value'}
    adapter = ScriptedAdapter([
        select_response('task_query'),
        {'tool_calls': [ScriptedAdapter.tool('call_tool', {'name': 'one', 'arguments': {}}, 'b1'),
                        ScriptedAdapter.tool('call_tool', {'name': 'two', 'arguments': {}}, 'b2')]},
        {'tool_calls': [ScriptedAdapter.tool('finish', {'action': 'tool_call', 'selected_skills': ['task_query'],
                                                        'summary': 'done private-key-value'}, 'end')]},
    ], capabilities=NATIVE_CAPS)
    actual = execution.run_case(request, adapter)
    assert actual['error'] is None
    assert actual['final_response'] == 'done ***'
    assert actual['tool_calls'] == [{'name': 'one', 'arguments': {}}, {'name': 'two', 'arguments': {}}]
    assert actual['tool_results'] == [{'name': 'one', 'arguments': {}, 'response': None},
                                      {'name': 'two', 'arguments': {}, 'response': {'password': '***', 'value': 3}}]
    assert trial._side(actual, None)['tool_results'] == actual['tool_results']


def event(seq, phase, repeat, response, variant='main'):
    return {'seq': seq, 'case_id': 'c', 'stage': 'retest', 'repeat_index': repeat,
            'variant': variant, 'type': 'tool_result',
            'payload': {'tool': 'edit', 'arguments': {'n': seq}, 'response': response}}


def done(seq, phase, repeat):
    return {'seq': seq, 'case_id': 'c', 'stage': 'retest', 'repeat_index': repeat,
            'type': 'case_completed', 'payload': {'phase': phase}}


def test_old_events_are_partitioned_by_phase_repeat_and_variant():
    events = [event(1, 'validation_baseline', 1, None), done(2, 'validation_baseline', 1),
              event(3, 'validation_candidate', 1, 'candidate'), event(4, 'validation_candidate', 1, 'other', 'other'),
              done(5, 'validation_candidate', 1), event(6, 'validation_candidate', 2, 'second'),
              done(7, 'validation_candidate', 2)]
    base = {'case_id': 'c', 'phase': 'validation_baseline', 'variant': 'main', 'repeat_index': 1, 'actual': {}}
    candidate = {**base, 'phase': 'validation_candidate'}
    assert services._side(base, events)['tool_results'][0]['response'] is None
    assert [x['response'] for x in services._side(candidate, events)['tool_results']] == ['candidate']
    assert [x['response'] for x in services._side({**candidate, 'repeat_index': 2}, events)['tool_results']] == ['second']
    assert services._side(candidate, events[:4])['tool_results'] == []
    embedded = {**candidate, 'actual': {'events': [event(9, '', 1, {'secret': 'bad'})], 'raw_output': 'hello'}}
    side = services._side(embedded, events)
    assert side['final_response'] == 'hello'
    assert side['tool_results'][0]['response'] == {'secret': '***'}


def test_case_detail_paginates_and_selects_validation_sides(monkeypatch):
    events = [{'seq': i, 'case_id': 'other'} for i in range(1, 601)]
    events += [{'seq': i, 'case_id': 'c', 'stage': 'preflight', 'type': 'info'} for i in range(1, 601)]
    events.sort(key=lambda e: e['seq'])
    events += [event(601, '', 1, 'base'), done(602, 'validation_baseline', 1),
               event(603, '', 1, 'candidate'), done(604, 'validation_candidate', 1)]
    rows = [{'case_id': 'c', 'phase': phase, 'variant': 'main', 'repeat_index': repeat, 'actual': {}}
            for phase, repeat in [('validation_candidate', 2), ('validation_candidate', 1), ('validation_baseline', 1)]]
    monkeypatch.setattr(services.persistence, 'get_run', lambda _: {'dataset_id': 'd', 'environment_id': 'e', 'mode': 'validate'})
    monkeypatch.setattr(services.persistence, 'get_case', lambda *_: {'case_id': 'c'})
    monkeypatch.setattr(services.persistence, 'get_case_results_for_run', lambda _: rows)
    monkeypatch.setattr(services.persistence, 'get_environment', lambda _: {})
    def list_events(_, after_seq=0, limit=200, case_ids=None):
        return [e for e in events if e['seq'] > after_seq and e['case_id'] in case_ids][:limit]
    monkeypatch.setattr(services.persistence, 'list_events', list_events)
    detail = services.run_case_detail('r', 'c')
    assert detail['baseline']['tool_results'][0]['response'] == 'base'
    assert detail['candidate']['tool_results'][0]['response'] == 'candidate'
    assert detail['candidate']['repeat_index'] == 1
    assert len(detail['events']) == 604


def test_text_execution_keeps_response_and_summary(package):
    root, manifest = package
    content = ('依据：查询\n子技能：task_query\n工具：list_tasks {"project_id":"P123","status":"in_progress"}\n'
               '结束：{"action":"tool_call","selected_skills":["task_query"],"missing_fields":[],"summary":"已查询"}')
    actual = execution.run_case(make_request(root, manifest),
                                ScriptedAdapter([{'content': content}], capabilities=TEXT_TOOL_CAPS))
    assert actual['error'] is None
    assert actual['final_response'] == '已查询'
    assert actual['tool_results'][0]['name'] == 'list_tasks'
    assert 'response' in actual['tool_results'][0]


def test_historical_offloaded_finish_summary_uses_captured_artifact(monkeypatch, tmp_path):
    artifact = tmp_path / 'response.json'
    artifact.write_text('{"tool_calls":[{"name":"finish","arguments":{"summary":"artifact result"}}]}')
    saved_event = {'seq': 42, 'type': 'model_call_finished', 'case_id': 'c',
                   'payload': {'artifacts': [{'kind': 'model_response', 'id': 'a'}]}}
    monkeypatch.setattr(services.persistence, 'get_run', lambda _: {'environment_id': 'e'})
    monkeypatch.setattr(services.persistence, 'get_event', lambda run_id, seq: saved_event if seq == 42 else None)
    monkeypatch.setattr(services.persistence, 'get_environment', lambda _: {})
    monkeypatch.setattr(services.persistence, 'get_artifact', lambda run_id, artifact_id: {'content_path': artifact})
    row = {'case_id': 'c', 'phase': 'baseline', 'actual': {'finish_protocol': 'ok', 'events': [saved_event]}}
    assert services._side(row, run_id='r')['final_response'] == 'artifact result'
    artifact.write_text('{}')
    # A legacy case-result fallback is never interpreted as this model call.
    monkeypatch.setattr(services, 'run_model_output', lambda *_: {'source': 'legacy_case_result',
                                                                  'response': {'tool_calls': [{'name': 'finish', 'arguments': {'summary': 'wrong'}}]}})
    assert services._side(row, run_id='r')['final_response'] == ''


def test_unaccepted_finish_summary_never_overrides_raw_output(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        select_response('task_query'),
        {'content': 'previous answer', 'tool_calls': [ScriptedAdapter.tool('finish', {
            'action': 'no_action', 'selected_skills': [], 'summary': 'unaccepted summary'}, 'end')]},
    ], capabilities=NATIVE_CAPS)
    actual = execution.run_case(make_request(root, manifest), adapter)
    assert actual['finish_protocol'] == 'error'
    assert actual['final_response'] == 'previous answer'
    event = {'type': 'model_call_finished', 'payload': {'response': {'tool_calls': [
        {'name': 'finish', 'arguments': {'summary': 'unaccepted summary'}}]}}}
    row = {'actual': {'finish_protocol': 'error', 'events': [event],
                      'final_response': 'unaccepted summary', 'raw_output': 'previous answer'}}
    assert services._side(row)['final_response'] == 'previous answer'
    row['actual']['finish_protocol'] = 'missing'
    row['actual'].pop('final_response')
    assert services._side(row)['final_response'] == 'previous answer'
