"""StageEntry self-describing behavior (issue #13).

After the refactor, each StageEntry carries its own ``owns_id`` predicate
and ``kwargs_fn`` builder. Adding a new stage 9 should be a one-place
change to ``default_stage_table()`` — no edits to Orchestrator switch
methods. These tests lock in the per-stage behavior of both fields,
including the fragile class_docs rule.
"""

from __future__ import annotations

import pytest

from designdoc.config import Config
from designdoc.orchestrator import StageEntry, default_stage_table

# ---- StageEntry.owns_id behavior --------------------------------------------


def _entry(stage_name: str) -> StageEntry:
    """Pull the StageEntry for the given stage name from the default table."""
    table = default_stage_table()
    entry = next((e for e in table if e.name == stage_name), None)
    assert entry is not None, f"no StageEntry for {stage_name!r} in default table"
    return entry


@pytest.mark.parametrize(
    "stage,artifact_id",
    [
        ("file_analysis", "file:src/foo.py"),
        ("class_docs", "src/payments/gateway.py::Gateway"),
        ("package_rollups", "package:tiny.payments"),
        ("mermaid", "mermaid:Gateway"),
        ("tech_debt", "dep:requests"),
        ("system_rollup", "system:rollup"),
    ],
)
def test_owns_id_claims_own_typical_artifact(stage: str, artifact_id: str) -> None:
    assert _entry(stage).owns_id(artifact_id), f"{stage} should own {artifact_id!r}"


@pytest.mark.parametrize(
    "foreign_id",
    [
        "file:src/foo.py",
        "package:tiny.payments",
        "mermaid:Gateway",
        "dep:requests",
        "system:rollup",
    ],
)
def test_class_docs_rejects_other_stage_ids(foreign_id: str) -> None:
    """The fragile class_docs rule (has '::' AND not in _OTHER_PREFIXES)
    must reject every other stage's typical id, including the system:rollup
    form added in PR #29. This is the regression case the StageEntry
    refactor is designed to make harder to break."""
    assert not _entry("class_docs").owns_id(foreign_id), (
        f"class_docs incorrectly claimed {foreign_id!r} — _OTHER_PREFIXES drift"
    )


def test_class_docs_claims_path_double_colon_form() -> None:
    """Positive case: an id with '::' that isn't a known prefix IS class_docs."""
    assert _entry("class_docs").owns_id("path/foo.py::MyClass")


def test_system_rollup_uses_exact_match_not_prefix() -> None:
    """system_rollup uses exact-string match. 'system:rollup:extra' must NOT
    classify (vs system:rollup which does). Guards against a future
    prefix-style change that would conflict with class_docs' rule."""
    e = _entry("system_rollup")
    assert e.owns_id("system:rollup")
    assert not e.owns_id("system:other")
    assert not e.owns_id("system:rollup:extra")


@pytest.mark.parametrize("stage", ["discover", "index", "finalize"])
@pytest.mark.parametrize(
    "artifact_id",
    ["file:foo", "package:bar", "src/foo.py::Bar", "system:rollup", "anything"],
)
def test_deterministic_stages_claim_no_artifact_ids(stage: str, artifact_id: str) -> None:
    """Stages 0/1/8 are deterministic — they don't write per-artifact entries
    to artifact_index, so their owns_id must always return False."""
    assert not _entry(stage).owns_id(artifact_id)


# ---- StageEntry.kwargs_fn behavior ------------------------------------------


def test_discover_kwargs_passes_exclude_and_languages() -> None:
    cfg = Config(exclude_paths=("foo",), include_languages=("python",))
    kwargs = _entry("discover").kwargs_fn(cfg)
    assert kwargs == {
        "exclude_paths": ["foo"],
        "include_languages": ["python"],
    }


def test_file_analysis_kwargs_passes_doer_model_and_parallelism() -> None:
    cfg = Config(doer_model="claude-sonnet-4-6", parallelism=4)
    kwargs = _entry("file_analysis").kwargs_fn(cfg)
    assert kwargs == {"doer_model": "claude-sonnet-4-6", "parallelism": 4}


def test_class_docs_kwargs_includes_checker_model() -> None:
    cfg = Config(
        doer_model="claude-sonnet-4-6",
        checker_model="claude-sonnet-4-6",
        parallelism=2,
    )
    kwargs = _entry("class_docs").kwargs_fn(cfg)
    assert kwargs == {
        "doer_model": "claude-sonnet-4-6",
        "checker_model": "claude-sonnet-4-6",
        "parallelism": 2,
    }


def test_system_rollup_kwargs_omits_parallelism() -> None:
    """Single-artifact stage — parallelism doesn't apply."""
    cfg = Config(doer_model="x", checker_model="y", parallelism=8)
    kwargs = _entry("system_rollup").kwargs_fn(cfg)
    assert kwargs == {"doer_model": "x", "checker_model": "y"}
    assert "parallelism" not in kwargs


def test_tech_debt_kwargs_includes_mcp_servers() -> None:
    cfg = Config(
        doer_model="x",
        checker_model="y",
        parallelism=1,
        perplexity_mcp=True,
        context7_mcp=True,
    )
    kwargs = _entry("tech_debt").kwargs_fn(cfg)
    assert kwargs["mcp_servers"] == ["perplexity", "context7"]


def test_deterministic_stages_kwargs_empty() -> None:
    cfg = Config()
    for stage in ("index", "finalize"):
        assert _entry(stage).kwargs_fn(cfg) == {}


# ---- __post_init__ fallback (compat with downstream test constructions) ----


def test_constructor_fills_owns_id_from_builtin() -> None:
    """A bare StageEntry("class_docs", fn) — without explicit owns_id —
    must still pick up the class_docs classifier rule. Required for
    test_orchestrator_checkpoint_logs.py and any future test that
    constructs a StageEntry by name with a fake stage function."""

    async def fake_fn(**_: object) -> None:
        return None

    entry = StageEntry("class_docs", fake_fn, needs_runner=False)
    assert entry.owns_id is not None
    assert entry.owns_id("path/foo.py::MyClass")
    assert not entry.owns_id("file:foo.py")


def test_constructor_explicit_owns_id_wins_over_builtin() -> None:
    """If a caller passes owns_id explicitly, the builtin is NOT used."""

    async def fake_fn(**_: object) -> None:
        return None

    entry = StageEntry(
        "class_docs",
        fake_fn,
        needs_runner=False,
        owns_id=lambda _: True,
    )
    assert entry.owns_id("anything")  # custom predicate, not class_docs rule


def test_unknown_stage_name_falls_back_to_no_owns_id() -> None:
    """An unfamiliar stage name (e.g. a future or test-only stage) gets a
    safe ``return False`` predicate — no false-positive id classification."""

    async def fake_fn(**_: object) -> None:
        return None

    entry = StageEntry("unknown_stage", fake_fn, needs_runner=False)
    assert entry.owns_id("anything") is False
    assert entry.kwargs_fn(Config()) == {}
