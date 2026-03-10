"""Integration test for example task via InMemoryBroker."""

from uuid import uuid4

import pytest

from worker_template.tasks.contracts import ExampleTaskInput


@pytest.mark.integration
async def test_example_task_via_broker(test_broker):
    """Test example task round-trip through InMemoryBroker."""
    from worker_template.tasks.example_task import example_task

    tenant_id = uuid4()
    task_input = ExampleTaskInput(
        tenant_id=tenant_id,
        document_url="https://example.com/doc.pdf",
        output_format="pdf",
    )

    result = await example_task(task_input.model_dump())
    assert result["success"] is True
    assert "result_url" in result
    assert result["processed_url"] is not None
