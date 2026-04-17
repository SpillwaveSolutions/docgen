"""Unit tests for system-designer and split_doer_output."""

from __future__ import annotations

from designdoc.agents.system_designer import (
    build_checker_prompt,
    build_doer_prompt,
    make_system_checker,
    make_system_designer,
    split_doer_output,
)


def test_system_designer_has_no_read_tool():
    """Stage 7 reads only package READMEs given in the prompt."""
    agent = make_system_designer()
    assert agent.allowed_tools == []


def test_doer_and_checker_are_distinct():
    doer = make_system_designer()
    checker = make_system_checker()
    assert doer.name != checker.name
    assert doer.system_prompt != checker.system_prompt


def test_doer_prompt_includes_every_package():
    prompt = build_doer_prompt({"payments": "## p readme", "reporting": "## r readme"})
    assert "payments" in prompt
    assert "reporting" in prompt
    assert "p readme" in prompt
    assert "r readme" in prompt


def test_checker_prompt_bundles_readmes_and_proposal():
    prompt = build_checker_prompt({"x": "readme body"}, "proposed combined docs")
    assert "readme body" in prompt
    assert "proposed combined docs" in prompt


def test_split_doer_output_with_markers():
    text = (
        "<<<SYSTEM_DESIGN>>>\n"
        "## Overview\nSystem body\n\n"
        "<<<ARCHITECTURE>>>\n"
        "## Containers\nArch body\n"
    )
    sys_md, arch_md = split_doer_output(text)
    assert sys_md.startswith("## Overview")
    assert arch_md.startswith("## Containers")


def test_split_doer_output_fallback_on_containers_heading():
    text = "## Overview\nstuff\n\n## Containers\nthings"
    sys_md, arch_md = split_doer_output(text)
    assert sys_md.startswith("## Overview")
    assert "## Containers" in arch_md


def test_split_doer_output_degraded_path_produces_two_files():
    """Even completely unstructured output must produce two non-empty files."""
    sys_md, arch_md = split_doer_output("no structure at all")
    assert sys_md
    assert arch_md
    assert "HIL" in arch_md
