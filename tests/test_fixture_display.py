import copy
import json

from app import persistence, services
from app.fixture_display import project_event
from app.fixtures import FixtureMatcher
from tests.conftest import create_demo_project, start_run


def event_for(fixtures, arguments):
    diagnosis = FixtureMatcher(fixtures).diagnose_miss('editImage', arguments)
    return {'seq': 1, 'type': 'fixture_miss', 'case_id': 'fixture_case', 'level': 'error',
            'payload': {'tool': 'editImage', 'arguments': arguments, 'issue': {
                'error_code': 'fixture_miss', 'title': '工具调用没有匹配的模拟返回',
                'message': '旧提示', 'next_action': '检查 fixture.match 的字段层级、值和数据类型。',
                'technical_detail': {'fixture_diagnosis': diagnosis}}}}


def test_argument_display_shows_both_values_without_claiming_business_expected():
    fixtures = [{'name': 'editImage', 'match': {'method': 'applyStyle'},
                 'response': {'private': 'DO_NOT_EXPOSE_RESPONSE'}}]
    event = event_for(fixtures, {'method': 'getVersion'})
    before = copy.deepcopy(event)
    result = project_event(event, {'fixtures': fixtures}, {})
    issue = result['display_issue']
    assert issue['title'] == '工具调用参数不匹配'
    assert issue['comparison_rows'] == [{'fixture_index': 0, 'field_path': '$.method',
                                        'actual': '"getVersion"', 'condition': '"applyStyle"'}]
    assert '不是评分标准答案' in issue['comparison_note']
    assert 'Skill' in issue['next_action'] and '调用符合规则' in issue['next_action']
    assert 'DO_NOT_EXPOSE_RESPONSE' not in json.dumps(result)
    assert event == before and result['payload'] == before['payload']


def test_missing_fields_nulls_types_and_multiple_conditions_are_distinct():
    fixtures = [{'name': 'editImage', 'match': {'args': {'strength': 3, 'nullable': None}}},
                {'name': 'editImage', 'match': {'args': {'strength': 4}}}]
    result = project_event(event_for(fixtures, {'args': {'strength': '3'}}), {'fixtures': fixtures}, {})
    rows = result['display_issue']['comparison_rows']
    assert rows[0]['actual'] == '"3"' and rows[0]['condition'] == '3'
    assert rows[1]['actual'] == '未提供该字段' and rows[1]['condition'] == 'null'
    assert rows[2]['fixture_index'] == 1 and rows[2]['condition'] == '4'


def test_display_redacts_condition_fields_and_known_credentials():
    fixtures = [{'name': 'editImage', 'match': {'api_key': 'secret-condition', 'method': 'SECRET_CONNECTION'}}]
    event = event_for(fixtures, {'api_key': 'secret-actual', 'method': 'getVersion'})
    issue = project_event(event, {'fixtures': fixtures}, {
        'model_config': {'api_key_ref': {'value': 'SECRET_CONNECTION'}}})['display_issue']
    text = json.dumps(issue)
    assert 'secret-condition' not in text and 'secret-actual' not in text
    assert 'SECRET_CONNECTION' not in text


def test_absent_historical_case_does_not_invent_conditions():
    event = event_for([{'name': 'editImage', 'match': {'method': 'applyStyle'}}], {'method': 'getVersion'})
    result = project_event(event, None, {})['display_issue']
    assert result['comparison_rows'] == []
    assert '无法可靠恢复' in result['comparison_note']
    assert 'applyStyle' not in json.dumps(result)


def test_other_miss_reasons_do_not_turn_into_argument_mismatches():
    for fixtures, phrase in [([], '未配置'), ([{'name': 'otherTool'}], '调用的工具')]:
        result = project_event(event_for(fixtures, {}), {'fixtures': fixtures}, {})['display_issue']
        assert phrase in result['title']
        assert not result['comparison_rows']
    preflight = {'type': 'fixture_coverage_warning', 'payload': {'error_code': 'fixture_miss'}}
    assert project_event(preflight, None, {}) == preflight


def test_history_and_issues_use_bound_dataset_not_project_current(client):
    case = {'case_id': 'fixture_case', 'query': '应用图片样式', 'split': 'optimize',
            'expected': {'action': 'tool_call', 'routes': [['task_query']],
                         'tool_calls': [{'name': 'editImage', 'arguments': {'method': 'applyStyle'}}]},
            'fixtures': [{'name': 'editImage', 'match': {'method': 'applyStyle'}, 'response': {'ok': True}}]}
    demo = create_demo_project(dataset_content=json.dumps(case))
    run = start_run(demo, 'baseline')
    template = event_for(case['fixtures'], {'method': 'getVersion'})
    event = persistence.append_event(run['id'], 'fixture_miss', case_id=case['case_id'],
                                     level='error', stage='baseline_eval', payload=template['payload'])
    before_run = persistence.get_run(run['id'])
    before_event = persistence.get_event(run['id'], event['seq'])
    changed = copy.deepcopy(case)
    changed['fixtures'][0]['match']['method'] = 'OTHER_VERSION'
    services.create_dataset(demo['project']['id'], {'filename': 'new.jsonl', 'content': json.dumps(changed),
                            'split_config': {'optimize': 1, 'validation': 0, 'final_test': 0}})
    issues = client.get(f"/api/v3/runs/{run['id']}/issues").json()
    assert issues['items'][0]['comparison_rows'][0]['condition'] == '"applyStyle"'
    assert issues['items'][0]['comparison_rows'][0]['expected'] == '"applyStyle"'
    history = client.get(f"/api/v3/runs/{run['id']}/event-history").json()['items']
    projected = next(row for row in history if row['seq'] == event['seq'])
    assert projected['display_issue']['comparison_rows'] == issues['items'][0]['comparison_rows']
    assert projected['payload'] == before_event['payload']
    assert persistence.get_event(run['id'], event['seq']) == before_event
    assert persistence.get_run(run['id']) == before_run


def test_large_differences_are_bounded():
    fixtures = [{'name': 'editImage', 'match': {f'field{i}': 'x' * 1000 for i in range(20)}}]
    issue = project_event(event_for(fixtures, {}), {'fixtures': fixtures}, {})['display_issue']
    assert len(issue['comparison_rows']) == 8
    assert all(len(row['condition']) < 350 for row in issue['comparison_rows'])


def test_subtree_scoring_does_not_invent_outer_method_expectation():
    fixtures = [{'name': 'editImage', 'match': {'method': 'applyStyle', 'args': {'level': 3}}}]
    case = {'fixtures': fixtures, 'expected': {'tool_calls': [
        {'name': 'editImage', 'arguments_path': 'args', 'arguments': {'level': 3}}]}}
    event = event_for(fixtures, {'method': 'getVersion', 'args': {'level': 4}})
    rows = project_event(event, case, {})['display_issue']['comparison_rows']
    assert 'expected' not in next(row for row in rows if row['field_path'] == '$.method')
    assert next(row for row in rows if row['field_path'] == '$.args.level')['expected'] == '3'


def test_alternative_methods_are_listed_without_guessing_which_call_failed():
    fixtures = [{'name': 'editImage', 'match': {'method': 'applyStyle'}}]
    case = {'fixtures': fixtures, 'expected': {'tool_calls': [
        {'name': 'editImage', 'arguments': {'method': 'applyStyle'}}], 'variants': [
        {'tool_calls': [{'name': 'editImage', 'arguments': {'method': 'applyTone'}}]}]}}
    row = project_event(event_for(fixtures, {'method': 'getVersion'}), case, {})['display_issue']['comparison_rows'][0]
    assert 'applyStyle' in row['expected'] and 'applyTone' in row['expected']
    assert '不同调用/备选路线' in row['expected']
