"""Tests for state tracking middleware."""

from unittest.mock import MagicMock
from uuid import uuid4

from worker_template.middleware.state_tracking import StateTrackingMiddleware


class TestStateTrackingExtraction:
    def test_extract_from_labels(self):
        mw = StateTrackingMiddleware()
        task_id = uuid4()
        msg = MagicMock()
        msg.labels = {"task_execution_id": str(task_id)}
        msg.kwargs = {}
        result = mw._extract_task_execution_id(msg)
        assert result == task_id

    def test_extract_from_kwargs(self):
        mw = StateTrackingMiddleware()
        task_id = uuid4()
        msg = MagicMock()
        msg.labels = {}
        msg.kwargs = {"task_execution_id": str(task_id)}
        result = mw._extract_task_execution_id(msg)
        assert result == task_id

    def test_extract_from_raw_input(self):
        mw = StateTrackingMiddleware()
        task_id = uuid4()
        msg = MagicMock()
        msg.labels = {}
        msg.kwargs = {"raw_input": {"task_execution_id": str(task_id)}}
        result = mw._extract_task_execution_id(msg)
        assert result == task_id

    def test_extract_none_when_missing(self):
        mw = StateTrackingMiddleware()
        msg = MagicMock()
        msg.labels = {}
        msg.kwargs = {}
        result = mw._extract_task_execution_id(msg)
        assert result is None

    def test_extract_none_for_invalid_uuid(self):
        mw = StateTrackingMiddleware()
        msg = MagicMock()
        msg.labels = {"task_execution_id": "not-a-uuid"}
        msg.kwargs = {}
        result = mw._extract_task_execution_id(msg)
        assert result is None
