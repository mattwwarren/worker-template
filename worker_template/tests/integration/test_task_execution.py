"""Integration tests for TaskExecution service."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from worker_template.models.task_execution import TaskStatus
from worker_template.services.task_execution_service import (
    create_task_execution,
    get_child_tasks,
    get_task_execution,
    list_task_executions,
    update_task_status,
)


@pytest.mark.integration
async def test_create_task_execution(session: AsyncSession):
    tenant_id = uuid4()
    task = await create_task_execution(
        session,
        task_name="test_task",
        tenant_id=tenant_id,
    )
    await session.commit()
    assert task.id is not None
    assert task.task_name == "test_task"
    assert task.status == TaskStatus.PENDING
    assert task.tenant_id == tenant_id


@pytest.mark.integration
async def test_get_task_execution(session: AsyncSession):
    tenant_id = uuid4()
    task = await create_task_execution(
        session,
        task_name="test_task",
        tenant_id=tenant_id,
    )
    await session.commit()

    fetched = await get_task_execution(session, task.id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.id == task.id


@pytest.mark.integration
async def test_get_task_execution_wrong_tenant(session: AsyncSession):
    tenant_id = uuid4()
    task = await create_task_execution(
        session,
        task_name="test_task",
        tenant_id=tenant_id,
    )
    await session.commit()

    wrong_tenant = uuid4()
    fetched = await get_task_execution(session, task.id, tenant_id=wrong_tenant)
    assert fetched is None


@pytest.mark.integration
async def test_update_task_status(session: AsyncSession):
    tenant_id = uuid4()
    task = await create_task_execution(
        session,
        task_name="test_task",
        tenant_id=tenant_id,
    )
    await session.commit()

    updated = await update_task_status(
        session,
        task.id,
        TaskStatus.RUNNING,
        status_message="Processing",
    )
    await session.commit()
    assert updated is not None
    assert updated.status == TaskStatus.RUNNING
    assert updated.started_at is not None


@pytest.mark.integration
async def test_update_task_completed(session: AsyncSession):
    tenant_id = uuid4()
    task = await create_task_execution(
        session,
        task_name="test_task",
        tenant_id=tenant_id,
    )
    await session.commit()

    await update_task_status(session, task.id, TaskStatus.RUNNING)
    await session.commit()

    updated = await update_task_status(
        session,
        task.id,
        TaskStatus.COMPLETED,
        result_url="s3://results/output.pdf",
    )
    await session.commit()
    assert updated is not None
    assert updated.status == TaskStatus.COMPLETED
    assert updated.completed_at is not None
    assert updated.result_url == "s3://results/output.pdf"


@pytest.mark.integration
async def test_list_task_executions(session: AsyncSession):
    tenant_id = uuid4()
    for i in range(3):
        await create_task_execution(
            session,
            task_name=f"task_{i}",
            tenant_id=tenant_id,
        )
    await session.commit()

    tasks = await list_task_executions(session, tenant_id)
    assert len(tasks) == 3


@pytest.mark.integration
async def test_list_with_status_filter(session: AsyncSession):
    tenant_id = uuid4()
    task1 = await create_task_execution(session, task_name="t1", tenant_id=tenant_id)
    await create_task_execution(session, task_name="t2", tenant_id=tenant_id)
    await session.commit()

    await update_task_status(session, task1.id, TaskStatus.RUNNING)
    await session.commit()

    running = await list_task_executions(session, tenant_id, status_filter=TaskStatus.RUNNING)
    assert len(running) == 1
    assert running[0].task_name == "t1"


@pytest.mark.integration
async def test_get_child_tasks(session: AsyncSession):
    tenant_id = uuid4()
    parent = await create_task_execution(
        session,
        task_name="parent",
        tenant_id=tenant_id,
    )
    await session.commit()

    await create_task_execution(
        session,
        task_name="child1",
        tenant_id=tenant_id,
        parent_task_id=parent.id,
    )
    await create_task_execution(
        session,
        task_name="child2",
        tenant_id=tenant_id,
        parent_task_id=parent.id,
    )
    await session.commit()

    children = await get_child_tasks(session, parent.id)
    assert len(children) == 2
    child_names = {c.task_name for c in children}
    assert child_names == {"child1", "child2"}


@pytest.mark.integration
async def test_retry_increments_count(session: AsyncSession):
    tenant_id = uuid4()
    task = await create_task_execution(
        session,
        task_name="retryable",
        tenant_id=tenant_id,
        max_retries=3,
    )
    await session.commit()

    updated = await update_task_status(
        session,
        task.id,
        TaskStatus.RETRYING,
        error_detail="Connection timeout",
    )
    await session.commit()
    assert updated is not None
    assert updated.retry_count == 1
    assert updated.error_detail == "Connection timeout"
