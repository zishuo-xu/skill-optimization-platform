import json

import pytest

from app import persistence, run_analysis, services
from app.adapters import AdapterError
from app.db import transaction


def configure(demo):
    return persistence.create_environment(demo['project']['id'], {
        **demo['environment'],
        'optimizer_config': {'provider': 'openai_http', 'model': 'analysis-test',
                             'base_url': 'http://localhost:9999/v1', 'stream': True,
                             'api_key_ref': {'value': 'PRIVATE_DIAGNOSTIC_KEY'}},
    }, set_current=True)


def url(run):
    return f"/api/v3/runs/{run['id']}/analysis"


def test_generate_persists_report_and_preserves_run(client, baseline_run, demo, monkeypatch):
    env = configure(demo)
    before = persistence.get_run(baseline_run['id'])
    cases = persistence.get_case_results_for_run(baseline_run['id'])
    events = persistence.max_event_seq(baseline_run['id'])
    seen = {}

    class Adapter:
        def invoke(self, messages, config, tools):
            seen.update(messages=messages, config=config, tools=tools)
            return {'content': '## 结论\n证据不足，需要检查网关。PRIVATE_DIAGNOSTIC_KEY',
                    'model': 'actual-analysis-model', 'usage': {'input_tokens': 123},
                    'finish_reason': 'stop'}

    monkeypatch.setattr(run_analysis, 'build_adapter', lambda config: Adapter())
    response = client.post(url(baseline_run), json={'question': '为什么优化失败？'})
    assert response.status_code == 200
    report = response.json()
    assert report['status'] == 'completed'
    assert report['metadata']['environment_id'] == env['id']
    assert report['metadata']['model'] == 'actual-analysis-model'
    assert report['metadata']['heuristic_fallback'] is False
    assert 'PRIVATE_DIAGNOSTIC_KEY' not in response.text
    assert seen['config']['stream'] is True
    assert not seen['tools']
    assert 'PRIVATE_DIAGNOSTIC_KEY' not in json.dumps(seen['messages'])
    assert '为什么优化失败' in report['markdown']
    loaded = client.get(url(baseline_run)).json()
    assert loaded['latest']['markdown'] == report['markdown']
    assert persistence.get_run(baseline_run['id']) == before
    assert persistence.get_case_results_for_run(baseline_run['id']) == cases
    assert persistence.max_event_seq(baseline_run['id']) == events


def test_evidence_json_remains_parseable_after_redaction(client, baseline_run, demo):
    configure(demo)
    persistence.append_event(baseline_run['id'], 'model_call_failed', level='error',
                             payload={'message': 'Authorization: Bearer abcdef', 'password': 'hidden'})
    result = client.get(url(baseline_run)).json()
    source = result['evidence_markdown'].split('```json\n')[1].split('\n```')[0]
    evidence = json.loads(source)
    assert evidence['run']['id'] == baseline_run['id']
    assert 'hidden' not in source


@pytest.mark.parametrize('mode', ['validate', 'final_test'])
def test_holdout_content_never_enters_analysis(client, baseline_run, demo, monkeypatch, mode):
    configure(demo)
    with transaction() as conn:
        conn.execute('UPDATE runs SET mode=?, error_message=? WHERE id=?',
                     (mode, 'HOLDOUT_SECRET', baseline_run['id']))
    persistence.append_event(baseline_run['id'], 'model_call_failed', level='error',
                             payload={'message': 'HOLDOUT_SECRET'}, split='validation')
    evidence = run_analysis.build_evidence(baseline_run['id'])
    assert evidence['cases'] == []
    assert 'HOLDOUT_SECRET' not in json.dumps(evidence)
    assert all('payload' not in event for event in evidence['events'])
    assert 'prompt' not in evidence


def test_daily_evidence_excludes_holdout_cases_and_secrets(client, baseline_run, demo):
    configure(demo)
    persistence.append_event(baseline_run['id'], 'model_call_failed', level='error',
        payload={'message': 'PRIVATE_DIAGNOSTIC_KEY Authorization: Bearer abcdef',
                 'reasoning_content': 'HIDDEN_THOUGHT', 'headers': {'X-Key': 'PRIVATE_HEADER'}})
    persistence.append_event(baseline_run['id'], 'case_failed', split='validation',
                             payload={'message': 'HOLDOUT_VALUE'})
    evidence = run_analysis.build_evidence(baseline_run['id'])
    text = json.dumps(evidence)
    for secret in ('PRIVATE_DIAGNOSTIC_KEY', 'HIDDEN_THOUGHT', 'PRIVATE_HEADER', 'HOLDOUT_VALUE', 'abcdef'):
        assert secret not in text
    assert evidence['cases']
    assert evidence['versions']['baseline']['content_hash']
    assert evidence['baseline_skill_excerpts']['SKILL.md']


def test_missing_skill_file_does_not_block_diagnosis(baseline_run):
    path = persistence.skill_files_dir(baseline_run['baseline_skill_version_id']) / 'SKILL.md'
    path.unlink()
    evidence = run_analysis.build_evidence(baseline_run['id'])
    assert evidence['baseline_skill_excerpts']['SKILL.md']['unavailable'] is True


@pytest.mark.parametrize('failure', ['timeout', 'empty'])
def test_model_failure_saves_exportable_evidence_without_fake_conclusion(client, baseline_run, demo, monkeypatch, failure):
    configure(demo)
    class Adapter:
        def invoke(self, *args):
            if failure == 'empty':
                return {'content': ''}
            raise AdapterError('network PRIVATE_DIAGNOSTIC_KEY', code='timeout')
    monkeypatch.setattr(run_analysis, 'build_adapter', lambda config: Adapter())
    response = client.post(url(baseline_run), json={})
    report = response.json()
    assert report['status'] == 'failed'
    assert not report['analysis']
    assert report['error']['code'] == ('timeout' if failure == 'timeout' else 'analysis_empty')
    assert 'PRIVATE_DIAGNOSTIC_KEY' not in response.text
    assert '可复查证据' in report['markdown']
    assert client.get(url(baseline_run)).json()['latest']['status'] == 'failed'


def test_no_model_active_missing_and_invalid_requests(client, baseline_run, demo):
    before = client.get(url(baseline_run)).json()
    assert before['configured'] is False
    assert '可复查证据' in before['evidence_markdown']
    assert client.post(url(baseline_run), json={}).status_code == 422
    assert client.post(url(baseline_run), json={'question': ['bad']}).status_code == 422
    active = services.create_run({'project_id': demo['project']['id'], 'mode': 'baseline'})
    configure(demo)
    assert client.post(url(active), json={}).status_code == 409
    assert client.get('/api/v3/runs/missing/analysis').status_code == 404


def test_long_log_keeps_terminal_failure_and_reports_sampling(baseline_run):
    for i in range(180):
        persistence.append_event(baseline_run['id'], 'model_call_finished',
                                 payload={'content': '模型回复' * 1000})
    event = persistence.append_event(baseline_run['id'], 'run_failed', level='error',
                                     payload={'message': 'LAST_GATEWAY_FAILURE'})
    evidence = run_analysis.build_evidence(baseline_run['id'])
    assert evidence['coverage']['through_event_seq'] == event['seq']
    assert evidence['coverage']['events_omitted'] > 0
    assert 'LAST_GATEWAY_FAILURE' in json.dumps(evidence)
    assert len(run_analysis._json(evidence)) <= 60000


def test_multiple_reports_keep_immutable_artifacts(client, baseline_run, demo, monkeypatch):
    configure(demo)
    class Adapter:
        def invoke(self, *args):
            return {'content': '报告'}
    monkeypatch.setattr(run_analysis, 'build_adapter', lambda config: Adapter())
    first = client.post(url(baseline_run), json={}).json()
    original = persistence.get_artifact(baseline_run['id'], first['artifact_id'])['content_path'].read_bytes()
    second = client.post(url(baseline_run), json={}).json()
    assert first['artifact_id'] != second['artifact_id']
    assert client.get(url(baseline_run)).json()['latest']['artifact_id'] == second['artifact_id']
    assert persistence.get_artifact(baseline_run['id'], first['artifact_id'])['content_path'].read_bytes() == original
