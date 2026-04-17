"""Tests for MCP-disable coercion in tech-debt agent factories.

Bug: `mcp_servers=mcp_servers or ["perplexity", "context7"]` treats an empty
list as falsy and silently restores the default servers. When a user sets
all MCP toggles to false in config, the factory was picking the defaults.

Fix: distinguish `None` (caller didn't care, use defaults) from `[]`
(caller explicitly wants no servers).
"""

from __future__ import annotations

from designdoc.agents.tech_debt import make_tech_debt_crossref, make_tech_debt_researcher


def test_researcher_defaults_when_mcp_servers_is_none():
    """None preserves the historic default behavior."""
    agent = make_tech_debt_researcher(mcp_servers=None)
    assert set(agent.mcp_servers) == {"perplexity", "context7"}


def test_crossref_defaults_when_mcp_servers_is_none():
    agent = make_tech_debt_crossref(mcp_servers=None)
    assert set(agent.mcp_servers) == {"perplexity", "context7"}


def test_researcher_respects_empty_list():
    """Empty list means NO servers — do NOT fall back to defaults."""
    agent = make_tech_debt_researcher(mcp_servers=[])
    assert agent.mcp_servers == []


def test_crossref_respects_empty_list():
    agent = make_tech_debt_crossref(mcp_servers=[])
    assert agent.mcp_servers == []


def test_researcher_respects_partial_list():
    """Partial disable: user wants only context7."""
    agent = make_tech_debt_researcher(mcp_servers=["context7"])
    assert agent.mcp_servers == ["context7"]
