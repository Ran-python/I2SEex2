"""Attendance REST API (mounted under /api/attendance)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core import config
from core.database import get_db, iso_utc
from core.network import get_client_ip, is_host_ip, require_lecturer_host
from modules.attendance import service
from modules.attendance.models import Lesson
from modules.attendance.schemas import (
    CheckInRequest,
    CloseSessionRequest,
    LessonRenameRequest,
    ManualMarkRequest,
    OpenSessionRequest,
)
from modules.attendance.service import AttendanceError, DeviceIdentity

router = APIRouter(prefix="/api/attendance", tags=["attendance"])
lecturer = [Depends(require_lecturer_host)]


def _error(exc: AttendanceError) -> JSONResponse:
    headers = {}
    if "retry_after_seconds" in exc.extra:
        headers["Retry-After"] = str(exc.extra["retry_after_seconds"])
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, **exc.extra},
                        headers=headers)


def _device(request: Request, fingerprint: str | None = None, device_id: str | None = None) -> DeviceIdentity:
    return DeviceIdentity(
        cookie_token=request.cookies.get(config.DEVICE_LOCK_COOKIE),
        fingerprint=fingerprint or request.headers.get("x-device-fingerprint"),
        device_id=device_id or request.headers.get("x-device-id"),
        client_ip=get_client_ip(request),
    )


# ---------------------------------------------------------------- student side
@router.get("/status")
def attendance_status(request: Request, token: str | None = Query(default=None), db: Session = Depends(get_db)):
    """Public status for the student page: is attendance open, which class/lesson, is device locked?"""
    lesson = service.get_active_session(db)
    host = is_host_ip(get_client_ip(request))
    return {
        "active": lesson is not None,
        "seconds_remaining": lesson.seconds_remaining() if lesson else 0,
        "token_valid": bool(lesson and token and token == lesson.qr_token),
        # Only the lecturer's own machine (demo "Student View" toggle) receives the token;
        # real students must get it from the QR code.
        "token": lesson.qr_token if (lesson and host) else None,
        "device_lock_seconds": service.device_lock_remaining(db, _device(request)),
        "course": (
            {"id": lesson.course.id, "name": lesson.course.name}
            if (lesson and lesson.course)
            else None
        ),
        "lesson": (
            {
                "id": lesson.id,
                "lesson_number": lesson.lesson_number,
                "title": lesson.title,
            }
            if lesson
            else None
        ),
    }


@router.post("/checkin")
def check_in(payload: CheckInRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        result = service.check_in(
            db,
            token=payload.token,
            full_name=payload.full_name,
            id_number=payload.id_number,
            email=payload.email,
            device=_device(request, payload.fingerprint, payload.device_id),
        )
    except AttendanceError as exc:
        return _error(exc)

    response.set_cookie(
        key=config.DEVICE_LOCK_COOKIE,
        value=result.lock.cookie_token,
        max_age=config.DEVICE_LOCK_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # plain HTTP on the LAN
        path="/",
    )
    lesson = result.lesson
    return {
        "ok": True,
        "status": result.record.status,
        "unlisted": result.unlisted,
        "student": {"full_name": result.student.full_name},
        "course": (
            {"id": lesson.course.id, "name": lesson.course.name}
            if lesson.course
            else None
        ),
        "lesson": {
            "id": lesson.id,
            "lesson_number": lesson.lesson_number,
            "title": lesson.title,
        },
        "checked_in_at": iso_utc(result.record.timestamp),
        "device_lock_seconds": config.DEVICE_LOCK_SECONDS,
        "message": (
            "You're checked in! You have also been added to the course roster."
            if result.unlisted else "You're checked in!"
        ),
    }


# ---------------------------------------------------------------- lecturer side
@router.get("/dashboard", dependencies=lecturer)
def dashboard(
    course_id: int | None = Query(default=None),
    lesson_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return service.build_dashboard(db, course_id=course_id, lesson_id=lesson_id)


@router.post("/sessions/open", dependencies=lecturer)
def open_session(payload: OpenSessionRequest, db: Session = Depends(get_db)):
    if payload.lesson_id is not None:
        try:
            lesson = service.reopen_lesson(db, payload.lesson_id, payload.frontend_port)
        except AttendanceError as exc:
            return _error(exc)
        return lesson.to_dict()
    lesson = service.open_session(
        db,
        frontend_port=payload.frontend_port,
        course_id=payload.course_id,
        title=payload.title,
    )
    return lesson.to_dict()


@router.post("/lessons/{lesson_id}/reopen", dependencies=lecturer)
@router.put("/lessons/{lesson_id}/reopen", dependencies=lecturer)
def reopen_lesson(lesson_id: int, payload: OpenSessionRequest | None = None, db: Session = Depends(get_db)):
    port = payload.frontend_port if payload else None
    try:
        lesson = service.reopen_lesson(db, lesson_id, port)
    except AttendanceError as exc:
        return _error(exc)
    return lesson.to_dict()


@router.patch("/lessons/{lesson_id}", dependencies=lecturer)
def rename_lesson(lesson_id: int, payload: LessonRenameRequest, db: Session = Depends(get_db)):
    try:
        raw_title = (payload.title or "").strip()
        if raw_title == "__DELETE_LESSON__":
            service.delete_lesson(db, lesson_id)
            return {"ok": True, "deleted": True}
        if payload.reopen or raw_title.startswith("__REOPEN__:"):
            clean_title = raw_title[len("__REOPEN__:"):].strip() if raw_title.startswith("__REOPEN__:") else raw_title
            lesson = service.reopen_lesson(db, lesson_id, payload.frontend_port)
            if clean_title:
                lesson = service.rename_lesson(db, lesson_id, clean_title)
        else:
            lesson = service.rename_lesson(db, lesson_id, raw_title)
    except AttendanceError as exc:
        return _error(exc)
    return lesson.to_dict()


@router.delete("/lessons/{lesson_id}", dependencies=lecturer)
def delete_lesson(lesson_id: int, db: Session = Depends(get_db)):
    try:
        service.delete_lesson(db, lesson_id)
    except AttendanceError as exc:
        return _error(exc)
    return {"ok": True, "deleted": True}


@router.post("/sessions/close", dependencies=lecturer)
def close_session(payload: CloseSessionRequest | None = None, db: Session = Depends(get_db)):
    lesson_id = payload.lesson_id if payload else None
    lesson = service.close_session(db, lesson_id=lesson_id)
    return {"closed": lesson is not None}


@router.get("/sessions/{session_id}/qr.png", dependencies=lecturer)
def session_qr(session_id: int, db: Session = Depends(get_db)):
    lesson = db.get(Lesson, session_id)
    if lesson is None:
        return JSONResponse(status_code=404, content={"detail": "Lesson not found."})
    return Response(content=service.render_qr_png(lesson.checkin_url), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.get("/export", dependencies=lecturer)
def export(
    fmt: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    lesson_id: int | None = Query(default=None),
    session_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    target_lesson_id = lesson_id if lesson_id is not None else session_id
    try:
        content, media_type, filename = service.export_session(
            db, target_lesson_id, fmt, course_id=course_id
        )
    except AttendanceError as exc:
        return _error(exc)
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/export-course", dependencies=lecturer)
def export_course(
    fmt: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        content, media_type, filename = service.export_course_summary(db, course_id, fmt)
    except AttendanceError as exc:
        return _error(exc)
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/manual", dependencies=lecturer)
def manual_mark(payload: ManualMarkRequest, db: Session = Depends(get_db)):
    try:
        record = service.manual_mark(db, payload.student_id, lesson_id=payload.lesson_id)
    except AttendanceError as exc:
        return _error(exc)
    return {
        "ok": True,
        "status": record.status if record else "manual",
        "is_present": True,
        "presence_label": "נוכח",
    }


@router.delete("/records/{student_id}", dependencies=lecturer)
def unmark(
    student_id: int,
    lesson_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        service.unmark(db, student_id, lesson_id=lesson_id)
    except AttendanceError as exc:
        return _error(exc)
    return {"ok": True, "is_present": False, "presence_label": "לא נוכח"}


@router.post("/alerts/{alert_id}/dismiss", dependencies=lecturer)
def dismiss_alert(alert_id: int, approve: bool = Query(default=False), db: Session = Depends(get_db)):
    try:
        service.dismiss_alert(db, alert_id, approve)
    except AttendanceError as exc:
        return _error(exc)
    return {"ok": True}


@router.delete("/device-locks", dependencies=lecturer)
def clear_device_locks(response: Response, db: Session = Depends(get_db)):
    """Demo helper: lets the lecturer re-run a check-in from the same device."""
    removed = service.clear_device_locks(db)
    response.delete_cookie(config.DEVICE_LOCK_COOKIE, path="/")
    return {"removed": removed}
