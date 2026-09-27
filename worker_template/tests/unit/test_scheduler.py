"""Tests for scheduler entrypoint wiring."""

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource


def test_scheduler_wired_to_broker_and_label_source():
    from worker_template.broker import broker
    from worker_template.scheduler import label_source, scheduler

    assert isinstance(scheduler, TaskiqScheduler)
    assert scheduler.broker is broker
    assert isinstance(label_source, LabelScheduleSource)
    assert scheduler.sources == [label_source]
