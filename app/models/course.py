from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.database.db import Base

class Course(Base):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    level = Column(String, nullable=True)
    language = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    current_graph_id = Column(
        Integer,
        ForeignKey(
            "course_graphs.id",
            name="fk_courses_current_graph_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )

    # Связь с модулями
    owner = relationship("User", back_populates="courses")
    modules = relationship("Module", back_populates="course", cascade="all, delete-orphan")
    course_modules = relationship(
        "CourseModule", back_populates="course", cascade="all, delete-orphan"
    )
    documents = relationship(
        "Document", back_populates="course", cascade="all, delete-orphan"
    )
    graphs = relationship(
        "CourseGraph",
        back_populates="course",
        foreign_keys="CourseGraph.course_id",
        cascade="all, delete-orphan",
    )
    current_graph = relationship(
        "CourseGraph", foreign_keys=[current_graph_id], post_update=True
    )
