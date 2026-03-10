"""Task registration module.

Import all task modules here to ensure @broker.task decorators
register tasks when the worker starts.
"""

from worker_template.tasks import example_task as example_task
