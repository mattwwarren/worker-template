"""Shared Pydantic schemas for cross-module use."""

from uuid import UUID

from pydantic import BaseModel


class TaskExecutionInfo(BaseModel):
    """Lightweight task execution reference."""

    id: UUID
    task_name: str
    status: str
