"""Homework Submissions & Deep LLM Reviews table."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base, iso_utc, utcnow
from modules.roster.models import Course


class HomeworkSubmission(Base):
    __tablename__ = "homework_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assignment_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"), nullable=True
    )
    student_name: Mapped[str] = mapped_column(String(200), nullable=False)
    submission_type: Mapped[str] = mapped_column(String(32), nullable=False, default="file")
    source_label: Mapped[str] = mapped_column(String(300), nullable=False)
    extracted_content: Mapped[str] = mapped_column(Text, nullable=False)
    score_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    student_review_md: Mapped[str] = mapped_column(Text, nullable=False)
    lecturer_audit_md: Mapped[str] = mapped_column(Text, nullable=False)
    design_metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    course: Mapped[Course] = relationship(lazy="joined")

    @property
    def design_metrics(self) -> dict:
        if not self.design_metrics_json:
            return {}
        try:
            data = json.loads(self.design_metrics_json)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def to_dict(self, include_code_and_audit: bool = False) -> dict:
        data = {
            "id": self.id,
            "course_id": self.course_id,
            "course_name": self.course.name if self.course else "Course",
            "assignment_name": self.assignment_name,
            "student_id": self.student_id,
            "student_name": self.student_name,
            "submission_type": self.submission_type,
            "source_label": self.source_label,
            "score_estimate": self.score_estimate,
            "student_review_md": self.student_review_md,
            "design_metrics": self.design_metrics,
            "created_at": iso_utc(self.created_at),
        }
        if include_code_and_audit:
            data["lecturer_audit_md"] = self.lecturer_audit_md
            data["extracted_content"] = self.extracted_content[:15000]
        return data
