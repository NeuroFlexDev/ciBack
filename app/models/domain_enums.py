from enum import Enum


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    ARCHIVED = "archived"


class CourseGraphStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class CourseStatus(str, Enum):
    DRAFT = "draft"
    CONFIGURED = "configured"
    GENERATING = "generating"
    READY = "ready"
    GENERATION_FAILED = "generation_failed"


class CourseDifficulty(str, Enum):
    INTERNSHIP = "internship"
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class CourseLanguage(str, Enum):
    RU = "ru"
    EN = "en"


class GenerationRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GenerationRunType(str, Enum):
    DOCUMENT_INDEX = "document_index"
    GRAPH_GENERATION = "graph_generation"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
