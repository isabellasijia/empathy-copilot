from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AnalyzeRequest(BaseModel):
    force: bool = False


class DraftRequest(BaseModel):
    tone: Literal["自然", "简洁", "更关心"] = "自然"
    instruction: str = Field(default="", max_length=200)


class QualityRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class SendRequest(QualityRequest):
    actor: str = Field(default="林小稚", max_length=50)


class ServiceNoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)
    actor: str = Field(default="林小稚", max_length=50)
    complete: bool = False


class IncomingMessageRequest(BaseModel):
    text: str = Field(default="", max_length=2000)
    image_data_url: str | None = Field(default=None, max_length=8_000_000)
    image_name: str | None = Field(default=None, max_length=180)

    @model_validator(mode="after")
    def require_content(self) -> "IncomingMessageRequest":
        if not self.text.strip() and not self.image_data_url:
            raise ValueError("文字和图片至少需要提供一项")
        return self


class RiskUpdateRequest(BaseModel):
    action: Literal[
        "remind",
        "reassign",
        "return",
        "approve",
    ] | None = None
    note: str | None = Field(default=None, max_length=1000)
    resolution: str | None = Field(default=None, max_length=1000)


class ServiceModeRequest(BaseModel):
    mode: Literal["ai", "human"]
    reason: str | None = Field(default=None, max_length=120)
