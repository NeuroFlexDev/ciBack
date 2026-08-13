import logging

from app.database.db import SessionLocal
from app.models.generation_run import GenerationRun
from app.services.pipeline_service import PipelineRunFailed, PipelineService

logger = logging.getLogger(__name__)


def execute_generation_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(GenerationRun, run_id)
        if run is None or run.status != "queued":
            return
        PipelineService.generate_graph(
            db, course_id=run.course_id, owner_id=run.owner_id, force=True, prepared_run_id=run.id
        )
    except PipelineRunFailed:
        logger.exception("Generation job failed", extra={"run_id": run_id})
    except Exception:
        db.rollback()
        logger.exception("Unexpected generation worker error", extra={"run_id": run_id})
        PipelineService.fail_run(db, run_id, code="generation_failed")
    finally:
        db.close()
