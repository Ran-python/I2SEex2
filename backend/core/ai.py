"""Unified multi-provider AI client (Google Gemini, Anthropic Claude, OpenAI GPT) via REST APIs."""
from __future__ import annotations

import base64
import io
import json
import os
import re
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
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
    timeout: int = 240,
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

    res = _http_json(url, payload, headers={}, timeout=timeout)
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


# ---------------------------------------------------------------------------
# Pipeline A: YouTube Video ID & Transcript Extraction (youtube-transcript-api)
# ---------------------------------------------------------------------------
def extract_youtube_video_id(url_or_id: str | None) -> str | None:
    """Extract the 11-character YouTube video_id from any YouTube URL format.

    Supports:
      - https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12s
      - https://youtu.be/dQw4w9WgXcQ?si=...
      - https://www.youtube.com/shorts/dQw4w9WgXcQ
      - https://www.youtube.com/live/dQw4w9WgXcQ
      - https://www.youtube.com/embed/dQw4w9WgXcQ
      - Direct 11-char video ID
    """
    if not url_or_id:
        return None
    raw = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return raw

    try:
        parsed = urllib.parse.urlparse(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path or ""

        if "youtu.be" in host:
            candidate = path.lstrip("/").split("/")[0]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                return candidate

        if "youtube.com" in host or "youtube-nocookie.com" in host:
            qs = urllib.parse.parse_qs(parsed.query)
            if "v" in qs and qs["v"]:
                candidate = qs["v"][0].strip()
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                    return candidate
            for prefix in ("/shorts/", "/embed/", "/live/", "/v/"):
                if path.startswith(prefix):
                    candidate = path[len(prefix) :].split("/")[0].split("?")[0]
                    if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                        return candidate
    except Exception:
        pass

    match = re.search(
        r"(?:v=|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})",
        raw,
    )
    return match.group(1) if match else None


def fetch_youtube_transcript(youtube_url: str) -> tuple[str, str]:
    """Fetch the transcript text for a YouTube video, prioritizing Hebrew ('he', 'iw') and English ('en').

    Returns ``(video_id, full_transcript_text)`` or raises ``AIError(400, ...)`` with a clear message.
    """
    video_id = extract_youtube_video_id(youtube_url)
    if not video_id:
        raise AIError(
            400,
            f"Invalid YouTube URL: '{youtube_url}'. Please provide a valid YouTube link "
            "(e.g. https://www.youtube.com/watch?v=... or https://youtu.be/...).",
        )

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise AIError(
            500,
            "youtube-transcript-api is not installed in the backend environment. "
            "Run: pip install youtube-transcript-api",
        ) from exc

    preferred_langs = ["he", "iw", "en", "en-US", "en-GB"]
    snippets = None

    try:
        # Support both youtube-transcript-api v1.x (instance API: ytt.fetch / ytt.list)
        # and v0.6.x (class method API: YouTubeTranscriptApi.get_transcript / list_transcripts)
        ytt = YouTubeTranscriptApi()
        if hasattr(ytt, "list") and callable(getattr(ytt, "list")):
            transcript_list = ytt.list(video_id)
            chosen = None
            try:
                chosen = transcript_list.find_manually_created_transcript(preferred_langs)
            except Exception:
                try:
                    chosen = transcript_list.find_generated_transcript(preferred_langs)
                except Exception:
                    for t in transcript_list:
                        chosen = t
                        break
            if chosen is not None:
                snippets = chosen.fetch()
            else:
                snippets = ytt.fetch(video_id, languages=preferred_langs)
        else:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            chosen = None
            try:
                chosen = transcript_list.find_manually_created_transcript(preferred_langs)
            except Exception:
                try:
                    chosen = transcript_list.find_generated_transcript(preferred_langs)
                except Exception:
                    for t in transcript_list:
                        chosen = t
                        break
            if chosen is not None:
                snippets = chosen.fetch()
            else:
                snippets = YouTubeTranscriptApi.get_transcript(video_id, languages=preferred_langs)
    except Exception as exc:
        err_text = str(exc)
        print(f"[YouTubeTranscript] Error fetching transcript for {video_id}: {err_text}")
        raise AIError(
            400,
            f"Could not extract a transcript from YouTube video '{video_id}'. "
            "Captions/subtitles may be disabled or unavailable for this video. "
            "Please try a YouTube video with captions enabled, or upload/paste the transcript directly.",
        ) from exc

    lines: list[str] = []
    if snippets:
        for item in snippets:
            if isinstance(item, dict):
                txt = (item.get("text") or "").strip()
            else:
                txt = (getattr(item, "text", "") or "").strip()
            if txt:
                lines.append(txt)

    full_text = " ".join(lines).strip()
    if not full_text:
        raise AIError(
            400,
            f"YouTube video '{video_id}' returned an empty transcript. "
            "Please try a video with spoken captions or upload the video/transcript directly.",
        )
    return video_id, full_text


_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")


def contains_hebrew(text: str | None) -> bool:
    """Return True if the text contains any Hebrew characters (U+0590..U+05FF)."""
    if not text:
        return False
    return bool(_HEBREW_RE.search(text))


def normalize_language_code(lang: str | None) -> str | None:
    """Normalize optional language code (e.g. 'he', 'iw', 'hebrew' -> 'he'; 'en' -> 'en'; 'auto'/'' -> None)."""
    if not lang:
        return None
    cleaned = lang.strip().lower()
    if cleaned in ("", "auto", "detect", "none", "null"):
        return None
    if cleaned in ("he", "iw", "heb", "hebrew", "עברית", "he-il"):
        return "he"
    if cleaned in ("en", "eng", "english", "en-us", "en-gb"):
        return "en"
    if re.fullmatch(r"[a-z]{2}", cleaned):
        return cleaned
    return None


# ---------------------------------------------------------------------------
# Speech-to-Text Integration (Preprocessed Audio Segments ONLY — Never MP4)
# ---------------------------------------------------------------------------
_BILINGUAL_TECH_PROMPT_HE = (
    "הרצאה אקדמית בעברית במדעי המחשב והנדסת תוכנה. "
    "שמור על מונחים טכניים באנגלית, שמות אלגוריתמים, סימון מתמטי ומונחי תכנות בדיוק כפי שנאמרו באנגלית, כגון: "
    "OOP, SOLID, Dependency Injection, Design Patterns, Singleton, Factory, Observer, "
    "Big-O, O(n log n), O(n^2), O(1), Dijkstra, BFS, DFS, Dynamic Programming, Recursion, "
    "SQL, PostgreSQL, FastAPI, React, REST API, HTTP, JSON, Thread, Mutex, Async."
)

_TECH_PROMPT_EN = (
    "University computer science and software engineering lecture. "
    "Preserve technical terminology, algorithm names, mathematical notation (e.g., O(n log n)), "
    "and programming identifiers accurately."
)


def resolve_stt_config(cfg: AIConfig) -> AIConfig:
    """Resolve the Speech-to-Text provider & API key while preserving the user's configured provider."""
    override = config.STT_PROVIDER_OVERRIDE
    if override == "openai":
        key = cfg.api_key if cfg.provider == "openai" else (config.OPENAI_API_KEY or "")
        if not key:
            raise AIError(400, "STT_PROVIDER is set to 'openai', but no OpenAI API key is configured.")
        return AIConfig(provider="openai", api_key=key, model=config.OPENAI_STT_MODEL)

    if override == "gemini":
        key = cfg.api_key if cfg.provider == "gemini" else (config.GEMINI_API_KEY or "")
        if not key:
            raise AIError(400, "STT_PROVIDER is set to 'gemini', but no Gemini API key is configured.")
        return AIConfig(provider="gemini", api_key=key, model=cfg.model if cfg.provider == "gemini" else None)

    if cfg.provider in ("openai", "gemini"):
        return cfg

    # Anthropic Claude does not expose a native Speech-to-Text endpoint; fall back to OpenAI or Gemini STT key if available
    if config.OPENAI_API_KEY:
        return AIConfig(provider="openai", api_key=config.OPENAI_API_KEY, model=config.OPENAI_STT_MODEL)
    if config.GEMINI_API_KEY:
        return AIConfig(provider="gemini", api_key=config.GEMINI_API_KEY, model=None)

    raise AIError(
        400,
        "Anthropic Claude does not provide a Speech-to-Text audio endpoint. "
        "Please select OpenAI or Google Gemini in ⚙️ AI Settings (or set OPENAI_API_KEY / GEMINI_API_KEY in .env) to transcribe MP4 audio.",
    )


def _openai_transcribe_audio_file(
    api_key: str,
    audio_path: Path,
    language: str | None = None,
    chunk_duration_sec: float = 0.0,
) -> list[dict]:
    """Call OpenAI Audio Transcriptions API (/v1/audio/transcriptions) with a preprocessed audio file."""
    stt_model = config.OPENAI_STT_MODEL or "whisper-1"
    norm_lang = normalize_language_code(language)
    prompt_hint = _BILINGUAL_TECH_PROMPT_HE if norm_lang in ("he", None) else _TECH_PROMPT_EN

    def _send_multipart(response_format: str) -> dict:
        boundary = f"----ClassPulseBoundary{int(time.time() * 1000)}{os.getpid()}"
        fields: list[tuple[str, str]] = [
            ("model", stt_model),
            ("response_format", response_format),
            ("temperature", "0"),
            ("prompt", prompt_hint),
        ]
        if norm_lang:
            fields.append(("language", norm_lang))

        body = io.BytesIO()
        for name, val in fields:
            body.write(f"--{boundary}\r\n".encode("utf-8"))
            body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            body.write(f"{val}\r\n".encode("utf-8"))

        body.write(f"--{boundary}\r\n".encode("utf-8"))
        body.write(
            f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'
            f"Content-Type: audio/mpeg\r\n\r\n".encode("utf-8")
        )
        with open(audio_path, "rb") as af:
            while True:
                buf_chunk = af.read(256 * 1024)
                if not buf_chunk:
                    break
                body.write(buf_chunk)
        body.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        payload_bytes = body.getvalue()
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=payload_bytes,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(payload_bytes)),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
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
            raise AIError(exc.code, f"OpenAI Audio Transcription error: {msg}") from exc
        except Exception as exc:
            raise AIError(502, f"OpenAI Audio Transcription network error: {exc}") from exc

    use_verbose = stt_model.startswith("whisper")
    try:
        res = _send_multipart("verbose_json" if use_verbose else "json")
    except AIError as exc:
        if use_verbose and exc.status_code == 400 and "verbose_json" in exc.message.lower():
            res = _send_multipart("json")
        else:
            raise

    raw_segments = res.get("segments")
    segments: list[dict] = []
    if isinstance(raw_segments, list) and raw_segments:
        for seg in raw_segments:
            txt = (seg.get("text") or "").strip()
            if not txt:
                continue
            start_val = float(seg.get("start") or 0.0)
            end_val = float(seg.get("end") or max(start_val + 1.0, chunk_duration_sec))
            segments.append({"start": start_val, "end": end_val, "text": txt})
    else:
        full_txt = (res.get("text") or "").strip()
        if full_txt:
            segments.append(
                {
                    "start": 0.0,
                    "end": max(1.0, float(chunk_duration_sec or 0.0)),
                    "text": full_txt,
                }
            )
    return segments


def _parse_timestamp_str(ts: str) -> float:
    parts = [p.strip() for p in ts.strip().split(":")]
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except Exception:
        return 0.0


def _parse_gemini_stt_output(raw_text: str, chunk_duration_sec: float) -> list[dict]:
    """Parse Gemini STT response (JSON array or [MM:SS - MM:SS] timestamped lines) into segment dicts."""
    cleaned = raw_text.strip()
    if not cleaned:
        return []

    # 1. Try JSON array / object first
    arr_match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if arr_match:
        try:
            parsed = json.loads(arr_match.group(0))
            if isinstance(parsed, list):
                segs = []
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    txt = str(item.get("text") or "").strip()
                    if not txt:
                        continue
                    s_raw = item.get("start", 0.0)
                    e_raw = item.get("end", s_raw)
                    s_val = _parse_timestamp_str(s_raw) if isinstance(s_raw, str) else float(s_raw or 0.0)
                    e_val = _parse_timestamp_str(e_raw) if isinstance(e_raw, str) else float(e_raw or max(s_val + 1.0, chunk_duration_sec))
                    if e_val <= s_val:
                        e_val = s_val + 2.0
                    segs.append({"start": s_val, "end": e_val, "text": txt})
                if segs:
                    return segs
        except Exception:
            pass

    # 2. Try [MM:SS - MM:SS] or [MM:SS] lines
    line_re = re.compile(
        r"^\s*\[?(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)(?:\s*[-–—>to]+\s*(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?))?\]?\s*[:\-]?\s*(.+)$"
    )
    segs = []
    for line in cleaned.splitlines():
        line_s = line.strip()
        if not line_s or line_s.startswith("```"):
            continue
        m = line_re.match(line_s)
        if m:
            s_val = _parse_timestamp_str(m.group(1))
            e_val = _parse_timestamp_str(m.group(2)) if m.group(2) else (s_val + 5.0)
            txt = m.group(3).strip()
            if txt:
                segs.append({"start": s_val, "end": max(s_val + 0.5, e_val), "text": txt})

    if segs:
        for i in range(len(segs) - 1):
            if segs[i]["end"] > segs[i + 1]["start"] and segs[i + 1]["start"] > segs[i]["start"]:
                segs[i]["end"] = segs[i + 1]["start"]
        return segs

    # 3. Plain text fallback
    plain = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", cleaned).strip()
    if plain:
        return [{"start": 0.0, "end": max(1.0, float(chunk_duration_sec or 0.0)), "text": plain}]
    return []


def _gemini_transcribe_audio_file(
    cfg: AIConfig,
    audio_path: Path,
    language: str | None = None,
    chunk_duration_sec: float = 0.0,
) -> list[dict]:
    """Transcribe a preprocessed mono 16kHz MP3 audio chunk using Gemini (audio ONLY, never video)."""
    norm_lang = normalize_language_code(language)
    if norm_lang == "he":
        lang_instruction = (
            "The primary spoken language of this lecture recording is HEBREW (he / עברית). "
            "Transcribe all Hebrew speech accurately in Hebrew script, while preserving all English technical terms, "
            "algorithm names (e.g. Dijkstra, BFS, QuickSort), mathematical notation (e.g. O(n log n)), "
            "and programming terms (e.g. OOP, SOLID, Dependency Injection, SQL, FastAPI, React) in English exactly as spoken. "
            "Do NOT translate English technical terms into Hebrew."
        )
    elif norm_lang == "en":
        lang_instruction = (
            "The spoken language of this lecture recording is English (en). "
            "Preserve all technical terminology, algorithm names, and mathematical notation accurately."
        )
    else:
        lang_instruction = (
            "Auto-detect the spoken language (frequently Hebrew עברית mixed with English computer-science terminology, or English). "
            "Transcribe verbatim in the spoken language. If spoken in Hebrew, write Hebrew in Hebrew script while keeping "
            "English technical terms, algorithm names, mathematical notation (e.g. O(n log n)), and code identifiers in English without translating them."
        )

    stt_system = (
        "You are a high-precision academic Speech-to-Text (STT) transcription engine. "
        "Transcribe ONLY what is spoken in the audio recording faithfully and chronologically."
    )
    stt_prompt = (
        f"{lang_instruction}\n\n"
        "Output ONLY a valid JSON array of timestamped transcript segments (in seconds from the start of this audio clip, "
        "grouped into natural sentences/phrases of ~5-20 seconds each) in this exact format:\n"
        '[{"start": 0.0, "end": 8.5, "text": "..."}, {"start": 8.5, "end": 19.2, "text": "..."}]\n'
        "If there is no intelligible speech at all in the audio, return []."
    )

    file_size = audio_path.stat().st_size
    last_err: AIError | None = None

    # For compact preprocessed MP3 chunks (<= 15 MB), inline audio/mp3 avoids extra File API round-trips
    if file_size <= 15 * 1024 * 1024:
        with open(audio_path, "rb") as af:
            b64_audio = base64.b64encode(af.read()).decode("ascii")
        parts = [
            {"inline_data": {"mime_type": "audio/mpeg", "data": b64_audio}},
            {"text": stt_prompt},
        ]
        for model in _candidate_models(cfg):
            try:
                raw_out = _gemini_generate(
                    cfg.api_key,
                    model,
                    parts,
                    system_prompt=stt_system,
                    max_tokens=8192,
                    timeout=240,
                )
                return _parse_gemini_stt_output(raw_out, chunk_duration_sec)
            except AIError as exc:
                last_err = exc
                if exc.status_code in (401, 403, 429):
                    break
        if last_err:
            raise last_err

    # For audio chunks > 15 MB, upload ONLY the preprocessed .mp3 audio chunk via google-genai
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise AIError(500, "google-genai SDK is required for large audio chunks.") from exc

    client = genai.Client(api_key=cfg.api_key)
    uploaded_audio = None
    try:
        try:
            uploaded_audio = client.files.upload(file=str(audio_path), config={"mime_type": "audio/mpeg"})
        except TypeError:
            uploaded_audio = client.files.upload(file=str(audio_path))

        for _ in range(40):
            st = (getattr(getattr(uploaded_audio, "state", None), "name", None) or str(getattr(uploaded_audio, "state", ""))).upper()
            if "ACTIVE" in st:
                break
            if "FAILED" in st:
                raise AIError(502, "Gemini audio file upload failed processing.")
            time.sleep(2)
            uploaded_audio = client.files.get(name=uploaded_audio.name)

        for model in _candidate_models(cfg):
            try:
                gen_cfg = types.GenerateContentConfig(
                    system_instruction=stt_system,
                    max_output_tokens=8192,
                    temperature=0.0,
                )
                resp = client.models.generate_content(
                    model=model,
                    contents=[uploaded_audio, stt_prompt],
                    config=gen_cfg,
                )
                raw_out = (getattr(resp, "text", None) or "").strip()
                return _parse_gemini_stt_output(raw_out, chunk_duration_sec)
            except Exception as exc:
                msg = str(exc)
                last_err = AIError(502, f"Gemini Audio STT failed ({model}): {msg}")
                if "401" in msg or "403" in msg or "429" in msg:
                    break
        raise last_err or AIError(502, "Gemini Audio STT failed.")
    finally:
        if uploaded_audio is not None and getattr(uploaded_audio, "name", None):
            try:
                client.files.delete(name=uploaded_audio.name)
            except Exception:
                pass


def transcribe_audio_segment(
    cfg: AIConfig,
    *,
    audio_path: str | Path,
    language: str | None = None,
    chunk_duration_sec: float = 0.0,
) -> list[dict]:
    """Transcribe a single preprocessed audio chunk (.mp3) using the configured STT provider."""
    path_obj = Path(audio_path)
    if not path_obj.is_file() or path_obj.stat().st_size == 0:
        raise AIError(400, f"Preprocessed audio chunk is missing or empty: {path_obj.name}")

    # Enforce strict guard: never allow video containers (.mp4/.mov/.mkv/.avi) to be sent directly
    if path_obj.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi"}:
        raise AIError(500, "Internal safety guard: Raw video files must be preprocessed to audio before transcription.")

    stt_cfg = resolve_stt_config(cfg)
    effective_lang = normalize_language_code(language) or config.TRANSCRIPTION_DEFAULT_LANGUAGE

    if stt_cfg.provider == "openai":
        return _openai_transcribe_audio_file(
            stt_cfg.api_key,
            path_obj,
            language=effective_lang,
            chunk_duration_sec=chunk_duration_sec,
        )
    return _gemini_transcribe_audio_file(
        stt_cfg,
        path_obj,
        language=effective_lang,
        chunk_duration_sec=chunk_duration_sec,
    )


def analyze_lecture_media(
    cfg: AIConfig,
    *,
    media_bytes: bytes | None = None,
    temp_file_path: str | Path | None = None,
    mime_type: str | None = None,
    filename: str | None = None,
    language: str | None = None,
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 4096,
) -> tuple[str, str]:
    """Analyze a recorded lecture media file (via FFmpeg audio extraction -> STT -> LLM) or text transcript."""
    is_media = bool(mime_type and (mime_type.startswith("video/") or mime_type.startswith("audio/")))

    if is_media and (temp_file_path or media_bytes):
        from modules.summary.audio_pipeline import process_media_file_to_transcript

        tmp_path: str | None = None
        try:
            target_path: Path
            if temp_file_path:
                target_path = Path(temp_file_path)
            else:
                suffix = Path(filename or "lecture.mp4").suffix or ".mp4"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(media_bytes or b"")
                    tmp_path = tmp.name
                target_path = Path(tmp_path)

            transcript_res = process_media_file_to_transcript(
                input_media_path=target_path,
                original_filename=filename or target_path.name,
                ai_cfg=cfg,
                language=language,
            )
            full_prompt = (
                f"=== TIMESTAMPED LECTURE TRANSCRIPT ===\n"
                f"{transcript_res.formatted_transcript[:60000]}\n\n"
                f"{prompt}"
            )
            return generate_text(cfg, full_prompt, system_prompt=system_prompt, max_tokens=max_tokens)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

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


_LATEX_WORD_CMDS = (
    "frac", "beta", "theta", "tau", "tilde", "times", "text", "textbf",
    "nabla", "neq", "nu", "mathbb", "mathcal", "mathrm", "rho", "rightarrow", "right",
)


def extract_json_object(raw_text: str) -> dict:
    """Extract a JSON object from an LLM response (handling markdown fences, LaTeX backslashes, and partial JSON)."""
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return {}

    # Strip outer markdown code fences without truncating on inner backticks
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned, count=1)

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = cleaned[first_brace : last_brace + 1]
    elif first_brace != -1:
        candidate = cleaned[first_brace:]
    else:
        candidate = cleaned

    # 1. Standard strict JSON parse
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 2. Repair unescaped LaTeX backslashes (e.g. \sigma, \int, \lambda, \frac{...}, \theta)
    repaired = re.sub(r'\\(?![/\"\\bfnrtu])', r"\\\\", candidate)
    repaired = re.sub(
        r"\\(" + "|".join(_LATEX_WORD_CMDS) + r")(?![A-Za-z])",
        r"\\\\\1",
        repaired,
    )
    try:
        data = json.loads(repaired)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 3. Resilient per-key string extractor if LLM output had unescaped quotes or hit token limit mid-JSON
    known_keys = (
        "executive_summary",
        "pedagogical_evaluation",
        "points_to_preserve",
        "points_for_improvement",
        "action_items",
        "score_estimate",
        "architecture_pattern",
        "student_review_md",
        "lecturer_audit_md",
        "headcount",
    )
    extracted: dict = {}
    key_positions: list[tuple[int, int, str]] = []
    for k in known_keys:
        m = re.search(rf'"{re.escape(k)}"\s*:\s*"', candidate)
        if m:
            key_positions.append((m.start(), m.end(), k))
    key_positions.sort(key=lambda item: item[0])

    for i, (_k_start, val_start, key_name) in enumerate(key_positions):
        bound_end = key_positions[i + 1][0] if i + 1 < len(key_positions) else len(candidate)
        raw_slice = candidate[val_start:bound_end].rstrip()
        # Trim trailing JSON delimiters (", }, whitespace)
        raw_slice = re.sub(r'"\s*,?\s*\}?\s*$', "", raw_slice)
        val_unescaped = (
            raw_slice.replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\\\", "\\")
            .strip()
        )
        if val_unescaped:
            extracted[key_name] = val_unescaped

    return extracted


_BIDI_MIRROR_MAP = str.maketrans(
    {
        "(": ")",
        ")": "(",
        "[": "]",
        "]": "[",
        "{": "}",
        "}": "{",
        "<": ">",
        ">": "<",
    }
)


def _split_bidi_runs(text: str, default_rtl: bool = False) -> list[tuple[str, bool]]:
    """Split mixed Hebrew/English text into directional runs (run_text, is_rtl_run) for OpenXML BiDi."""
    if not text:
        return [("", default_rtl)]
    if not contains_hebrew(text):
        return [(text, False)]

    # Tokenize into strong LTR chunks (Latin/digits/math expressions like O(n log n) or [00:12:34]),
    # strong RTL chunks (Hebrew), and neutral whitespace/punctuation.
    token_re = re.compile(
        r"(\[\d{1,2}:\d{2}(?::\d{2})?(?:\s*-\s*\d{1,2}:\d{2}(?::\d{2})?)?\]"
        r"|[A-Za-z][A-Za-z0-9_+#.\-/()*^ ]*[A-Za-z0-9_+#)]"
        r"|[A-Za-z0-9_+#.\-]+"
        r"|[\u0590-\u05FF]+"
        r"|[^\u0590-\u05FFA-Za-z0-9]+)"
    )
    raw_tokens = token_re.findall(text)
    runs: list[tuple[str, bool]] = []
    for tok in raw_tokens:
        if contains_hebrew(tok):
            is_rtl = True
        elif re.search(r"[A-Za-z0-9]", tok):
            is_rtl = False
        else:
            is_rtl = runs[-1][1] if runs else default_rtl

        if runs and runs[-1][1] == is_rtl:
            runs[-1] = (runs[-1][0] + tok, is_rtl)
        else:
            runs.append((tok, is_rtl))
    return runs


def _is_md_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return False
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    return bool(cells) and all(bool(re.fullmatch(r":?-{2,}:?", c)) for c in cells)


def _parse_md_table_row(line: str) -> list[str]:
    return [re.sub(r"\*\*(.*?)\*\*", r"\1", c.strip()) for c in line.strip().strip("|").split("|")]


def markdown_to_docx_bytes(title: str, markdown_text: str) -> bytes:
    """Build a Microsoft Word (.docx) document with full Hebrew RTL BiDi & Markdown table support."""
    doc_is_rtl = contains_hebrew(title) or contains_hebrew(markdown_text)
    body_elements: list[str] = []

    def build_para_xml(text: str, *, bold: bool = False, size_half_pts: int = 22, force_rtl: bool | None = None) -> str:
        para_rtl = force_rtl if force_rtl is not None else (contains_hebrew(text) or (doc_is_rtl and not text.strip()))
        ppr = "<w:pPr><w:bidi/><w:jc w:val=\"right\"/></w:pPr>" if para_rtl else ""
        if not text:
            return f"<w:p>{ppr}</w:p>"

        # Support inline **bold** segments while preserving BiDi directional runs
        segments = re.split(r"(\*\*.*?\*\*)", text)
        runs_xml: list[str] = []
        for seg in segments:
            if not seg:
                continue
            seg_bold = bold
            clean_seg = seg
            if seg.startswith("**") and seg.endswith("**") and len(seg) > 4:
                seg_bold = True
                clean_seg = seg[2:-2]

            for run_str, run_rtl in _split_bidi_runs(clean_seg, default_rtl=para_rtl):
                escaped = xml_escape(run_str)
                rpr_parts = [
                    '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>',
                    "<w:b/><w:bCs/>" if seg_bold else "",
                    f'<w:sz w:val="{size_half_pts}"/><w:szCs w:val="{size_half_pts}"/>',
                    '<w:lang w:val="en-US" w:bidi="he-IL"/>',
                    "<w:rtl/>" if run_rtl else "",
                ]
                rpr = f"<w:rPr>{''.join(rpr_parts)}</w:rPr>"
                runs_xml.append(f'<w:r>{rpr}<w:t xml:space="preserve">{escaped}</w:t></w:r>')

        return f"<w:p>{ppr}{''.join(runs_xml)}</w:p>"

    def build_table_xml(rows: list[list[str]]) -> str:
        if not rows:
            return ""
        col_count = max(len(r) for r in rows)
        tbl_rtl = doc_is_rtl or any(contains_hebrew(cell) for r in rows for cell in r)
        bidi_tag = "<w:bidiVisual/>" if tbl_rtl else ""
        tbl_pr = (
            "<w:tblPr>"
            f"{bidi_tag}"
            '<w:tblW w:w="5000" w:type="pct"/>'
            '<w:tblBorders>'
            '<w:top w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
            '<w:left w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
            '<w:bottom w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
            '<w:right w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
            '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>'
            '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>'
            "</w:tblBorders>"
            "</w:tblPr>"
        )
        tr_list: list[str] = []
        for r_idx, row in enumerate(rows):
            padded = row + [""] * (col_count - len(row))
            tc_list: list[str] = []
            for cell_text in padded:
                shd = '<w:shd w:val="clear" w:color="auto" w:fill="F1F5F9"/>' if r_idx == 0 else ""
                tc_pr = f"<w:tcPr>{shd}</w:tcPr>"
                cell_para = build_para_xml(
                    cell_text,
                    bold=(r_idx == 0),
                    size_half_pts=20,
                    force_rtl=tbl_rtl if contains_hebrew(cell_text) or tbl_rtl else False,
                )
                tc_list.append(f"<w:tc>{tc_pr}{cell_para}</w:tc>")
            tr_list.append(f"<w:tr>{''.join(tc_list)}</w:tr>")
        return f"<w:tbl>{tbl_pr}{''.join(tr_list)}</w:tbl>"

    body_elements.append(build_para_xml(title, bold=True, size_half_pts=32, force_rtl=doc_is_rtl))
    body_elements.append(build_para_xml("", bold=False, size_half_pts=22, force_rtl=doc_is_rtl))

    lines = markdown_text.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            body_elements.append(build_para_xml("", size_half_pts=22, force_rtl=doc_is_rtl))
            idx += 1
            continue

        # Detect Markdown table block
        if line.startswith("|") and line.endswith("|"):
            table_rows: list[list[str]] = []
            while idx < len(lines):
                cand = lines[idx].strip()
                if not (cand.startswith("|") and cand.endswith("|")):
                    break
                if not _is_md_table_separator(cand):
                    table_rows.append(_parse_md_table_row(cand))
                idx += 1
            if table_rows:
                body_elements.append(build_table_xml(table_rows))
                body_elements.append(build_para_xml("", size_half_pts=16, force_rtl=doc_is_rtl))
            continue

        if line == "---":
            idx += 1
            continue
        if line.startswith("### "):
            body_elements.append(build_para_xml(line[4:].strip(), bold=True, size_half_pts=24))
        elif line.startswith("## "):
            body_elements.append(build_para_xml(line[3:].strip(), bold=True, size_half_pts=26))
        elif line.startswith("# "):
            body_elements.append(build_para_xml(line[2:].strip(), bold=True, size_half_pts=30))
        else:
            body_elements.append(build_para_xml(line, bold=False, size_half_pts=22))
        idx += 1

    sect_bidi = "<w:bidi/>" if doc_is_rtl else ""
    body_xml = "".join(body_elements)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}<w:sectPr>{sect_bidi}</w:sectPr></w:body>"
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


def _visual_bidi_text(text: str, base_rtl: bool) -> str:
    """Convert logical mixed Hebrew/English text to visual order for non-shaping renderers (Pillow PDF)."""
    if not text or (not base_rtl and not contains_hebrew(text)):
        return text

    runs = _split_bidi_runs(text, default_rtl=True)
    visual_parts: list[str] = []
    for run_text, is_rtl in reversed(runs):
        if is_rtl:
            # Mirror brackets and reverse codepoints in RTL segments
            mirrored = run_text.translate(_BIDI_MIRROR_MAP)
            visual_parts.append(mirrored[::-1])
        else:
            # Preserve leading/trailing spaces symmetrically around embedded LTR runs
            lead_ws = len(run_text) - len(run_text.lstrip(" "))
            trail_ws = len(run_text) - len(run_text.rstrip(" "))
            core = run_text.strip(" ")
            visual_parts.append((" " * trail_ws) + core + (" " * lead_ws))
    return "".join(visual_parts)


def _resolve_unicode_ttf_paths() -> tuple[str | None, str | None]:
    """Locate regular and bold Unicode TrueType fonts with Hebrew & Latin glyph coverage."""
    candidates = [
        (Path(r"C:\Windows\Fonts\arial.ttf"), Path(r"C:\Windows\Fonts\arialbd.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/usr/share/fonts/TTF/DejaVuSans.ttf"), Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")),
    ]
    for reg, bld in candidates:
        if reg.is_file():
            return str(reg), str(bld if bld.is_file() else reg)

    try:
        import importlib.util
        spec = importlib.util.find_spec("matplotlib")
        if spec and spec.origin:
            ttf_dir = Path(spec.origin).parent / "mpl-data" / "fonts" / "ttf"
            reg = ttf_dir / "DejaVuSans.ttf"
            bld = ttf_dir / "DejaVuSans-Bold.ttf"
            if reg.is_file():
                return str(reg), str(bld if bld.is_file() else reg)
    except Exception:
        pass
    return None, None


def markdown_to_pdf_bytes(title: str, markdown_text: str) -> bytes:
    """Render a multi-page A4 PDF supporting Hebrew RTL, mixed English technical terms, and RTL tables."""
    from PIL import Image, ImageDraw, ImageFont

    page_w, page_h = 1240, 1754  # A4 at 150 DPI
    margin_x, margin_y = 90, 95
    usable_w = page_w - 2 * margin_x

    reg_path, bold_path = _resolve_unicode_ttf_paths()
    if reg_path:
        font_title = ImageFont.truetype(bold_path or reg_path, 34)
        font_h2 = ImageFont.truetype(bold_path or reg_path, 26)
        font_h3 = ImageFont.truetype(bold_path or reg_path, 22)
        font_body = ImageFont.truetype(reg_path, 20)
        font_bold = ImageFont.truetype(bold_path or reg_path, 20)
        font_table = ImageFont.truetype(reg_path, 18)
        font_table_b = ImageFont.truetype(bold_path or reg_path, 18)
    else:
        default_f = ImageFont.load_default()
        font_title = font_h2 = font_h3 = font_body = font_bold = font_table = font_table_b = default_f

    doc_is_rtl = contains_hebrew(title) or contains_hebrew(markdown_text)
    pages: list[Image.Image] = []
    cur_img = Image.new("RGB", (page_w, page_h), "white")
    draw = ImageDraw.Draw(cur_img)
    cursor_y = margin_y

    def new_page():
        nonlocal cur_img, draw, cursor_y
        pages.append(cur_img)
        cur_img = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(cur_img)
        cursor_y = margin_y

    def measure_w(txt: str, font) -> float:
        bbox = draw.textbbox((0, 0), txt, font=font)
        return float(bbox[2] - bbox[0])

    def wrap_logical_line(text: str, font, max_w: float) -> list[str]:
        words = text.split(" ")
        if not words:
            return [""]
        wrapped: list[str] = []
        cur = words[0]
        for w in words[1:]:
            cand = f"{cur} {w}"
            if measure_w(cand, font) <= max_w:
                cur = cand
            else:
                wrapped.append(cur)
                cur = w
        wrapped.append(cur)
        return wrapped

    def draw_paragraph(text: str, font, *, line_h: int = 30, color=(15, 23, 42), extra_after: int = 8):
        nonlocal cursor_y
        clean = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        if not clean.strip():
            cursor_y += line_h // 2
            return
        line_rtl = contains_hebrew(clean) or doc_is_rtl
        for wline in wrap_logical_line(clean, font, usable_w):
            if cursor_y + line_h > page_h - margin_y:
                new_page()
            vis = _visual_bidi_text(wline, base_rtl=line_rtl)
            tw = measure_w(vis, font)
            x = (page_w - margin_x - tw) if line_rtl else margin_x
            draw.text((x, cursor_y), vis, font=font, fill=color)
            cursor_y += line_h
        cursor_y += extra_after

    def draw_table(rows: list[list[str]]):
        nonlocal cursor_y
        if not rows:
            return
        col_count = max(len(r) for r in rows)
        tbl_rtl = doc_is_rtl or any(contains_hebrew(c) for r in rows for c in r)
        col_w = usable_w / max(1, col_count)
        row_h = 38

        for r_idx, row in enumerate(rows):
            if cursor_y + row_h > page_h - margin_y:
                new_page()
            padded = row + [""] * (col_count - len(row))
            ordered_cells = list(reversed(padded)) if tbl_rtl else padded
            fnt = font_table_b if r_idx == 0 else font_table

            for c_idx, cell_txt in enumerate(ordered_cells):
                x0 = margin_x + c_idx * col_w
                x1 = x0 + col_w
                bg = (241, 245, 249) if r_idx == 0 else (255, 255, 255)
                draw.rectangle([x0, cursor_y, x1, cursor_y + row_h], fill=bg, outline=(203, 213, 225), width=1)
                cell_rtl = contains_hebrew(cell_txt) or tbl_rtl
                clipped = cell_txt[:45]
                vis = _visual_bidi_text(clipped, base_rtl=cell_rtl)
                tw = measure_w(vis, fnt)
                tx = (x1 - 10 - tw) if cell_rtl else (x0 + 10)
                draw.text((max(x0 + 4, tx), cursor_y + 8), vis, font=fnt, fill=(15, 23, 42))
            cursor_y += row_h
        cursor_y += 14

    draw_paragraph(title, font_title, line_h=44, color=(30, 58, 138), extra_after=18)

    lines = markdown_text.splitlines()
    idx = 0
    while idx < len(lines):
        raw = lines[idx].strip()
        if not raw:
            cursor_y += 12
            idx += 1
            continue
        if raw.startswith("|") and raw.endswith("|"):
            t_rows: list[list[str]] = []
            while idx < len(lines):
                cand = lines[idx].strip()
                if not (cand.startswith("|") and cand.endswith("|")):
                    break
                if not _is_md_table_separator(cand):
                    t_rows.append(_parse_md_table_row(cand))
                idx += 1
            draw_table(t_rows)
            continue
        if raw == "---":
            if cursor_y + 16 > page_h - margin_y:
                new_page()
            draw.line([(margin_x, cursor_y + 6), (page_w - margin_x, cursor_y + 6)], fill=(203, 213, 225), width=2)
            cursor_y += 18
            idx += 1
            continue
        if raw.startswith("### "):
            draw_paragraph(raw[4:].strip(), font_h3, line_h=32, color=(30, 64, 175), extra_after=6)
        elif raw.startswith("## "):
            draw_paragraph(raw[3:].strip(), font_h2, line_h=36, color=(30, 58, 138), extra_after=8)
        elif raw.startswith("# "):
            draw_paragraph(raw[2:].strip(), font_title, line_h=40, color=(30, 58, 138), extra_after=10)
        else:
            bullet_clean = raw
            if bullet_clean.startswith(("- ", "* ")):
                bullet_clean = "• " + bullet_clean[2:].strip()
            draw_paragraph(bullet_clean, font_body, line_h=29, extra_after=6)
        idx += 1

    pages.append(cur_img)
    out_buf = io.BytesIO()
    pages[0].save(
        out_buf,
        format="PDF",
        resolution=150.0,
        save_all=True,
        append_images=pages[1:],
    )
    return out_buf.getvalue()
