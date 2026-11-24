"""On-the-Spot KISS Polls REST API (mounted under /api/polls)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import get_db, utcnow
from core.network import require_lecturer_pin, resolve_app_base_url
from modules.attendance.service import render_qr_png
from modules.polls.models import Poll, PollVote
from modules.roster.service import clean_text, resolve_course

router = APIRouter(prefix="/api/polls", tags=["polls"])


class PollCreate(BaseModel):
    course_id: int | None = None
    lesson_id: int | None = None
    question: str = Field(min_length=3, max_length=500)
    options: list[str] = Field(min_length=2, max_length=6)
    base_url: str | None = Field(default=None, max_length=500)


class PollToggleRequest(BaseModel):
    is_active: bool | None = None
    base_url: str | None = Field(default=None, max_length=500)


class PollVoteRequest(BaseModel):
    option_index: int = Field(ge=0, le=10)
    student_id: int | None = None


def _voter_keys(request: Request, student_id: int | None = None) -> list[str]:
    keys = []
    if student_id is not None:
        keys.append(f"student:{student_id}")
    fp = (request.headers.get("x-device-fingerprint") or "").strip()
    dev = (request.headers.get("x-device-id") or "").strip()
    if fp:
        keys.append(f"fp:{fp}")
    elif dev:
        keys.append(f"dev:{dev}")
    return keys or ["anon"]


def _serialize_polls(
    db: Session,
    polls: list[Poll],
    request: Request,
    student_id: int | None = None,
) -> list[dict]:
    if not polls:
        return []
    poll_ids = [p.id for p in polls]
    agg_rows = db.execute(
        select(PollVote.poll_id, PollVote.option_index, func.count(PollVote.id))
        .where(PollVote.poll_id.in_(poll_ids))
        .group_by(PollVote.poll_id, PollVote.option_index)
    ).all()
    counts_by_poll: dict[int, dict[int, int]] = {pid: {} for pid in poll_ids}
    for pid, opt_idx, cnt in agg_rows:
        counts_by_poll.setdefault(pid, {})[opt_idx] = cnt

    v_keys = _voter_keys(request, student_id)
    my_votes: dict[int, int] = {}
    for pv in db.scalars(
        select(PollVote).where(PollVote.poll_id.in_(poll_ids), PollVote.voter_key.in_(v_keys))
    ):
        my_votes[pv.poll_id] = pv.option_index

    return [p.to_dict(vote_counts=counts_by_poll.get(p.id), my_vote=my_votes.get(p.id)) for p in polls]


@router.get("")
def list_polls(
    request: Request,
    course_id: int | None = Query(default=None),
    poll_id: int | None = Query(default=None),
    student_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if poll_id is not None:
        target = db.get(Poll, poll_id)
        if target is not None:
            course = resolve_course(db, target.course_id)
        else:
            course = resolve_course(db, course_id)
    else:
        course = resolve_course(db, course_id)

    polls = db.scalars(
        select(Poll)
        .where(Poll.course_id == course.id)
        .order_by(Poll.is_active.desc(), Poll.id.desc())
    ).all()
    serialized = _serialize_polls(db, polls, request, student_id=student_id)
    return {
        "course": course.to_dict(),
        "polls": serialized,
    }


@router.post("", dependencies=[Depends(require_lecturer_pin)])
def create_poll(payload: PollCreate, request: Request, db: Session = Depends(get_db)):
    course = resolve_course(db, payload.course_id)
    question = clean_text(payload.question)
    if not question:
        raise HTTPException(status_code=422, detail="Poll question is required.")
    cleaned_opts = [clean_text(o) for o in payload.options if clean_text(o)]
    if len(cleaned_opts) < 2 or len(cleaned_opts) > 5:
        raise HTTPException(status_code=422, detail="Please provide between 2 and 5 poll options.")

    poll = Poll(
        course_id=course.id,
        lesson_id=payload.lesson_id,
        question=question,
        options_json=json.dumps(cleaned_opts, ensure_ascii=False),
        is_active=True,
        checkin_url="",
    )
    db.add(poll)
    db.flush()

    base = resolve_app_base_url(request, payload.base_url)
    poll.checkin_url = f"{base}/polls?poll_id={poll.id}&course_id={course.id}"
    db.commit()
    db.refresh(poll)
    return _serialize_polls(db, [poll], request)[0]


@router.patch("/{poll_id}/toggle", dependencies=[Depends(require_lecturer_pin)])
def toggle_poll(
    poll_id: int,
    payload: PollToggleRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    poll = db.get(Poll, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="Poll not found.")
    next_state = (not poll.is_active) if payload.is_active is None else bool(payload.is_active)
    poll.is_active = next_state
    poll.closed_at = None if next_state else utcnow()
    if next_state and payload.base_url:
        base = resolve_app_base_url(request, payload.base_url)
        poll.checkin_url = f"{base}/polls?poll_id={poll.id}&course_id={poll.course_id}"
    db.commit()
    db.refresh(poll)
    return _serialize_polls(db, [poll], request)[0]


@router.delete("/{poll_id}", dependencies=[Depends(require_lecturer_pin)])
def delete_poll(poll_id: int, db: Session = Depends(get_db)):
    poll = db.get(Poll, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="Poll not found.")
    db.delete(poll)
    db.commit()
    return {"ok": True}


@router.get("/{poll_id}/qr.png")
def poll_qr_png(poll_id: int, request: Request, db: Session = Depends(get_db)):
    poll = db.get(Poll, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="Poll not found.")
    url = poll.checkin_url or f"{resolve_app_base_url(request)}/polls?poll_id={poll.id}&course_id={poll.course_id}"
    return Response(
        content=render_qr_png(url),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{poll_id}/vote")
def vote_poll(
    poll_id: int,
    payload: PollVoteRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    poll = db.get(Poll, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="Poll not found.")
    if not poll.is_active:
        raise HTTPException(status_code=410, detail="This poll is currently closed.")

    opts = poll.options
    if payload.option_index < 0 or payload.option_index >= len(opts):
        raise HTTPException(status_code=422, detail="Invalid poll option.")

    v_keys = _voter_keys(request, payload.student_id)
    existing = db.scalar(
        select(PollVote).where(PollVote.poll_id == poll.id, PollVote.voter_key.in_(v_keys))
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="You have already voted in this poll.")

    primary_key = v_keys[0]
    vote = PollVote(
        poll_id=poll.id,
        student_id=payload.student_id,
        voter_key=primary_key,
        option_index=payload.option_index,
    )
    db.add(vote)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="You have already voted in this poll.") from exc

    return _serialize_polls(db, [poll], request, student_id=payload.student_id)[0]
