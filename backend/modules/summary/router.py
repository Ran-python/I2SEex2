"""Lecturer-only ML Lecture Video Summary & Pedagogical Evaluation API (/api/summaries)."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from core import config
from core.ai import (
    AIConfig,
    AIError,
    contains_hebrew,
    extract_ai_config,
    extract_json_object,
    extract_youtube_video_id,
    fetch_youtube_transcript,
    generate_text,
    markdown_to_docx_bytes,
    markdown_to_pdf_bytes,
    normalize_language_code,
)
from core.database import SessionLocal, engine, get_db
from core.network import require_lecturer_pin
from modules.attendance.models import Lesson
from modules.roster.service import resolve_course
from modules.summary.audio_pipeline import (
    ALLOWED_MEDIA_EXTENSIONS,
    process_media_file_to_transcript,
    sanitize_media_filename,
    stream_upload_to_file,
)
from modules.summary.models import LectureSummary

# Strictly restricted to Lecturer Mode (PIN 3358)
router = APIRouter(
    prefix="/api/summaries",
    tags=["lecture-summary"],
    dependencies=[Depends(require_lecturer_pin)],
)

# Bounded background worker pool & thread-safe job state store for non-blocking MP4 processing
_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, config.SUMMARY_MAX_WORKERS), thread_name_prefix="summary_mp4")
_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_JOB_TTL_SECONDS = 3600.0

PEDAGOGICAL_SYSTEM_PROMPT = """You are a senior university pedagogical coach and computer-science instructional evaluator.
Analyze the provided timestamped lecture transcript and generate a thorough, constructive, and structured lecture summary and teaching evaluation report.

Language & Technical Terminology Rules:
- Write in the same primary language as the lecture (if the lecture transcript is in Hebrew or requested in Hebrew, write the ENTIRE report in natural academic Hebrew; if in English, write in English).
- Preserve English technical terms, algorithm names (e.g. Dijkstra, QuickSort, BFS), mathematical notation (e.g. O(n log n)), and programming identifiers (e.g. OOP, SOLID, Dependency Injection, SQL, FastAPI, React) in English WITHOUT awkward translation.
- Preserve chronological timestamps (e.g. [00:12:30]) when referencing key explanations or transitions in the lecture.

Return ONLY a valid JSON object with the following exact 5 string keys (use rich Markdown bullet points, and Markdown tables where helpful, inside each string):
{
  "executive_summary": "Overview & Executive Lecture Summary — Comprehensive overview of the lecture, learning objectives, and key academic/technical topics & definitions covered.",
  "pedagogical_evaluation": "Detailed Chronological Breakdown & Pedagogical Evaluation — Timestamped breakdown of the lecture modules plus a structured evaluation across 5 parameters: (1) Clarity of Explanations, (2) Pacing & Time Management, (3) Logical Structure & Flow, (4) Student Engagement & Q&A Handling, and (5) Quality of Concrete Examples.",
  "points_to_preserve": "Points to Preserve (נקודות לשימור) — Specific teaching practices, explanations, or examples that worked exceptionally well and should be kept.",
  "points_for_improvement": "Points for Improvement (נקודות לשיפור) — Concrete, actionable pedagogical recommendations to improve clarity, pacing, or student comprehension in future lectures.",
  "action_items": "Review Questions & Action Items for Next Class — 3 to 5 self-check review questions on today's material, plus concepts to revisit next lesson."
}"""


def _compose_full_markdown(
    course_name: str,
    lesson_label: str,
    source_name: str,
    provider_label: str,
    sections: dict[str, str],
    is_hebrew: bool = False,
) -> str:
    if is_hebrew:
        return (
            f"# סיכום הרצאה והערכת הוראה פדגוגית\n"
            f"**קורס:** {course_name}  \n"
            f"**שיעור:** {lesson_label}  \n"
            f"**מקור הקלטה:** {source_name}  \n"
            f"**מודל AI:** {provider_label}\n\n"
            f"---\n\n"
            f"## 1. תקציר מנהלים ומושגי מפתח\n"
            f"{sections['executive_summary']}\n\n"
            f"## 2. פירוט כרונולוגי והערכה פדגוגית\n"
            f"{sections['pedagogical_evaluation']}\n\n"
            f"## 3. נקודות לשימור\n"
            f"{sections['points_to_preserve']}\n\n"
            f"## 4. נקודות לשיפור\n"
            f"{sections['points_for_improvement']}\n\n"
            f"## 5. שאלות חזרה ומשימות להמשך\n"
            f"{sections['action_items']}\n"
        )

    return (
        f"# Lecture Summary & Pedagogical Evaluation\n"
        f"**Course:** {course_name}  \n"
        f"**Lesson:** {lesson_label}  \n"
        f"**Source:** {source_name}  \n"
        f"**AI Model:** {provider_label}\n\n"
        f"---\n\n"
        f"## 1. Overview, Executive Summary & Key Definitions\n"
        f"{sections['executive_summary']}\n\n"
        f"## 2. Detailed Breakdown & Pedagogical Evaluation Parameters\n"
        f"{sections['pedagogical_evaluation']}\n\n"
        f"## 3. Points to Preserve (נקודות לשימור)\n"
        f"{sections['points_to_preserve']}\n\n"
        f"## 4. Points for Improvement (נקודות לשיפור)\n"
        f"{sections['points_for_improvement']}\n\n"
        f"## 5. Review Questions & Action Items for Next Class\n"
        f"{sections['action_items']}\n"
    )


def _safe_int(val: str | int | None) -> int | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("null", "undefined", "none"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _safe_bool(val: str | bool | None) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _prune_old_jobs() -> None:
    now = time.time()
    with _JOBS_LOCK:
        stale = [jid for jid, info in _JOBS.items() if now - info.get("updated_at", now) > _JOB_TTL_SECONDS]
        for jid in stale:
            _JOBS.pop(jid, None)


def _update_job(job_id: str | None, **updates) -> None:
    if not job_id:
        return
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(updates)
            _JOBS[job_id]["updated_at"] = time.time()


def _execute_summary_pipeline(
    *,
    course_id: int | None,
    lesson_id: int | None,
    ai_cfg: AIConfig,
    youtube_url: str,
    pasted_transcript: str,
    staged_media_path: Path | None,
    original_filename: str | None,
    language: str | None,
    job_id: str | None = None,
) -> dict:
    """Core summary workflow shared by synchronous requests and non-blocking background jobs.

    Uses short-lived DB sessions ONLY for initial metadata lookup and final INSERT so no
    PostgreSQL connection is ever held open (idle-in-transaction) during FFmpeg / STT / LLM calls.
    """
    # 1. Fast metadata lookup (~5 ms), then immediately close the DB session & transaction
    with SessionLocal() as read_db:
        course = resolve_course(read_db, course_id)
        resolved_course_id = course.id
        course_name = course.name
        resolved_lesson_id: int | None = None
        lesson_label = "General Course Lecture"
        if lesson_id is not None:
            candidate = read_db.get(Lesson, lesson_id)
            if candidate and candidate.course_id == resolved_course_id:
                resolved_lesson_id = candidate.id
                lesson_label = f"Lesson {candidate.lesson_number}: {candidate.title}"
        read_db.rollback()

    extracted_text = pasted_transcript.strip()
    yt_link = youtube_url.strip()
    source_name = original_filename

    if not yt_link and extracted_text and len(extracted_text.splitlines()) == 1:
        if "youtube.com/" in extracted_text or "youtu.be/" in extracted_text:
            if extract_youtube_video_id(extracted_text):
                yt_link = extracted_text
                extracted_text = ""

    # Pipeline A: YouTube Transcript Extraction (0 DB connections held)
    if yt_link:
        _update_job(job_id, status="processing", stage="📺 Extracting YouTube transcript...")
        video_id, yt_transcript = fetch_youtube_transcript(yt_link)
        source_name = f"YouTube ({video_id})"
        extracted_text = f"{extracted_text}\n\n{yt_transcript}".strip() if extracted_text else yt_transcript

    # Pipeline B: MP4 / Media File -> FFmpeg Audio Preprocessing -> Chunking -> Speech-to-Text (0 DB connections held)
    if staged_media_path is not None:
        audio_res = process_media_file_to_transcript(
            input_media_path=staged_media_path,
            original_filename=original_filename or staged_media_path.name,
            ai_cfg=ai_cfg,
            language=language,
            progress_callback=lambda msg: _update_job(job_id, status="processing", stage=msg),
        )
        extracted_text = (
            f"{extracted_text}\n\n{audio_res.formatted_transcript}".strip()
            if extracted_text
            else audio_res.formatted_transcript
        )

    if not extracted_text:
        raise AIError(
            400,
            "Please enter a YouTube URL, upload a lecture video/audio file (.mp4, .mov, .webm), or paste a transcript.",
        )

    norm_lang = normalize_language_code(language)
    is_hebrew_lecture = (norm_lang == "he") or contains_hebrew(extracted_text)

    lang_directive = (
        "IMPORTANT: This lecture is in HEBREW (עברית). Write all 5 JSON report values in fluent academic HEBREW, "
        "while keeping English technical terms, algorithm names, mathematical notation (e.g. O(n log n)), and code identifiers in English without translating them.\n\n"
        if is_hebrew_lecture
        else ""
    )

    user_prompt = (
        f"Course: {course_name}\n"
        f"Lesson: {lesson_label}\n"
        f"Source: {source_name or 'Pasted Transcript'}\n\n"
        f"{lang_directive}"
        f"=== TIMESTAMPED LECTURE TRANSCRIPT / NOTES ===\n{extracted_text[:65000]}\n\n"
        "Generate the complete 5-part lecture summary & pedagogical evaluation report as a valid JSON object with keys: "
        "executive_summary, pedagogical_evaluation, points_to_preserve, points_for_improvement, action_items."
    )

    _update_job(job_id, status="processing", stage="🧠 Generating structured pedagogical evaluation report...")
    raw_reply, model_used = generate_text(
        ai_cfg,
        user_prompt,
        system_prompt=PEDAGOGICAL_SYSTEM_PROMPT,
        max_tokens=8192,
    )

    parsed = extract_json_object(raw_reply)
    if is_hebrew_lecture:
        default_eval = "בוצעה הערכה פדגוגית של בהירות ההסברים, קצב ההרצאה, מבנה לוגי, מעורבות סטודנטים ואיכות הדוגמאות."
        default_preserve = "• הצגה מובנית ובהירה של מושגי הליבה ושילוב מדויק של מונחים טכניים."
        default_improve = "• מומלץ לשלב עצירות קצרות לבדיקת הבנה לאחר הגדרות מרכזיות."
        default_actions = "• לפתוח את השיעור הבא בחזרה קצרה בת 5 דקות ובסקר הבנה על חומר ההרצאה."
    else:
        default_eval = "Evaluated clarity, pacing, structure, engagement, and example usage based on the lecture recording."
        default_preserve = "• Clear presentation of core concepts and structured progression through examples."
        default_improve = "• Pause more frequently for student comprehension checks after key definitions."
        default_actions = "• Begin the next session with a 5-minute recap and short poll on today's material."

    sections = {
        "executive_summary": str(parsed.get("executive_summary") or raw_reply).strip(),
        "pedagogical_evaluation": str(parsed.get("pedagogical_evaluation") or default_eval).strip(),
        "points_to_preserve": str(parsed.get("points_to_preserve") or default_preserve).strip(),
        "points_for_improvement": str(parsed.get("points_for_improvement") or default_improve).strip(),
        "action_items": str(parsed.get("action_items") or default_actions).strip(),
    }

    report_is_hebrew = is_hebrew_lecture or contains_hebrew(sections["executive_summary"])
    provider_label = f"{ai_cfg.provider.upper()} ({model_used})"
    full_md = _compose_full_markdown(
        course_name=course_name,
        lesson_label=lesson_label,
        source_name=source_name or "Zoom/Meet Transcript",
        provider_label=provider_label,
        sections=sections,
        is_hebrew=report_is_hebrew,
    )

    # 3. Fresh short-lived write session to persist the completed report in Neon PostgreSQL / SQLite
    _update_job(job_id, status="processing", stage="💾 Saving lecture summary report to database...")
    for db_attempt in range(2):
        try:
            with SessionLocal() as write_db:
                summary = LectureSummary(
                    course_id=resolved_course_id,
                    lesson_id=resolved_lesson_id,
                    source_filename=source_name or "Transcript",
                    ai_provider=provider_label,
                    executive_summary=sections["executive_summary"],
                    pedagogical_evaluation=sections["pedagogical_evaluation"],
                    points_to_preserve=sections["points_to_preserve"],
                    points_for_improvement=sections["points_for_improvement"],
                    action_items=sections["action_items"],
                    full_markdown=full_md,
                )
                write_db.add(summary)
                write_db.commit()
                write_db.refresh(summary)
                return summary.to_dict()
        except OperationalError:
            if db_attempt == 0:
                engine.dispose()
                continue
            raise


def _run_background_summary_job(
    *,
    job_id: str,
    stage_dir: Path | None,
    staged_media_path: Path | None,
    original_filename: str | None,
    course_id: int | None,
    lesson_id: int | None,
    ai_cfg: AIConfig,
    youtube_url: str,
    pasted_transcript: str,
    language: str | None,
) -> None:
    try:
        result = _execute_summary_pipeline(
            course_id=course_id,
            lesson_id=lesson_id,
            ai_cfg=ai_cfg,
            youtube_url=youtube_url,
            pasted_transcript=pasted_transcript,
            staged_media_path=staged_media_path,
            original_filename=original_filename,
            language=language,
            job_id=job_id,
        )
        _update_job(
            job_id,
            status="completed",
            stage="✅ Lecture evaluation report completed!",
            summary=result,
        )
    except AIError as exc:
        _update_job(
            job_id,
            status="failed",
            status_code=exc.status_code,
            detail=exc.message,
            stage="❌ Processing failed",
        )
    except Exception as exc:
        traceback.print_exc()
        _update_job(
            job_id,
            status="failed",
            status_code=500,
            detail=f"Lecture summary generation failed: {exc}",
            stage="❌ Processing failed",
        )
    finally:
        if stage_dir is not None and stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)


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


@router.get("/jobs/{job_id}")
def get_summary_job_status(job_id: str):
    """Poll the status of a non-blocking MP4 audio extraction & transcription job."""
    _prune_old_jobs()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Summary processing job not found or expired.")
        return dict(job)


@router.post("/generate")
def generate_summary(
    request: Request,
    course_id: str | None = Form(default=None),
    lesson_id: str | None = Form(default=None),
    youtube_url: str | None = Form(default=None),
    transcript: str | None = Form(default=None),
    language: str | None = Form(default=None),
    async_job: str | None = Form(default=None),
    provider: str | None = Form(default=None),
    api_key: str | None = Form(default=None),
    model: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
):
    stage_dir: Path | None = None
    handed_to_bg = False
    try:
        parsed_course_id = _safe_int(course_id)
        parsed_lesson_id = _safe_int(lesson_id)
        run_async = _safe_bool(async_job) or _safe_bool(request.query_params.get("async_job"))

        ai_cfg = extract_ai_config(
            request,
            provider_override=provider,
            api_key_override=api_key,
            model_override=model,
        )

        staged_media_path: Path | None = None
        original_filename: str | None = None
        extracted_text = (transcript or "").strip()
        yt_link = (youtube_url or "").strip()

        if file is not None and file.filename:
            raw_name = file.filename.strip()
            lower_name = Path(raw_name).name.lower()

            if lower_name.endswith((".txt", ".vtt", ".srt", ".md", ".csv")):
                original_filename = Path(raw_name).name[:120]
                raw_bytes = file.file.read(10 * 1024 * 1024 + 1)
                if len(raw_bytes) > 10 * 1024 * 1024:
                    return JSONResponse(status_code=413, content={"detail": "Text transcript file exceeds 10 MB limit."})
                text_from_file = raw_bytes.decode("utf-8", errors="replace").strip()
                extracted_text = f"{extracted_text}\n\n{text_from_file}".strip() if extracted_text else text_from_file
            else:
                safe_name = sanitize_media_filename(raw_name)
                original_filename = safe_name
                stage_dir = Path(tempfile.mkdtemp(prefix="classpulse_upload_stage_"))
                staged_media_path = stream_upload_to_file(file.file, stage_dir, safe_name)

        if not yt_link and not extracted_text and staged_media_path is None:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Please enter a YouTube URL, upload a lecture video/audio file (.mp4, .mov, .webm), or paste a transcript.",
                },
            )

        if run_async:
            _prune_old_jobs()
            job_id = uuid.uuid4().hex
            now_ts = time.time()
            job_record = {
                "job_id": job_id,
                "status": "queued",
                "stage": "📥 Upload received — queued for FFmpeg audio extraction...",
                "filename": original_filename or ("YouTube" if yt_link else "Transcript"),
                "created_at": now_ts,
                "updated_at": now_ts,
                "summary": None,
                "detail": None,
            }
            with _JOBS_LOCK:
                _JOBS[job_id] = job_record

            handed_to_bg = True
            _JOB_EXECUTOR.submit(
                _run_background_summary_job,
                job_id=job_id,
                stage_dir=stage_dir,
                staged_media_path=staged_media_path,
                original_filename=original_filename,
                course_id=parsed_course_id,
                lesson_id=parsed_lesson_id,
                ai_cfg=ai_cfg,
                youtube_url=yt_link,
                pasted_transcript=extracted_text,
                language=language,
            )
            return JSONResponse(status_code=202, content=job_record)

        result = _execute_summary_pipeline(
            course_id=parsed_course_id,
            lesson_id=parsed_lesson_id,
            ai_cfg=ai_cfg,
            youtube_url=yt_link,
            pasted_transcript=extracted_text,
            staged_media_path=staged_media_path,
            original_filename=original_filename,
            language=language,
        )
        return result

    except AIError as exc:
        print(f"[SummaryError] AIError ({exc.status_code}): {exc.message}", flush=True)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"detail": f"Lecture summary generation failed: {exc}"},
        )
    finally:
        if not handed_to_bg and stage_dir is not None and stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)


@router.get("/{summary_id}/export")
def export_summary(
    summary_id: int,
    fmt: str = Query(default="md", pattern="^(md|docx|pdf)$"),
    db: Session = Depends(get_db),
):
    summary = db.get(LectureSummary, summary_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Summary report not found.")
    slug = re.sub(r"[^A-Za-z0-9\-]+", "_", (summary.course.name if summary.course else "lecture")).strip("_") or "lecture"
    base_name = f"lecture_evaluation_{slug}_{summary.id}"

    is_heb = contains_hebrew(summary.full_markdown)
    course_title = summary.course.name if summary.course else ("קורס" if is_heb else "Course")
    title = f"דוח הערכת הרצאה - {course_title}" if is_heb else f"Lecture Evaluation - {course_title}"

    if fmt == "docx":
        content = markdown_to_docx_bytes(title, summary.full_markdown)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.docx"'},
        )

    if fmt == "pdf":
        content = markdown_to_pdf_bytes(title, summary.full_markdown)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.pdf"'},
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
