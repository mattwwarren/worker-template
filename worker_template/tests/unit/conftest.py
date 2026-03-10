"""Unit test conftest - disable DB fixtures."""

import pytest


@pytest.fixture(autouse=True)
async def reset_db() -> None:
    """No-op: unit tests don't use database."""
    return
