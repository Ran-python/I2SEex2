"""Live & Offline Comments / Suggestions table."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base, iso_utc, utcnow
from modules.attendance.models import Lesson
from modules.roster.models import Course

VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"  # Visible to Lecturer ONLY


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"), nullable=True
    )
    author_student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"), nullable=True, index=True
    )
    author_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default=VISIBILITY_PUBLIC)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    lecturer_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    course: Mapped[Course] = relationship(lazy="joined")
    lesson: Mapped[Lesson | None] = relationship(lazy="joined")

    def is_owned_by(self, viewer_student_id: int | None) -> bool:
        if viewer_student_id is None:
            return False
        owner_id = self.author_student_id if self.author_student_id is not None else self.student_id
        return owner_id == viewer_student_id

    def to_dict(self, viewer_student_id: int | None = None) -> dict:
        course_name = self.course.name if self.course else "Course"
        if self.lesson is not None:
            base = f"Lesson {self.lesson.lesson_number}"
            if self.lesson.title and self.lesson.title.lower() != base.lower():
                lesson_label = f"{base}: {self.lesson.title}"
            else:
                lesson_label = base
        else:
            lesson_label = "General Course"

        is_mine = self.is_owned_by(viewer_student_id)
        owner_id = self.author_student_id if self.author_student_id is not None else self.student_id

        if self.is_anonymous:
            display_author = "Anonymous Student (You)" if is_mine else "Anonymous Student"
            visible_student_id = owner_id if is_mine else None
        else:
            display_author = self.author_name
            visible_student_id = owner_id

        return {
            "id": self.id,
            "course_id": self.course_id,
            "course_name": course_name,
            "lesson_id": self.lesson_id,
            "lesson_number": self.lesson.lesson_number if self.lesson else None,
            "lesson_title": self.lesson.title if self.lesson else None,
            "lesson_label": lesson_label,
            "context_badge": f"{course_name} • {lesson_label}",
            "student_id": visible_student_id,
            "author_name": display_author,
            "is_anonymous": bool(self.is_anonymous),
            "is_mine": bool(is_mine),
            "visibility": self.visibility,
            "content": self.content,
            "lecturer_reply": self.lecturer_reply,
            "resolved": bool(self.resolved),
            "created_at": iso_utc(self.created_at),
        }
