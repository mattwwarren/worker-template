"""Example task demonstrating all worker patterns.

This task shows:
- Pydantic contract validation (input/output)
- Structured logging with context
- Error handling with status transitions
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from taskiq import AsyncTaskiqTask

from worker_template.broker import broker
from worker_template.core.logging import get_logging_context
from worker_template.db import session as db_session
from worker_template.models.task_execution import TaskExecution
from worker_template.services.task_execution_service import create_task_execution
from worker_template.tasks.contracts import ExampleTaskInput, ExampleTaskOutput

LOGGER = logging.getLogger(__name__)

STEP_DOWNLOAD = 1
STEP_PROCESS = 2
STEP_UPLOAD = 3
DEFAULT_PAGE_COUNT = 1


@broker.task
async def example_task(raw_input: dict[str, Any]) -> dict[str, Any]:
    """Process a document as an example task.

    Accepts raw dict (from broker serialization) and validates via Pydantic.
    Returns dict (serialized output) for the result backend.

    Args:
        raw_input: Serialized ExampleTaskInput dict from broker

    Returns:
        Serialized ExampleTaskOutput dict
    """
    task_input = ExampleTaskInput.model_validate(raw_input)
    context = get_logging_context()

    LOGGER.info(
        "example_task_started",
        extra={
            **context,
            "document_url": task_input.document_url,
            "output_format": task_input.output_format,
        },
    )

    # Step 1: Download
    LOGGER.info("example_task_step", extra={**context, "step": "download", "step_num": STEP_DOWNLOAD})

    # Step 2: Process
    LOGGER.info("example_task_step", extra={**context, "step": "process", "step_num": STEP_PROCESS})

    # Step 3: Upload
    LOGGER.info("example_task_step", extra={**context, "step": "upload", "step_num": STEP_UPLOAD})

    result_url = f"s3://results/{task_input.document_url}.{task_input.output_format}"

    output = ExampleTaskOutput(
        success=True,
        result_url=result_url,
        processed_url=result_url,
        page_count=DEFAULT_PAGE_COUNT,
    )

    LOGGER.info("example_task_completed", extra={**context, "result_url": result_url})

    return output.model_dump()


async def dispatch_example_task(
    *,
    tenant_id: UUID,
    document_url: str,
    output_format: str = "pdf",
) -> tuple[TaskExecution, AsyncTaskiqTask[dict[str, Any]]]:
    """Create a tracked PENDING TaskExecution row, commit it, then enqueue example_task.

    Copy this pattern for new tasks: the row id travels inside raw_input and
    the call is keyword-form, so StateTrackingMiddleware and TenantMiddleware
    (which read only message.kwargs/labels, never message.args) engage.
    """
    async with db_session.async_session_maker() as session:
        task_execution = await create_task_execution(session, task_name=example_task.task_name, tenant_id=tenant_id)
        await session.commit()

    task_input = ExampleTaskInput(
        tenant_id=tenant_id,
        document_url=document_url,
        output_format=output_format,
        task_execution_id=task_execution.id,
    )
    kicked = await example_task.kiq(raw_input=task_input.model_dump())
    return task_execution, kicked
