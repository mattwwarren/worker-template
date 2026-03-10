"""Tests for Prometheus metrics."""

from worker_template.core.metrics import (
    task_duration_seconds,
    tasks_completed_total,
    tasks_failed_total,
    tasks_in_progress,
    tasks_started_total,
)


def test_metrics_exist():
    """Verify all expected metrics are registered."""
    assert tasks_started_total is not None
    assert tasks_completed_total is not None
    assert tasks_failed_total is not None
    assert tasks_in_progress is not None
    assert task_duration_seconds is not None
