"""Tests for Pydantic task contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from worker_template.tasks.contracts import (
    ExampleTaskInput,
    ExampleTaskOutput,
    TaskInput,
    TaskOutput,
)


class TestTaskInput:
    def test_valid_input(self):
        inp = TaskInput(tenant_id=uuid4())
        assert inp.priority == 5
        assert inp.parent_task_id is None

    def test_priority_range(self):
        inp = TaskInput(tenant_id=uuid4(), priority=0)
        assert inp.priority == 0
        inp = TaskInput(tenant_id=uuid4(), priority=10)
        assert inp.priority == 10

    def test_priority_too_high(self):
        with pytest.raises(ValidationError):
            TaskInput(tenant_id=uuid4(), priority=11)

    def test_priority_too_low(self):
        with pytest.raises(ValidationError):
            TaskInput(tenant_id=uuid4(), priority=-1)


class TestTaskOutput:
    def test_success_output(self):
        out = TaskOutput(success=True, result_url="s3://bucket/result.pdf")
        assert out.success is True
        assert out.error_detail is None

    def test_failure_output(self):
        out = TaskOutput(success=False, error_detail="Processing failed")
        assert out.success is False


class TestExampleContracts:
    def test_example_input(self):
        inp = ExampleTaskInput(
            tenant_id=uuid4(),
            document_url="https://example.com/doc.pdf",
        )
        assert inp.output_format == "pdf"

    def test_example_input_serialization(self):
        inp = ExampleTaskInput(
            tenant_id=uuid4(),
            document_url="https://example.com/doc.pdf",
            output_format="html",
        )
        data = inp.model_dump()
        restored = ExampleTaskInput.model_validate(data)
        assert restored.document_url == inp.document_url
        assert restored.output_format == "html"

    def test_example_output(self):
        out = ExampleTaskOutput(
            success=True,
            processed_url="s3://results/doc.pdf",
            page_count=5,
        )
        assert out.page_count == 5
