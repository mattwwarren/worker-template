"""Append-only audit records for task retry attempts."""

import enum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field

from worker_template.models.base import TimestampedTable
from worker_template.models.task_execution import TaskStatus


class TaskDispatchResult(enum.StrEnum):
    """Outcome of a retry dispatch decision."""

    BLOCKED = "blocked"
    DISPATCHED = "dispatched"
    DISPATCH_FAILED = "dispatch_failed"
    PENDING = "pending"
    SHADOWED = "shadowed"


class TaskAttempt(TimestampedTable, table=True):
    """Durable record of a retry decision and its dispatch outcome."""

    __tablename__ = "task_attempt"
    __table_args__ = (
        sa.Index("ix_task_attempt_task_execution_created", "task_execution_id", "created_at"),
        sa.Index("ix_task_attempt_tenant_created", "tenant_id", "created_at"),
        sa.CheckConstraint(
            "dispatch_result IN ('blocked', 'dispatched', 'dispatch_failed', 'pending', 'shadowed')",
            name="ck_task_attempt_dispatch_result",
        ),
    )

    task_execution_id: UUID = Field(
        sa_type=PGUUID(as_uuid=True),
        foreign_key="task_execution.id",
        index=True,
    )  # type: ignore[call-overload]
    tenant_id: UUID = Field(sa_type=PGUUID(as_uuid=True), index=True)  # type: ignore[call-overload]
    attempt_number: int = Field(ge=1)
    status_before: TaskStatus = Field(  # type: ignore[call-overload]
        sa_type=sa.Enum(TaskStatus, name="taskstatus", create_constraint=True),
    )
    status_after: TaskStatus = Field(  # type: ignore[call-overload]
        sa_type=sa.Enum(TaskStatus, name="taskstatus", create_constraint=True),
    )
    error_detail: str | None = Field(default=None)
    dispatch_result: TaskDispatchResult = Field(sa_column=sa.Column(sa.String(length=32), nullable=False))
