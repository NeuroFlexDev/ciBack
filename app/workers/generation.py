import logging

from app.database.db import SessionLocal
from app.models.generation_run import GenerationRun
from app.services.pipeline_service import PipelineRunFailed, PipelineService

logger = logging.getLogger(__name__)


def execute_generation_run(run_id: int) -> None:
    db = SessionLocal()
    claimed = False
    try:
        run = db.get(GenerationRun, run_id)
        if run is None or run.status not in {"queued", "running"}:
            return
        from app.services.ai_resources import reserve, claim
        reserve(db, owner_id=run.owner_id, course_id=run.course_id, operation="generation", lane="background",
            key=f"generation:{run.id}", run_id=run.id, queued=True)
        db.commit()
        claimed = claim(db, f"generation:{run.id}")
        if not claimed:
            return
        PipelineService.generate_graph(
            db, course_id=run.course_id, owner_id=run.owner_id, force=True, prepared_run_id=run.id
        )
    except PipelineRunFailed as exc:
        logger.warning("Generation job failed; run_id=%s", run_id)
    except Exception as exc:
        db.rollback()
        from app.services.ai_resources import CapacityBusy
        if isinstance(exc, CapacityBusy) and not claimed:
            # RQ may retry admission only. No provider has been contacted.
            from rq import get_current_job
            job = get_current_job()
            if job is not None and not job.retries_left:
                PipelineService.pause_enqueue(db, run_id)
                return
            raise
        logger.error("Generation worker error; run_id=%s type=%s", run_id, type(exc).__name__)
        PipelineService.fail_run(db, run_id, code="generation_failed")
    finally:
        if claimed:
            from app.services.ai_resources import finish
            finish(db, f"generation:{run_id}", failed=db.get(GenerationRun, run_id).status not in {"succeeded", "completed"})
        db.close()


def execute_document_index_run(run_id: int) -> None:
    db = SessionLocal()
    claimed = False
    try:
        run = db.get(GenerationRun, run_id)
        if run is None or run.run_type != "document_index" or run.status != "queued":
            return
        from app.services.ai_resources import claim
        claimed = claim(db, f"document:{run_id}")
        if not claimed:
            return
        from app.services.file_storage import get_file_storage
        PipelineService.reindex_document(db, document_id=run.document_id, owner_id=run.owner_id,
            storage=get_file_storage(), run_id=run_id)
    except Exception as exc:
        db.rollback()
        logger.error("Document index worker error; run_id=%s type=%s", run_id, type(exc).__name__)
        run = db.get(GenerationRun, run_id)
        if run is not None and run.status in {"queued", "running"}:
            run.status = "failed"
            run.error = "document_index_failed"
            from datetime import datetime
            run.finished_at = datetime.utcnow()
            document = db.get(__import__("app.models.document", fromlist=["Document"]).Document, run.document_id)
            if document is not None and document.status in {"queued", "processing"}:
                document.status = "failed"
            db.commit()
    finally:
        if claimed:
            from app.services.ai_resources import finish
            run = db.get(GenerationRun, run_id)
            finish(db, f"document:{run_id}", failed=run is None or run.status not in {"succeeded", "completed"})
        db.close()
