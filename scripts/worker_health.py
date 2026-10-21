"""Check the RQ process in this container, not an unrelated healthy worker."""
from datetime import datetime, timezone
from pathlib import Path
import socket

from redis import Redis
from rq import Worker
from app.core.config import settings


def healthy(worker, *, hostname=None, now=None, process_exists=None):
    hostname = hostname or socket.gethostname()
    now = now or datetime.now(timezone.utc)
    heartbeat = worker.last_heartbeat
    if not heartbeat or worker.hostname != hostname or not worker.pid:
        return False
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    if not 0 <= (now-heartbeat).total_seconds() < 180:
        return False
    if process_exists is not None:
        return bool(process_exists(worker.pid))
    try:
        return b'rq' in Path(f'/proc/{int(worker.pid)}/cmdline').read_bytes()
    except (OSError, ValueError):
        return False


def main():
    try:
        connection = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=3)
        ok = any(healthy(worker) for worker in Worker.all(connection=connection))
    except Exception:
        ok = False
    if not ok:
        raise SystemExit('No live RQ process with a fresh heartbeat in this container')


if __name__ == '__main__': main()
