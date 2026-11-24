"""Attendance business logic: lessons/sessions, GPS + Radius QR check-in, device lockout, dashboard, export."""
from __future__ import annotations

import io
import math
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import qrcode
from qrcode.constants import ERROR_CORRECT_M

if TYPE_CHECKING:
    import pandas as pd
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import config
from core.database import iso_utc, utcnow
from core.network import resolve_app_base_url
from modules.attendance.models import (
    LESSON_CLOSED,
    LESSON_OPEN,
    STATUS_MANUAL,
    STATUS_PRESENT,
    STATUS_UNLISTED,
    AttendanceAlert,
    AttendanceRecord,
    DeviceLock,
    Lesson,
)
from modules.roster.models import Course, Student
from modules.roster.service import (
    clean_text,
    find_student,
    list_courses,
    normalize_email,
    normalize_id_number,
    resolve_course,
)

# Serialises check-ins so two simultaneous requests from one device can't both pass the lock check.
_checkin_lock = threading.Lock()
EARTH_RADIUS_METERS = 6_371_000.0


def calculate_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance in meters between two GPS coordinates using the Haversine formula."""
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    delta_phi = math.radians(float(lat2) - float(lat1))
    delta_lambda = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return round(EARTH_RADIUS_METERS * c, 2)


class AttendanceError(Exception):
    """Domain error translated into an HTTP response by the router."""

    def __init__(self, status_code: int, message: str, **extra):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.extra = extra


# ---------------------------------------------------------------------------
# Lessons / Sessions
# ---------------------------------------------------------------------------
def _sync_expired_lessons(db: Session) -> None:
    """Ensure lessons whose 15-minute window has elapsed have status='closed' in SQLite."""
    now = utcnow()
    expired = db.scalars(
        select(Lesson).where(
            Lesson.status == LESSON_OPEN,
            or_(Lesson.closed_at.is_not(None), Lesson.expires_at <= now),
        )
    ).all()
    if expired:
        for lesson in expired:
            lesson.status = LESSON_CLOSED
            if lesson.closed_at is None:
                lesson.closed_at = lesson.expires_at
        db.commit()


def get_active_session(db: Session, course_id: int | None = None) -> Lesson | None:
    _sync_expired_lessons(db)
    now = utcnow()
    stmt = select(Lesson).where(
        Lesson.status == LESSON_OPEN,
        Lesson.closed_at.is_(None),
        Lesson.expires_at > now,
    )
    if course_id is not None:
        stmt = stmt.where(Lesson.course_id == course_id)
    return db.scalar(stmt.order_by(Lesson.id.desc()))


def get_current_session(db: Session, course_id: int | None = None) -> Lesson | None:
    """The active lesson, or the most recent one (so results stay visible after closing)."""
    active = get_active_session(db, course_id=course_id)
    if active is not None:
        return active
    stmt = select(Lesson)
    if course_id is not None:
        stmt = stmt.where(Lesson.course_id == course_id)
    return db.scalar(stmt.order_by(Lesson.lesson_number.desc(), Lesson.id.desc()))


def next_lesson_number(db: Session, course_id: int) -> int:
    max_num = db.scalar(select(func.max(Lesson.lesson_number)).where(Lesson.course_id == course_id))
    return (max_num or 0) + 1


def build_checkin_url(
    token: str,
    frontend_port: int | None = None,
    base_url: str | None = None,
) -> str:
    resolved = resolve_app_base_url(request=None, base_url=base_url, frontend_port=frontend_port)
    return f"{resolved}/attendance/checkin?token={token}"


def open_session(
    db: Session,
    frontend_port: int | None,
    course_id: int | None = None,
    title: str | None = None,
    base_url: str | None = None,
    latitude: float = 0.0,
    longitude: float = 0.0,
    radius_meters: float = 50.0,
) -> Lesson:
    course = resolve_course(db, course_id)
    now = utcnow()

    # Close any currently active lesson so only one QR session is open at a time.
    for active in db.scalars(
        select(Lesson).where(Lesson.status == LESSON_OPEN, Lesson.closed_at.is_(None))
    ):
        active.status = LESSON_CLOSED
        active.closed_at = now

    num = next_lesson_number(db, course.id)
    lesson_title = clean_text(title) or f"Lesson {num}"
    token = secrets.token_urlsafe(18)

    # Reset is_present to False ("לא נוכח") for all students in this course when a new lesson starts.
    db.execute(
        update(Student).where(Student.course_id == course.id).values(is_present=False)
    )

    lesson = Lesson(
        course_id=course.id,
        lesson_number=num,
        title=lesson_title,
        status=LESSON_OPEN,
        qr_token=token,
        checkin_url=build_checkin_url(token, frontend_port=frontend_port, base_url=base_url),
        latitude=float(latitude),
        longitude=float(longitude),
        radius_meters=float(radius_meters),
        opened_at=now,
        expires_at=now + timedelta(seconds=config.SESSION_DURATION_SECONDS),
    )
    db.add(lesson)
    db.commit()
    db.refresh(lesson)
    return lesson


def reopen_lesson(
    db: Session,
    lesson_id: int,
    frontend_port: int | None,
    base_url: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_meters: float | None = None,
) -> Lesson:
    """Re-open an existing lesson's QR session for another 15 minutes."""
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise AttendanceError(404, "Lesson not found.")
    now = utcnow()
    for active in db.scalars(
        select(Lesson).where(Lesson.status == LESSON_OPEN, Lesson.closed_at.is_(None))
    ):
        active.status = LESSON_CLOSED
        active.closed_at = now

    token = secrets.token_urlsafe(18)
    lesson.status = LESSON_OPEN
    lesson.closed_at = None
    lesson.opened_at = now
    lesson.expires_at = now + timedelta(seconds=config.SESSION_DURATION_SECONDS)
    lesson.qr_token = token
    lesson.checkin_url = build_checkin_url(token, frontend_port=frontend_port, base_url=base_url)
    if latitude is not None:
        lesson.latitude = float(latitude)
    if longitude is not None:
        lesson.longitude = float(longitude)
    if radius_meters is not None:
        lesson.radius_meters = float(radius_meters)
    db.commit()
    db.refresh(lesson)
    return lesson


def rename_lesson(db: Session, lesson_id: int, title: str) -> Lesson:
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise AttendanceError(404, "Lesson not found.")
    cleaned = clean_text(title)
    if not cleaned:
        raise AttendanceError(422, "Lesson title cannot be empty.")
    lesson.title = cleaned
    db.commit()
    return lesson


def delete_lesson(db: Session, lesson_id: int) -> None:
    """Delete a lesson and its attendance records/alerts, renumbering subsequent lessons."""
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise AttendanceError(404, "Lesson not found.")
    course_id = lesson.course_id
    deleted_num = lesson.lesson_number

    db.execute(delete(AttendanceAlert).where(AttendanceAlert.lesson_id == lesson_id))
    db.execute(delete(AttendanceRecord).where(AttendanceRecord.lesson_id == lesson_id))
    db.execute(update(DeviceLock).where(DeviceLock.lesson_id == lesson_id).values(lesson_id=None))
    db.delete(lesson)
    db.flush()

    # Shift subsequent lessons in ascending order so lesson_number stays sequential (1, 2, 3...)
    subsequent = db.scalars(
        select(Lesson)
        .where(Lesson.course_id == course_id, Lesson.lesson_number > deleted_num)
        .order_by(Lesson.lesson_number.asc())
    ).all()
    for sub in subsequent:
        old_num = sub.lesson_number
        new_num = old_num - 1
        if sub.title == f"Lesson {old_num}":
            sub.title = f"Lesson {new_num}"
        sub.lesson_number = new_num
        db.flush()

    db.commit()


def close_session(db: Session, lesson_id: int | None = None) -> Lesson | None:
    now = utcnow()
    lesson = db.get(Lesson, lesson_id) if lesson_id else get_active_session(db)
    if lesson and lesson.is_active(now):
        lesson.status = LESSON_CLOSED
        lesson.closed_at = now
        db.commit()
        return lesson
    return None


def list_lessons_for_course(db: Session, course_id: int | None = None) -> list[dict]:
    _sync_expired_lessons(db)
    course = resolve_course(db, course_id)
    lessons = db.scalars(
        select(Lesson)
        .where(Lesson.course_id == course.id)
        .order_by(Lesson.lesson_number.desc(), Lesson.id.desc())
    ).all()
    return [l.to_dict() for l in lessons]


def update_photo_headcount(
    db: Session,
    lesson_id: int,
    headcount: int | None,
    analysis: str | None = None,
) -> dict:
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise AttendanceError(404, "Lesson not found.")
    lesson.photo_headcount = headcount
    lesson.photo_analysis = clean_text(analysis) if analysis else None
    lesson.photo_checked_at = utcnow() if headcount is not None else None
    db.commit()
    db.refresh(lesson)

    registered = (
        db.scalar(
            select(func.count(AttendanceRecord.id)).where(AttendanceRecord.lesson_id == lesson.id)
        )
        or 0
    )
    discrepancy = (registered - headcount) if headcount is not None else 0
    return {
        "lesson": lesson.to_dict(present_count=registered),
        "registered_count": registered,
        "photo_headcount": headcount,
        "photo_analysis": lesson.photo_analysis,
        "discrepancy": discrepancy,
        "discrepancy_alert": bool(headcount is not None and registered > headcount),
    }


def analyze_classroom_photo(
    db: Session,
    lesson_id: int,
    image_bytes: bytes,
    mime_type: str,
    ai_cfg,
) -> dict:
    from core.ai import AIError, analyze_image, extract_json_object

    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise AttendanceError(404, "Lesson not found.")
    if not image_bytes:
        raise AttendanceError(400, "The uploaded image file is empty.")

    system_prompt = (
        "You are an expert computer-vision classroom attendance auditor. "
        "Carefully count every distinct student seated in the lecture hall/classroom photo. "
        "Do not count empty chairs or laptops without a person. "
        "Return ONLY a JSON object with keys: "
        '{"headcount": <integer>, "row_breakdown": "<short summary by front/middle/back rows>", '
        '"confidence": "high|medium|low", "notes": "<1-2 sentence summary>"}'
    )
    user_prompt = (
        "Count all visible students seated in this classroom photo. "
        "Respond strictly with valid JSON containing headcount (integer), row_breakdown, confidence, and notes."
    )
    try:
        raw_reply, model_used = analyze_image(
            ai_cfg,
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=user_prompt,
            system_prompt=system_prompt,
        )
    except AIError as exc:
        raise AttendanceError(exc.status_code, exc.message) from exc

    parsed = extract_json_object(raw_reply)
    count_val = parsed.get("headcount")
    if count_val is None:
        nums = re.findall(r"\b(\d{1,3})\b", raw_reply)
        count_val = int(nums[0]) if nums else 0
    else:
        try:
            count_val = max(0, int(count_val))
        except Exception:
            count_val = 0

    breakdown = parsed.get("row_breakdown") or ""
    confidence = parsed.get("confidence") or "high"
    notes = parsed.get("notes") or raw_reply[:300]
    summary = f"Counted {count_val} students ({confidence} confidence via {model_used})."
    if breakdown:
        summary += f" Rows: {breakdown}."
    elif notes:
        summary += f" {notes}"

    res = update_photo_headcount(db, lesson_id=lesson.id, headcount=count_val, analysis=summary[:950])
    res["model_used"] = model_used
    return res


def render_qr_png(data: str) -> bytes:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=12, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Device lockout
# ---------------------------------------------------------------------------
@dataclass
class DeviceIdentity:
    cookie_token: str | None
    fingerprint: str | None
    device_id: str | None


def device_lock_remaining(db: Session, device: DeviceIdentity) -> int:
    """Seconds until this device may register again (0 = not locked)."""
    conditions = []
    if device.cookie_token:
        conditions.append(DeviceLock.cookie_token == device.cookie_token)
    if device.device_id:
        conditions.append(DeviceLock.device_id == device.device_id)
    if device.fingerprint:
        conditions.append(DeviceLock.fingerprint == device.fingerprint)
    if not conditions:
        return 0
    now = utcnow()
    lock = db.scalar(
        select(DeviceLock)
        .where(DeviceLock.expires_at > now, or_(*conditions))
        .order_by(DeviceLock.expires_at.desc())
    )
    if lock is None:
        return 0
    return max(1, int((lock.expires_at - now).total_seconds()))


def format_remaining(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    if minutes and secs:
        return f"{minutes} min {secs} sec"
    if minutes:
        return f"{minutes} min"
    return f"{secs} sec"


def clear_device_locks(db: Session) -> int:
    result = db.execute(delete(DeviceLock))
    db.commit()
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Check-in (student, via QR)
# ---------------------------------------------------------------------------
@dataclass
class CheckInResult:
    student: Student
    lesson: Lesson
    record: AttendanceRecord
    lock: DeviceLock
    unlisted: bool


def check_in(
    db: Session,
    *,
    token: str,
    full_name: str,
    id_number: str | None,
    email: str | None,
    latitude: float | None,
    longitude: float | None,
    device: DeviceIdentity,
) -> CheckInResult:
    with _checkin_lock:
        lesson = get_active_session(db)
        if lesson is None or not secrets.compare_digest(lesson.qr_token, token):
            raise AttendanceError(410, "Attendance is currently closed or this QR code has expired.")

        if latitude is None or longitude is None:
            raise AttendanceError(
                400,
                "GPS location coordinates (latitude and longitude) are required to check in. "
                "Please enable location services in your browser and try again.",
            )

        distance_meters: float | None = None
        if lesson.latitude is not None and lesson.longitude is not None:
            distance_meters = calculate_distance_meters(
                lesson.latitude,
                lesson.longitude,
                latitude,
                longitude,
            )
            allowed_radius = float(lesson.radius_meters if lesson.radius_meters is not None else 50.0)
            if distance_meters > allowed_radius:
                raise AttendanceError(
                    403,
                    f"You are outside the allowed classroom radius "
                    f"(Distance: {round(distance_meters)}m, Allowed: {round(allowed_radius)}m).",
                    distance_meters=distance_meters,
                    allowed_radius_meters=allowed_radius,
                )

        effective_fp = device.fingerprint or (f"fp_id_{device.device_id}" if device.device_id else None)
        if not effective_fp:
            raise AttendanceError(400, "Missing device fingerprint. Please reload the page and try again.")
        device.fingerprint = effective_fp

        remaining = device_lock_remaining(db, device)
        if remaining:
            raise AttendanceError(
                429,
                "This device was already used to register. "
                f"Please try again in {format_remaining(remaining)}.",
                retry_after_seconds=remaining,
            )

        name = clean_text(full_name)
        if not name or len(name) < 2:
            raise AttendanceError(422, "Please enter your full name.")
        raw_email = clean_text(email)
        norm_email = normalize_email(raw_email)
        if raw_email and not norm_email:
            raise AttendanceError(422, "The email address looks invalid.")
        norm_id = normalize_id_number(id_number)

        student = find_student(db, lesson.course_id, name, norm_id, norm_email)
        unlisted = student is None or not student.in_roster
        if student is None:
            # Automatically add the new student permanently to this course's roster
            # while still flagging the check-in record and raising a lecturer alert.
            student = Student(
                course_id=lesson.course_id,
                full_name=name,
                id_number=norm_id,
                email=norm_email,
                is_present=True,
                in_roster=True,
            )
            db.add(student)
            db.flush()
        else:  # enrich the roster entry with any details the student provided
            student.in_roster = True
            if norm_id and not student.id_number:
                student.id_number = norm_id
            if norm_email and not student.email:
                student.email = norm_email

        existing = db.scalar(select(AttendanceRecord).where(
            AttendanceRecord.lesson_id == lesson.id, AttendanceRecord.student_id == student.id))
        if existing:
            db.rollback()
            when = to_local(existing.timestamp).strftime("%H:%M")
            raise AttendanceError(409, f"{student.full_name} is already checked in for this lesson (at {when}).")

        student.is_present = True
        now = utcnow()
        record = AttendanceRecord(
            lesson_id=lesson.id,
            student_id=student.id,
            timestamp=now,
            status=STATUS_UNLISTED if unlisted else STATUS_PRESENT,
            device_fingerprint=device.fingerprint,
            student_latitude=float(latitude),
            student_longitude=float(longitude),
            distance_meters=distance_meters,
        )
        lock = DeviceLock(
            cookie_token=secrets.token_urlsafe(24),
            fingerprint=device.fingerprint,
            device_id=device.device_id,
            lesson_id=lesson.id,
            student_id=student.id,
            created_at=now,
            expires_at=now + timedelta(seconds=config.DEVICE_LOCK_SECONDS),
        )
        db.add_all([record, lock])
        if unlisted:
            db.add(AttendanceAlert(
                lesson_id=lesson.id,
                student_id=student.id,
                full_name=name,
                id_number=norm_id,
                email=norm_email,
                created_at=now,
            ))
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise AttendanceError(409, "You are already checked in for this lesson.") from exc
        return CheckInResult(student=student, lesson=lesson, record=record, lock=lock, unlisted=unlisted)


# ---------------------------------------------------------------------------
# Lecturer actions
# ---------------------------------------------------------------------------
def _resolve_target_lesson(
    db: Session,
    lesson_id: int | None = None,
    course_id: int | None = None,
) -> Lesson:
    if lesson_id is not None:
        lesson = db.get(Lesson, lesson_id)
        if lesson is None:
            raise AttendanceError(404, "Lesson not found.")
        return lesson
    lesson = get_current_session(db, course_id=course_id)
    if lesson is None:
        raise AttendanceError(400, "No lesson exists for this class yet. Open an attendance session first.")
    return lesson


def set_student_presence(
    db: Session,
    student_id: int,
    is_present: bool,
    lesson_id: int | None = None,
    course_id: int | None = None,
) -> Student:
    """Lecturer-only action to set a student's presence (נוכח / לא נוכח) manually."""
    student = db.get(Student, student_id)
    if student is None:
        raise AttendanceError(404, "Student not found.")

    lesson: Lesson | None = None
    if lesson_id is not None:
        lesson = db.get(Lesson, lesson_id)
        if lesson is None:
            raise AttendanceError(404, "Lesson not found.")
    else:
        lesson = get_current_session(db, course_id=course_id or student.course_id)

    if lesson is not None:
        if student.course_id != lesson.course_id:
            raise AttendanceError(400, "Student belongs to a different class.")
        record = db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.lesson_id == lesson.id,
                AttendanceRecord.student_id == student_id,
            )
        )
        if is_present and record is None:
            db.add(
                AttendanceRecord(
                    lesson_id=lesson.id,
                    student_id=student_id,
                    status=STATUS_MANUAL,
                )
            )
        elif not is_present and record is not None:
            db.delete(record)

    student.is_present = bool(is_present)
    db.commit()
    return student


def manual_mark(
    db: Session,
    student_id: int,
    lesson_id: int | None = None,
    course_id: int | None = None,
) -> AttendanceRecord | None:
    student = set_student_presence(
        db, student_id=student_id, is_present=True, lesson_id=lesson_id, course_id=course_id
    )
    lesson = (
        db.get(Lesson, lesson_id)
        if lesson_id is not None
        else get_current_session(db, course_id=course_id or student.course_id)
    )
    if lesson is None:
        return None
    return db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.lesson_id == lesson.id,
            AttendanceRecord.student_id == student_id,
        )
    )


def unmark(
    db: Session,
    student_id: int,
    lesson_id: int | None = None,
    course_id: int | None = None,
) -> None:
    set_student_presence(
        db, student_id=student_id, is_present=False, lesson_id=lesson_id, course_id=course_id
    )


def dismiss_alert(db: Session, alert_id: int, approve: bool) -> None:
    alert = db.get(AttendanceAlert, alert_id)
    if alert is None:
        raise AttendanceError(404, "Alert not found.")
    alert.dismissed = True
    if approve and alert.student_id:
        student = db.get(Student, alert.student_id)
        if student:
            student.in_roster = True
    db.commit()


# ---------------------------------------------------------------------------
# Dashboard + export
# ---------------------------------------------------------------------------
def to_local(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc).astimezone()


def _attendance_rows(db: Session, course_id: int, lesson: Lesson | None) -> list[dict]:
    records: dict[int, AttendanceRecord] = {}
    if lesson is not None:
        records = {r.student_id: r for r in db.scalars(
            select(AttendanceRecord).where(AttendanceRecord.lesson_id == lesson.id))}

    latest_lesson = get_current_session(db, course_id=course_id)
    is_latest = lesson is not None and latest_lesson is not None and lesson.id == latest_lesson.id
    dirty = False

    rows = []
    for student in db.scalars(
        select(Student).where(Student.course_id == course_id).order_by(Student.full_name)
    ):
        record = records.get(student.id)
        present_flag = (record is not None) if lesson is not None else bool(student.is_present)
        if is_latest and bool(student.is_present) != present_flag:
            student.is_present = present_flag
            dirty = True
        status = record.status if record else (STATUS_MANUAL if present_flag else None)
        rows.append({
            **student.to_dict(),
            "is_present": present_flag,
            "presence_label": "נוכח" if present_flag else "לא נוכח",
            "status": status,
            "checked_in_at": iso_utc(record.timestamp) if record else None,
            "student_latitude": record.student_latitude if record else None,
            "student_longitude": record.student_longitude if record else None,
            "distance_meters": record.distance_meters if record else None,
            "_timestamp": record.timestamp if record else None,
        })
    if dirty:
        db.commit()
    return rows


def build_dashboard(
    db: Session,
    course_id: int | None = None,
    lesson_id: int | None = None,
) -> dict:
    _sync_expired_lessons(db)
    courses = list_courses(db)
    active_global = get_active_session(db)

    # Determine which course to display
    selected_course: Course
    if course_id is not None:
        selected_course = resolve_course(db, course_id)
    elif lesson_id is not None:
        target = db.get(Lesson, lesson_id)
        selected_course = resolve_course(db, target.course_id if target else None)
    elif active_global is not None:
        selected_course = resolve_course(db, active_global.course_id)
    else:
        selected_course = resolve_course(db, None)

    # Fetch all lessons for the selected course (newest first) + attendance counts
    course_lessons = db.scalars(
        select(Lesson)
        .where(Lesson.course_id == selected_course.id)
        .order_by(Lesson.lesson_number.desc(), Lesson.id.desc())
    ).all()
    lesson_counts = dict(
        db.execute(
            select(AttendanceRecord.lesson_id, func.count(AttendanceRecord.id))
            .join(Lesson, Lesson.id == AttendanceRecord.lesson_id)
            .where(Lesson.course_id == selected_course.id)
            .group_by(AttendanceRecord.lesson_id)
        ).all()
    )

    # Determine which lesson of selected_course to display
    selected_lesson: Lesson | None = None
    if lesson_id is not None:
        candidate = db.get(Lesson, lesson_id)
        if candidate and candidate.course_id == selected_course.id:
            selected_lesson = candidate
    if selected_lesson is None:
        selected_lesson = get_current_session(db, course_id=selected_course.id)

    rows = _attendance_rows(db, selected_course.id, selected_lesson)
    for row in rows:
        row.pop("_timestamp", None)

    alerts = []
    if selected_lesson is not None:
        alerts = [a.to_dict() for a in db.scalars(
            select(AttendanceAlert)
            .where(AttendanceAlert.lesson_id == selected_lesson.id, AttendanceAlert.dismissed.is_(False))
            .order_by(AttendanceAlert.created_at.desc()))]

    present = sum(1 for r in rows if r["status"])
    roster_size = sum(1 for r in rows if r["in_roster"])
    next_num = next_lesson_number(db, selected_course.id)

    return {
        "courses": courses,
        "course": selected_course.to_dict(),
        "lessons": [
            l.to_dict(present_count=lesson_counts.get(l.id, 0))
            for l in course_lessons
        ],
        "next_lesson_number": next_num,
        "next_lesson_default_title": f"Lesson {next_num}",
        "session": (
            selected_lesson.to_dict(present_count=lesson_counts.get(selected_lesson.id, 0))
            if selected_lesson
            else None
        ),
        "active_session": active_global.to_dict() if active_global else None,
        "students": rows,
        "alerts": alerts,
        "stats": {
            "roster_size": roster_size,
            "present": present,
            "absent": len(rows) - present,
            "unlisted": sum(1 for r in rows if r["status"] == STATUS_UNLISTED),
            "manual": sum(1 for r in rows if r["status"] == STATUS_MANUAL),
        },
        "server_time": iso_utc(utcnow()),
    }


STATUS_LABELS = {
    STATUS_PRESENT: "Present",
    STATUS_MANUAL: "Present (manual)",
    STATUS_UNLISTED: "Present (unlisted)",
    None: "Absent",
}


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\-]+", "_", text.strip())
    return cleaned.strip("_") or "course"


def _safe_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]+", " ", name).strip()
    return (cleaned or "Attendance")[:31]


def _format_df_export(table: pd.DataFrame, sheet_name: str, base_filename: str, fmt: str) -> tuple[bytes, str, str]:
    import pandas as pd

    safe_sheet = _safe_sheet_name(sheet_name)
    if fmt == "xlsx":
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            table.to_excel(writer, index=False, sheet_name=safe_sheet)
            sheet = writer.sheets[safe_sheet]
            for column_cells in sheet.columns:
                width = max(len(str(c.value or "")) for c in column_cells) + 2
                sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width, 10), 50)
        return (
            buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"{base_filename}.xlsx",
        )
    # utf-8-sig adds a BOM so Excel displays Hebrew names correctly.
    return table.to_csv(index=False).encode("utf-8-sig"), "text/csv; charset=utf-8", f"{base_filename}.csv"


def export_session(
    db: Session,
    lesson_id: int | None,
    fmt: str,
    course_id: int | None = None,
) -> tuple[bytes, str, str]:
    import pandas as pd

    lesson = db.get(Lesson, lesson_id) if lesson_id else get_current_session(db, course_id=course_id)
    if lesson is None:
        raise AttendanceError(404, "No lesson to export.")
    course = db.get(Course, lesson.course_id) or resolve_course(db, lesson.course_id)

    rows = _attendance_rows(db, course.id, lesson)
    rows.sort(key=lambda r: (not r["is_present"], r["full_name"].casefold()))
    lesson_label = f"Lesson {lesson.lesson_number}: {lesson.title}"
    columns = ["Course", "Lesson", "Full Name", "ID Number", "Email", "Presence (נוכחות)", "Status", "Check-in Time", "In Roster"]
    table = pd.DataFrame([{
        "Course": course.name,
        "Lesson": lesson_label,
        "Full Name": r["full_name"],
        "ID Number": r["id_number"] or "",
        "Email": r["email"] or "",
        "Presence (נוכחות)": r["presence_label"],
        "Status": STATUS_LABELS.get(r["status"], r["status"]),
        "Check-in Time": to_local(r["_timestamp"]).strftime("%Y-%m-%d %H:%M:%S") if r["_timestamp"] else "",
        "In Roster": "Yes" if r["in_roster"] else "No",
    } for r in rows], columns=columns)

    stamp = to_local(lesson.opened_at).strftime("%Y-%m-%d_%H%M")
    base = f"attendance_{_slug(course.name)}_lesson{lesson.lesson_number}_{stamp}"
    return _format_df_export(table, f"Lesson {lesson.lesson_number}", base, fmt)


def export_course_summary(
    db: Session,
    course_id: int | None,
    fmt: str,
) -> tuple[bytes, str, str]:
    """Export an entire class's attendance history across all lessons."""
    import pandas as pd

    course = resolve_course(db, course_id)
    lessons = db.scalars(
        select(Lesson)
        .where(Lesson.course_id == course.id)
        .order_by(Lesson.lesson_number.asc(), Lesson.id.asc())
    ).all()
    students = db.scalars(
        select(Student)
        .where(Student.course_id == course.id)
        .order_by(Student.full_name.asc())
    ).all()

    records_by_pair: dict[tuple[int, int], AttendanceRecord] = {}
    if lessons:
        lesson_ids = [l.id for l in lessons]
        for rec in db.scalars(select(AttendanceRecord).where(AttendanceRecord.lesson_id.in_(lesson_ids))):
            records_by_pair[(rec.lesson_id, rec.student_id)] = rec

    lesson_headers = [
        f"L{l.lesson_number}: {l.title} ({to_local(l.opened_at).strftime('%m-%d')})"
        for l in lessons
    ]
    base_cols = ["Full Name", "ID Number", "Email", "In Roster"]
    summary_cols = ["Total Attended", "Total Lessons", "Attendance Rate (%)"]
    all_cols = base_cols + lesson_headers + summary_cols

    data_rows = []
    total_lessons = len(lessons)
    for student in students:
        attended = 0
        row_dict = {
            "Full Name": student.full_name,
            "ID Number": student.id_number or "",
            "Email": student.email or "",
            "In Roster": "Yes" if student.in_roster else "No",
        }
        for lesson, col_name in zip(lessons, lesson_headers):
            rec = records_by_pair.get((lesson.id, student.id))
            if rec:
                attended += 1
                row_dict[col_name] = STATUS_LABELS.get(rec.status, rec.status)
            else:
                row_dict[col_name] = "Absent"
        rate = round((attended / total_lessons) * 100) if total_lessons > 0 else 0
        row_dict["Total Attended"] = attended
        row_dict["Total Lessons"] = total_lessons
        row_dict["Attendance Rate (%)"] = f"{rate}%"
        data_rows.append(row_dict)

    table = pd.DataFrame(data_rows, columns=all_cols)
    base = f"course_summary_{_slug(course.name)}"
    return _format_df_export(table, "Course Summary", base, fmt)
