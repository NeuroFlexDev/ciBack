"""Recheck current authorization between background AI calls and checkpoints."""
from sqlalchemy import select
from app.models.course import Course
from app.models.generation_run import GenerationRun
from app.services.course_access import course_access_filter


def generation_access_active(db, run_id):
    # Read SQL columns, not a cached ORM user/organization captured at enqueue.
    run = db.execute(select(GenerationRun.owner_id, GenerationRun.course_id).where(GenerationRun.id == run_id)).first()
    if run is None or run.course_id is None:
        return False
    return db.execute(select(Course.id).where(Course.id == run.course_id, Course.is_deleted.is_(False), course_access_filter(run.owner_id))).first() is not None
