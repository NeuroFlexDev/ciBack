"""Opt-in, bounded live evaluation. Reads credentials from a file without printing them.
Run: python scripts/evaluate_ai.py --credentials ../creeds --output /tmp/ai-evaluation.json
"""
import argparse,json,os,sys,tempfile,time
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--credentials',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--lessons',type=int,default=2,choices=[2,4])
args=parser.parse_args()
workspace=tempfile.TemporaryDirectory(prefix='lernium-ai-eval-')
os.environ.update(DATABASE_URL='sqlite:///'+str(Path(workspace.name)/'eval.db'),
    DEBUG='false',ENV='evaluation',JWT_SECRET='evaluation-only-jwt-secret-at-least-32-bytes',
    VSELLM_API_KEY=args.credentials.read_text().strip(),
    AI_CHECKPOINT_SQLITE_PATH=str(Path(workspace.name)/'checkpoints.sqlite'),
    AI_MAX_CALLS_PER_RUN='32',AI_MAX_TOKENS_PER_RUN='400000',AI_MAX_REVISIONS='1')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app.models
from app.database.db import Base,engine,SessionLocal
from app.models.user import User
from app.models.course import Course
from app.models.generation_run import GenerationRun
from app.models.ai_state import AICall
from app.services.agent_runtime import AgentRuntime
from app.services.generation_service import generate_from_prompt
from app.services.agentic_course_pipeline import AgenticCoursePipeline
Base.metadata.create_all(engine)
session=SessionLocal()
user=User(email='evaluation@example.invalid',password_hash='unused');session.add(user);session.flush()
course=Course(owner_id=user.id,name='Безопасный запуск установки',status='ready');session.add(course);session.flush()
run=GenerationRun(owner_id=user.id,course_id=course.id,run_type='graph_generation',status='running');session.add(run);session.commit()
sources=[{'id':'src:doc:1:v1:chunk:1','document_id':1,'document_version':1,'document_content_hash':'synthetic-v1','chunk_id':1,'chunk_index':0,'page':1,'section':'Запуск',
'quote':'Перед запуском оператор проверяет давление по манометру. При давлении выше 8 бар запуск запрещён. Оператор сообщает о превышении начальнику смены и не запускает установку до устранения причины. Если давление не превышает 8 бар и ограждения закрыты, оператор выполняет запуск кнопкой Пуск. Время проверки не регламентировано.'},
{'id':'src:doc:1:v1:chunk:2','document_id':1,'document_version':1,'document_content_hash':'synthetic-v1','chunk_id':2,'chunk_index':1,'page':2,'section':'Остановка',
'quote':'При обнаружении утечки оператор нажимает Аварийный стоп и сообщает начальнику смены. Самостоятельный ремонт оператором запрещён. После остановки оператор записывает время, показание манометра и причину остановки в журнал. Разрешение на повторный запуск даёт начальник смены после устранения причины.'}]
started=time.monotonic()
report={'success':False,'stages':[], 'repairs':[]}
def tracked_generate(*args, **kwargs):
 if kwargs.get('repair_feedback'):
  report['repairs'].append({'agent': kwargs.get('agent_role'), 'diagnostic': kwargs['repair_feedback'][:1500]})
 return generate_from_prompt(*args, **kwargs)
try:
 from app.models.document import Document, DocumentChunk
 from app.ai.retrieval import persist_embeddings, PersistentVectorStore
 from app.services.retrieval_service import RetrievalService
 doc=Document(owner_id=user.id,course_id=course.id,storage_key='synthetic.txt',version=1,
     status='indexed',content_hash='synthetic-v1',source_type='upload',original_filename='synthetic.txt',
     mime_type='text/plain',size_bytes=sum(len(s['quote'].encode()) for s in sources))
 session.add(doc);session.flush()
 chunks=[DocumentChunk(document_id=doc.id,document_version=1,chunk_index=i,text=s['quote'],page=i+1) for i,s in enumerate(sources)]
 session.add_all(chunks);session.flush()
 for source,chunk in zip(sources,chunks):
  source.update(document_id=doc.id,chunk_id=chunk.id)
 embedding_ids=persist_embeddings(session,chunks)
 for chunk,embedding_id in zip(chunks,embedding_ids): chunk.embedding_id=embedding_id
 session.commit()
 found=RetrievalService.search_course(session,course_id=course.id,owner_id=user.id,
     query='Что делать при утечке?',limit=2,vector_store=PersistentVectorStore(session))
 assert found.citations and all(c.document_id==doc.id for c in found.citations)
 report['embeddings_and_retrieval']=True
 result=AgenticCoursePipeline(runtime=AgentRuntime(db=session,run_id=run.id,course_id=course.id,generate=tracked_generate),
   checkpoint=lambda stage,progress:(print(stage,progress,flush=True),report['stages'].append(stage))).run(
   course_title=course.name,settings_snapshot={'goal':'Безопасно запускать и останавливать установку','target_audience':'Новые операторы',
   'difficulty':'basic','language':'ru','lesson_count':args.lessons,'module_tests_enabled':True,'final_test_enabled':True},source_catalog=sources)
 from app.services.course_materialization_service import CourseMaterializationService
 from app.services.course_publication_service import CoursePublicationService
 from app.models.course_graph import CourseGraph
 from app.models.course_source_link import CourseSourceLink
 from app.services.source_catalog_service import graph_source_links
 graph=CourseGraph(course_id=course.id,version=1,nodes=[],edges=[],created_by=user.id,status='draft')
 session.add(graph);session.flush();course.current_graph=graph
 materialized=CourseMaterializationService.materialize(session,course=course,nodes=result.nodes,edges=result.edges)
 graph.nodes=materialized.pop('canvas_nodes');graph.edges=materialized.pop('canvas_edges')
 learning_map=CourseMaterializationService.materialize_learning_map(session,course=course,result=result.result)
 links=graph_source_links(graph.nodes,sources)
 session.add_all([CourseSourceLink(course_id=course.id,graph_id=graph.id,run_id=run.id,**link) for link in links])
 run.status='completed';session.commit()
 publication=CoursePublicationService.publish(session,course.id,user.id)
 report.update(success=True,qa=result.qa_summary,nodes=result.nodes,edges=result.edges,
     materialized=materialized,learning_map=learning_map,source_links=len(links),publication=publication)
 report['artifacts']=result.result.model_dump(mode='json')
except Exception as exc:
 report['error_type']=type(exc).__name__
 report['error']=str(exc)[:2000] if type(exc).__name__ in {'ValueError','ValidationError'} else 'See usage status; provider details redacted'
finally:
 session.rollback()
 report['seconds']=round(time.monotonic()-started,2)
 report['calls']=[{'agent':c.agent,'model':c.model,'status':c.status,'input_tokens':c.input_tokens,'output_tokens':c.output_tokens,'error_code':c.error_code} for c in session.query(AICall).all()]
 args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
 print(json.dumps({k:v for k,v in report.items() if k not in {'nodes','edges','artifacts'}},ensure_ascii=False,default=str),flush=True)
 session.close();workspace.cleanup()
 sys.exit(0 if report['success'] else 1)
