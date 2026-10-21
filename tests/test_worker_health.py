from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from scripts.worker_health import healthy


def test_worker_health_rejects_other_container_stale_or_dead_worker():
    now=datetime.now(timezone.utc)
    row=SimpleNamespace(hostname='this-container',pid=7,last_heartbeat=now-timedelta(seconds=10))
    assert healthy(row,hostname='this-container',now=now,process_exists=lambda pid:pid==7)
    assert not healthy(row,hostname='other-container',now=now,process_exists=lambda pid:True)
    assert not healthy(row,hostname='this-container',now=now,process_exists=lambda pid:False)
    row.last_heartbeat=now-timedelta(seconds=181)
    assert not healthy(row,hostname='this-container',now=now,process_exists=lambda pid:True)
