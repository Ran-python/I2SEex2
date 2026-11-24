"""Live & Offline Comments REST API (mounted under /api/comments)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import config
from core.database import get_db
from core.network import require_lecturer_pin
from modules.attendance.models import Lesson
from modules.comments.models import VISIBILITY_PRIVATE, VISIBILITY_PUBLIC, Comment
from modules.roster.service import clean_text, resolve_course

router = APIRouter(prefix="/api/comments", tags=["comments"])


class CommentCreate(BaseModel):
    course_id: int | None = None
    lesson_id: int | None = None  # None = General Course comment
    student_id: int | None = None
    author_name: str = Field(default="Student", max_length=200)
    is_anonymous: bool = False
    visibility: str = Field(default=VISIBILITY_PUBLIC, pattern="^(public|private)$")
    content: str = Field(min_length=2, max_length=2000)


class CommentUpdate(BaseModel):
    lecturer_reply: str | None = Field(default=None, max_length=2000)
    resolved: bool | None = None


def _is_lecturer_request(request: Request) -> bool:
    pin = (request.headers.get("x-lecturer-pin") or request.query_params.get("pin") or "").strip()
    return pin == config.LECTURER_PIN


@router.get("")
def list_comments(
    request: Request,
    course_id: int | None = Query(default=None),
    lesson_id: int | None = Query(default=None),
    scope: str = Query(default="all"),  # all | general | lesson | private
    view_role: str = Query(default="student"),
    student_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    course = resolve_course(db, course_id)
    is_lecturer = _is_lecturer_request(request) and view_role == "lecturer"

    stmt = select(Comment).where(Comment.course_id == course.id)
    if scope == "general":
        stmt = stmt.where(Comment.lesson_id.is_(None))
    elif scope == "lesson" and lesson_id is not None:
        stmt = stmt.where(Comment.lesson_id == lesson_id)
    elif scope == "private" and is_lecturer:
        stmt = stmt.where(Comment.visibility == VISIBILITY_PRIVATE)

    comments = db.scalars(stmt.order_by(Comment.created_at.desc(), Comment.id.desc())).all()
    results = []
    for c in comments:
        is_mine = c.is_owned_by(student_id)
        # Private comments are visible to the lecturer AND to the student who authored them (named or anonymous)
        if c.visibility == VISIBILITY_PRIVATE and not is_lecturer and not is_mine:
            continue
        results.append(c.to_dict(viewer_student_id=student_id if not is_lecturer else None))
    return results


@router.post("")
def create_comment(payload: CommentCreate, db: Session = Depends(get_db)):
    course = resolve_course(db, payload.course_id)
    text_body = clean_text(payload.content)
    if not text_body or len(text_body) < 2:
        raise HTTPException(status_code=422, detail="Please enter a comment or suggestion.")

    target_lesson_id: int | None = None
    if payload.lesson_id is not None:
        lesson = db.get(Lesson, payload.lesson_id)
        if lesson and lesson.course_id == course.id:
            target_lesson_id = lesson.id

    real_author = clean_text(payload.author_name) or "Student"
    vis = VISIBILITY_PRIVATE if payload.visibility == VISIBILITY_PRIVATE else VISIBILITY_PUBLIC

    comment = Comment(
        course_id=course.id,
        lesson_id=target_lesson_id,
        student_id=payload.student_id,
        author_student_id=payload.student_id,
        author_name=real_author,
        is_anonymous=bool(payload.is_anonymous),
        visibility=vis,
        content=text_body,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment.to_dict(viewer_student_id=payload.student_id)


@router.patch("/{comment_id}", dependencies=[Depends(require_lecturer_pin)])
def update_comment(comment_id: int, payload: CommentUpdate, db: Session = Depends(get_db)):
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found.")
    if payload.lecturer_reply is not None:
        comment.lecturer_reply = clean_text(payload.lecturer_reply)
    if payload.resolved is not None:
        comment.resolved = bool(payload.resolved)
    db.commit()
    db.refresh(comment)
    return comment.to_dict()


@router.delete("/{comment_id}", dependencies=[Depends(require_lecturer_pin)])
def delete_comment(comment_id: int, db: Session = Depends(get_db)):
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found.")
    db.delete(comment)
    db.commit()
    return {"ok": True}
