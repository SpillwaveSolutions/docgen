"""Unit tests for package-documenter + package-doc-checker."""

from __future__ import annotations

from designdoc.agents.package_documenter import (
    build_checker_prompt,
    build_doer_prompt,
    make_package_doc_checker,
    make_package_documenter,
)


def test_package_documenter_has_no_read_tool():
    """Stage 4 reads only the class docs passed in the prompt — never source."""
    agent = make_package_documenter()
    assert agent.allowed_tools == []


def test_checker_has_no_read_tool():
    agent = make_package_doc_checker()
    assert agent.allowed_tools == []


def test_doer_and_checker_are_distinct():
    doer = make_package_documenter()
    checker = make_package_doc_checker()
    assert doer.name != checker.name
    assert doer.system_prompt != checker.system_prompt


def test_doer_prompt_bundles_class_docs():
    prompt = build_doer_prompt("payments", {"Gateway": "## Gateway doc", "Charge": "## Charge doc"})
    assert "payments" in prompt
    assert "Gateway" in prompt
    assert "Charge" in prompt
    assert "## Gateway doc" in prompt


def test_checker_prompt_includes_readme_and_class_docs():
    prompt = build_checker_prompt("x", {"A": "class A doc"}, "# x\nproposed readme")
    assert "proposed readme" in prompt
    assert "class A doc" in prompt
    assert "JSON" in prompt or "json" in prompt
