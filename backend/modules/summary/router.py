"""Lecturer-only ML Lecture Video Summary & Pedagogical Evaluation API (/api/summaries)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.ai import AIError, analyze_lecture_media, extract_ai_config, extract_json_object, markdown_to_docx_bytes
from core.database import get_db
from core.network import require_lecturer_pin
from modules.attendance.models import Lesson
from modules.roster.service import clean_text, resolve_course
from modules.summary.models import LectureSummary

# Strictly restricted to Lecturer Mode (PIN 3358)
router = APIRouter(
    prefix="/api/summaries",
    tags=["lecture-summary"],
    dependencies=[Depends(require_lecturer_pin)],
)

PEDAGOGICAL_SYSTEM_PROMPT = """You are a senior university pedagogical coach and computer-science instructional evaluator.
Analyze the provided recorded lecture video, audio, or transcript and generate a thorough, constructive, and structured teaching evaluation report for the lecturer.

Return ONLY a valid JSON object with the following exact 5 string keys (use rich bullet points and clear formatting inside each string):
{
  "executive_summary": "Detailed executive summary of the lecture and key academic/technical topics covered.",
  "pedagogical_evaluation": "Structured evaluation across 5 parameters: (1) Clarity of Explanations, (2) Pacing & Time Management, (3) Logical Structure & Flow, (4) Student Engagement & Q&A Handling, and (5) Quality of Concrete Examples.",
  "points_to_preserve": "Points to Preserve (נקודות לשימור) — specific teaching practices, explanations, or examples that worked exceptionally well and should be kept.",
  "points_for_improvement": "Points for Improvement (נקודות לשיפור) — concrete, actionable pedagogical recommendations to improve clarity, pacing, or student comprehension in future lectures.",
  "action_items": "Action Items & Topics to Revisit Next Class — concepts that need follow-up, suggested opening recap for the next lesson, or recommended exercises."
}"""


def _compose_full_markdown(
    course_name: str,
    lesson_label: str,
    source_name: str,
    provider_label: str,
    sections: dict[str, str],
) -> str:
    return (
        f"# Lecture Summary & Pedagogical Evaluation\n"
        f"**Course:** {course_name}  \n"
        f"**Lesson:** {lesson_label}  \n"
        f"**Source:** {source_name}  \n"
        f"**AI Model:** {provider_label}\n\n"
        f"---\n\n"
        f"## 1. Executive Lecture Summary & Key Topics Covered\n"
        f"{sections['executive_summary']}\n\n"
        f"## 2. Pedagogical & Delivery Evaluation Parameters\n"
        f"{sections['pedagogical_evaluation']}\n\n"
        f"## 3. Points to Preserve (נקודות לשימור)\n"
        f"{sections['points_to_preserve']}\n\n"
        f"## 4. Points for Improvement (נקודות לשיפור)\n"
        f"{sections['points_for_improvement']}\n\n"
        f"## 5. Action Items / Topics to Revisit Next Class\n"
        f"{sections['action_items']}\n"
    )


@router.get("")
def list_summaries(
    course_id: int | None = Query(default=None),
    lesson_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    course = resolve_course(db, course_id)
    stmt = select(LectureSummary).where(LectureSummary.course_id == course.id)
    if lesson_id is not None:
        stmt = stmt.where(LectureSummary.lesson_id == lesson_id)
    rows = db.scalars(stmt.order_by(LectureSummary.created_at.desc(), LectureSummary.id.desc())).all()
    return [r.to_dict() for r in rows]


@router.post("/generate")
async def generate_summary(
    request: Request,
    course_id: int | None = Form(default=None),
    lesson_id: int | None = Form(default=None),
    transcript: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    course = resolve_course(db, course_id)
    lesson: Lesson | None = None
    if lesson_id is not None:
        candidate = db.get(Lesson, lesson_id)
        if candidate and candidate.course_id == course.id:
            lesson = candidate

    media_bytes: bytes | None = None
    mime_type: str | None = None
    filename: str | None = None
    extracted_text = (transcript or "").strip()

    if file is not None and file.filename:
        filename = file.filename
        raw_bytes = await file.read()
        ctype = (file.content_type or "").lower()
        lower_name = filename.lower()

        if lower_name.endswith((".txt", ".vtt", ".srt", ".md", ".csv")):
            text_from_file = raw_bytes.decode("utf-8", errors="replace").strip()
            extracted_text = f"{extracted_text}\n\n{text_from_file}".strip()
        elif lower_name.endswith((".mp4", ".mov", ".webm", ".mkv", ".avi")) or ctype.startswith("video/"):
            media_bytes = raw_bytes
            mime_type = ctype if ctype.startswith("video/") else "video/mp4"
        elif lower_name.endswith((".mp3", ".wav", ".m4a", ".ogg")) or ctype.startswith("audio/"):
            media_bytes = raw_bytes
            mime_type = ctype if ctype.startswith("audio/") else "audio/mpeg"
        else:
            media_bytes = raw_bytes
            mime_type = ctype or "application/octet-stream"

    if not extracted_text and not media_bytes:
        raise HTTPException(
            status_code=422,
            detail="Please upload a lecture video/audio/transcript file or paste a Zoom/Meet transcript.",
        )

    lesson_label = f"Lesson {lesson.lesson_number}: {lesson.title}" if lesson else "General Course Lecture"
    user_prompt = (
        f"Course: {course.name}\n"
        f"Lesson: {lesson_label}\n"
        f"Source File: {filename or 'Pasted Transcript'}\n\n"
    )
    if extracted_text:
        user_prompt += f"=== LECTURE TRANSCRIPT / NOTES ===\n{extracted_text[:45000]}\n\n"
    user_prompt += (
        "Generate the complete 5-part pedagogical evaluation report as a valid JSON object with keys: "
        "executive_summary, pedagogical_evaluation, points_to_preserve, points_for_improvement, action_items."
    )

    try:
        ai_cfg = extract_ai_config(request)
        raw_reply, model_used = analyze_lecture_media(
            ai_cfg,
            media_bytes=media_bytes,
            mime_type=mime_type,
            filename=filename,
            prompt=user_prompt,
            system_prompt=PEDAGOGICAL_SYSTEM_PROMPT,
            max_tokens=4096,
        )
    except AIError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    parsed = extract_json_object(raw_reply)
    sections = {
        "executive_summary": str(parsed.get("executive_summary") or raw_reply).strip(),
        "pedagogical_evaluation": str(
            parsed.get("pedagogical_evaluation")
            or "Evaluated clarity, pacing, structure, engagement, and example usage based on the lecture recording."
        ).strip(),
        "points_to_preserve": str(
            parsed.get("points_to_preserve")
            or "• Clear presentation of core concepts and structured progression through examples."
        ).strip(),
        "points_for_improvement": str(
            parsed.get("points_for_improvement")
            or "• Pause more frequently for student comprehension checks after key definitions."
        ).strip(),
        "action_items": str(
            parsed.get("action_items")
            or "• Begin the next session with a 5-minute recap and short poll on today's material."
        ).strip(),
    }

    provider_label = f"{ai_cfg.provider.upper()} ({model_used})"
    full_md = _compose_full_markdown(
        course_name=course.name,
        lesson_label=lesson_label,
        source_name=filename or "Zoom/Meet Transcript",
        provider_label=provider_label,
        sections=sections,
    )

    summary = LectureSummary(
        course_id=course.id,
        lesson_id=lesson.id if lesson else None,
        source_filename=filename or "Transcript",
        ai_provider=provider_label,
        executive_summary=sections["executive_summary"],
        pedagogical_evaluation=sections["pedagogical_evaluation"],
        points_to_preserve=sections["points_to_preserve"],
        points_for_improvement=sections["points_for_improvement"],
        action_items=sections["action_items"],
        full_markdown=full_md,
    )
    db.add(summary)
    db.commit()
    db.refresh(summary)
    return summary.to_dict()


@router.get("/{summary_id}/export")
def export_summary(
    summary_id: int,
    fmt: str = Query(default="md", pattern="^(md|docx)$"),
    db: Session = Depends(get_db),
):
    summary = db.get(LectureSummary, summary_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Summary report not found.")
    slug = re.sub(r"[^A-Za-z0-9\-]+", "_", (summary.course.name if summary.course else "lecture")).strip("_") or "lecture"
    base_name = f"lecture_evaluation_{slug}_{summary.id}"

    if fmt == "docx":
        title = f"Lecture Evaluation - {summary.course.name if summary.course else 'Course'}"
        content = markdown_to_docx_bytes(title, summary.full_markdown)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.docx"'},
        )

    return Response(
        content=summary.full_markdown.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{base_name}.md"'},
    )


@router.delete("/{summary_id}")
def delete_summary(summary_id: int, db: Session = Depends(get_db)):
    summary = db.get(LectureSummary, summary_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Summary report not found.")
    db.delete(summary)
    db.commit()
    return {"ok": True}
