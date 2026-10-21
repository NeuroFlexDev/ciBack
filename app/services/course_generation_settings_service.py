from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.course_generation_settings import CourseGenerationSettings
from app.models.domain_enums import CourseStatus
from app.repositories.course_generation_settings import (
    CourseGenerationSettingsRepository,
)
from app.schemas.course_generation_settings import (
    CourseGenerationSettingsResponse,
    CourseGenerationSettingsUpdate,
)


def settings_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "generation_settings_not_found",
            "message": "Настройки генерации курса не найдены",
        },
    )


def settings_response(course, settings) -> CourseGenerationSettingsResponse:
    return CourseGenerationSettingsResponse(
        course_id=course.id,
        revision=settings.revision,
        title=course.name,
        goal=settings.goal,
        target_audience=settings.target_audience,
        difficulty=settings.difficulty,
        language=settings.language,
        lesson_count=settings.lesson_count,
        module_tests_enabled=settings.module_tests_enabled,
        final_test_enabled=settings.final_test_enabled,
        approved_plan=settings.approved_plan or [],
        course_status=course.status,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


def generation_settings_snapshot(course, settings) -> dict:
    return {
        "title": course.name,
        "goal": settings.goal,
        "target_audience": settings.target_audience,
        "difficulty": settings.difficulty,
        "language": settings.language,
        "lesson_count": settings.lesson_count,
        "module_tests_enabled": settings.module_tests_enabled,
        "final_test_enabled": settings.final_test_enabled,
        "approved_plan": settings.approved_plan or [],
        "settings_revision": settings.revision,
    }


class CourseGenerationSettingsService:
    @staticmethod
    def get(
        db: Session, course_id: int, owner_id: int
    ) -> CourseGenerationSettingsResponse:
        course = CourseGenerationSettingsRepository.get_owned_course(
            db, course_id, owner_id
        )
        if course is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
        settings = CourseGenerationSettingsRepository.get_for_course(db, course_id)
        if settings is None:
            raise settings_not_found()
        return settings_response(course, settings)

    @staticmethod
    def upsert(
        db: Session,
        course_id: int,
        owner_id: int,
        payload: CourseGenerationSettingsUpdate,
        *,
        commit: bool = True,
    ) -> CourseGenerationSettingsResponse:
        try:
            course = CourseGenerationSettingsRepository.get_owned_course(
                db, course_id, owner_id, for_update=True
            )
            if course is None:
                raise HTTPException(status_code=404, detail="Курс не найден")
            if course.status == CourseStatus.GENERATING.value:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "course_generation_in_progress",
                        "message": "Нельзя менять настройки во время генерации",
                    },
                )

            settings = CourseGenerationSettingsRepository.get_for_course(
                db, course_id, include_deleted=True
            )
            current_revision = settings.revision if settings is not None else 0
            if payload.expected_revision is not None and payload.expected_revision != current_revision:
                raise HTTPException(409, {"code": "settings_revision_conflict", "message": "Настройки изменены в другой вкладке",
                    "expected_revision": payload.expected_revision, "current_revision": current_revision})
            data = payload.model_dump(exclude={"title", "expected_revision"}, mode="json")
            if settings is None:
                settings = CourseGenerationSettings(
                    course_id=course_id,
                    created_by=owner_id,
                    updated_by=owner_id,
                    **data,
                )
                db.add(settings)
            else:
                changed = course.name != payload.title or any(getattr(settings, field) != value for field, value in data.items())
                for field, value in data.items():
                    setattr(settings, field, value)
                if changed:
                    settings.revision += 1
                settings.updated_by = owner_id
                settings.is_deleted = False

            if course.name != payload.title:
                from app.services.course_publication_service import CoursePublicationService
                before = course.content_revision
                CoursePublicationService.prepare_for_edit(course)
                if before == course.content_revision:
                    course.content_revision += 1
            course.name = payload.title
            course.status = CourseStatus.CONFIGURED.value
            if commit:
                db.commit()
            else:
                db.flush()
            db.refresh(settings)
            db.refresh(course)
            return settings_response(course, settings)
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            raise
