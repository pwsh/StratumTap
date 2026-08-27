"""Shared pytest fixtures."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_text(name: str) -> str:
    """Read a captured-output fixture as text."""
    return (FIXTURES / name).read_text()


@pytest.fixture
def tracking_csv() -> str:
    """Captured ``chronyc -c tracking`` output."""
    return fixture_text("chronyc_tracking.csv")


@pytest.fixture
def tracking_text() -> str:
    """Captured ``chronyc tracking`` output."""
    return fixture_text("chronyc_tracking.txt")


async def wait_for(predicate, timeout: float = 5.0, interval: float = 0.02):
    """Poll *predicate* until it is truthy or *timeout* elapses."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")
