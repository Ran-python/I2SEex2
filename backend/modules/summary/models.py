"""Lecturer-only ML Lecture Video Summary & Pedagogical Evaluation table."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base, iso_utc, utcnow
from modules.attendance.models import Lesson
from modules.roster.models import Course


class LectureSummary(Base):
    __tablename__ = "lecture_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ai_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    pedagogical_evaluation: Mapped[str] = mapped_column(Text, nullable=False)
    points_to_preserve: Mapped[str] = mapped_column(Text, nullable=False)
    points_for_improvement: Mapped[str] = mapped_column(Text, nullable=False)
    action_items: Mapped[str] = mapped_column(Text, nullable=False)
    full_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    course: Mapped[Course] = relationship(lazy="joined")
    lesson: Mapped[Lesson | None] = relationship(lazy="joined")

    def to_dict(self) -> dict:
        course_name = self.course.name if self.course else "Course"
        if self.lesson is not None:
            lesson_label = f"Lesson {self.lesson.lesson_number}: {self.lesson.title}"
        else:
            lesson_label = "General Lecture"
        return {
            "id": self.id,
            "course_id": self.course_id,
            "course_name": course_name,
            "lesson_id": self.lesson_id,
            "lesson_label": lesson_label,
            "source_filename": self.source_filename,
            "ai_provider": self.ai_provider,
            "executive_summary": self.executive_summary,
            "pedagogical_evaluation": self.pedagogical_evaluation,
            "points_to_preserve": self.points_to_preserve,
            "points_for_improvement": self.points_for_improvement,
            "action_items": self.action_items,
            "full_markdown": self.full_markdown,
            "created_at": iso_utc(self.created_at),
        }
