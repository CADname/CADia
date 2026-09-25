from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    display_name: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


class CommandRequest(BaseModel):
    command: str
    arguments: dict = Field(default_factory=dict)


class SelectionRequest(BaseModel):
    type: str | None = None
    face_ref: str | None = None
    edge_ref: str | None = None
    feature_name: str | None = None
    occurrence_name: str | None = None
    mode: str | None = None


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    model: str | None = None
    effort: str | None = None


class McpTokenCreate(BaseModel):
    label: str = Field(default="MCP client", min_length=1, max_length=120)
    expires_days: int = Field(default=30, ge=1, le=365)


class AIProviderConnectRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    api_key: str | None = Field(default=None, max_length=1000)
    model: str | None = Field(default=None, max_length=160)


class AIProviderSelectRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
