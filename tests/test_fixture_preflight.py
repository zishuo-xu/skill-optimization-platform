"""Fixture coverage follows runtime name and argument matching."""
from app.single_round import _fixture_gap_cases


def case(fixtures):
    return {'case_id': 'image-case', 'expected': {
        'tool_calls': [{'name': 'imageEditWorkflow', 'arguments': {}}],
        'variants': [{'tool_calls': [{'name': 'alternativeTool', 'arguments': {}}]}],
    }, 'fixtures': fixtures}


def test_wildcard_fixture_covers_top_level_and_alternative_tool_names():
    assert _fixture_gap_cases([case([{'name': '*', 'match': {}, 'response': {}}])]) == []


def test_missing_alternative_tool_is_reported_once():
    assert _fixture_gap_cases([case([{'name': 'imageEditWorkflow'}])]) == ['image-case']


def test_argument_mismatch_is_reported_as_uncovered():
    assert _fixture_gap_cases([case([
        {'name': 'imageEditWorkflow', 'match': {'method': 'specific'}, 'consume': 'once'},
        {'name': 'alternativeTool'},
    ])]) == ['image-case']
