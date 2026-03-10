"""Scheduler entrypoint for TaskIQ cron jobs.

Run with: taskiq scheduler worker_template.scheduler:scheduler

Runs as a separate deployment. Cron schedules defined via task labels:

    @broker.task(schedule=[{"cron": "0 */6 * * *"}])
    async def periodic_cleanup(...): ...
"""

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from worker_template.broker import broker

# LabelScheduleSource reads schedule metadata from @broker.task(schedule=[...]) labels
label_source = LabelScheduleSource(broker)

# The scheduler object is the entrypoint for `taskiq scheduler`
scheduler = TaskiqScheduler(broker=broker, sources=[label_source])
