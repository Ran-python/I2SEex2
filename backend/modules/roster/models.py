"""Course and Student roster tables."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, iso_utc, utcnow

DEFAULT_COURSE_NAME = "Introduction to Software Engineering"


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": iso_utc(self.created_at),
        }


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    id_number: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)   # תעודת זהות
    email: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # True -> נוכח (Present), False -> לא נוכח (Not Present).
    # Automatically set to True when the student registers via QR; only the lecturer can change it manually.
    is_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # True  -> came from an imported roster / added by the lecturer
    # False -> self-registered via QR without matching the roster ("unlisted")
    in_roster: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "course_id": self.course_id,
            "full_name": self.full_name,
            "id_number": self.id_number,
            "email": self.email,
            "is_present": bool(self.is_present),
            "presence_label": "נוכח" if self.is_present else "לא נוכח",
            "in_roster": self.in_roster,
        }
