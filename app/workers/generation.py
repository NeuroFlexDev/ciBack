import logging

from app.database.db import SessionLocal
from app.models.generation_run import GenerationRun
from app.services.pipeline_service import PipelineRunFailed, PipelineService

logger = logging.getLogger(__name__)


def execute_generation_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(GenerationRun, run_id)
        if run is None or run.status not in {"queued", "running"}:
            return
        PipelineService.generate_graph(
            db, course_id=run.course_id, owner_id=run.owner_id, force=True, prepared_run_id=run.id
        )
    except PipelineRunFailed as exc:
        from app.ai.gateway import retryable
        from app.models.course import Course
        from rq import get_current_job
        job = get_current_job()
        logger.warning("Generation job failed; run_id=%s", run_id)
        if job and job.retries_left and exc.__cause__ and retryable(exc.__cause__):
            run = db.get(GenerationRun, run_id)
            run.status = "queued"
            run.finished_at = None
            course = db.get(Course, run.course_id)
            if course:
                course.status = "generating"
            db.commit()
            # Let RQ schedule the same run; LangGraph resumes its checkpoint.
            raise RuntimeError("Transient AI failure; resume scheduled") from None
    except Exception as exc:
        db.rollback()
        logger.error("Generation worker error; run_id=%s type=%s", run_id, type(exc).__name__)
        PipelineService.fail_run(db, run_id, code="generation_failed")
    finally:
        db.close()
