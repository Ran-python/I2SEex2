"""Pydantic request schemas for the attendance API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OpenSessionRequest(BaseModel):
    course_id: int | None = None
    lesson_id: int | None = None
    title: str | None = Field(default=None, max_length=200)
    # Port the browser used to load the frontend (5173 for Vite dev, 8000 when the backend
    # serves the built frontend). The host part of the QR URL is always the detected LAN IP.
    frontend_port: int | None = Field(default=None, ge=1, le=65535)


class CloseSessionRequest(BaseModel):
    lesson_id: int | None = None


class LessonRenameRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    reopen: bool = False
    frontend_port: int | None = Field(default=None, ge=1, le=65535)


class CheckInRequest(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    full_name: str = Field(min_length=2, max_length=200)
    id_number: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=200)
    fingerprint: str | None = Field(default=None, min_length=8, max_length=128)
    device_id: str | None = Field(default=None, max_length=64)


class ManualMarkRequest(BaseModel):
    student_id: int
    lesson_id: int | None = None
