"""Tests for extract_json_object — tolerant JSON extraction for LLM outputs.

The Stage 2 file-analyzer's pydantic schema check fails 94% of the time on
real-codebase runs (issue #41) because Sonnet wraps JSON in code fences,
adds prose preambles, and occasionally appends trailing commentary —
despite system-prompt rules forbidding all of these.

extract_json_object is a non-destructive defensive parser: clean input
passes through unchanged; LLM-flavored input gets its embedded JSON
extracted. If no balanced {...} can be found, the original input is
returned so pydantic gives its native error.
"""

from __future__ import annotations

import pytest

from designdoc.verdict import extract_json_object


def test_clean_json_object_passes_through_unchanged():
    raw = '{"purpose":"x","key_types":[]}'
    assert extract_json_object(raw) == raw


def test_clean_json_object_with_whitespace_is_trimmed():
    raw = '   {"purpose":"x"}   \n'
    assert extract_json_object(raw) == '{"purpose":"x"}'


def test_strips_json_code_fence():
    raw = '```json\n{"purpose":"x"}\n```'
    assert extract_json_object(raw) == '{"purpose":"x"}'


def test_strips_plain_code_fence():
    raw = '```\n{"purpose":"x"}\n```'
    assert extract_json_object(raw) == '{"purpose":"x"}'


def test_strips_tilde_code_fence():
    raw = '~~~json\n{"purpose":"x"}\n~~~'
    assert extract_json_object(raw) == '{"purpose":"x"}'


def test_extracts_json_after_prose_preamble():
    raw = 'Here is the analysis:\n\n{"purpose":"x","notes":"y"}'
    assert extract_json_object(raw) == '{"purpose":"x","notes":"y"}'


def test_extracts_json_before_trailing_prose():
    raw = '{"purpose":"x"}\n\nThis file does interesting things.'
    assert extract_json_object(raw) == '{"purpose":"x"}'


def test_extracts_json_with_both_preamble_and_trailing():
    raw = 'Sure, here you go:\n{"purpose":"x","notes":"y"}\nHope that helps!'
    assert extract_json_object(raw) == '{"purpose":"x","notes":"y"}'


def test_extracts_fenced_json_with_prose_preamble():
    raw = 'Here is the analysis:\n\n```json\n{"purpose":"x"}\n```'
    # The outer code-fence stripper only matches strings whose entire content
    # is a fence; with preamble it won't strip. The balanced-brace extractor
    # picks the JSON out anyway.
    assert extract_json_object(raw) == '{"purpose":"x"}'


def test_handles_nested_braces():
    raw = '{"a":{"b":{"c":1}},"d":2}'
    assert extract_json_object(raw) == raw


def test_handles_braces_inside_strings():
    """Strings containing { or } must not confuse the balance counter."""
    raw = '{"text":"hello {world}","other":"a}b"}'
    assert extract_json_object(raw) == raw


def test_handles_escaped_quotes_in_strings():
    """An escaped quote does not exit the string state."""
    raw = '{"text":"he said \\"hi\\" then {left}"}'
    assert extract_json_object(raw) == raw


def test_no_object_returns_original_input():
    """No `{` at all → pass through; pydantic will give its native error."""
    raw = "I cannot summarize this file."
    assert extract_json_object(raw) == raw


def test_unbalanced_braces_returns_original_input():
    """Defensive: never invent a closing brace."""
    raw = 'Sure: {"purpose":"x"'  # missing closing brace
    assert extract_json_object(raw) == raw


def test_picks_first_balanced_object_when_multiple():
    """If there are two top-level objects (rare but possible),
    pick the first complete one."""
    raw = '{"first":1} and then {"second":2}'
    assert extract_json_object(raw) == '{"first":1}'


def test_empty_string_returns_empty_string():
    assert extract_json_object("") == ""


@pytest.mark.parametrize(
    "raw",
    [
        '{"purpose":"x"}',
        '   {"purpose":"x"}   ',
        '```json\n{"purpose":"x"}\n```',
        'Here:\n{"purpose":"x"}',
    ],
)
def test_round_trip_through_pydantic(raw):
    """The whole point: every shape extract_json_object handles must
    actually parse via FileSummary.model_validate_json."""
    from designdoc.agents.file_analyzer import FileSummary

    extracted = extract_json_object(raw)
    fs = FileSummary.model_validate_json(extracted)
    assert fs.purpose == "x"
