"""TaskExecution model for tracking async task state."""

import enum
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field

from worker_template.models.base import TimestampedTable


class TaskStatus(enum.StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


class TaskExecution(TimestampedTable, table=True):
    __tablename__ = "task_execution"
    __table_args__ = (sa.Index("ix_task_execution_tenant_status", "tenant_id", "status"),)

    task_name: str = Field(max_length=255, index=True)
    status: TaskStatus = Field(  # type: ignore[call-overload]
        default=TaskStatus.PENDING,
        sa_type=sa.Enum(TaskStatus, name="taskstatus", create_constraint=True),
    )
    total_steps: int | None = Field(default=None)
    completed_steps: int = Field(default=0)
    status_message: str | None = Field(default=None, max_length=1000)
    tenant_id: UUID = Field(  # type: ignore[call-overload]
        sa_type=PGUUID(as_uuid=True),
        index=True,
    )
    parent_task_id: UUID | None = Field(  # type: ignore[call-overload]
        default=None,
        sa_type=PGUUID(as_uuid=True),
        foreign_key="task_execution.id",
        index=True,
    )
    config_snapshot: dict[str, object] | None = Field(default=None, sa_type=JSONB)
    result_url: str | None = Field(default=None, max_length=2048)
    error_detail: str | None = Field(default=None)
    started_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))  # type: ignore[call-overload]
    completed_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))  # type: ignore[call-overload]
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)
