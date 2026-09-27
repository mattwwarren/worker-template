"""Append-only audit records for task retry attempts."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field

from worker_template.models.base import TimestampedTable
from worker_template.models.task_execution import TaskStatus


class TaskAttempt(TimestampedTable, table=True):
    """Durable record of a retry decision and its dispatch outcome."""

    __tablename__ = "task_attempt"
    __table_args__ = (
        sa.Index("ix_task_attempt_task_execution_created", "task_execution_id", "created_at"),
        sa.Index("ix_task_attempt_tenant_created", "tenant_id", "created_at"),
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
    dispatch_result: str = Field(max_length=32)
