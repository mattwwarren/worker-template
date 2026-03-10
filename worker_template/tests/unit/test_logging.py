"""Tests for core logging module: context vars, get_logging_context, log_with_context."""

import logging
from unittest.mock import MagicMock

from worker_template.core.logging import (
    clear_task_context,
    get_logger,
    get_logging_context,
    get_task_id,
    get_task_name,
    get_tenant_id,
    log_with_context,
    set_task_context,
)


class TestSetAndGetTaskContext:
    """Tests for set_task_context / get_* accessors."""

    def setup_method(self):
        clear_task_context()

    def teardown_method(self):
        clear_task_context()

    def test_defaults_are_none(self):
        assert get_task_id() is None
        assert get_tenant_id() is None
        assert get_task_name() is None

    def test_set_all_fields(self):
        set_task_context(task_id="t-1", tenant_id="ten-1", task_name="my_task")

        assert get_task_id() == "t-1"
        assert get_tenant_id() == "ten-1"
        assert get_task_name() == "my_task"

    def test_set_partial_fields(self):
        set_task_context(task_id="t-2")

        assert get_task_id() == "t-2"
        assert get_tenant_id() is None
        assert get_task_name() is None

    def test_overwrite_existing_values(self):
        set_task_context(task_id="old")
        set_task_context(task_id="new")

        assert get_task_id() == "new"

    def test_none_values_are_ignored(self):
        set_task_context(task_id="keep")
        set_task_context(task_id=None)

        assert get_task_id() == "keep"


class TestClearTaskContext:
    def setup_method(self):
        clear_task_context()

    def teardown_method(self):
        clear_task_context()

    def test_clear_resets_all(self):
        set_task_context(task_id="t-1", tenant_id="ten-1", task_name="name")
        clear_task_context()

        assert get_task_id() is None
        assert get_tenant_id() is None
        assert get_task_name() is None


class TestGetLoggingContext:
    def setup_method(self):
        clear_task_context()

    def teardown_method(self):
        clear_task_context()

    def test_returns_dict_with_all_keys(self):
        ctx = get_logging_context()
        assert set(ctx.keys()) == {"task_id", "tenant_id", "task_name"}

    def test_returns_set_values(self):
        set_task_context(task_id="abc", tenant_id="org-1", task_name="proc")
        ctx = get_logging_context()

        assert ctx["task_id"] == "abc"
        assert ctx["tenant_id"] == "org-1"
        assert ctx["task_name"] == "proc"

    def test_returns_none_for_unset(self):
        ctx = get_logging_context()

        assert ctx["task_id"] is None
        assert ctx["tenant_id"] is None
        assert ctx["task_name"] is None


class TestLogWithContext:
    def setup_method(self):
        clear_task_context()

    def teardown_method(self):
        clear_task_context()

    def test_logs_at_correct_level(self):
        logger = MagicMock(spec=logging.Logger)

        log_with_context(logger, logging.WARNING, "test warning")

        logger.log.assert_called_once()
        args = logger.log.call_args
        assert args[0][0] == logging.WARNING
        assert args[0][1] == "test warning"

    def test_merges_context_into_extra(self):
        set_task_context(task_id="ctx-id")
        logger = MagicMock(spec=logging.Logger)

        log_with_context(logger, logging.INFO, "msg", extra={"custom": "val"})

        call_kwargs = logger.log.call_args
        extra = call_kwargs[1]["extra"]
        assert extra["task_id"] == "ctx-id"
        assert extra["custom"] == "val"

    def test_context_without_extra(self):
        set_task_context(task_name="task_a")
        logger = MagicMock(spec=logging.Logger)

        log_with_context(logger, logging.DEBUG, "debug msg")

        call_kwargs = logger.log.call_args
        extra = call_kwargs[1]["extra"]
        assert extra["task_name"] == "task_a"


class TestGetLogger:
    def test_returns_logger_with_name(self):
        logger = get_logger("my.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "my.module"

    def test_returns_logger_with_dunder_name(self):
        logger = get_logger(__name__)
        assert isinstance(logger, logging.Logger)
        assert logger.name == __name__
