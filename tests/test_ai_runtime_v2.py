import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, SecretStr, ValidationError

from app.ai import gateway
from app.ai.evidence import rank_sources, source_batches
from app.ai.models import model_policy
from app.models.ai_state import AICall, AIResponseCache
from app.models.generation_run import GenerationRun
from app.services.agent_runtime import AgentRuntime, expand_source_refs, _fingerprint
from app.schemas.agentic_pipeline import QAArtifact
from tests.factories import make_course

SOURCE = {'id':'src:doc:1:v1:chunk:1','document_id':1,'document_version':1,
          'document_content_hash':'hash','chunk_id':1,'chunk_index':0,'page':None,'section':None,'quote':'Давление не выше 8 бар.'}

class Value(BaseModel):
    value: int


def make_runtime(db, user, generate):
    course = make_course(db, owner_id=user.id)
    run = GenerationRun(owner_id=user.id,course_id=course.id,run_type='graph_generation',status='running')
    db.add(run);db.commit()
    return AgentRuntime(db=db,run_id=run.id,course_id=course.id,generate=generate)


def execute(runtime, **kw):
    return runtime.execute(agent='ingestion',artifact='example',sequence=0,template_name='unused.j2',
        response_model=Value,prompt_context={'input':'hello'},max_tokens=100,**kw)


def test_sources_are_expanded_only_from_trusted_catalog():
    assert expand_source_refs({'source_refs':[SOURCE['id']]},{SOURCE['id']:SOURCE})['source_refs'] == [SOURCE]
    with pytest.raises(ValueError,match='Unknown'):
        expand_source_refs({'source_refs':['src:fake']},{SOURCE['id']:SOURCE})
    with pytest.raises(ValueError,match='Changed'):
        expand_source_refs({'source_refs':[{**SOURCE,'quote':'Давление до 100 бар.'}]},{SOURCE['id']:SOURCE})


@pytest.mark.parametrize('invalid', [123, {'id': []}, None])
def test_malformed_source_ids_are_repairable_validation_errors(invalid):
    with pytest.raises(ValueError):
        expand_source_refs({'source_refs':[invalid]}, {SOURCE['id']:SOURCE})


def test_competency_indexes_are_derived_only_from_known_evidence():
    from app.ai.curriculum import complete_competency_inventories
    raw = {'source_refs': [], 'source_knowledge_item_ids': [],
           'knowledge': [{'id': 'know:limit', 'source_ref_ids': [SOURCE['id']],
                          'source_knowledge_item_ids': ['kn:limit']}]}
    result = complete_competency_inventories(raw, sources={SOURCE['id']}, knowledge={'kn:limit'})
    assert result['source_refs'] == [SOURCE['id']]
    assert result['source_knowledge_item_ids'] == ['kn:limit']
    assert raw['source_refs'] == []
    with pytest.raises(ValueError, match='Unknown'):
        complete_competency_inventories(raw, sources={SOURCE['id']}, knowledge={'kn:another'})
    with pytest.raises(ValueError, match='Unknown'):
        complete_competency_inventories(raw, sources=set(), knowledge={'kn:limit'})


def test_sdk_truncation_retains_usage_and_is_retryable(monkeypatch):
    from types import SimpleNamespace
    from openai import LengthFinishReasonError
    from app.core.config import settings
    completion = SimpleNamespace(model='model:test', usage=SimpleNamespace(
        prompt_tokens=120, completion_tokens=40, prompt_tokens_details=None))
    class TruncatedLLM:
        def __init__(self, **kwargs): pass
        def bind(self, **kwargs): return self
        def invoke(self, messages): raise LengthFinishReasonError(completion=completion)
    monkeypatch.setattr(settings, 'VSELLM_API_KEY', SecretStr('test-only'))
    monkeypatch.setattr(gateway, 'ChatOpenAI', TruncatedLLM)
    with pytest.raises(HTTPException) as exc:
        gateway.invoke_messages('ingestion', [{'role':'user','content':'Test'}], json_mode=True)
    assert exc.value.ai_usage == {'input_tokens':120,'output_tokens':40,'cached_tokens':0}
    assert gateway.retryable(exc.value)


def test_failed_truncated_call_uses_actual_usage_in_ledger(db_session, auth_user, monkeypatch):
    from app.services import generation_service
    monkeypatch.setattr(gateway, 'configured', lambda: True)
    monkeypatch.setattr(generation_service, 'render_prompt', lambda *a, **kw: 'prompt')
    def generate(*a, **kw):
        error = HTTPException(502, 'Truncated')
        error.ai_usage = {'input_tokens':120,'output_tokens':40,'cached_tokens':20}
        error.ai_model = 'test-model'
        raise error
    runtime = make_runtime(db_session, auth_user, generate)
    runtime.max_attempts = 1
    with pytest.raises(HTTPException): execute(runtime)
    call = db_session.query(AICall).one()
    assert (call.status, call.input_tokens, call.output_tokens, call.cached_tokens) == ('failed',120,40,20)


def test_qa_can_reference_real_ingested_knowledge():
    report = QAArtifact.model_validate({'source_refs':[SOURCE], 'checked_artifact_ids':['kn:pressure_limit'],
        'issues':[], 'verdict':'pass', 'coverage_score':1, 'grounding_score':1,
        'difficulty_score':1,'assessment_quality_score':1,'summary':'All claims checked'})
    assert report.checked_artifact_ids == ['kn:pressure_limit']


def test_rubric_cannot_require_more_than_its_weighted_maximum():
    from app.schemas.agentic_pipeline import AssessmentRubric
    criteria = [{'id':f'criterion:c{i}', 'title':'Check', 'description':'Check procedure', 'weight':0.5,
        'levels':[{'id':'level:no','title':'Missing','description':'Missing step','score':0},
                  {'id':'level:yes','title':'Complete','description':'All steps','score':1}]} for i in range(2)]
    with pytest.raises(ValidationError, match='exceeds maximum'):
        AssessmentRubric.model_validate({'id':'rubric:check','title':'Check','objective_ids':['obj:check'],
            'competency_ids':['cmp:check'],'source_ref_ids':[SOURCE['id']], 'criteria':criteria,'passing_score':1.5})


def test_worker_transient_failure_is_scheduled_for_checkpoint_resume(db_session, auth_user, monkeypatch):
    from types import SimpleNamespace
    from app.workers import generation
    from app.services.pipeline_service import PipelineRunFailed
    runtime = make_runtime(db_session, auth_user, lambda: None)
    monkeypatch.setattr(generation, 'SessionLocal', lambda: db_session)
    monkeypatch.setattr(db_session, 'close', lambda: None)
    monkeypatch.setattr('rq.get_current_job', lambda: SimpleNamespace(retries_left=1))
    def fail(*args, **kwargs):
        run = db_session.get(GenerationRun, runtime.run_id)
        run.status = 'failed'; db_session.commit()
        raise PipelineRunFailed(run.id, 'TimeoutError') from TimeoutError()
    monkeypatch.setattr(generation.PipelineService, 'generate_graph', fail)
    with pytest.raises(RuntimeError, match='resume scheduled'):
        generation.execute_generation_run(runtime.run_id)
    assert db_session.get(GenerationRun, runtime.run_id).status == 'queued'


def test_ingestion_batches_cover_tail_of_every_document():
    catalog = [{**SOURCE,'id':f'src:doc:1:v1:chunk:{i}','quote':'x'*2000} for i in range(100)]
    batches = source_batches(catalog,10000)
    assert len(batches) == 20
    assert [s['id'] for batch in batches for s in batch] == [s['id'] for s in catalog]


def test_retrieval_finds_relevant_tail_not_only_first_chunks():
    sources = [{'id':i,'quote':'Общие сведения о компании'} for i in range(20)]
    sources.append({'id':99,'quote':'При утечке оператор выполняет аварийную остановку'})
    assert rank_sources('аварийная остановка при утечке',sources)[0]['id'] == 99


def test_low_scores_cannot_pass_quality_gate():
    with pytest.raises(ValidationError,match='minimum'):
        QAArtifact.model_validate({'source_refs':[SOURCE],'checked_artifact_ids':['lesson:one'],
            'issues':[],'verdict':'pass','coverage_score':0,'grounding_score':0,
            'difficulty_score':1,'assessment_quality_score':1,'revision_required_for':[],'summary':'ok'})


def test_retry_contains_validation_feedback(db_session,auth_user):
    calls=[]
    def generate(*args,**kwargs):
        calls.append(kwargs)
        return {'value':'invalid'} if len(calls)==1 else {'value':2}
    runtime=make_runtime(db_session,auth_user,generate)
    assert execute(runtime).value == 2
    assert 'value' in calls[1]['repair_feedback']


def test_semantic_validator_retries_before_persisting_success(db_session,auth_user):
    calls=[]
    def generate(*args,**kwargs):
        calls.append(kwargs)
        return {'value':len(calls)}
    runtime=make_runtime(db_session,auth_user,generate)
    def validator(value):
        if value.value < 2: raise ValueError('must be at least 2')
    assert execute(runtime,validator=validator).value == 2
    assert 'at least 2' in calls[1]['repair_feedback']


def test_token_guard_has_no_network_dependency():
    with patch('urllib.request.urlopen',side_effect=AssertionError('network')):
        assert gateway.token_count('Привет') >= 6


def test_lc_engine_preserves_history(monkeypatch):
    from app.chat_engine import lc_engine
    seen=[]
    monkeypatch.setattr(lc_engine,'get_llm',lambda **kw: RunnableLambda(lambda x: seen.append(str(x)) or 'ok'))
    lc_engine.LangChainEngine().generate([{'role':'user','content':'earlier-constraint'},
                                       {'role':'assistant','content':'previous-answer'},
                                       {'role':'user','content':'continue'}])
    assert 'earlier-constraint' in seen[0] and 'previous-answer' in seen[0]


def test_cache_fingerprint_changes_when_schema_or_model_changes():
    assert _fingerprint('unused',Value,{'model':'a'}) != _fingerprint('unused',Value,{'model':'b'})


def test_raw_giga_uses_valid_client_signature(monkeypatch):
    from app.chat_engine import giga_engine
    class Client:
        def generate(self,prompt,max_tokens=1024):
            assert 'earlier' in prompt
            return 'ok'
    monkeypatch.setattr(giga_engine,'get_gigachat_client',lambda m:(Client(),'test'))
    assert giga_engine.GigaEngine().generate([{'role':'user','content':'earlier'}])['text'] == 'ok'


def test_live_budget_stops_before_provider_call(db_session,auth_user,monkeypatch):
    from app.core.config import settings
    from app.services import generation_service
    monkeypatch.setattr(gateway,'configured',lambda:True)
    monkeypatch.setattr(generation_service,'render_prompt',lambda *a,**kw:'prompt')
    monkeypatch.setattr(settings,'AI_MAX_TOKENS_PER_RUN',1)
    calls=[]
    runtime=make_runtime(db_session,auth_user,lambda *a,**kw:calls.append(1) or {'value':1})
    with pytest.raises(HTTPException) as exc: execute(runtime)
    assert exc.value.status_code == 402
    assert calls == []


def test_oversized_context_is_rejected_before_creating_a_billable_call(db_session, auth_user, monkeypatch):
    from app.core.config import settings
    from app.services import generation_service
    monkeypatch.setattr(gateway, 'configured', lambda: True)
    monkeypatch.setattr(generation_service, 'render_prompt', lambda *a, **kw: 'x' * 2000)
    monkeypatch.setattr(settings, 'AI_MAX_INPUT_TOKENS', 1000)
    runtime = make_runtime(db_session, auth_user, lambda *a, **kw: pytest.fail('must not call provider'))
    with pytest.raises(HTTPException) as exc:
        execute(runtime)
    assert exc.value.status_code == 413
    assert db_session.query(AICall).count() == 0


def test_qa_splits_oversized_pairs_without_omitting_lessons(monkeypatch):
    from types import SimpleNamespace
    from app.ai import curriculum
    from app.services.agentic_course_pipeline import AgenticCoursePipeline
    lessons = [SimpleNamespace(id=f'lesson:item{i}') for i in range(3)]
    writer = SimpleNamespace(lessons=lessons)
    def sliced(plan, drafts, ids):
        return None, SimpleNamespace(lessons=[l for l in drafts.lessons if l.id in ids])
    monkeypatch.setattr(curriculum, 'slice_curriculum', sliced)
    monkeypatch.setattr(curriculum, 'assessment_excerpt', lambda *a: None)
    pipeline = AgenticCoursePipeline(runtime=None, checkpoint=lambda *a: None)
    attempts = []
    def review(**kwargs):
        ids = [l.id for l in kwargs['writer'].lessons]
        attempts.append(ids)
        if len(ids) > 1:
            raise HTTPException(413, 'Too much evidence')
        return QAArtifact.model_validate({'source_refs':[SOURCE], 'checked_artifact_ids':ids,
            'issues':[], 'verdict':'pass','coverage_score':1,'grounding_score':1,
            'difficulty_score':1,'assessment_quality_score':1,'summary':'Checked'})
    monkeypatch.setattr(pipeline, '_review_batch', review)
    result = pipeline._review(common={}, ingestion=None, competency_map=None,
        course_plan=None, writer=writer, assessment=None, revision=0)
    assert result.checked_artifact_ids == [l.id for l in lessons]
    assert attempts == [['lesson:item0','lesson:item1'], ['lesson:item0'], ['lesson:item1'], ['lesson:item2']]


def test_learner_text_hides_only_declared_citation_markers():
    from app.ai.evidence import learner_markdown
    assert learner_markdown(f'Правило. [{SOURCE["id"]}]', {SOURCE['id']}) == 'Правило.'
    with pytest.raises(ValueError, match='not declared'):
        learner_markdown('Правило. [src:unknown]', {SOURCE['id']})


def test_cache_is_scoped_to_course_and_owner(db_session,auth_user,monkeypatch):
    from app.services import generation_service
    monkeypatch.setattr(gateway,'configured',lambda:True)
    monkeypatch.setattr(generation_service,'render_prompt',lambda *a,**kw:'prompt')
    calls=[]
    def generate(*a,**kw):
        calls.append(1);return {'value':1,'_model':'test','_usage':{'input_tokens':1,'output_tokens':1}}
    runtime=make_runtime(db_session,auth_user,generate)
    execute(runtime)
    # Retry in the SAME course reuses the validated cache.
    prior=db_session.get(GenerationRun,runtime.run_id);prior.status='failed';db_session.commit()
    run=GenerationRun(owner_id=auth_user.id,course_id=runtime.course_id,run_type='graph_generation',status='running')
    db_session.add(run);db_session.commit()
    execute(AgentRuntime(db=db_session,run_id=run.id,course_id=runtime.course_id,generate=generate))
    assert len(calls)==1
    execute(make_runtime(db_session,auth_user,generate))
    assert len(calls)==2
    assert db_session.query(AIResponseCache).count()==2
