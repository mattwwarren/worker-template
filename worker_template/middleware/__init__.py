"""TaskIQ middleware registration.

Execution order: logging_mw -> tenant -> metrics_mw -> state_tracking
"""

from taskiq import AsyncBroker

from worker_template.middleware.logging_mw import LoggingMiddleware
from worker_template.middleware.metrics_mw import MetricsMiddleware
from worker_template.middleware.state_tracking import StateTrackingMiddleware
from worker_template.middleware.tenant import TenantMiddleware


def register_middleware(broker: AsyncBroker) -> None:
    """Register all middleware on the broker in correct order.

    Order matters: middleware runs pre_execute top-to-bottom,
    post_execute/on_error bottom-to-top.

    Args:
        broker: TaskIQ broker instance
    """
    broker.add_middlewares(
        LoggingMiddleware(),
        TenantMiddleware(),
        MetricsMiddleware(),
        StateTrackingMiddleware(),
    )
