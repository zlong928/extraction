from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


QUEUE_PAYLOAD_SCHEMA_VERSION = 2
QueueTaskType = Literal["paper_parse", "chart_only_run"]


class QueuePayloadError(ValueError):
    """Raised when a Redis message is not a supported queue contract."""


class QueuePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=QUEUE_PAYLOAD_SCHEMA_VERSION, ge=1)
    task_type: QueueTaskType
    job_id: int | None = Field(default=None, gt=0)
    paper_id: int | None = Field(default=None, gt=0)

    @classmethod
    def paper_parse(cls, job_id: int) -> QueuePayload:
        return cls(task_type="paper_parse", job_id=job_id)

    @classmethod
    def chart_only_run(cls, job_id: int) -> QueuePayload:
        return cls(task_type="chart_only_run", job_id=job_id)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> QueuePayload:
        normalized = dict(payload)
        normalized.setdefault("schema_version", 1 if "paper_id" in normalized and "job_id" not in normalized else QUEUE_PAYLOAD_SCHEMA_VERSION)
        try:
            parsed = cls.model_validate(normalized)
        except ValidationError as exc:
            raise QueuePayloadError(f"Invalid queue payload: {exc}") from exc
        if parsed.schema_version not in {1, QUEUE_PAYLOAD_SCHEMA_VERSION}:
            raise QueuePayloadError(
                f"Unsupported queue payload schema_version={parsed.schema_version}; "
                f"expected {QUEUE_PAYLOAD_SCHEMA_VERSION}."
            )
        if parsed.schema_version == QUEUE_PAYLOAD_SCHEMA_VERSION and parsed.job_id is None:
            raise QueuePayloadError("Queue payload schema v2 requires job_id")
        if parsed.schema_version == 1 and parsed.paper_id is None:
            raise QueuePayloadError("Legacy queue payload schema v1 requires paper_id")
        return parsed


def queue_payload(task_type: str, job_id: int) -> dict[str, Any]:
    if task_type == "paper_parse":
        return QueuePayload.paper_parse(job_id).model_dump(exclude_none=True)
    if task_type == "chart_only_run":
        return QueuePayload.chart_only_run(job_id).model_dump(exclude_none=True)
    raise QueuePayloadError(f"Unsupported queue task_type={task_type!r}.")
