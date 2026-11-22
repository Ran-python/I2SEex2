"""Pydantic request schemas for the courses + roster API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CourseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)


class StudentCreate(BaseModel):
    course_id: int | None = None
    lesson_id: int | None = None
    full_name: str = Field(min_length=2, max_length=200)
    id_number: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=200)
    # Immediately mark the new student as present (נוכח).
    mark_present: bool = False


class StudentUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    id_number: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=200)


class StudentPresenceUpdate(BaseModel):
    is_present: bool
    lesson_id: int | None = None
