from sqlalchemy.orm import Session
from app.models.course import Course
from typing import Optional, List

class CourseRepository:
    @staticmethod
    def create(db: Session, course_create):
        """
        Создает курс в базе данных.
        """
        new_course = Course(
            name=course_create.title,
            description=course_create.description,
            level=course_create.level,
            language=course_create.language,
        )
        db.add(new_course)
        db.commit()
        db.refresh(new_course)
        return new_course

    @staticmethod
    def list_all(db: Session) -> List[Course]:
        """
        Возвращает список всех курсов, сохраненных в базе данных.
        """
        return db.query(Course).all()

    @staticmethod
    def get_by_id(db: Session, course_id: int) -> Optional[Course]:
        return db.query(Course).filter(Course.id == course_id).first()

    @staticmethod
    def update(db: Session, course: Course, update_data: dict):
        """
        Обновляет данные курса по указанному ID.
        """
        for field, value in update_data.items():
            # Если поле 'title' передается, оно маппится на поле 'name' в модели Course
            if field == "title":
                course.name = value
            else:
                setattr(course, field, value)
        db.commit()
        db.refresh(course)
        return course

    @staticmethod
    def delete(db: Session, course: Course):
        """
        Удаляет курс по указанному ID.
        """
        course.is_deleted = True
        # db.delete(course)
        db.commit()
