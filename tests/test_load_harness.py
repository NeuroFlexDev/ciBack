import asyncio
from datetime import datetime, timezone
import httpx
import pytest

from app.core.config import settings
from app.models.administration import Organization
from app.models.learning import LearningAssignment, LearningResponse
from app.models.user import User
from scripts.seed_load_fixture import seed, sessions, deactivate, require_staging, private_write
from scripts.load_platform import run, validate_bundle, Measurements, percentile


def bundle(db):
    payload=seed(db,users=2,courses=4,lessons=2)
    sessions(db,payload)
    payload['base_url']='http://localhost/api'
    db.commit()
    return payload


def test_real_api_load_smoke_preserves_grades_and_revokes_only_fixture(client,db_session):
    unrelated=Organization(name='Unrelated customer')
    db_session.add(unrelated);db_session.flush()
    untouched=User(email='unrelated@example.test',password_hash='unused',organization_id=unrelated.id)
    db_session.add(untouched);db_session.commit()
    data=bundle(db_session)
    # Serialize this SQLite test transport, not the real load runner. Every route
    # still goes through real authentication, authorization and transactional services.
    def route(request):
        response=client.request(request.method,request.url.path+('?' + request.url.query.decode() if request.url.query else ''),
                                headers=dict(request.headers),content=request.content)
        return httpx.Response(response.status_code,content=response.content)
    report=asyncio.run(run(data,quick=True,transport=httpx.MockTransport(route)))
    assert report['stop_reason'] is None, report
    assert report['ai_workloads']['paid_cost_rub']==0
    assert {row['endpoint'] for row in report['endpoints']} >= {'identity','tenant_isolation','answer_draft','answer_submit','lesson_progress','assignment_results'}
    responses=db_session.query(LearningResponse).all()
    assert len(responses)==2 and all(row.status=='reviewed' and row.points==1 for row in responses)
    before=db_session.query(LearningAssignment).count()
    result=deactivate(db_session,data);db_session.commit()
    assert result['accounts_deactivated']==4 and not result['data_deleted']
    assert db_session.query(LearningAssignment).count()==before
    assert db_session.get(User,untouched.id).is_active
    denied=client.get('/api/learning/assignments',headers={'Authorization':'Bearer '+data['sessions'][0]['token']})
    assert denied.status_code==401


def test_guard_rejects_production_and_shared_sessions(db_session,monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'ENV','stage')
    with pytest.raises(ValueError):require_staging('https://platform.lernium.ru')
    monkeypatch.setattr(settings,'ENV','production')
    with pytest.raises(ValueError):require_staging('https://dev.platform.lernium.ru')
    data=bundle(db_session)
    data['sessions'][1]['token']=data['sessions'][0]['token']
    with pytest.raises(ValueError,match='Shared tokens'):validate_bundle(data,quick=True)
    output=tmp_path/'private.json';private_write(output,{'token':'private'})
    with pytest.raises(FileExistsError):private_write(output,{'token':'replacement'})


def test_error_budget_request_bound_and_latency_percentiles():
    metrics=Measurements(100)
    for index in range(100):
        assert metrics.permit()
        metrics.record('phase','detail',index/1000,500 if index<6 else 200)
    assert not metrics.permit()
    assert metrics.stop_reason.startswith('server_or_transport_errors')
    assert percentile([1,2,3,4,100],95)==100
    assert metrics.report()['endpoints'][0]['statuses']['500']==6
