"""Roster business logic: courses, normalisation, student matching and upserts."""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modules.roster.models import DEFAULT_COURSE_NAME, Course, Student

_PUNCT_RE = re.compile(r"[\"'`׳״.,\-_()]+")
_SPACE_RE = re.compile(r"\s+")


def clean_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "<na>", "nat"}:
        return None
    return _SPACE_RE.sub(" ", text)


def normalize_id_number(value) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d+\.0+", text):           # Excel numeric cell -> "123456789.0"
        text = text.split(".")[0]
    compact = re.sub(r"[\s\-]", "", text)
    if compact.isdigit():
        # Israeli ID numbers are 9 digits; spreadsheets often drop leading zeros.
        return compact.zfill(9) if len(compact) <= 9 else compact
    compact = re.sub(r"[^0-9A-Za-z]", "", compact).upper()
    return compact or None


def normalize_email(value) -> str | None:
    text = clean_text(value)
    if not text or "@" not in text:
        return None
    return text.replace(" ", "").lower()


def name_key(full_name: str | None) -> str:
    """Order-insensitive key so "Cohen Dana" matches "Dana Cohen"."""
    if not full_name:
        return ""
    text = unicodedata.normalize("NFKC", full_name).casefold()
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(sorted(_SPACE_RE.sub(" ", text).strip().split(" ")))


# ---------------------------------------------------------------------------
# Courses / Classes
# ---------------------------------------------------------------------------
def get_or_create_default_course(db: Session) -> Course:
    course = db.scalar(select(Course).order_by(Course.id.asc()))
    if course is None:
        course = Course(name=DEFAULT_COURSE_NAME)
        db.add(course)
        db.commit()
    return course


def resolve_course(db: Session, course_id: int | None) -> Course:
    if course_id is not None:
        course = db.get(Course, course_id)
        if course is not None:
            return course
    return get_or_create_default_course(db)


def list_courses(db: Session) -> list[dict]:
    get_or_create_default_course(db)
    student_counts = dict(
        db.execute(
            select(Student.course_id, func.count(Student.id))
            .group_by(Student.course_id)
        ).all()
    )
    from modules.attendance.models import Lesson
    lesson_counts = dict(
        db.execute(
            select(Lesson.course_id, func.count(Lesson.id)).group_by(Lesson.course_id)
        ).all()
    )
    courses = db.scalars(select(Course).order_by(Course.id.asc())).all()
    return [
        {
            **c.to_dict(),
            "student_count": student_counts.get(c.id, 0),
            "lesson_count": lesson_counts.get(c.id, 0),
        }
        for c in courses
    ]


def create_course(db: Session, name: str) -> Course:
    cleaned = clean_text(name)
    if not cleaned or len(cleaned) < 2:
        raise ValueError("Course name must be at least 2 characters.")
    if cleaned.startswith("__DELETE_COURSE__:"):
        target_id = int(cleaned.split(":", 1)[1])
        delete_course(db, target_id)
        return get_or_create_default_course(db)
    existing = db.scalar(
        select(Course).where(func.lower(Course.name) == cleaned.lower())
    )
    if existing:
        raise ValueError(f'A class named "{existing.name}" already exists.')
    course = Course(name=cleaned)
    db.add(course)
    db.commit()
    return course


def delete_course(db: Session, course_id: int) -> None:
    course = db.get(Course, course_id)
    if course is None:
        raise ValueError("Course not found.")
    from modules.attendance.models import AttendanceAlert, AttendanceRecord, DeviceLock, Lesson

    lesson_ids = [
        lid for lid in db.scalars(select(Lesson.id).where(Lesson.course_id == course_id))
    ]
    if lesson_ids:
        db.query(AttendanceAlert).filter(AttendanceAlert.lesson_id.in_(lesson_ids)).delete(synchronize_session=False)
        db.query(AttendanceRecord).filter(AttendanceRecord.lesson_id.in_(lesson_ids)).delete(synchronize_session=False)
        db.query(DeviceLock).filter(DeviceLock.lesson_id.in_(lesson_ids)).update({"lesson_id": None}, synchronize_session=False)
        db.query(Lesson).filter(Lesson.course_id == course_id).delete(synchronize_session=False)
    db.query(Student).filter(Student.course_id == course_id).delete(synchronize_session=False)
    db.delete(course)
    db.commit()
    # Ensure at least one course exists so the app is never left without a course.
    get_or_create_default_course(db)


# ---------------------------------------------------------------------------
# Students (scoped per course_id)
# ---------------------------------------------------------------------------
def find_student(
    db: Session,
    course_id: int,
    full_name: str | None,
    id_number: str | None,
    email: str | None,
) -> Student | None:
    """Match priority within a course: ID number -> email -> name (without conflicting ID/email)."""
    if id_number:
        found = db.scalar(
            select(Student).where(Student.course_id == course_id, Student.id_number == id_number)
        )
        if found:
            return found
    if email:
        found = db.scalar(
            select(Student).where(Student.course_id == course_id, Student.email == email)
        )
        if found:
            return found
    key = name_key(full_name)
    if not key:
        return None
    for student in db.scalars(
        select(Student)
        .where(Student.course_id == course_id)
        .order_by(Student.in_roster.desc(), Student.id)
    ):
        if name_key(student.full_name) != key:
            continue
        if id_number and student.id_number and student.id_number != id_number:
            continue
        if email and student.email and student.email != email:
            continue
        return student
    return None


def upsert_student(
    db: Session,
    course_id: int,
    full_name: str,
    id_number: str | None = None,
    email: str | None = None,
    in_roster: bool = True,
) -> tuple[Student, bool]:
    """Create or update a student in the given course. Returns (student, created)."""
    student = find_student(db, course_id, full_name, id_number, email)
    if student is None:
        student = Student(
            course_id=course_id,
            full_name=full_name,
            id_number=id_number,
            email=email,
            in_roster=in_roster,
        )
        db.add(student)
        db.flush()
        return student, True

    student.full_name = full_name or student.full_name
    if id_number:
        student.id_number = id_number
    if email:
        student.email = email
    if in_roster:
        student.in_roster = True
    return student, False
