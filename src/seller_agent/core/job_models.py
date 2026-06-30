from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


JobStatus = Literal[
    "created",
    "queued",
    "running",
    "waiting_confirmation",
    "success",
    "partial_success",
    "failed",
    "timeout",
    "cancelled",
]

ApprovalStatus = Literal[
    "draft",
    "pending_review",
    "approved",
    "applying",
    "applying_unknown",
    "applied",
    "verified",
    "closed",
]


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    task_id: str
    status: JobStatus
    actor: str
    created_at: str
    updated_at: str
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    started_at: str = ""
    finished_at: str = ""


@dataclass(frozen=True)
class JobEvent:
    job_id: str
    event_type: str
    created_at: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    event_id: int | None = None


@dataclass(frozen=True)
class TelegramUpdateRecord:
    update_id: int
    chat_id: str
    command: str
    processing_status: str
    received_at: str
    job_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceLease:
    resource_key: str
    owner_id: str
    acquired_at: str
    expires_at: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    status: ApprovalStatus
    source_job_id: str
    created_at: str
    updated_at: str
    owner_job_id: str = ""
    checksum: str = ""
    data: dict[str, Any] = field(default_factory=dict)
