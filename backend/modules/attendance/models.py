"""Attendance tables: lessons (sessions), records, device locks and lecturer alerts."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base, iso_utc, utcnow
from modules.roster.models import Course, Student

STATUS_PRESENT = "present"     # checked in via QR and matched the roster
STATUS_MANUAL = "manual"       # marked present by the lecturer
STATUS_UNLISTED = "unlisted"   # checked in via QR but not found in the roster

LESSON_OPEN = "open"
LESSON_CLOSED = "closed"


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("course_id", "lesson_number", name="uq_course_lesson_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=LESSON_OPEN)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    qr_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    checkin_url: Mapped[str] = mapped_column(String(500), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    radius_meters: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    photo_headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    photo_analysis: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    photo_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    course: Mapped[Course] = relationship(lazy="joined")

    @property
    def token(self) -> str:
        return self.qr_token

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.status == LESSON_OPEN and self.closed_at is None and now < self.expires_at

    def seconds_remaining(self, now: datetime | None = None) -> int:
        if not self.is_active(now):
            return 0
        return max(0, int((self.expires_at - (now or utcnow())).total_seconds()))

    def effective_status(self, now: datetime | None = None) -> str:
        return LESSON_OPEN if self.is_active(now) else LESSON_CLOSED

    def to_dict(self, present_count: int | None = None) -> dict:
        now = utcnow()
        active = self.is_active(now)
        data = {
            "id": self.id,
            "course_id": self.course_id,
            "course_name": self.course.name if self.course else None,
            "lesson_number": self.lesson_number,
            "title": self.title,
            "status": LESSON_OPEN if active else LESSON_CLOSED,
            "qr_token": self.qr_token,
            "token": self.qr_token,
            "checkin_url": self.checkin_url,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "radius_meters": self.radius_meters if self.radius_meters is not None else 50.0,
            "photo_headcount": self.photo_headcount,
            "photo_analysis": self.photo_analysis,
            "photo_checked_at": iso_utc(self.photo_checked_at),
            "opened_at": iso_utc(self.opened_at),
            "expires_at": iso_utc(self.expires_at),
            "closed_at": iso_utc(self.closed_at if self.closed_at else (None if active else self.expires_at)),
            "active": active,
            "seconds_remaining": self.seconds_remaining(now),
        }
        if present_count is not None:
            data["present_count"] = present_count
        return data


# Backwards-compatible alias
AttendanceSession = Lesson


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("lesson_id", "student_id", name="uq_lesson_student"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    device_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    student_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    student_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)

    student: Mapped[Student] = relationship(lazy="joined")


class DeviceLock(Base):
    """One row per successful QR registration; blocks the device for DEVICE_LOCK_SECONDS."""
    __tablename__ = "device_locks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cookie_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class AttendanceAlert(Base):
    """System alert raised when an unlisted student checks in via QR."""
    __tablename__ = "attendance_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    id_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lesson_id": self.lesson_id,
            "session_id": self.lesson_id,
            "student_id": self.student_id,
            "full_name": self.full_name,
            "id_number": self.id_number,
            "email": self.email,
            "created_at": iso_utc(self.created_at),
        }
