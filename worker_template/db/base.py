"""Import all models so Alembic can discover them."""

from worker_template.models.task_attempt import TaskAttempt  # noqa: F401
from worker_template.models.task_execution import TaskExecution  # noqa: F401
