"""Unified multi-provider AI client (Google Gemini, Anthropic Claude, OpenAI GPT) via REST APIs."""
from __future__ import annotations

import base64
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from xml.sax.saxutils import escape as xml_escape

from fastapi import HTTPException, Request

from core import config

PROVIDER_MODELS: dict[str, tuple[str, ...]] = {
    "gemini": ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"),
    "claude": (
        "claude-sonnet-4-20250514",
        "claude-3-7-sonnet-latest",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "claude-3-haiku-20240307",
    ),
    "openai": ("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "o3-mini"),
}

_MODELS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_MODELS_CACHE_TTL = 300.0


class AIError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class AIConfig:
    provider: str
    api_key: str
    model: str | None = None


def extract_ai_config(
    request: Request | None = None,
    provider_override: str | None = None,
    api_key_override: str | None = None,
    model_override: str | None = None,
) -> AIConfig:
    provider = (
        provider_override
        or (request.headers.get("x-ai-provider") if request else None)
        or "gemini"
    ).strip().lower()
    if provider not in PROVIDER_MODELS:
        provider = "gemini"

    raw_key = (
        api_key_override
        or (request.headers.get("x-ai-api-key") if request else None)
        or ""
    ).strip()

    if not raw_key:
        if provider == "gemini" and config.GEMINI_API_KEY:
            raw_key = config.GEMINI_API_KEY
        elif provider == "claude" and config.ANTHROPIC_API_KEY:
            raw_key = config.ANTHROPIC_API_KEY
        elif provider == "openai" and config.OPENAI_API_KEY:
            raw_key = config.OPENAI_API_KEY

    model = (
        model_override
        or (request.headers.get("x-ai-model") if request else None)
        or ""
    ).strip() or None

    if not raw_key:
        raise AIError(
            400,
            f"No API key configured for {provider.upper()}. "
            "Click '⚙️ AI Settings' in the top navigation bar to enter your API key.",
        )

    return AIConfig(provider=provider, api_key=raw_key, model=model)


def _normalize_model_id(provider: str, model_id: str | None) -> str | None:
    if not model_id:
        return None
    clean = model_id.strip()
    if provider == "gemini" and clean.startswith("models/"):
        clean = clean[len("models/") :]
    return clean or None


def _http_get_json(url: str, headers: dict[str, str], timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw_err = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(raw_err)
            msg = (
                err_json.get("error", {}).get("message")
                if isinstance(err_json.get("error"), dict)
                else err_json.get("error") or err_json.get("message") or raw_err
            )
        except Exception:
            msg = raw_err
        raise AIError(exc.code, f"{msg}") from exc
    except Exception as exc:
        raise AIError(502, f"Could not reach AI provider: {exc}") from exc


def list_provider_models(cfg: AIConfig, force_refresh: bool = False) -> dict:
    """Query the live provider models API (Gemini, OpenAI, or Claude) and return available models."""
    cache_key = f"{cfg.provider}:{cfg.api_key[:16]}"
    now = time.time()
    if not force_refresh and cache_key in _MODELS_CACHE:
        cached_at, cached_models = _MODELS_CACHE[cache_key]
        if now - cached_at < _MODELS_CACHE_TTL and cached_models:
            return {
                "provider": cfg.provider,
                "models": cached_models,
                "default_model": cached_models[0]["id"],
            }

    models_list: list[dict] = []

    if cfg.provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(cfg.api_key)}"
        res = _http_get_json(url, headers={})
        raw_models = res.get("models") or []
        excluded_substrings = ("embedding", "aqa", "imagen", "veo", "tts", "audio", "bisheng", "learnlm")
        for m in raw_models:
            methods = m.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            raw_name = m.get("name") or ""
            short_id = raw_name[len("models/") :] if raw_name.startswith("models/") else raw_name
            if not short_id:
                continue
            low = short_id.lower()
            if any(sub in low for sub in excluded_substrings):
                continue
            display = m.get("displayName") or short_id
            models_list.append(
                {
                    "id": short_id,
                    "label": f"{short_id} ({display})" if display and display != short_id else short_id,
                }
            )

        priority_order = [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-flash-latest",
            "gemini-pro-latest",
        ]

        def _gemini_rank(item: dict) -> tuple[int, str]:
            mid = item["id"]
            if mid in priority_order:
                return (priority_order.index(mid), mid)
            if mid.startswith("gemini-2.5"):
                return (10, mid)
            if mid.startswith("gemini-2.0"):
                return (20, mid)
            if mid.startswith("gemini-"):
                return (30, mid)
            return (50, mid)

        models_list.sort(key=_gemini_rank)

    elif cfg.provider == "openai":
        url = "https://api.openai.com/v1/models"
        res = _http_get_json(url, headers={"Authorization": f"Bearer {cfg.api_key}"})
        raw_models = res.get("data") or []
        allowed_prefixes = ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo", "o1", "o3", "o4", "chatgpt-4o")
        excluded = ("audio", "realtime", "tts", "transcribe", "whisper", "embedding", "dall-e", "moderation", "search", "instruct")
        for m in raw_models:
            mid = (m.get("id") or "").strip()
            low = mid.lower()
            if not mid or not any(low.startswith(p) for p in allowed_prefixes):
                continue
            if any(x in low for x in excluded):
                continue
            models_list.append({"id": mid, "label": mid})

        priority_openai = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "o3-mini", "o4-mini"]

        def _openai_rank(item: dict) -> tuple[int, str]:
            mid = item["id"]
            if mid in priority_openai:
                return (priority_openai.index(mid), mid)
            return (20, mid)

        models_list.sort(key=_openai_rank)

    elif cfg.provider == "claude":
        url = "https://api.anthropic.com/v1/models"
        try:
            res = _http_get_json(
                url,
                headers={
                    "x-api-key": cfg.api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            for m in res.get("data") or []:
                mid = (m.get("id") or "").strip()
                if not mid:
                    continue
                display = m.get("display_name") or mid
                models_list.append(
                    {
                        "id": mid,
                        "label": f"{mid} ({display})" if display and display != mid else mid,
                    }
                )
        except AIError as exc:
            if exc.status_code in (401, 403):
                raise
            models_list = [{"id": m, "label": m} for m in PROVIDER_MODELS["claude"]]

    if not models_list:
        models_list = [{"id": m, "label": m} for m in PROVIDER_MODELS.get(cfg.provider, ("gemini-2.5-flash",))]

    _MODELS_CACHE[cache_key] = (now, models_list)
    return {
        "provider": cfg.provider,
        "models": models_list,
        "default_model": models_list[0]["id"],
    }


def _candidate_models(cfg: AIConfig) -> list[str]:
    selected = _normalize_model_id(cfg.provider, cfg.model)
    dynamic_ids: list[str] = []
    try:
        fetched = list_provider_models(cfg)
        dynamic_ids = [m["id"] for m in fetched.get("models", []) if m.get("id")]
    except AIError as exc:
        if exc.status_code in (401, 403):
            raise
    except Exception:
        pass

    defaults = dynamic_ids if dynamic_ids else list(PROVIDER_MODELS.get(cfg.provider, ("gemini-2.5-flash",)))
    if selected:
        return [selected] + [m for m in defaults if m != selected]
    return defaults


def _http_json(url: str, payload: dict, headers: dict[str, str], timeout: int = 90) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw_err = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(raw_err)
            msg = (
                err_json.get("error", {}).get("message")
                if isinstance(err_json.get("error"), dict)
                else err_json.get("error") or err_json.get("message") or raw_err
            )
        except Exception:
            msg = raw_err
        raise AIError(exc.code, f"{msg}") from exc
    except Exception as exc:
        raise AIError(502, f"Could not reach AI provider: {exc}") from exc


# ---------------------------------------------------------------------------
# Provider-specific REST implementations
# ---------------------------------------------------------------------------
def _gemini_generate(
    api_key: str,
    model: str,
    parts: list[dict],
    system_prompt: str = "",
    max_tokens: int = 4096,
) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}"
        f":generateContent?key={urllib.parse.quote(api_key)}"
    )
    payload: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
    }
    if system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    res = _http_json(url, payload, headers={}, timeout=120)
    candidates = res.get("candidates") or []
    if not candidates:
        raise AIError(502, f"Gemini returned no candidates: {res}")
    content_parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in content_parts).strip()
    if not text:
        raise AIError(502, "Gemini returned an empty response.")
    return text


def _claude_generate(
    api_key: str,
    model: str,
    content_blocks: list[dict],
    system_prompt: str = "",
    max_tokens: int = 4096,
) -> str:
    url = "https://api.anthropic.com/v1/messages"
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content_blocks}],
    }
    if system_prompt:
        payload["system"] = system_prompt

    res = _http_json(
        url,
        payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=120,
    )
    blocks = res.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if not text:
        raise AIError(502, "Claude returned an empty response.")
    return text


def _openai_generate(
    api_key: str,
    model: str,
    user_content: list[dict] | str,
    system_prompt: str = "",
    max_tokens: int = 4096,
) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_content})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    res = _http_json(
        url,
        payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120,
    )
    choices = res.get("choices") or []
    if not choices:
        raise AIError(502, "OpenAI returned no choices.")
    text = (choices[0].get("message", {}).get("content") or "").strip()
    if not text:
        raise AIError(502, "OpenAI returned an empty response.")
    return text


# ---------------------------------------------------------------------------
# Public high-level operations
# ---------------------------------------------------------------------------
def generate_text(
    cfg: AIConfig,
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 4096,
) -> tuple[str, str]:
    """Run text generation across Gemini / Claude / OpenAI. Returns (response_text, model_used)."""
    last_err: AIError | None = None
    for model in _candidate_models(cfg):
        try:
            if cfg.provider == "gemini":
                out = _gemini_generate(cfg.api_key, model, [{"text": prompt}], system_prompt, max_tokens)
            elif cfg.provider == "claude":
                out = _claude_generate(cfg.api_key, model, [{"type": "text", "text": prompt}], system_prompt, max_tokens)
            else:
                out = _openai_generate(cfg.api_key, model, prompt, system_prompt, max_tokens)
            return out, model
        except AIError as exc:
            last_err = exc
            if exc.status_code in (401, 403):
                break
    raise last_err or AIError(502, "AI request failed.")


def analyze_image(
    cfg: AIConfig,
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 2048,
) -> tuple[str, str]:
    """Run multimodal Vision analysis across Gemini / Claude / OpenAI."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    last_err: AIError | None = None
    for model in _candidate_models(cfg):
        try:
            if cfg.provider == "gemini":
                parts = [
                    {"inline_data": {"mime_type": mime_type, "data": b64}},
                    {"text": prompt},
                ]
                out = _gemini_generate(cfg.api_key, model, parts, system_prompt, max_tokens)
            elif cfg.provider == "claude":
                blocks = [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime_type, "data": b64},
                    },
                    {"type": "text", "text": prompt},
                ]
                out = _claude_generate(cfg.api_key, model, blocks, system_prompt, max_tokens)
            else:
                content = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                ]
                out = _openai_generate(cfg.api_key, model, content, system_prompt, max_tokens)
            return out, model
        except AIError as exc:
            last_err = exc
            if exc.status_code in (401, 403):
                break
    raise last_err or AIError(502, "Vision AI request failed.")


def _gemini_upload_file(api_key: str, file_bytes: bytes, mime_type: str, display_name: str) -> str:
    """Upload a video/audio file to the Gemini File API and wait until it is ACTIVE."""
    start_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={urllib.parse.quote(api_key)}"
    meta = json.dumps({"file": {"display_name": display_name}}).encode("utf-8")
    start_req = urllib.request.Request(
        start_url,
        data=meta,
        method="POST",
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(file_bytes)),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(start_req, timeout=60) as resp:
            upload_url = resp.headers.get("X-Goog-Upload-URL") or resp.headers.get("x-goog-upload-url")
    except Exception as exc:
        raise AIError(502, f"Failed to initialize Gemini video upload: {exc}") from exc

    if not upload_url:
        raise AIError(502, "Gemini File API did not return an upload URL.")

    up_req = urllib.request.Request(
        upload_url,
        data=file_bytes,
        method="POST",
        headers={
            "Content-Length": str(len(file_bytes)),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
    )
    try:
        with urllib.request.urlopen(up_req, timeout=180) as resp:
            file_info = json.loads(resp.read().decode("utf-8")).get("file", {})
    except Exception as exc:
        raise AIError(502, f"Failed to upload video to Gemini File API: {exc}") from exc

    file_uri = file_info.get("uri")
    file_name = file_info.get("name")
    state = (file_info.get("state") or "").upper()

    # Poll up to 45 seconds if the video is still PROCESSING
    if file_name and state == "PROCESSING":
        check_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={urllib.parse.quote(api_key)}"
        for _ in range(15):
            time.sleep(2)
            try:
                with urllib.request.urlopen(check_url, timeout=30) as c_resp:
                    c_info = json.loads(c_resp.read().decode("utf-8"))
                    state = (c_info.get("state") or "").upper()
                    if state == "ACTIVE":
                        break
                    if state == "FAILED":
                        raise AIError(502, "Gemini video processing failed.")
            except AIError:
                raise
            except Exception:
                break

    if not file_uri:
        raise AIError(502, "Gemini File API did not return a file URI.")
    return file_uri


def analyze_lecture_media(
    cfg: AIConfig,
    *,
    media_bytes: bytes | None,
    mime_type: str | None,
    filename: str | None,
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 4096,
) -> tuple[str, str]:
    """Analyze a recorded lecture video/audio file (or transcript) to produce a pedagogical evaluation."""
    if media_bytes and mime_type and (mime_type.startswith("video/") or mime_type.startswith("audio/")):
        if cfg.provider == "gemini":
            # Use inline base64 for smaller clips (<= 14 MB), File API for larger videos
            if len(media_bytes) <= 14 * 1024 * 1024:
                b64 = base64.b64encode(media_bytes).decode("ascii")
                parts = [
                    {"inline_data": {"mime_type": mime_type, "data": b64}},
                    {"text": prompt},
                ]
            else:
                file_uri = _gemini_upload_file(cfg.api_key, media_bytes, mime_type, filename or "lecture.mp4")
                parts = [
                    {"file_data": {"mime_type": mime_type, "file_uri": file_uri}},
                    {"text": prompt},
                ]
            last_err: AIError | None = None
            for model in _candidate_models(cfg):
                try:
                    out = _gemini_generate(cfg.api_key, model, parts, system_prompt, max_tokens)
                    return out, model
                except AIError as exc:
                    last_err = exc
                    if exc.status_code in (401, 403):
                        break
            raise last_err or AIError(502, "Gemini video analysis failed.")

        # For Claude/OpenAI when a binary video file is uploaded alongside or without a transcript:
        note = (
            f"\n[Uploaded Lecture Media File: '{filename}' ({mime_type}, {len(media_bytes) // 1024} KB). "
            "Note: Direct binary video ingestion is native to Google Gemini; evaluating based on provided "
            "lecture metadata and transcript context.]\n"
        )
        return generate_text(cfg, prompt + note, system_prompt=system_prompt, max_tokens=max_tokens)

    return generate_text(cfg, prompt, system_prompt=system_prompt, max_tokens=max_tokens)


def test_ai_connection(cfg: AIConfig) -> dict:
    reply, used_model = generate_text(
        cfg,
        prompt="Reply with the exact single word: CONNECTED",
        system_prompt="You are a connection health check.",
        max_tokens=32,
    )
    return {
        "ok": True,
        "provider": cfg.provider,
        "model": used_model,
        "reply": reply[:80],
    }


def extract_json_object(raw_text: str) -> dict:
    """Extract a JSON object from an LLM response (stripping markdown code fences if present)."""
    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if brace_match:
            cleaned = brace_match.group(0)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def markdown_to_docx_bytes(title: str, markdown_text: str) -> bytes:
    """Build a valid Microsoft Word (.docx) document from Markdown text using stdlib zipfile."""
    paragraphs_xml = []

    def add_para(text: str, bold: bool = False, size_half_pts: int = 22):
        escaped = xml_escape(text)
        rpr = f"<w:rPr>{'<w:b/>' if bold else ''}<w:sz w:val=\"{size_half_pts}\"/></w:rPr>"
        paragraphs_xml.append(
            f"<w:p><w:r>{rpr}<w:t xml:space=\"preserve\">{escaped}</w:t></w:r></w:p>"
        )

    add_para(title, bold=True, size_half_pts=32)
    add_para("", bold=False, size_half_pts=22)

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            add_para("")
            continue
        if line.startswith("### "):
            add_para(line[4:].strip(), bold=True, size_half_pts=24)
        elif line.startswith("## "):
            add_para(line[3:].strip(), bold=True, size_half_pts=26)
        elif line.startswith("# "):
            add_para(line[2:].strip(), bold=True, size_half_pts=30)
        else:
            clean_line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
            add_para(clean_line, bold=False, size_half_pts=22)

    body_xml = "".join(paragraphs_xml)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}<w:sectPr/></w:body>"
        "</w:document>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()
