from pydantic import BaseModel, ConfigDict
from typing import Any


class AuditEntry(BaseModel):
    skill: str
    field: str
    original: Any
    corrected: Any
    reason: str
    confidence: float


class SkillResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    record: dict[str, Any]
    audit: list[AuditEntry]
    confidence: float
