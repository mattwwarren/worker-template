"""Pydantic data contracts for task inputs and outputs.

All task inputs/outputs are Pydantic models. This ensures:
- Type-safe serialization to message broker (model_dump -> JSON)
- Validated deserialization on worker (model_validate -> typed object)
- Failed validation = reject message (no silent corruption)

Usage:
    class MyTaskInput(TaskInput):
        document_url: str
        output_format: str = "pdf"

    class MyTaskOutput(TaskOutput):
        processed_url: str | None = None
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

MIN_PRIORITY = 0
MAX_PRIORITY = 10
DEFAULT_PRIORITY = 5


class TaskInput(BaseModel):
    """Base for all task inputs. Every task carries tenant context."""

    tenant_id: UUID
    priority: int = Field(default=DEFAULT_PRIORITY, ge=MIN_PRIORITY, le=MAX_PRIORITY)
    parent_task_id: UUID | None = None


class TaskOutput(BaseModel):
    """Base for all task outputs."""

    success: bool
    result_url: str | None = None
    error_detail: str | None = None


class ExampleTaskInput(TaskInput):
    """Example task input demonstrating extension pattern."""

    document_url: str
    output_format: str = "pdf"


class ExampleTaskOutput(TaskOutput):
    """Example task output demonstrating extension pattern."""

    processed_url: str | None = None
    page_count: int | None = None
