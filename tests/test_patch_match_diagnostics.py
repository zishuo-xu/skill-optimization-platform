import pytest

from app import patch, optimizer, persistence
from tests.conftest import complete_run


@pytest.mark.parametrize('body,search,reason,normalized_count', [
    ('# Edit\r\nQuery version\r\nApply style\r\n', 'Query version\nApply style', 'line_ending_mismatch', 1),
    ('# Edit\nQuery version\n   ↓\nApply style\n', 'Query version\n  ↓\nApply style', 'whitespace_mismatch', 0),
    ('# Edit\nQuery version\nApply style\n', 'Query capability\nApply style', 'text_mismatch', 0),
    ('# Edit\nRepeat\nRepeat\n', 'Repeat', 'multiple_matches', 2),
])
def test_match_diagnostics_never_relax_or_apply_nonunique_patch(body, search, reason, normalized_count):
    files = {'SKILL.md': body.encode()}
    edited, issues = patch.apply_edits(files, [{'path': 'SKILL.md', 'search': search, 'replace': 'new'}], files)
    assert edited == files
    assert len(issues) == 1
    issue = issues[0]
    assert issue['mismatch_reason'] == reason
    assert issue['normalized_newline_match_count'] == normalized_count
    assert issue['prior_edit_indices'] == []
    assert issue['search_chars'] == len(search)


def test_reports_sequential_conflict_and_rolls_back_whole_patch():
    files = {'SKILL.md': b'# Edit\nQuery version\nApply style\n'}
    edited, issues = patch.apply_edits(files, [
        {'path': 'SKILL.md', 'search': 'Query version', 'replace': 'Query capability'},
        {'path': 'SKILL.md', 'search': 'Query version\nApply style', 'replace': 'New steps'},
    ], files)
    assert edited == files
    assert issues[0]['edit_index'] == 1
    assert issues[0]['mismatch_reason'] == 'prior_edit_conflict'
    assert issues[0]['original_match_count'] == 1
    assert issues[0]['current_match_count'] == 0
    assert issues[0]['prior_edit_indices'] == [0]


def test_edits_in_other_files_are_not_reported_as_conflicts():
    files = {'SKILL.md': b'# Guide\nOld\n', 'references/edit.md': b'# Edit\nOld text\n'}
    edited, issues = patch.apply_edits(files, [
        {'path': 'SKILL.md', 'search': 'Old', 'replace': 'New'},
        {'path': 'references/edit.md', 'search': 'Old  text', 'replace': 'New text'},
    ], files)
    assert edited == files
    assert issues[0]['mismatch_reason'] == 'whitespace_mismatch'
    assert issues[0]['prior_edit_indices'] == []


def test_reference_excerpt_is_bounded_and_does_not_guess_ambiguous_anchor():
    files = {'SKILL.md': ('# Edit\nAnchor\n' + 'a' * 2000).encode()}
    search = 'Anchor\n' + 'b' * 2000
    _, issues = patch.apply_edits(files, [{'path': 'SKILL.md', 'search': search, 'replace': 'new'}], files)
    assert issues[0]['reference_line'] == 2
    assert len(issues[0]['reference_excerpt']) == 1200
    assert issues[0]['reference_excerpt_truncated']
    files['SKILL.md'] += b'\nAnchor\n'
    _, issues = patch.apply_edits(files, [{'path': 'SKILL.md', 'search': search, 'replace': 'new'}], files)
    assert 'reference_excerpt' not in issues[0]


@pytest.mark.parametrize('fallback', [False, True])
def test_pipeline_reports_actual_failed_edit_and_preserves_rejected_patch(demo, monkeypatch, fallback):
    original = optimizer.generate_candidate
    def generate(*args, **kwargs):
        outcome = original(*args, **kwargs)
        assert outcome['status'] == 'ok'
        outcome['patch']['edits'] = [{'path': 'SKILL.md', 'search': 'synthetic missing source', 'replace': 'new'}]
        outcome['used_fallback'] = fallback
        return outcome
    monkeypatch.setattr(optimizer, 'generate_candidate', generate)
    run = complete_run(demo, 'optimize')
    event = next(e for e in persistence.list_events(run['id'], limit=2000) if e['type'] == 'static_check_failed')
    payload = event['payload']
    assert '第 1 条修改 · SKILL.md' in payload['message']
    assert ('规则候选' if fallback else '模型候选') in payload['message']
    assert payload['issues'][0]['mismatch_reason'] == 'text_mismatch'
    assert payload['baseline_skill_version_id'] == run['baseline_skill_version_id']
    assert '优化器返回内容' in payload['next_action']
    candidate = persistence.get_candidate_for_run(run['id'])
    assert candidate['patch']['edits'][0]['search'] == 'synthetic missing source'
    assert not run['candidate_skill_version_id']
    assert not persistence.list_events(run['id'], event_type='retest_started')


def test_terminal_patch_summary_excludes_source_text():
    from app.runtime_logging import event_summary
    summary = event_summary({'type': 'static_check_failed', 'payload': {'issues': [{
        'code': 'edit_not_found', 'path': 'SKILL.md', 'edit_index': 2,
        'mismatch_reason': 'text_mismatch', 'original_match_count': 0,
        'current_match_count': 0, 'reference_excerpt': 'PRIVATE_TEXT',
        'search_preview': 'PRIVATE_SEARCH'}]}})
    assert summary['errors'][0]['edit_index'] == 2
    assert summary['errors'][0]['mismatch_reason'] == 'text_mismatch'
    assert 'PRIVATE_' not in str(summary)
