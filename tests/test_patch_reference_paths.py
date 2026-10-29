import pytest

from app import patch
from tests.test_a06_optimizer import BASE_FILES, MANIFEST


def check_reference(text):
    modified = dict(BASE_FILES)
    modified['references/task_query.md'] += ('\n' + text + '\n').encode()
    return patch.static_check(BASE_FILES, modified, MANIFEST)


@pytest.mark.parametrize('reference', [
    '`references/weekly_report.md`',
    '`references/weekly_report.md`，再次参考 `references/weekly_report.md`',
    '[周报](weekly_report.md)',
    '[周报](./weekly_report.md#说明)',
    '[主技能](../SKILL.md)',
    '[周报](weekly_report.md)，工具路径 `references/weekly_report.md`',
])
def test_subskill_package_paths_and_relative_links_use_their_own_bases(reference):
    result = check_reference(reference)
    assert result['ok'], result['errors']


def test_missing_package_path_is_still_rejected_and_duplicates_are_collapsed():
    result = check_reference('参考 `references/missing.md` 和 `references/missing.md`')
    errors = [e for e in result['errors'] if e['code'] == 'dangling_reference']
    assert len(errors) == 1
    assert errors[0]['resolved_path'] == 'references/missing.md'
    assert errors[0]['reference_kind'] == 'package_path'
    assert errors[0]['line'] > 1
    assert not result['ok']


def test_markdown_link_does_not_fall_back_to_an_existing_package_root_path():
    # A hyperlink in a nested document is document-relative, even when a
    # similarly named file happens to exist at the package root.
    result = check_reference('[周报](references/weekly_report.md)')
    error = next(e for e in result['errors'] if e['code'] == 'dangling_reference')
    assert error['resolved_path'] == 'references/references/weekly_report.md'
    assert error['reference_kind'] == 'markdown_link'
    assert error['reference'] == 'references/weekly_report.md'


def test_existing_package_reference_does_not_hide_other_broken_links():
    result = check_reference('`references/weekly_report.md` 和 [缺失](missing.md)')
    errors = [e for e in result['errors'] if e['code'] == 'dangling_reference']
    assert len(errors) == 1 and errors[0]['resolved_path'] == 'references/missing.md'
