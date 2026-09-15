from app.core.config import settings


def enqueue_generation(run_id: int) -> None:
    if settings.JOB_EAGER:
        from app.workers.generation import execute_generation_run

        execute_generation_run(run_id)
        return
    from redis import Redis
    from rq import Queue, Retry

    queue = Queue(settings.GENERATION_QUEUE_NAME, connection=Redis.from_url(settings.REDIS_URL))
    queue.enqueue("app.workers.generation.execute_generation_run", run_id,
                  job_id=f"generation-run-{run_id}", job_timeout=settings.AI_JOB_TIMEOUT_SECONDS,
                  result_ttl=86400, failure_ttl=604800, retry=Retry(max=2, interval=[30, 120]))
