"""Tenant middleware for extracting tenant_id from task kwargs."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from taskiq import TaskiqMessage, TaskiqMiddleware

from worker_template.core.logging import set_task_context
from worker_template.core.tenants import clear_tenant_id, set_tenant_id

LOGGER = logging.getLogger(__name__)

RAW_INPUT_KEY = "raw_input"
TENANT_ID_KEY = "tenant_id"


class TenantMiddleware(TaskiqMiddleware):
    """Extract tenant_id from task kwargs and set ContextVar.

    Tasks receive input as a dict with a tenant_id field (from TaskInput contract).
    This middleware extracts it and sets the tenant ContextVar for downstream use.
    """

    async def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        """Extract tenant_id from task kwargs."""
        tenant_id = self._extract_tenant_id(message.kwargs)
        if tenant_id is not None:
            set_tenant_id(tenant_id)
            set_task_context(tenant_id=str(tenant_id))
            LOGGER.debug(
                "tenant_context_set",
                extra={"tenant_id": str(tenant_id), "task_id": message.task_id},
            )
        return message

    async def post_execute(self, message: TaskiqMessage, result: Any) -> None:  # noqa: ANN401
        """Clear tenant context after task execution."""
        clear_tenant_id()

    async def on_error(
        self,
        message: TaskiqMessage,
        result: Any,  # noqa: ANN401
        exception: BaseException,
    ) -> None:
        """Clear tenant context on error."""
        clear_tenant_id()

    def _extract_tenant_id(self, kwargs: dict[str, Any]) -> UUID | None:
        """Extract tenant_id from task kwargs.

        Supports both direct kwargs and nested raw_input dict patterns.

        Args:
            kwargs: Task keyword arguments from broker message

        Returns:
            Extracted tenant UUID or None
        """
        # Direct kwarg: tenant_id=UUID(...)
        if TENANT_ID_KEY in kwargs:
            return self._parse_uuid(kwargs[TENANT_ID_KEY])

        # Nested in raw_input dict (Pydantic contract pattern)
        raw_input = kwargs.get(RAW_INPUT_KEY)
        if isinstance(raw_input, dict) and TENANT_ID_KEY in raw_input:
            return self._parse_uuid(raw_input[TENANT_ID_KEY])

        return None

    def _parse_uuid(self, value: object) -> UUID | None:
        """Parse a value to UUID, returning None on failure."""
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (ValueError, AttributeError):
            return None
