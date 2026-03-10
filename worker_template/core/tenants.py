"""Tenant isolation for worker tasks via ContextVar.

Simplified tenant isolation for workers. Unlike the FastAPI template which uses
HTTP middleware for tenant extraction, workers extract tenant_id from task kwargs
(via Pydantic contract) and set it in a ContextVar.

Usage:
    from worker_template.core.tenants import set_tenant_id, get_tenant_id

    # In middleware (automatic)
    set_tenant_id(task_input.tenant_id)

    # In task code
    tenant = get_tenant_id()
    stmt = select(Model).where(Model.tenant_id == tenant)
"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID

_tenant_id_var: ContextVar[UUID | None] = ContextVar("tenant_id", default=None)


def set_tenant_id(tenant_id: UUID) -> None:
    """Set tenant ID in context for current async task.

    Args:
        tenant_id: UUID of the tenant/organization
    """
    _tenant_id_var.set(tenant_id)


def get_tenant_id() -> UUID | None:
    """Get tenant ID from context.

    Returns:
        Tenant UUID if set, None otherwise
    """
    return _tenant_id_var.get()


def clear_tenant_id() -> None:
    """Clear tenant ID from context."""
    _tenant_id_var.set(None)
