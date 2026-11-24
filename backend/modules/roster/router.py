"""Courses & Roster REST API (mounted under /api/courses and /api/students)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.database import get_db
from core.network import require_lecturer_host
from modules.roster.importer import RosterImportError, parse_roster_file
from modules.roster.models import Student
from modules.roster.schemas import (
    CourseCreate,
    StudentCreate,
    StudentPresenceUpdate,
    StudentUpdate,
)
from modules.roster.service import (
    clean_text,
    create_course,
    delete_course,
    list_courses,
    normalize_email,
    normalize_id_number,
    resolve_course,
    upsert_student,
)

router = APIRouter(tags=["roster"])
lecturer = [Depends(require_lecturer_host)]

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------- courses
@router.get("/api/courses")
def get_courses(db: Session = Depends(get_db)):
    """Public list of courses (used by both Lecturer Dashboard and Student Quick-Login bar)."""
    return list_courses(db)


@router.post("/api/courses", dependencies=lecturer)
def add_course(payload: CourseCreate, db: Session = Depends(get_db)):
    try:
        course = create_course(db, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **course.to_dict(),
        "student_count": 0,
        "lesson_count": 0,
    }


@router.delete("/api/courses/{course_id}", dependencies=lecturer)
def remove_course(course_id: int, db: Session = Depends(get_db)):
    """Lecturer-only endpoint to delete a course and all its lessons/roster."""
    try:
        delete_course(db, course_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "courses": list_courses(db)}


# ---------------------------------------------------------------- students
@router.get("/api/students")
def list_students(course_id: int | None = Query(default=None), db: Session = Depends(get_db)):
    """Public list of students in a course (used by Student Quick-Login dropdown and Lecturer view)."""
    course = resolve_course(db, course_id)
    return [
        s.to_dict()
        for s in db.scalars(
            select(Student)
            .where(Student.course_id == course.id)
            .order_by(Student.full_name)
        )
    ]


@router.post("/api/students", dependencies=lecturer)
def add_student(payload: StudentCreate, db: Session = Depends(get_db)):
    course = resolve_course(db, payload.course_id)
    name = clean_text(payload.full_name)
    if not name:
        raise HTTPException(status_code=422, detail="Full name is required.")
    email = normalize_email(payload.email)
    if clean_text(payload.email) and not email:
        raise HTTPException(status_code=422, detail="The email address looks invalid.")
    student, created = upsert_student(
        db, course.id, name, normalize_id_number(payload.id_number), email, in_roster=True
    )
    if payload.mark_present:
        student.is_present = True
    db.commit()

    marked = False
    if payload.mark_present:
        from modules.attendance import service as attendance_service  # avoid circular import
        try:
            attendance_service.set_student_presence(
                db, student.id, is_present=True, lesson_id=payload.lesson_id, course_id=course.id
            )
            marked = True
        except attendance_service.AttendanceError:
            marked = True
    return {"student": student.to_dict(), "created": created, "marked_present": marked}


@router.patch("/api/students/{student_id}", dependencies=lecturer)
def update_student(
    student_id: int,
    payload: StudentUpdate,
    db: Session = Depends(get_db),
):
    """Lecturer-only endpoint to edit a student's details (name, ID number, email)."""
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found.")
    name = clean_text(payload.full_name)
    if not name or len(name) < 2:
        raise HTTPException(status_code=422, detail="Full name is required.")
    email = normalize_email(payload.email)
    if clean_text(payload.email) and not email:
        raise HTTPException(status_code=422, detail="The email address looks invalid.")
    student.full_name = name
    student.id_number = normalize_id_number(payload.id_number)
    student.email = email
    student.in_roster = True
    db.commit()
    return {"ok": True, "student": student.to_dict()}


@router.delete("/api/students/{student_id}", dependencies=lecturer)
def delete_student(student_id: int, db: Session = Depends(get_db)):
    """Lecturer-only endpoint to remove a student from the course roster."""
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found.")
    db.delete(student)
    db.commit()
    return {"ok": True}


@router.patch("/api/students/{student_id}/presence", dependencies=lecturer)
def update_student_presence(
    student_id: int,
    payload: StudentPresenceUpdate,
    db: Session = Depends(get_db),
):
    """Lecturer-only endpoint to manually change a student's presence (נוכח / לא נוכח)."""
    from modules.attendance import service as attendance_service
    try:
        student = attendance_service.set_student_presence(
            db,
            student_id=student_id,
            is_present=payload.is_present,
            lesson_id=payload.lesson_id,
        )
    except attendance_service.AttendanceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"ok": True, "student": student.to_dict()}


@router.post("/api/students/import", dependencies=lecturer)
async def import_roster(
    file: UploadFile = File(...),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    course = resolve_course(db, course_id)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (max 5 MB).")
    try:
        parsed = parse_roster_file(file.filename or "", content)
    except RosterImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created = updated = 0
    for row in parsed.rows:
        _, was_created = upsert_student(
            db, course.id, row["full_name"], row["id_number"], row["email"], in_roster=True
        )
        created += was_created
        updated += not was_created
    db.commit()
    return {
        "course_id": course.id,
        "course_name": course.name,
        "created": created,
        "updated": updated,
        "skipped": parsed.skipped_rows,
        "total_rows": len(parsed.rows) + parsed.skipped_rows,
        "detected_columns": parsed.detected_columns,
    }
