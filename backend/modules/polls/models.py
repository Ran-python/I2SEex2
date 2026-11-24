"""On-the-Spot KISS Polls and Votes tables."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base, iso_utc, utcnow
from modules.roster.models import Course


class Poll(Base):
    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(String(500), nullable=False)
    options_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    checkin_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    course: Mapped[Course] = relationship(lazy="joined")

    @property
    def options(self) -> list[str]:
        try:
            data = json.loads(self.options_json)
            return [str(x) for x in data] if isinstance(data, list) else []
        except Exception:
            return []

    def to_dict(self, vote_counts: dict[int, int] | None = None, my_vote: int | None = None) -> dict:
        opts = self.options
        counts = vote_counts or {}
        total = sum(counts.values())
        breakdown = []
        for idx, label in enumerate(opts):
            cnt = counts.get(idx, 0)
            pct = round((cnt / total) * 100) if total > 0 else 0
            breakdown.append({
                "index": idx,
                "label": label,
                "votes": cnt,
                "percentage": pct,
            })
        return {
            "id": self.id,
            "course_id": self.course_id,
            "course_name": self.course.name if self.course else "Course",
            "lesson_id": self.lesson_id,
            "question": self.question,
            "options": opts,
            "results": breakdown,
            "total_votes": total,
            "my_vote": my_vote,
            "is_active": bool(self.is_active),
            "checkin_url": self.checkin_url,
            "created_at": iso_utc(self.created_at),
            "closed_at": iso_utc(self.closed_at),
        }


class PollVote(Base):
    __tablename__ = "poll_votes"
    __table_args__ = (UniqueConstraint("poll_id", "voter_key", name="uq_poll_voter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[int] = mapped_column(
        ForeignKey("polls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"), nullable=True
    )
    voter_key: Mapped[str] = mapped_column(String(128), nullable=False)
    option_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
