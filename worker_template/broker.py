"""TaskIQ broker singleton.

Uses InMemoryBroker for testing, AioPikaBroker + RedisAsyncResultBackend
for production.
"""

from __future__ import annotations

import os

from taskiq import AsyncBroker, InMemoryBroker

TASKIQ_ENV = os.environ.get("TASKIQ_ENV", "production")

broker: AsyncBroker

if TASKIQ_ENV == "test":
    broker = InMemoryBroker()
else:
    from taskiq_aio_pika import AioPikaBroker
    from taskiq_redis import RedisAsyncResultBackend

    from worker_template.core.config import settings

    broker = AioPikaBroker(
        url=settings.rabbitmq_url,
        queue_name=settings.rabbitmq_queue_name,
        max_priority=settings.rabbitmq_max_priority,
    ).with_result_backend(RedisAsyncResultBackend(redis_url=settings.redis_url))
