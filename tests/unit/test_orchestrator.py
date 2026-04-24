"""Unit tests for Orchestrator stage classifier (T4.2).

Guard test against the fragile class_docs classification rule:
"has '::' in id and not in _OTHER_PREFIXES". Adding a future stage that
also uses '::' in its ids would silently break this rule. These tests
lock in the behavioral contract.

Future-proofing: the test uses an adapter shim `classify(...)` that tries
StageEntry.owns_id first and falls back to Orchestrator._id_belongs_to_stage.
This way the test survives issue #13's pending refactor (which may move
the classifier from the Orchestrator classmethod onto StageEntry) without
needing a follow-up edit, regardless of merge order.
"""

from __future__ import annotations

import pytest

from designdoc.orchestrator import Orchestrator, default_stage_table


def classify(stage_name: str, artifact_id: str) -> bool:
    """Adapter — tries StageEntry.owns_id first, falls back to Orchestrator._id_belongs_to_stage.

    Survives issue #13's StageEntry refactor regardless of merge order:
    - Pre-#13: only the Orchestrator classmethod exists, fallback is taken.
    - Post-#13: StageEntry.owns_id exists, primary path is taken.
    """
    table = default_stage_table()
    entry = next((e for e in table if e.name == stage_name), None)
    if entry is not None and hasattr(entry, "owns_id"):
        return entry.owns_id(artifact_id)
    return Orchestrator._id_belongs_to_stage(artifact_id, stage_name)


# ---- Each stage claims its own typical artifact id --------------------------


def test_file_analysis_claims_file_prefix() -> None:
    assert classify("file_analysis", "file:src/foo.py")


def test_class_docs_claims_path_double_colon_id() -> None:
    assert classify("class_docs", "src/payments/gateway.py::Gateway")


def test_package_rollups_claims_package_prefix() -> None:
    assert classify("package_rollups", "package:tiny.payments")


def test_mermaid_claims_mermaid_prefix() -> None:
    assert classify("mermaid", "mermaid:Gateway")


def test_tech_debt_claims_dep_prefix() -> None:
    assert classify("tech_debt", "dep:requests")


def test_system_rollup_claims_system_rollup() -> None:
    assert classify("system_rollup", "system:rollup")


# ---- The fragile class_docs rule must reject every other stage's ids -------
# This is the regression guard most worth having: a future stage adding "::"
# to its ids would silently steal class_docs' classification today.


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
    """class_docs uses 'has :: and not in _OTHER_PREFIXES' — must reject every
    other stage's typical id, including the system:rollup form added in PR #29."""
    assert not classify("class_docs", foreign_id), (
        f"class_docs incorrectly claimed {foreign_id!r} — _OTHER_PREFIXES drift"
    )


# ---- Cross-stage rejection (each stage rejects others' typical ids) --------


def test_file_analysis_rejects_class_doc_id() -> None:
    assert not classify("file_analysis", "src/foo.py::Bar")


def test_package_rollups_rejects_file_id() -> None:
    assert not classify("package_rollups", "file:src/foo.py")


def test_system_rollup_only_claims_exact_id() -> None:
    """system_rollup uses exact-string match, not prefix match.
    'system:other' must NOT classify as system_rollup."""
    assert classify("system_rollup", "system:rollup")
    assert not classify("system_rollup", "system:other")
    assert not classify("system_rollup", "system:rollup:extra")


# ---- Deterministic stages (discover, index, finalize) claim no artifact ids -


@pytest.mark.parametrize("stage", ["discover", "index", "finalize"])
@pytest.mark.parametrize(
    "artifact_id",
    ["file:foo", "package:bar", "src/foo.py::Bar", "system:rollup", "anything"],
)
def test_deterministic_stages_claim_no_ids(stage: str, artifact_id: str) -> None:
    """Stages 0/1/8 don't write per-artifact entries to artifact_index, so
    their classifier must always return False."""
    assert not classify(stage, artifact_id)
