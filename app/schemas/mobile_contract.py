"""Response schemas for the native learner pilot API."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.auth import UserResponse


class MobileBootstrapResponse(BaseModel):
    api_origin: str
    display_domain: str
    login_methods: list[str]
    oidc_available: bool
    api_compatibility: str
    minimum_app_version: str
    recommended_app_version: str | None = None
    app_store_url_ios: str | None = None
    app_store_url_android: str | None = None
    push_notifications: bool


class MobileDataPolicyResponse(BaseModel):
    material_downloads: bool = False
    offline_access: bool = False
    cache_ttl_hours: int = 24
    backups: bool = False
    features: dict[str, bool] = Field(default_factory=dict)


class PushDeviceInput(BaseModel):
    token: str
    platform: str


class PushDeviceResponse(BaseModel):
    registered: bool


class MobilePushPreferences(BaseModel):
    assignment_enabled: bool = True
    deadline_enabled: bool = True
    result_enabled: bool = True
    timezone: str = "UTC"


class MobileSessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str
    expires_in: int
    user: UserResponse


class MobileMFAChallengeResponse(BaseModel):
    mfa_required: bool = True
    challenge_token: str
    expires_at: str


class OkResponse(BaseModel):
    ok: bool


class AssignmentSummaryResponse(BaseModel):
    id: int
    course_id: int
    course_version_id: int
    title: str
    revision: int
    user_id: int
    group_id: int | None
    state: str
    status: str
    due_at: datetime | None
    created_at: datetime
    completed_at: datetime | None
    completed_lessons: list[int]
    lesson_count: int
    last_lesson_id: int | None
    last_item_key: str | None
    progress_revision: int
    progress_percent: int
    points: float
    max_points: float
    score_percent: float | None
    pending_reviews: int
    rules: dict[str, Any]
    rules_revision: int


class AssignmentListResponse(BaseModel):
    total: int
    items: list[AssignmentSummaryResponse]


class LearningResponseItem(BaseModel):
    id: int
    assignment_id: int
    assessment_id: str
    attempt: int
    revision: int
    submission_key: str | None = None
    answer: dict[str, Any]
    status: str
    submitted_at: datetime | None
    points: float | None
    feedback: str | None
    updated_at: datetime
    max_points: float
    grading_metadata: dict[str, Any]
    can_retry: bool
    correct_answers: list[str] | None = None


class AssignmentDetailResponse(AssignmentSummaryResponse):
    content: dict[str, Any]
    responses: list[LearningResponseItem]


class ResultsResponse(AssignmentSummaryResponse):
    responses: list[LearningResponseItem]


class AssistantMessageResponse(BaseModel):
    id: int
    request_id: str
    lesson_id: int
    question: str
    answer: str | None
    status: str
    citations: list[dict[str, Any]]
    error_code: str | None
    created_at: datetime
    course_version_id: int
    model: str | None


class AssistantHistoryResponse(BaseModel):
    items: list[AssistantMessageResponse]
    total: int
    limit: int
    offset: int
    retention_days: int
    can_send: bool
    disabled_reason: str | None
