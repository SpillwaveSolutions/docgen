"""Unit tests for class-documenter + doc-quality-checker agents."""

from __future__ import annotations

from designdoc.agents.class_documenter import build_prompt, make_class_documenter
from designdoc.agents.doc_quality_checker import (
    build_prompt as build_checker_prompt,
)
from designdoc.agents.doc_quality_checker import (
    make_doc_quality_checker,
)


def test_documenter_has_read_tool():
    agent = make_class_documenter()
    assert agent.name == "class-documenter"
    assert "Read" in agent.allowed_tools
    assert agent.max_output_tokens >= 2048


def test_documenter_prompt_includes_class_and_path():
    p = build_prompt("Foo", "src/a.py", '{"name":"Foo"}')
    assert "Foo" in p
    assert "src/a.py" in p


def test_checker_is_separate_agent_with_distinct_prompt():
    doer = make_class_documenter()
    checker = make_doc_quality_checker()
    assert doer.name != checker.name
    assert doer.system_prompt != checker.system_prompt
    # Checker must request a JSON object per the verdict schema — this is the
    # mechanism that lets parse_verdict synthesize a fail on malformed output.
    assert "json" in checker.system_prompt.lower()
    assert "status" in checker.system_prompt.lower()


def test_checker_prompt_includes_doc_and_source_path():
    p = build_checker_prompt("Foo", "src/a.py", "# Foo\nThis is a class.")
    assert "src/a.py" in p
    assert "Foo" in p
    assert "This is a class" in p
