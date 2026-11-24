"""Deep LLM Homework Review & Cross-Student Comparison API (/api/homework)."""
from __future__ import annotations

import io
import json
import re
import urllib.request
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import config
from core.ai import AIError, extract_ai_config, extract_json_object, generate_text
from core.database import get_db
from core.network import require_lecturer_pin
from modules.homework.models import HomeworkSubmission
from modules.roster.service import clean_text, resolve_course

router = APIRouter(prefix="/api/homework", tags=["homework"])

CODE_EXTENSIONS = (
    ".py", ".java", ".c", ".cpp", ".cc", ".h", ".hpp", ".cs", ".js", ".jsx",
    ".ts", ".tsx", ".go", ".rs", ".rb", ".php", ".kt", ".swift", ".sql",
    ".html", ".css", ".json", ".yaml", ".yml", ".md", ".txt", ".sh",
)


class CompareAssignmentRequest(BaseModel):
    course_id: int | None = None
    assignment_name: str = Field(min_length=1, max_length=120)


def _is_lecturer_request(request: Request) -> bool:
    pin = (request.headers.get("x-lecturer-pin") or request.query_params.get("pin") or "").strip()
    return pin == config.LECTURER_PIN


def _extract_zip_bytes(raw_bytes: bytes, max_chars: int = 35000) -> str:
    parts = []
    total_len = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
            for info in zf.infolist():
                if info.is_dir() or info.file_size > 250_000:
                    continue
                lower = info.filename.lower()
                if any(skip in lower for skip in ("node_modules/", ".git/", "__pycache__/", ".venv/", "dist/", "build/")):
                    continue
                if not lower.endswith(CODE_EXTENSIONS):
                    continue
                try:
                    text = zf.read(info.filename).decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if not text:
                    continue
                snippet = f"// --- File: {info.filename} ---\n{text}\n"
                parts.append(snippet)
                total_len += len(snippet)
                if total_len >= max_chars:
                    break
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read ZIP archive: {exc}") from exc
    return "\n".join(parts)[:max_chars]


def _extract_pdf_fallback(raw_bytes: bytes, max_chars: int = 25000) -> str:
    """Extract readable ASCII/UTF-8 text strings from a PDF buffer without heavy external binaries."""
    text_chunks = re.findall(rb"[\x20-\x7e\r\n\t]{4,}", raw_bytes)
    joined = b"\n".join(text_chunks).decode("latin-1", errors="ignore")
    return joined[:max_chars]


def _fetch_github_repo(url: str) -> tuple[str, str]:
    cleaned = url.strip().rstrip("/")
    match = re.search(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)", cleaned)
    if not match:
        raise HTTPException(
            status_code=422,
            detail="Invalid GitHub repository URL. Expected format: https://github.com/owner/repo",
        )
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    zip_url = f"https://api.github.com/repos/{owner}/{repo}/zipball"
    req = urllib.request.Request(
        zip_url,
        headers={"User-Agent": "ClassPulse-HomeworkReviewer/2.0", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw_zip = resp.read()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not download GitHub repository '{owner}/{repo}' (make sure it is public): {exc}",
        ) from exc

    extracted = _extract_zip_bytes(raw_zip)
    if not extracted:
        raise HTTPException(
            status_code=400,
            detail=f"No readable source code files found in GitHub repository '{owner}/{repo}'.",
        )
    return f"github.com/{owner}/{repo}", extracted


HOMEWORK_REVIEW_SYSTEM_PROMPT = """You are a senior Software Engineering & OOP professor reviewing a university student's homework submission.
You are given the student's submitted code/document AND an anonymized summary of other peer submissions in the same class for the same assignment.

Return ONLY a valid JSON object with the exact following keys:
{
  "score_estimate": <integer 0-100>,
  "architecture_pattern": "<short description of architecture/design pattern used, e.g. 'Modular MVC / Clean OOP' or 'Monolithic Script'>",
  "strengths": ["<strength 1>", "<strength 2>", "<strength 3>"],
  "improvements": ["<improvement 1>", "<improvement 2>", "<improvement 3>"],
  "student_review_md": "Comprehensive Markdown review addressed to the student covering: (1) Correctness & Completeness, (2) OOP & Software Engineering Design Principles (SOLID, modularity, encapsulation), (3) Code Quality & Edge Cases, and (4) Constructive Anonymized Comparison to Class Peers (how their approach, complexity, and structure compare to other submissions in the course without revealing any peer names or raw peer code).",
  "lecturer_audit_md": "Detailed technical audit for the lecturer covering: (1) Grading Rubric Breakdown, (2) Architectural Strengths & Weaknesses, (3) Comparison against specific peer submissions in the class, and (4) Any notable implementation choices or similarity observations."
}"""


@router.get("/submissions")
def list_submissions(
    request: Request,
    course_id: int | None = Query(default=None),
    assignment_name: str | None = Query(default=None),
    student_name: str | None = Query(default=None),
    student_id: int | None = Query(default=None),
    view_role: str = Query(default="student"),
    db: Session = Depends(get_db),
):
    course = resolve_course(db, course_id)
    is_lecturer = _is_lecturer_request(request) and view_role == "lecturer"

    all_course_subs = db.scalars(
        select(HomeworkSubmission)
        .where(HomeworkSubmission.course_id == course.id)
        .order_by(HomeworkSubmission.created_at.desc(), HomeworkSubmission.id.desc())
    ).all()

    assignments = sorted({s.assignment_name for s in all_course_subs})

    filtered = all_course_subs
    if assignment_name and assignment_name.strip():
        target_assign = assignment_name.strip().lower()
        filtered = [s for s in filtered if s.assignment_name.lower() == target_assign]

    scores = [s.score_estimate for s in filtered if s.score_estimate is not None]
    avg_score = round(sum(scores) / len(scores)) if scores else None

    if is_lecturer:
        visible = [s.to_dict(include_code_and_audit=True) for s in filtered]
    else:
        # Students only see their own submissions (never other students' private submissions/code)
        s_name = (student_name or "").strip().lower()
        visible = [
            s.to_dict(include_code_and_audit=False)
            for s in filtered
            if (student_id is not None and s.student_id == student_id)
            or (s_name and s.student_name.strip().lower() == s_name)
        ]

    return {
        "course": course.to_dict(),
        "assignments": assignments,
        "class_stats": {
            "total_submissions": len(filtered),
            "average_score": avg_score,
        },
        "submissions": visible,
    }


@router.post("/submit")
def submit_homework(
    request: Request,
    course_id: int | None = Form(default=None),
    assignment_name: str = Form(...),
    student_name: str = Form(...),
    student_id: int | None = Form(default=None),
    github_url: str | None = Form(default=None),
    code_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    course = resolve_course(db, course_id)
    assign = clean_text(assignment_name) or "Ex2"
    s_name = clean_text(student_name)
    if not s_name or len(s_name) < 2:
        raise HTTPException(status_code=422, detail="Please enter the student's name.")

    submission_type = "code"
    source_label = "Pasted Code"
    extracted_content = (code_text or "").strip()

    if github_url and github_url.strip():
        submission_type = "github"
        source_label, gh_code = _fetch_github_repo(github_url.strip())
        extracted_content = f"{extracted_content}\n\n{gh_code}".strip()
    elif file is not None and file.filename:
        submission_type = "file"
        source_label = file.filename
        raw_bytes = file.file.read()
        lower = file.filename.lower()
        if lower.endswith(".zip"):
            file_code = _extract_zip_bytes(raw_bytes)
        elif lower.endswith(".pdf"):
            file_code = _extract_pdf_fallback(raw_bytes)
        else:
            file_code = raw_bytes.decode("utf-8", errors="replace").strip()
        extracted_content = f"{extracted_content}\n\n{file_code}".strip()

    if not extracted_content or len(extracted_content) < 10:
        raise HTTPException(
            status_code=422,
            detail="Please upload a code/ZIP/text file, enter a public GitHub repository URL, or paste your code.",
        )

    # Gather anonymized peer context from other submissions in the same course & assignment
    peer_subs = db.scalars(
        select(HomeworkSubmission)
        .where(
            HomeworkSubmission.course_id == course.id,
            HomeworkSubmission.assignment_name == assign,
        )
        .order_by(HomeworkSubmission.id.desc())
        .limit(8)
    ).all()

    peer_context_lines = []
    for idx, peer in enumerate(peer_subs, start=1):
        if peer.student_name.strip().lower() == s_name.lower():
            continue
        metrics = peer.design_metrics
        arch = metrics.get("architecture_pattern") or "Standard implementation"
        peer_snippet = peer.extracted_content[:1200]
        peer_context_lines.append(
            f"--- Peer Submission #{idx} (Score: {peer.score_estimate or 'N/A'}, Architecture: {arch}) ---\n"
            f"{peer_snippet}\n"
        )

    peer_block = (
        "\n".join(peer_context_lines)
        if peer_context_lines
        else "No other student submissions exist for this assignment yet (this is the first submission in the class)."
    )

    user_prompt = (
        f"Course: {course.name}\n"
        f"Assignment: {assign}\n"
        f"Student: {s_name}\n"
        f"Source: {source_label}\n\n"
        f"=== THIS STUDENT'S SUBMISSION ===\n{extracted_content[:30000]}\n\n"
        f"=== ANONYMIZED PEER SUBMISSIONS IN THIS CLASS ({len(peer_context_lines)} peers) ===\n{peer_block}\n\n"
        "Analyze this submission thoroughly, compare it against the class peers, and return the JSON object."
    )

    try:
        ai_cfg = extract_ai_config(request)
        raw_reply, model_used = generate_text(
            ai_cfg,
            prompt=user_prompt,
            system_prompt=HOMEWORK_REVIEW_SYSTEM_PROMPT,
            max_tokens=4096,
        )
    except AIError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    parsed = extract_json_object(raw_reply)
    raw_score = parsed.get("score_estimate")
    try:
        score = max(0, min(100, int(raw_score))) if raw_score is not None else 88
    except Exception:
        score = 88

    student_md = str(parsed.get("student_review_md") or raw_reply).strip()
    lecturer_md = str(parsed.get("lecturer_audit_md") or student_md).strip()
    metrics = {
        "architecture_pattern": str(parsed.get("architecture_pattern") or "Modular Design"),
        "strengths": parsed.get("strengths") if isinstance(parsed.get("strengths"), list) else [],
        "improvements": parsed.get("improvements") if isinstance(parsed.get("improvements"), list) else [],
        "ai_model": f"{ai_cfg.provider.upper()} ({model_used})",
        "peers_compared": len(peer_context_lines),
    }

    submission = HomeworkSubmission(
        course_id=course.id,
        assignment_name=assign,
        student_id=student_id,
        student_name=s_name,
        submission_type=submission_type,
        source_label=source_label,
        extracted_content=extracted_content[:40000],
        score_estimate=score,
        student_review_md=student_md,
        lecturer_audit_md=lecturer_md,
        design_metrics_json=json.dumps(metrics, ensure_ascii=False),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission.to_dict(include_code_and_audit=_is_lecturer_request(request))


@router.post("/compare", dependencies=[Depends(require_lecturer_pin)])
def compare_all_submissions(
    payload: CompareAssignmentRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Lecturer-only Cross-Student Comparison Matrix across all submissions for an assignment."""
    course = resolve_course(db, payload.course_id)
    assign = clean_text(payload.assignment_name) or "Ex2"
    subs = db.scalars(
        select(HomeworkSubmission)
        .where(
            HomeworkSubmission.course_id == course.id,
            HomeworkSubmission.assignment_name == assign,
        )
        .order_by(HomeworkSubmission.id.asc())
    ).all()

    if len(subs) < 2:
        raise HTTPException(
            status_code=400,
            detail=f"At least 2 submissions for '{assign}' are required to generate a Cross-Student Comparison Matrix (currently {len(subs)}).",
        )

    digest_parts = []
    for s in subs[:15]:
        m = s.design_metrics
        digest_parts.append(
            f"### Student: {s.student_name} (Source: {s.source_label}, Est. Score: {s.score_estimate})\n"
            f"Architecture: {m.get('architecture_pattern', 'N/A')}\n"
            f"Code Excerpt:\n```\n{s.extracted_content[:2000]}\n```\n"
        )

    system_prompt = (
        "You are a Software Engineering course lead conducting a Cross-Student Homework Comparison Audit. "
        "Produce a structured Markdown report containing:\n"
        "1. **Cross-Student Comparison Matrix** (Markdown table comparing each student's Architecture, OOP Design Quality, Strengths, Weaknesses, and Score)\n"
        "2. **Standout Implementations & Best Practices in Class**\n"
        "3. **Common Class-Wide Mistakes & Misconceptions**\n"
        "4. **Code Similarity / Originality Check** (noting whether submissions use distinct designs or share suspicious structural/naming overlap)."
    )
    user_prompt = (
        f"Course: {course.name}\nAssignment: {assign}\nTotal Submissions: {len(subs)}\n\n"
        + "\n".join(digest_parts)
    )

    try:
        ai_cfg = extract_ai_config(request)
        report_md, model_used = generate_text(
            ai_cfg,
            prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=4096,
        )
    except AIError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    return {
        "course_id": course.id,
        "course_name": course.name,
        "assignment_name": assign,
        "submission_count": len(subs),
        "ai_model": f"{ai_cfg.provider.upper()} ({model_used})",
        "comparison_markdown": report_md,
    }


@router.delete("/submissions/{submission_id}", dependencies=[Depends(require_lecturer_pin)])
def delete_submission(submission_id: int, db: Session = Depends(get_db)):
    sub = db.get(HomeworkSubmission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Submission not found.")
    db.delete(sub)
    db.commit()
    return {"ok": True}
