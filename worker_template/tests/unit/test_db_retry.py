"""Tests for db.retry: retry_on_connection_error decorator."""

import pytest
from sqlalchemy.exc import OperationalError

from worker_template.db.retry import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_WAIT,
    DEFAULT_MIN_WAIT,
    DEFAULT_WAIT_MULTIPLIER,
    _log_retry_attempt,
    create_db_retry,
    db_retry,
)


class TestDefaultConstants:
    def test_default_max_attempts(self):
        assert DEFAULT_MAX_ATTEMPTS == 3

    def test_default_wait_multiplier(self):
        assert DEFAULT_WAIT_MULTIPLIER == 1

    def test_default_min_wait(self):
        assert DEFAULT_MIN_WAIT == 1

    def test_default_max_wait(self):
        assert DEFAULT_MAX_WAIT == 10


class TestDbRetryDecorator:
    async def test_no_retry_on_success(self):
        call_count = 0

        @db_retry
        async def my_func():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await my_func()

        assert result == "ok"
        assert call_count == 1

    async def test_retries_on_operational_error(self):
        call_count = 0

        @create_db_retry(max_attempts=3, min_wait=0, max_wait=0, wait_multiplier=0)
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OperationalError("connection lost", {}, Exception())
            return "recovered"

        result = await flaky_func()

        assert result == "recovered"
        assert call_count == 3

    async def test_raises_after_max_attempts(self):
        call_count = 0

        @create_db_retry(max_attempts=2, min_wait=0, max_wait=0, wait_multiplier=0)
        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise OperationalError("connection lost", {}, Exception())

        with pytest.raises(OperationalError):
            await always_fails()

        assert call_count == 2

    async def test_does_not_retry_non_operational_errors(self):
        call_count = 0

        @db_retry
        async def raises_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not a db error")

        with pytest.raises(ValueError, match="not a db error"):
            await raises_value_error()

        assert call_count == 1


class TestCreateDbRetry:
    def test_returns_callable_decorator(self):
        decorator = create_db_retry()
        assert callable(decorator)

    async def test_custom_max_attempts(self):
        call_count = 0

        @create_db_retry(max_attempts=5, min_wait=0, max_wait=0, wait_multiplier=0)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 5:
                raise OperationalError("fail", {}, Exception())
            return "ok"

        result = await flaky()

        assert result == "ok"
        assert call_count == 5

    async def test_preserves_function_return_value(self):
        @create_db_retry(min_wait=0, max_wait=0, wait_multiplier=0)
        async def returns_dict():
            return {"key": "value", "count": 42}

        result = await returns_dict()

        assert result == {"key": "value", "count": 42}


class TestLogRetryAttempt:
    def test_logs_without_error(self):
        """Ensure _log_retry_attempt doesn't crash with mock state."""
        from unittest.mock import MagicMock, patch

        mock_state = MagicMock()
        mock_state.attempt_number = 2
        mock_state.next_action = MagicMock()
        mock_state.next_action.sleep = 1.5
        mock_outcome = MagicMock()
        mock_outcome.exception.return_value = OperationalError("fail", {}, Exception())
        mock_state.outcome = mock_outcome

        with patch("worker_template.db.retry.LOGGER") as mock_logger:
            _log_retry_attempt(mock_state)

            mock_logger.warning.assert_called_once()
            call_kwargs = mock_logger.warning.call_args
            assert call_kwargs[1]["extra"]["attempt"] == 2

    def test_handles_no_next_action(self):
        from unittest.mock import MagicMock, patch

        mock_state = MagicMock()
        mock_state.attempt_number = 1
        mock_state.next_action = None
        mock_outcome = MagicMock()
        mock_outcome.exception.return_value = OperationalError("fail", {}, Exception())
        mock_state.outcome = mock_outcome

        with patch("worker_template.db.retry.LOGGER") as mock_logger:
            _log_retry_attempt(mock_state)

            mock_logger.warning.assert_called_once()
            extra = mock_logger.warning.call_args[1]["extra"]
            assert extra["wait_seconds"] == 0

    def test_handles_no_outcome(self):
        from unittest.mock import MagicMock, patch

        mock_state = MagicMock()
        mock_state.attempt_number = 1
        mock_state.next_action = None
        mock_state.outcome = None

        with patch("worker_template.db.retry.LOGGER") as mock_logger:
            _log_retry_attempt(mock_state)

            mock_logger.warning.assert_called_once()
            extra = mock_logger.warning.call_args[1]["extra"]
            assert extra["exception_type"] == "unknown"
