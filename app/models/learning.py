"""Tenant-scoped learning records. Published content and grade history are append-only."""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from app.database.db import Base


class OrganizationLearningSettings(Base):
    __tablename__ = 'organization_learning_settings'
    organization_id = Column(Integer, ForeignKey('organizations.id'), primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    profile = Column(String(32), nullable=False, default='business')
    languages = Column(JSON, nullable=False, default=lambda: ['ru'])
    role_labels = Column(JSON, nullable=False, default=dict)
    features = Column(JSON, nullable=False, default=dict)
    delivery = Column(JSON, nullable=False, default=dict)
    rules = Column(JSON, nullable=False, default=dict)
    ai_context = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class LearningRoleGrant(Base):
    __tablename__ = 'learning_role_grants'
    __table_args__ = (UniqueConstraint('organization_id', 'user_id', 'role', 'scope_type', 'scope_id', name='uq_learning_role_scope'),)
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    role = Column(String(16), nullable=False)
    scope_type = Column(String(16), nullable=False, default='organization')
    scope_id = Column(Integer, nullable=False, default=0)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)


class LearningDepartment(Base):
    __tablename__ = 'learning_departments'
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    parent_id = Column(Integer, ForeignKey('learning_departments.id'), nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False)


class LearningPerson(Base):
    __tablename__ = 'learning_people'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    department_id = Column(Integer, ForeignKey('learning_departments.id'), nullable=True)
    position = Column(String(200), nullable=False, default='')
    position_id = Column(Integer, ForeignKey("business_positions.id"), nullable=True, index=True)
    external_id = Column(String(200), nullable=True)
    attributes = Column(JSON, nullable=False, default=dict)


class LearningGroup(Base):
    __tablename__ = 'learning_groups'
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default='')
    is_archived = Column(Boolean, nullable=False, default=False)


class LearningGroupMember(Base):
    __tablename__ = 'learning_group_members'
    group_id = Column(Integer, ForeignKey('learning_groups.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    joined_at = Column(DateTime, nullable=False, server_default=func.now())


class PublishedLearningContent(Base):
    __tablename__ = 'published_learning_content'
    course_version_id = Column(Integer, ForeignKey('course_versions.id'), primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=True, index=True)
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class AssessmentDefinition(Base):
    __tablename__ = 'learning_assessment_definitions'
    __table_args__ = (UniqueConstraint('test_id', name='uq_assessment_test'), UniqueConstraint('task_id', name='uq_assessment_task'),
                     CheckConstraint('(test_id IS NULL) <> (task_id IS NULL)', name='ck_assessment_one_parent'))
    id = Column(Integer, primary_key=True)
    test_id = Column(Integer, ForeignKey('tests.id'), nullable=True)
    task_id = Column(Integer, ForeignKey('tasks.id'), nullable=True)
    question_type = Column(String(16), nullable=False, default='single')
    correct_answers = Column(JSON, nullable=False, default=list)
    max_points = Column(Float, nullable=False, default=1)
    criteria = Column(JSON, nullable=False, default=list)
    grading_mode = Column(String(16), nullable=False, default='automatic')
    revision = Column(Integer, nullable=False, default=1)


class LearningAssignment(Base):
    __tablename__ = 'learning_assignments'
    __table_args__ = (UniqueConstraint('organization_id', 'course_version_id', 'user_id', 'cycle_key', name='uq_learning_assignment_cycle'),)
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    course_version_id = Column(Integer, ForeignKey('course_versions.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    assigned_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    group_id = Column(Integer, ForeignKey('learning_groups.id'), nullable=True)
    cycle_key = Column(String(64), nullable=False, default='initial')
    state = Column(String(16), nullable=False, default='active')
    rules_revision = Column(Integer, nullable=False)
    rules = Column(JSON, nullable=False)
    due_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    revoked_at = Column(DateTime, nullable=True)
    progress_revision = Column(Integer, nullable=False, default=0)
    completed_lessons = Column(JSON, nullable=False, default=list)
    last_lesson_id = Column(Integer, nullable=True)
    last_item_key = Column(String(64), nullable=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime, nullable=True)
    prerequisite_id = Column(Integer, ForeignKey('learning_assignments.id'), nullable=True)


class LearningProgressOperation(Base):
    __tablename__ = 'learning_progress_operations'
    __table_args__ = (UniqueConstraint('assignment_id', 'operation_key', name='uq_learning_progress_operation_key'),)
    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey('learning_assignments.id', ondelete='CASCADE'), nullable=False, index=True)
    operation_key = Column(String(64), nullable=False)
    operation_type = Column(String(16), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class LearningGroupEnrollment(Base):
    __tablename__ = 'learning_group_enrollments'
    __table_args__ = (UniqueConstraint('group_id', 'course_version_id', 'cycle_key', name='uq_learning_group_enrollment'),)
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey('learning_groups.id'), nullable=False)
    course_version_id = Column(Integer, ForeignKey('course_versions.id'), nullable=False)
    cycle_key = Column(String(64), nullable=False, default='initial')
    assigned_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    rules_revision = Column(Integer, nullable=False)
    rules = Column(JSON, nullable=False)
    due_at = Column(DateTime, nullable=True)
    include_future_members = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)


class LearningResponse(Base):
    __tablename__ = 'learning_responses'
    __table_args__ = (UniqueConstraint('assignment_id', 'assessment_id', 'attempt', name='uq_learning_response_attempt'),
                     UniqueConstraint('assignment_id', 'submission_key', name='uq_learning_submission_key'))
    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey('learning_assignments.id'), nullable=False, index=True)
    assessment_id = Column(String(64), nullable=False)
    attempt = Column(Integer, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    answer = Column(JSON, nullable=False, default=dict)
    status = Column(String(16), nullable=False, default='draft')
    submission_key = Column(String(64), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    points = Column(Float, nullable=True)
    feedback = Column(Text, nullable=False, default='')
    grading_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class LearningGradeEvent(Base):
    __tablename__ = 'learning_grade_events'
    id = Column(Integer, primary_key=True)
    response_id = Column(Integer, ForeignKey('learning_responses.id'), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    old_points = Column(Float, nullable=True)
    points = Column(Float, nullable=False)
    feedback = Column(Text, nullable=False)
    criteria_scores = Column(JSON, nullable=False, default=list)
    source = Column(String(16), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class LearningProgram(Base):
    __tablename__ = 'learning_programs'
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default='')
    revision = Column(Integer, nullable=False, default=1)
    steps = Column(JSON, nullable=False, default=list)
    recurrence_days = Column(Integer, nullable=True)
    reminder_days = Column(Integer, nullable=False, default=3)
    is_archived = Column(Boolean, nullable=False, default=False)


class LearningProgramEnrollment(Base):
    __tablename__ = 'learning_program_enrollments'
    __table_args__ = (UniqueConstraint('program_id', 'user_id', 'cycle_key', name='uq_learning_program_cycle'),)
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    program_id = Column(Integer, ForeignKey('learning_programs.id'), nullable=False)
    program_revision = Column(Integer, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    cycle_key = Column(String(64), nullable=False)
    snapshot = Column(JSON, nullable=False)
    assignment_ids = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    next_cycle_at = Column(DateTime, nullable=True)


class OrganizationOwnershipTransfer(Base):
    __tablename__ = 'organization_ownership_transfers'
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    from_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    to_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)


class LearningPublicationApproval(Base):
    __tablename__ = 'learning_publication_approvals'
    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey('courses.id'), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    content_digest = Column(String(64), nullable=False)
    revision = Column(Integer, nullable=False)
    reviewer_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    decision = Column(String(16), nullable=False)
    comment = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class SupportAccessGrant(Base):
    __tablename__ = 'support_access_grants'
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    operator_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    scope_type = Column(String(16), nullable=False)
    scope_id = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False)
    requested_hours = Column(Integer, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default='pending')
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    decision_comment = Column(Text, nullable=False, default='')
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(Integer, ForeignKey('users.id'), nullable=True)

