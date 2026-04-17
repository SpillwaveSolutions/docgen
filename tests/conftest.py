"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Force anyio to use asyncio (not trio) for @pytest.mark.anyio tests."""
    return "asyncio"
