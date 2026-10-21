"""Bounded HTTP load against real synthetic sessions; never targets production.

This baseline deliberately makes zero AI requests. OCR/chat/generation need a
separately reviewed provider cost reservation and are reported as untested here.
"""
import argparse
import asyncio
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import time
from urllib.parse import urlparse

import httpx

PHASES = [('25-users', 25, 90, 15), ('100-users', 100, 120, 30),
          ('300-users', 300, 180, 60), ('300-user-spike', 300, 45, 0)]


def percentile(values, percent):
    if not values: return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)], 2)


class Measurements:
    def __init__(self, max_requests=20000):
        self.max_requests = max_requests
        self.requests = 0
        self.records = defaultdict(list)
        self.recent_failures = deque(maxlen=100)
        self.stop_reason = None
        self.inflight = 0
        self.peak_inflight = 0

    def permit(self):
        if self.stop_reason: return False
        if self.requests >= self.max_requests:
            self.stop_reason = 'reviewed_request_ceiling_reached'
            return False
        self.requests += 1
        return True

    def record(self, phase, endpoint, elapsed, status, size=0):
        self.records[(phase, endpoint)].append((elapsed * 1000, status, size))
        self.recent_failures.append(status == 0 or status >= 500)
        if len(self.recent_failures) == 100 and sum(self.recent_failures) > 5:
            self.stop_reason = 'server_or_transport_errors_exceeded_5_percent_of_last_100'
        if status == 401:
            self.stop_reason = 'session_expired_or_revoked'

    def report(self):
        rows = []
        for (phase, endpoint), samples in sorted(self.records.items()):
            latency = [sample[0] for sample in samples]
            statuses = Counter(str(sample[1]) for sample in samples)
            rows.append({'phase': phase, 'endpoint': endpoint, 'requests': len(samples),
                'p50_ms': percentile(latency, 50), 'p95_ms': percentile(latency, 95),
                'p99_ms': percentile(latency, 99), 'max_ms': round(max(latency), 2),
                'statuses': dict(statuses), 'response_bytes': sum(s[2] for s in samples)})
        return {'requests': self.requests, 'peak_inflight_http_requests': self.peak_inflight,
                'stop_reason': self.stop_reason, 'endpoints': rows}


async def request(client, metrics, phase, session, method, path, endpoint, payload=None, expected=(200,)):
    if not metrics.permit(): return None
    started = time.monotonic()
    metrics.inflight += 1
    metrics.peak_inflight = max(metrics.peak_inflight, metrics.inflight)
    try:
        response = await client.request(method, path, headers={'Authorization': 'Bearer '+session['token']}, json=payload)
        metrics.record(phase, endpoint, time.monotonic()-started, response.status_code, len(response.content))
        if response.status_code not in expected:
            if response.status_code in {403, 404, 409, 422}:
                metrics.stop_reason = 'unexpected_application_contract_or_fixture_state'
            return None
        return response.json() if response.content else {}
    except (httpx.TimeoutException, httpx.TransportError):
        metrics.record(phase, endpoint, time.monotonic()-started, 0)
        return None
    finally:
        metrics.inflight -= 1


def validate_bundle(bundle, quick=False):
    url = urlparse(bundle['base_url'])
    loopback = url.hostname in {'localhost', '127.0.0.1', '::1'}
    if not loopback and (url.hostname != 'dev.platform.lernium.ru' or url.scheme != 'https'):
        raise ValueError('Refusing any target other than approved staging/loopback')
    if url.username or url.password or url.query or url.fragment or url.path.rstrip('/') != '/api':
        raise ValueError('Unexpected API target')
    minimum = 2 if quick else 300
    sessions = bundle['sessions']
    if len(sessions) < minimum or len({s['user_id'] for s in sessions}) != len(sessions):
        raise ValueError('Each simulated learner requires a distinct real account')
    if len({s['token'] for s in sessions}) != len(sessions):
        raise ValueError('Shared tokens would invalidate the concurrent-session test')
    if bundle.get('paid_ai_enabled') is not False:
        raise ValueError('Baseline fixture must disable paid AI')
    for session in sessions:
        if len(session['assignments']) < 4: raise ValueError('Four independent phase assignments required')
    issued = datetime.fromisoformat(bundle['tokens_issued_at'])
    remaining = bundle['token_ttl_minutes'] * 60 - (datetime.now(timezone.utc)-issued).total_seconds()
    required = 30 if quick else sum(p[2]+p[3] for p in PHASES)+90
    if remaining < required:
        raise ValueError('Tokens would expire during the reviewed run; refresh the private bundle')


async def learner(client, metrics, name, session, phase_index, duration, delay, seed, think_seconds):
    rng = random.Random(seed)
    await asyncio.sleep(delay)
    deadline = time.monotonic()+duration
    item = session['assignments'][phase_index]
    prefix = '/learning/assignments/'+str(item['id'])
    question = prefix+'/responses/'+item['assessment_id']
    # Each worker reads its real revision before any mutation. No fabricated revisions or retries.
    detail = await request(client, metrics, name, session, 'GET', prefix, 'assignment_detail')
    if not detail: return
    revision = detail['progress_revision']
    if detail['responses']:
        metrics.stop_reason = 'phase_assignment_already_used_create_a_fresh_fixture'
        return
    saved = await request(client, metrics, name, session, 'PUT', question, 'answer_draft',
        {'expected_revision': 0, 'attempt': 1, 'answer': {'selected': ['Check evidence']}})
    if not saved: return
    submitted = False
    iteration = 0
    while time.monotonic() < deadline and not metrics.stop_reason:
        selection = iteration % 7
        if selection == 0:
            await request(client, metrics, name, session, 'GET', '/learning/assignments?limit=20', 'assignment_list')
        elif selection in {1, 2, 4}:
            await request(client, metrics, name, session, 'GET', prefix, 'assignment_detail')
        elif selection == 3:
            result = await request(client, metrics, name, session, 'PUT', prefix+'/progress', 'lesson_progress',
                {'expected_revision': revision, 'lesson_id': item['lesson_ids'][(iteration//7)%len(item['lesson_ids'])], 'completed': True})
            if result: revision = result['progress_revision']
        elif selection == 5 and not submitted:
            result = await request(client, metrics, name, session, 'POST', question+'/submit', 'answer_submit',
                {'expected_revision': saved['revision'], 'attempt': 1, 'answer': {'selected': ['Check evidence']},
                 'submission_key': f'load:{item["id"]}:{phase_index}'})
            if result: submitted = True
        else:
            await request(client, metrics, name, session, 'GET', prefix+'/results', 'assignment_results')
        iteration += 1
        await asyncio.sleep(think_seconds*rng.uniform(0.75, 1.25))


async def run(bundle, *, quick=False, max_requests=20000, transport=None):
    validate_bundle(bundle, quick=quick)
    metrics = Measurements(max_requests)
    phases = [('smoke', 2, 3, 0)] if quick else PHASES
    started = time.monotonic()
    summaries = []
    async with httpx.AsyncClient(base_url=bundle['base_url'].rstrip('/')+'/',
        timeout=httpx.Timeout(15, connect=5), follow_redirects=False,
        limits=httpx.Limits(max_connections=320, max_keepalive_connections=320), transport=transport) as client:
        # A leading slash on httpx relative paths retains the /api base path.
        identity = await request(client, metrics, 'preflight', bundle['sessions'][0], 'GET', '/auth/me', 'identity')
        if not identity or identity.get('id') != bundle['sessions'][0]['user_id']:
            raise ValueError('Authenticated account does not match the private session bundle')
        denied = await request(client, metrics, 'preflight', bundle['foreign'], 'GET',
            '/learning/assignments/'+str(bundle['sessions'][0]['assignments'][0]['id']), 'tenant_isolation', expected=(403,404))
        if denied is None:
            raise ValueError('Cross-organization denial not confirmed')
        for phase_index, (name, count, duration, ramp) in enumerate(phases):
            phase_start = time.monotonic(); before = metrics.requests
            await asyncio.gather(*(learner(client, metrics, name, session, phase_index, duration,
                ramp*index/max(1,count-1), index, 0.03 if quick else 3.0)
                for index, session in enumerate(bundle['sessions'][:count])))
            elapsed = time.monotonic()-phase_start
            summaries.append({'phase': name, 'configured_sessions': count, 'hold_seconds_per_user': duration,
                'ramp_seconds': ramp, 'actual_seconds': round(elapsed, 2),
                'requests_per_second': round((metrics.requests-before)/elapsed,2)})
            if metrics.stop_reason: break
    report = {'run_id': bundle['run_id'], 'checked_at': datetime.now(timezone.utc).isoformat(),
        'target': bundle['base_url'], 'fixture': {k:bundle[k] for k in ('users','courses','lessons_per_course','assignments')},
        'auth': 'Distinct signed JWT sessions; identity and cross-organization denial verified',
        'login_burst_tested': False, 'total_seconds': round(time.monotonic()-started,2), 'phases': summaries,
        'ai_workloads': {'executed':False,'paid_cost_rub':0,
            'reason':'OCR/chat/generation require a separate approved cost reservation and provider qualification'},
        'capacity_claim': 'None. Interpret measured latency, errors and server metrics together; HTTP activity is not simultaneous UI rendering.',
        **metrics.report()}
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--quick',action='store_true')
    parser.add_argument('--max-requests',type=int,default=20000)
    args=parser.parse_args()
    if not 1 <= args.max_requests <= 20000: parser.error('Reviewed ceiling is 20,000 requests')
    if Path(args.report).exists(): parser.error('Refusing to overwrite previous evidence')
    bundle=json.loads(Path(args.bundle).read_text(encoding='utf-8'))
    report=asyncio.run(run(bundle,quick=args.quick,max_requests=args.max_requests))
    Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'requests':report['requests'],'stop_reason':report['stop_reason'],'total_seconds':report['total_seconds']}))
    if report['stop_reason']: raise SystemExit(1)


if __name__=='__main__': main()
