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
        title=course.name,
        goal=settings.goal,
        target_audience=settings.target_audience,
        difficulty=settings.difficulty,
        language=settings.language,
        lesson_count=settings.lesson_count,
        module_tests_enabled=settings.module_tests_enabled,
        final_test_enabled=settings.final_test_enabled,
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
            data = payload.model_dump(exclude={"title"}, mode="json")
            if settings is None:
                settings = CourseGenerationSettings(
                    course_id=course_id,
                    created_by=owner_id,
                    updated_by=owner_id,
                    **data,
                )
                db.add(settings)
            else:
                for field, value in data.items():
                    setattr(settings, field, value)
                settings.updated_by = owner_id
                settings.is_deleted = False

            course.name = payload.title
            course.status = CourseStatus.CONFIGURED.value
            db.commit()
            db.refresh(settings)
            db.refresh(course)
            return settings_response(course, settings)
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            raise
