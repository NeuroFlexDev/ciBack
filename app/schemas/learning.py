from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class LearningRules(StrictInput):
    max_attempts: int = Field(default=3, ge=1, le=100)
    pass_percent: float = Field(default=60, ge=0, le=100)
    require_all_lessons: bool = True
    require_all_assessments: bool = True
    allow_late_submission: bool = False
    reveal_answers: Literal['never', 'after_pass', 'after_final_attempt'] = 'after_pass'
    allow_ai_assistant: bool = True
    allow_ai_grading: bool = False
    require_independent_approval: bool = False
    assignment_overrides: list[Literal['max_attempts', 'pass_percent', 'allow_late_submission', 'reveal_answers']] = Field(default_factory=list)


class SettingsInput(StrictInput):
    expected_revision: int = Field(ge=1)
    profile: Literal['business', 'education', 'training_center', 'government', 'individual'] = 'business'
    languages: list[Literal['ru', 'en']] = Field(default_factory=lambda: ['ru'], min_length=1, max_length=2)
    role_labels: dict[str, str] = Field(default_factory=dict)
    rules: LearningRules = Field(default_factory=LearningRules)
    ai_context: dict[str, str] = Field(default_factory=dict)

    @field_validator('role_labels', 'ai_context')
    @classmethod
    def bounded_text(cls, value):
        if len(value) > 20 or any(len(k) > 80 or len(v) > 2000 for k, v in value.items()):
            raise ValueError('Слишком большой набор настроек')
        return value


class DeliveryInput(StrictInput):
    expected_revision: int = Field(ge=1)
    features: dict[Literal['learning', 'authoring', 'assistant', 'ai_grading', 'programs', 'integrations', 'images'], bool] = Field(default_factory=dict)
    delivery: dict[str, Any] = Field(default_factory=dict)
    profile: Literal['business', 'education', 'training_center', 'government', 'individual'] | None = None
    languages: list[Literal['ru', 'en']] | None = Field(default=None, min_length=1, max_length=2)
    role_labels: dict[str, str] | None = None
    ai_context: dict[str, str] | None = None

    @field_validator('delivery')
    @classmethod
    def neutral_resource_profile(cls, value):
        if 'resource_profile' in value and value['resource_profile'] not in ('pilot', 'team', 'organization', 'custom'):
            raise ValueError('Выберите профиль ресурсов: пилот, команда, организация или индивидуальный')
        return value

    @field_validator('role_labels', 'ai_context')
    @classmethod
    def bounded_optional_text(cls, value):
        return SettingsInput.bounded_text(value) if value is not None else None


class GroupInput(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=10000)


class MemberIds(StrictInput):
    user_ids: list[int] = Field(min_length=1, max_length=1000)


class DepartmentInput(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = Field(default=None, gt=0)


class LifecycleInput(StrictInput):
    expected_state: str | None = Field(default=None, min_length=64, max_length=64)


class GroupUpdate(GroupInput):
    expected_state: str | None = Field(default=None, min_length=64, max_length=64)


class DepartmentUpdate(DepartmentInput):
    expected_state: str | None = Field(default=None, min_length=64, max_length=64)


class RevisionInput(StrictInput):
    expected_revision: int | None = Field(default=None, ge=1)


class PersonInput(StrictInput):
    department_id: int | None = Field(default=None, gt=0)
    position: str = Field(default='', max_length=200)
    external_id: str | None = Field(default=None, max_length=200)
    attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator('attributes', mode='before')
    @classmethod
    def bounded_attributes(cls, value):
        if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
            raise ValueError('Дополнительные поля должны содержать строковые названия и значения')
        if len(value) > 50 or any(not key.strip() or len(key) > 80 or len(item) > 2000 for key, item in value.items()):
            raise ValueError('Дополнительные поля: не более 50, название до 80 и значение до 2000 символов')
        if len({key.strip() for key in value}) != len(value):
            raise ValueError('Названия дополнительных полей не должны повторяться')
        return {key.strip(): item for key, item in value.items()}


class RoleInput(StrictInput):
    role: Literal['admin', 'author', 'reviewer', 'manager', 'member']
    scope_type: Literal['organization', 'course', 'group', 'department'] = 'organization'
    scope_id: int = Field(default=0, ge=0)


class CriterionLevel(StrictInput):
    title: str = Field(default='', max_length=500)
    description: str = Field(default='', max_length=5000)
    points: float = Field(ge=0, le=10000, allow_inf_nan=False)


class Criterion(StrictInput):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    max_points: float = Field(gt=0, le=10000, allow_inf_nan=False)
    description: str = Field(default='', max_length=5000)
    levels: list[CriterionLevel] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def levels_within_scale(self):
        if any(level.points > self.max_points for level in self.levels):
            raise ValueError('Балл уровня не может превышать максимум критерия')
        return self


class AssessmentInput(StrictInput):
    expected_revision: int = Field(default=0, ge=0)
    question_type: Literal['single', 'multiple', 'open'] = 'single'
    correct_answers: list[str] = Field(default_factory=list, max_length=100)
    max_points: float = Field(default=1, gt=0, le=10000)
    criteria: list[Criterion] = Field(default_factory=list, max_length=100)
    grading_mode: Literal['automatic', 'manual', 'ai_assisted'] = 'automatic'

    @model_validator(mode='after')
    def valid_assessment(self):
        if self.question_type == 'open' and self.grading_mode == 'automatic':
            raise ValueError('Открытый ответ требует критериев и проверки')
        if self.question_type != 'open' and not self.correct_answers:
            raise ValueError('Укажите правильные ответы')
        if self.question_type == 'single' and len(self.correct_answers) != 1:
            raise ValueError('Выберите один правильный ответ')
        if self.question_type == 'open' and not self.criteria:
            raise ValueError('Укажите критерии открытого ответа')
        if self.criteria and abs(sum(c.max_points for c in self.criteria) - self.max_points) > 0.0001:
            raise ValueError('Сумма баллов критериев должна совпадать с максимальным баллом')
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError('Критерии должны иметь разные идентификаторы')
        return self


class AssignmentInput(StrictInput):
    expected_rules_revision: int | None = Field(default=None, ge=1)
    user_ids: list[int] = Field(default_factory=list, max_length=1000)
    group_ids: list[int] = Field(default_factory=list, max_length=100)
    course_version_id: int | None = Field(default=None, gt=0)
    due_at: datetime | None = None
    include_future_members: bool = False
    cycle_key: str = Field(default='initial', min_length=1, max_length=64, pattern=r'^[A-Za-z0-9:_-]+$')
    rule_overrides: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def targets(self):
        if not self.user_ids and not self.group_ids:
            raise ValueError('Выберите участников или группы')
        return self


class ProgressInput(StrictInput):
    expected_revision: int = Field(ge=0)
    lesson_id: int = Field(gt=0)
    completed: bool = True


class BookmarkInput(StrictInput):
    expected_revision: int = Field(ge=0)
    item_key: str = Field(min_length=1, max_length=64)


class AnswerInput(StrictInput):
    expected_revision: int = Field(default=0, ge=0)
    attempt: int = Field(default=1, ge=1, le=100)
    answer: dict[str, Any]

    @field_validator('answer')
    @classmethod
    def answer_shape(cls, value):
        import json
        if set(value) - {'text', 'selected'} or len(json.dumps(value, ensure_ascii=False)) > 100000:
            raise ValueError('Ответ должен содержать text или selected, не более 100000 символов')
        if 'text' in value and not isinstance(value['text'], str):
            raise ValueError('Текст ответа должен быть строкой')
        if 'selected' in value and (not isinstance(value['selected'], list) or not all(isinstance(v, str) for v in value['selected'])):
            raise ValueError('Выбранные варианты должны быть строками')
        return value


class SubmitInput(AnswerInput):
    submission_key: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9:_-]+$')


class CriterionScore(StrictInput):
    id: str = Field(min_length=1, max_length=64)
    points: float = Field(ge=0, le=10000)


class GradeInput(StrictInput):
    expected_revision: int = Field(ge=1)
    points: float = Field(ge=0, le=10000)
    feedback: str = Field(min_length=1, max_length=50000)
    criteria_scores: list[CriterionScore] = Field(default_factory=list)


class ProgramStep(StrictInput):
    course_version_id: int = Field(gt=0)
    due_days: int | None = Field(default=None, ge=1, le=3650)


class ProgramInput(StrictInput):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=10000)
    expected_revision: int = Field(default=0, ge=0)
    steps: list[ProgramStep] = Field(min_length=1, max_length=100)
    recurrence_days: int | None = Field(default=None, ge=1, le=3650)
    reminder_days: int = Field(default=3, ge=0, le=365)

    @model_validator(mode='after')
    def unique_steps(self):
        if len({s.course_version_id for s in self.steps}) != len(self.steps):
            raise ValueError('Версия курса не должна повторяться в одной программе')
        return self


class ProgramEnrollInput(StrictInput):
    user_ids: list[int] = Field(min_length=1, max_length=1000)
    cycle_key: str = Field(default='initial', min_length=1, max_length=64, pattern=r'^[A-Za-z0-9:_-]+$')


class OwnerTransferInput(StrictInput):
    user_id: int = Field(gt=0)


class PublicationApprovalInput(StrictInput):
    content_digest: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]+$')
    decision: Literal['approved', 'rejected']
    comment: str = Field(min_length=1, max_length=10000)
