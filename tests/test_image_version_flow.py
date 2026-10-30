"""A version query is specific to one reference, not a global tool preamble."""
import copy
import shutil

from app import execution, patch
from app.adapters.registry import build_adapter
from app.evaluation import score_case
from tests.test_image_sample import _samples, _image_fixture_data, _case_request


def execute(case, manifest, root=None):
    return execution.run_case(_case_request(case, manifest, root or _samples() / 'image_skill_package'),
                              build_adapter({'provider': 'demo_scripted', 'domain': 'image'}))


def test_only_one_of_ten_references_requires_version_query():
    cases, manifest = _image_fixture_data()
    assert len(manifest['subskills']) == 10
    for entry in manifest['subskills']:
        if entry['skill_id'] == 'image_style':
            continue
        case = next(c for c in cases if c['case_id'] == entry['skill_id'] + '_opt_1')
        actual = execute(case, manifest)
        assert score_case(case['expected'], actual)['joint']
        assert len(actual['tool_calls']) == 1
        assert actual['tool_calls'][0]['arguments']['method'] != 'query_style_version'


def test_version_response_changes_the_same_requests_capabilities_and_bounds():
    cases, manifest = _image_fixture_data()
    case = copy.deepcopy(next(c for c in cases if c['case_id'] == 'style_warm_v2'))
    first = execute(case, manifest)
    case['fixtures'][0]['response']['version'] = 3
    case['fixtures'][1]['match']['args']['temperature'] = 9
    second = execute(case, manifest)
    assert first['tool_calls'][1]['arguments']['args']['temperature'] == 20
    assert second['tool_calls'][1]['arguments']['args']['temperature'] == 9
    case = copy.deepcopy(next(c for c in cases if c['case_id'] == 'style_black_v2'))
    case['fixtures'][0]['response']['version'] = 1
    case['fixtures'][1] = {'name': 'editImage', 'match': {'method': 'retouch_from_text'}, 'response': {'code': 0}}
    actual = execute(case, manifest)
    assert [c['arguments']['method'] for c in actual['tool_calls']] == ['query_style_version', 'retouch_from_text']
    assert actual['selected_skills'] == ['image_style', 'image_one_word_fix']


def test_missing_query_fixture_is_not_hidden_by_skipping_required_step():
    cases, manifest = _image_fixture_data()
    case = copy.deepcopy(next(c for c in cases if c['case_id'] == 'style_black_v2'))
    case['fixtures'] = case['fixtures'][1:]
    actual = execute(case, manifest)
    assert actual['error']['code'] == 'fixture_miss'
    assert len(actual['tool_calls']) == 1
    assert actual['tool_calls'][0]['arguments']['method'] == 'query_style_version'


def test_query_response_and_fallback_reference_are_read_before_edit():
    cases, manifest = _image_fixture_data()
    for cid in ('style_black_v2', 'style_black_v1'):
        actual = execute(next(c for c in cases if c['case_id'] == cid), manifest)
        events = actual['events']
        starts = [i for i, e in enumerate(events) if e['type'] == 'tool_call_started']
        results = [i for i, e in enumerate(events) if e['type'] == 'tool_result']
        assert starts[0] < results[0] < starts[1] < results[1]
        if cid == 'style_black_v1':
            assert any(results[0] < i < starts[1] and e['type'] == 'skill_read'
                       and e['payload']['path'] == 'references/image-one-word-fix.md' for i, e in enumerate(events))


def test_version_limits_come_from_loaded_skill_not_dataset_or_adapter(tmp_path):
    cases, manifest = _image_fixture_data()
    source = _samples() / 'image_skill_package'
    root = tmp_path / 'skill'
    shutil.copytree(source, root)
    ref = root / 'references/image-style.md'
    ref.write_text(ref.read_text().replace('"temperature": [0, 20]', '"temperature": [0, 17]'))
    case = copy.deepcopy(next(c for c in cases if c['case_id'] == 'style_warm_v2'))
    case['fixtures'][1]['match'] = {'method': 'apply_style'}
    actual = execute(case, manifest, root)
    assert actual['tool_calls'][1]['arguments']['args']['temperature'] == 17


def test_style_response_memory_and_sample_static_integrity():
    cases, manifest = _image_fixture_data()
    actual = execute(next(c for c in cases if c['case_id'] == 'style_memory'), manifest)
    assert actual['raw_output'] == '风格处理完成；偏好：饱和度=3'
    root = _samples() / 'image_skill_package'
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert patch.static_check(files, files, manifest, [c['case_id'] for c in cases])['ok']
