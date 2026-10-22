from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shlex
from types import SimpleNamespace
import pytest
from redis import Redis
from rq import Worker
from scripts.worker_health import healthy


def test_worker_health_rejects_other_container_stale_or_dead_worker():
    now=datetime.now(timezone.utc)
    row=SimpleNamespace(hostname='this-container',pid=7,last_heartbeat=now-timedelta(seconds=10))
    assert healthy(row,hostname='this-container',now=now,process_exists=lambda pid:pid==7)
    assert not healthy(row,hostname='other-container',now=now,process_exists=lambda pid:True)
    assert not healthy(row,hostname='this-container',now=now,process_exists=lambda pid:False)
    row.last_heartbeat=now-timedelta(seconds=181)
    assert not healthy(row,hostname='this-container',now=now,process_exists=lambda pid:True)


@pytest.mark.parametrize('profile', ['deploy/dev-platform/docker-compose.yml', 'deploy/customer/compose.yml'])
def test_deployed_idle_dequeue_fits_strict_worker_health_window(profile):
    text = (Path(__file__).resolve().parents[1] / profile).read_text(encoding='utf8')
    section = re.split(r'(?m)^  [\w-]+:', text.split('\n  worker:\n', 1)[1], maxsplit=1)[0]
    inline = re.search(r'^    command: (.+)$', section, re.MULTILINE)
    if inline:
        command = json.loads(inline[1])
    else:
        block = re.search(r'^    command:\n((?:      - .+\n)+)', section, re.MULTILINE)
        assert block, 'worker command missing'
        command = [shlex.split(line.strip()[2:])[0] for line in block[1].splitlines()]
    ttl = int(command[command.index('--worker-ttl') + 1])
    # This constructs the installed RQ implementation without contacting Redis.
    worker = Worker(['generation'], connection=Redis(), worker_ttl=ttl, prepare_for_work=False)
    assert ttl == 120
    assert worker.dequeue_timeout == 105
    assert worker.job_monitoring_interval == 30
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(hostname='worker-container', pid=7,
                          last_heartbeat=now-timedelta(seconds=worker.dequeue_timeout+30))
    assert healthy(row, hostname='worker-container', now=now, process_exists=lambda pid: True)
    row.last_heartbeat = now-timedelta(seconds=180)
    assert not healthy(row, hostname='worker-container', now=now, process_exists=lambda pid: True)
