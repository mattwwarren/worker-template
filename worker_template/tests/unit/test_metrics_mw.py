"""Tests for MetricsMiddleware: pre_execute, post_execute, on_error."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from worker_template.middleware.metrics_mw import _TASK_START_TIME_KEY, MetricsMiddleware


def make_message(task_name="test_task", labels=None):
    """Create a mock TaskIQ message."""
    msg = MagicMock()
    msg.task_id = str(uuid4())
    msg.task_name = task_name
    msg.kwargs = {}
    msg.labels = labels if labels is not None else {}
    return msg


def make_result(is_err=False, error=None):
    """Create a mock TaskIQ result."""
    result = MagicMock()
    result.is_err = is_err
    result.error = error
    return result


class TestMetricsMiddlewarePreExecute:
    @pytest.fixture
    def middleware(self):
        return MetricsMiddleware()

    async def test_increments_started_counter(self, middleware):
        msg = make_message(task_name="my_task")

        with patch("worker_template.middleware.metrics_mw.tasks_started_total") as mock_counter:
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            await middleware.pre_execute(msg)

            mock_counter.labels.assert_called_once()
            mock_labels.inc.assert_called_once()

    async def test_increments_in_progress_gauge(self, middleware):
        msg = make_message(task_name="my_task")

        with patch("worker_template.middleware.metrics_mw.tasks_in_progress") as mock_gauge:
            mock_labels = MagicMock()
            mock_gauge.labels.return_value = mock_labels

            await middleware.pre_execute(msg)

            mock_gauge.labels.assert_called_once_with(task_name="my_task")
            mock_labels.inc.assert_called_once()

    async def test_stores_start_time_in_labels(self, middleware):
        msg = make_message()

        await middleware.pre_execute(msg)

        assert _TASK_START_TIME_KEY in msg.labels
        # Should be a string representation of a float
        float(msg.labels[_TASK_START_TIME_KEY])

    async def test_returns_message(self, middleware):
        msg = make_message()

        result = await middleware.pre_execute(msg)

        assert result is msg


class TestMetricsMiddlewarePostExecute:
    @pytest.fixture
    def middleware(self):
        return MetricsMiddleware()

    async def test_decrements_in_progress_on_success(self, middleware):
        msg = make_message()
        msg.labels[_TASK_START_TIME_KEY] = "1000.0"
        result = make_result(is_err=False)

        with patch("worker_template.middleware.metrics_mw.tasks_in_progress") as mock_gauge:
            mock_labels = MagicMock()
            mock_gauge.labels.return_value = mock_labels

            await middleware.post_execute(msg, result)

            mock_labels.dec.assert_called_once()

    async def test_increments_completed_on_success(self, middleware):
        msg = make_message()
        msg.labels[_TASK_START_TIME_KEY] = "1000.0"
        result = make_result(is_err=False)

        with patch("worker_template.middleware.metrics_mw.tasks_completed_total") as mock_counter:
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            await middleware.post_execute(msg, result)

            mock_labels.inc.assert_called_once()

    async def test_increments_failed_on_error(self, middleware):
        msg = make_message()
        msg.labels[_TASK_START_TIME_KEY] = "1000.0"
        result = make_result(is_err=True, error="boom")

        with patch("worker_template.middleware.metrics_mw.tasks_failed_total") as mock_counter:
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            await middleware.post_execute(msg, result)

            mock_labels.inc.assert_called_once()

    async def test_records_duration_when_start_time_present(self, middleware):
        msg = make_message()
        msg.labels[_TASK_START_TIME_KEY] = "1000.0"
        result = make_result(is_err=False)

        with patch("worker_template.middleware.metrics_mw.task_duration_seconds") as mock_hist:
            mock_labels = MagicMock()
            mock_hist.labels.return_value = mock_labels

            await middleware.post_execute(msg, result)

            mock_hist.labels.assert_called_once_with(task_name=msg.task_name)
            mock_labels.observe.assert_called_once()
            # Duration should be a positive number
            observed_duration = mock_labels.observe.call_args[0][0]
            assert observed_duration > 0

    async def test_no_duration_when_start_time_missing(self, middleware):
        msg = make_message()
        # No start time in labels
        result = make_result(is_err=False)

        with patch("worker_template.middleware.metrics_mw.task_duration_seconds") as mock_hist:
            await middleware.post_execute(msg, result)

            mock_hist.labels.assert_not_called()


class TestMetricsMiddlewareOnError:
    @pytest.fixture
    def middleware(self):
        return MetricsMiddleware()

    async def test_decrements_in_progress(self, middleware):
        msg = make_message()
        msg.labels[_TASK_START_TIME_KEY] = "1000.0"
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        with patch("worker_template.middleware.metrics_mw.tasks_in_progress") as mock_gauge:
            mock_labels = MagicMock()
            mock_gauge.labels.return_value = mock_labels

            await middleware.on_error(msg, result, exc)

            mock_labels.dec.assert_called_once()

    async def test_increments_failed_counter(self, middleware):
        msg = make_message()
        msg.labels[_TASK_START_TIME_KEY] = "1000.0"
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        with patch("worker_template.middleware.metrics_mw.tasks_failed_total") as mock_counter:
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            await middleware.on_error(msg, result, exc)

            mock_labels.inc.assert_called_once()

    async def test_records_duration_on_error(self, middleware):
        msg = make_message()
        msg.labels[_TASK_START_TIME_KEY] = "1000.0"
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        with patch("worker_template.middleware.metrics_mw.task_duration_seconds") as mock_hist:
            mock_labels = MagicMock()
            mock_hist.labels.return_value = mock_labels

            await middleware.on_error(msg, result, exc)

            mock_labels.observe.assert_called_once()

    async def test_no_duration_when_start_time_missing(self, middleware):
        msg = make_message()
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        with patch("worker_template.middleware.metrics_mw.task_duration_seconds") as mock_hist:
            await middleware.on_error(msg, result, exc)

            mock_hist.labels.assert_not_called()
