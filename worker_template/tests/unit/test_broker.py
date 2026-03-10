"""Tests for broker configuration."""

from taskiq import InMemoryBroker


def test_broker_is_in_memory():
    """In test mode (TASKIQ_ENV=test), broker should be InMemoryBroker."""
    from worker_template.broker import broker

    assert isinstance(broker, InMemoryBroker)
