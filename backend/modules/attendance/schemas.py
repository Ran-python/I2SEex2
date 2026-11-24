"""Pydantic request schemas for the attendance API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OpenSessionRequest(BaseModel):
    course_id: int | None = None
    lesson_id: int | None = None
    title: str | None = Field(default=None, max_length=200)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius_meters: float = Field(default=50.0, ge=5.0, le=5000.0)
    # Origin URL from window.location.origin (e.g. https://my-app.onrender.com or tunnel URL)
    base_url: str | None = Field(default=None, max_length=500)
    frontend_port: int | None = Field(default=None, ge=1, le=65535)


class CloseSessionRequest(BaseModel):
    lesson_id: int | None = None


class LessonRenameRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    reopen: bool = False
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    radius_meters: float | None = Field(default=None, ge=5.0, le=5000.0)
    base_url: str | None = Field(default=None, max_length=500)
    frontend_port: int | None = Field(default=None, ge=1, le=65535)


class CheckInRequest(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    full_name: str = Field(min_length=2, max_length=200)
    id_number: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    fingerprint: str | None = Field(default=None, min_length=8, max_length=128)
    device_id: str | None = Field(default=None, max_length=64)


class ManualMarkRequest(BaseModel):
    student_id: int
    lesson_id: int | None = None


class PhotoHeadcountUpdate(BaseModel):
    photo_headcount: int | None = Field(default=None, ge=0, le=2000)
    photo_analysis: str | None = Field(default=None, max_length=1000)
