"""Tests for middleware pipeline."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from worker_template.core.logging import get_task_id
from worker_template.core.tenants import get_tenant_id as get_ctx_tenant_id
from worker_template.middleware.logging_mw import LoggingMiddleware
from worker_template.middleware.tenant import TenantMiddleware


def make_message(task_id=None, task_name="test_task", kwargs=None, labels=None):
    """Create a mock TaskIQ message."""
    msg = MagicMock()
    msg.task_id = task_id or str(uuid4())
    msg.task_name = task_name
    msg.kwargs = kwargs or {}
    msg.labels = labels or {}
    return msg


def make_result(is_err=False, error=None, return_value=None):
    """Create a mock TaskIQ result."""
    result = MagicMock()
    result.is_err = is_err
    result.error = error
    result.return_value = return_value
    return result


class TestLoggingMiddleware:
    @pytest.fixture
    def middleware(self):
        return LoggingMiddleware()

    async def test_pre_execute_sets_context(self, middleware):
        msg = make_message(task_id="abc-123", task_name="my_task")
        await middleware.pre_execute(msg)
        assert get_task_id() == "abc-123"

    async def test_post_execute_success(self, middleware):
        msg = make_message()
        result = make_result(is_err=False)
        # Should not raise
        await middleware.post_execute(msg, result)

    async def test_post_execute_error(self, middleware):
        msg = make_message()
        result = make_result(is_err=True, error="something failed")
        await middleware.post_execute(msg, result)


class TestTenantMiddleware:
    @pytest.fixture
    def middleware(self):
        return TenantMiddleware()

    async def test_extracts_tenant_from_raw_input(self, middleware):
        tenant_id = uuid4()
        msg = make_message(kwargs={"raw_input": {"tenant_id": str(tenant_id)}})
        await middleware.pre_execute(msg)
        assert get_ctx_tenant_id() == tenant_id

    async def test_extracts_tenant_from_direct_kwarg(self, middleware):
        tenant_id = uuid4()
        msg = make_message(kwargs={"tenant_id": str(tenant_id)})
        await middleware.pre_execute(msg)
        assert get_ctx_tenant_id() == tenant_id

    async def test_no_tenant_id(self, middleware):
        msg = make_message(kwargs={})
        await middleware.pre_execute(msg)
        # Should not set tenant

    async def test_post_execute_clears_tenant(self, middleware):
        tenant_id = uuid4()
        msg = make_message(kwargs={"tenant_id": str(tenant_id)})
        await middleware.pre_execute(msg)
        await middleware.post_execute(msg, MagicMock())
        assert get_ctx_tenant_id() is None
