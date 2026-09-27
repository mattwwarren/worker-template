"""Integration tests for TaskExecution service."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from worker_template.broker import broker
from worker_template.middleware import register_middleware
from worker_template.middleware.state_tracking import StateTrackingMiddleware
from worker_template.models.task_execution import TaskStatus
from worker_template.services.task_execution_service import (
    create_task_execution,
    get_child_tasks,
    get_task_execution,
    list_task_executions,
    update_task_status,
)

_ATTEMPT_COUNTS: dict[str, int] = {}


@broker.task
async def _flaky_retry_task(raw_input: dict[str, Any]) -> dict[str, Any]:
    """Fail `fail_times` times for a given attempt_key, then succeed."""
    attempt_key = raw_input["attempt_key"]
    fail_times = raw_input["fail_times"]
    _ATTEMPT_COUNTS[attempt_key] = _ATTEMPT_COUNTS.get(attempt_key, 0) + 1
    if _ATTEMPT_COUNTS[attempt_key] <= fail_times:
        error_msg = "flaky failure"
        raise RuntimeError(error_msg)
    return {"success": True}


@broker.task
async def _always_failing_task(raw_input: dict[str, Any]) -> dict[str, Any]:
    """Always raise, to exercise retry exhaustion."""
    error_msg = "always fails"
    raise RuntimeError(error_msg)


@pytest.fixture
async def retry_broker(
    test_broker: Any,
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Point StateTrackingMiddleware at the test DB and run retries synchronously."""
    monkeypatch.setattr("worker_template.middleware.state_tracking.async_session_maker", session_maker)
    monkeypatch.setattr("worker_template.middleware.state_tracking.settings.task_retry_enabled", True)
    monkeypatch.setattr("worker_template.middleware.state_tracking.settings.task_retry_shadow_mode", False)
    if not any(isinstance(mw, StateTrackingMiddleware) for mw in test_broker.middlewares):
        register_middleware(test_broker)
    original_await_inplace = test_broker.await_inplace
    test_broker.await_inplace = True
    try:
        yield test_broker
    finally:
        test_broker.await_inplace = original_await_inplace


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


@pytest.mark.integration
async def test_flaky_task_retries_until_success(
    session: AsyncSession,
    session_maker: async_sessionmaker[AsyncSession],
    retry_broker: Any,
) -> None:
    tenant_id = uuid4()
    task = await create_task_execution(
        session,
        task_name="_flaky_retry_task",
        tenant_id=tenant_id,
        max_retries=2,
    )
    await session.commit()

    attempt_key = str(uuid4())
    await _flaky_retry_task.kiq(
        raw_input={
            "task_execution_id": str(task.id),
            "tenant_id": str(tenant_id),
            "attempt_key": attempt_key,
            "fail_times": 1,
        }
    )

    async with session_maker() as verify_session:
        updated = await get_task_execution(verify_session, task.id)
    assert updated is not None
    assert updated.status == TaskStatus.COMPLETED
    assert updated.retry_count == 1


@pytest.mark.integration
async def test_always_failing_task_exhausts_retries(
    session: AsyncSession,
    session_maker: async_sessionmaker[AsyncSession],
    retry_broker: Any,
) -> None:
    tenant_id = uuid4()
    task = await create_task_execution(
        session,
        task_name="_always_failing_task",
        tenant_id=tenant_id,
        max_retries=1,
    )
    await session.commit()

    await _always_failing_task.kiq(
        raw_input={
            "task_execution_id": str(task.id),
            "tenant_id": str(tenant_id),
        }
    )

    async with session_maker() as verify_session:
        updated = await get_task_execution(verify_session, task.id)
    assert updated is not None
    assert updated.status == TaskStatus.FAILED
    assert updated.retry_count == 1
