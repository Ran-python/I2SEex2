"""MP4 Audio Extraction, Preprocessing, Silence-Aware Chunking & Transcription Pipeline.

Flow:
  MP4 Upload -> Media Validation (ffprobe) -> Audio Extraction & Speech Preprocessing (FFmpeg:
  strip video, mono, 16 kHz, speech-band filter + MP3 compression) -> Silence-Aware Chunking
  (only when exceeding configured upload limit, with 1-2s overlap) -> Per-Chunk Retryable
  Speech-to-Text (memoized to prevent duplicate API calls) -> Overlap-Deduplicated Timestamped
  Transcript -> Existing LLM Report Pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import ntpath
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable

from core import config
from core.ai import AIConfig, AIError, contains_hebrew, normalize_language_code, resolve_stt_config, transcribe_audio_segment

logger = logging.getLogger("classpulse.audio_pipeline")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] [AudioPipeline] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

ALLOWED_MEDIA_EXTENSIONS: set[str] = {
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
    ".m4a",
    ".mp3",
    ".wav",
    ".ogg",
}

_STREAM_COPY_BUFFER_BYTES = 1024 * 1024  # 1 MB streaming buffer (constant RAM usage)
_FFMPEG_CACHE: tuple[str, str] | None = None


@dataclass(frozen=True)
class MediaProbeInfo:
    format_name: str
    duration_sec: float
    size_bytes: int
    has_video: bool
    has_audio: bool
    audio_codec: str | None


@dataclass(frozen=True)
class AudioChunk:
    index: int
    path: Path
    start_sec: float
    end_sec: float
    nominal_start_sec: float
    nominal_end_sec: float
    size_bytes: int

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


@dataclass
class TranscriptSegment:
    start_sec: float
    end_sec: float
    text: str
    chunk_index: int = 0


@dataclass
class AudioPipelineResult:
    formatted_transcript: str
    plain_transcript: str
    segments: list[TranscriptSegment]
    duration_sec: float
    extracted_audio_bytes: int
    chunk_count: int
    partial_failures: int = 0
    detected_hebrew: bool = False
    chunk_metadata: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. FFmpeg & FFprobe Resolution (Safe Subprocess Execution, Never Shell)
# ---------------------------------------------------------------------------
def _search_winget_binary(exe_name: str) -> str | None:
    if os.name != "nt":
        return None
    local_app = os.environ.get("LOCALAPPDATA")
    if not local_app:
        return None
    base = Path(local_app) / "Microsoft" / "WinGet"
    # Prefer direct binary inside Packages first (works without Windows App Execution Alias quirks)
    pkgs_dir = base / "Packages"
    if pkgs_dir.is_dir():
        for match in pkgs_dir.rglob(exe_name):
            if match.is_file():
                return str(match)
    links_candidate = base / "Links" / exe_name
    if links_candidate.is_file():
        return str(links_candidate)
    return None


def resolve_ffmpeg_binaries(force_refresh: bool = False) -> tuple[str, str]:
    """Locate and verify executable ``ffmpeg`` and ``ffprobe`` binaries on the server."""
    global _FFMPEG_CACHE
    if not force_refresh and _FFMPEG_CACHE is not None:
        return _FFMPEG_CACHE

    exe_suffix = ".exe" if os.name == "nt" else ""
    ffmpeg_candidates = [
        config.FFMPEG_BIN,
        shutil.which("ffmpeg"),
        _search_winget_binary(f"ffmpeg{exe_suffix}"),
        r"C:\ffmpeg\bin\ffmpeg.exe" if os.name == "nt" else None,
        "/usr/bin/ffmpeg" if os.name != "nt" else None,
        "/usr/local/bin/ffmpeg" if os.name != "nt" else None,
    ]
    ffprobe_candidates = [
        config.FFPROBE_BIN,
        shutil.which("ffprobe"),
        _search_winget_binary(f"ffprobe{exe_suffix}"),
        r"C:\ffmpeg\bin\ffprobe.exe" if os.name == "nt" else None,
        "/usr/bin/ffprobe" if os.name != "nt" else None,
        "/usr/local/bin/ffprobe" if os.name != "nt" else None,
    ]

    ffmpeg_bin = next((c for c in ffmpeg_candidates if c and Path(c).is_file()), None)
    ffprobe_bin = next((c for c in ffprobe_candidates if c and Path(c).is_file()), None)

    if not ffmpeg_bin or not ffprobe_bin:
        raise AIError(
            500,
            "FFmpeg / FFprobe is not installed or not found on the server. "
            "Install FFmpeg (e.g. `apt-get install ffmpeg` on Linux or `winget install Gyan.FFmpeg.Essentials` on Windows) "
            "or set FFMPEG_BIN and FFPROBE_BIN environment variables.",
        )

    # Verify both execute cleanly without shell interpolation
    for label, binary in (("ffmpeg", ffmpeg_bin), ("ffprobe", ffprobe_bin)):
        try:
            proc = subprocess.run(
                [binary, "-version"],
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                raise AIError(500, f"{label} binary exited with code {proc.returncode}.")
        except AIError:
            raise
        except Exception as exc:
            raise AIError(500, f"Failed to execute {label} on server: {exc}") from exc

    _FFMPEG_CACHE = (ffmpeg_bin, ffprobe_bin)
    return _FFMPEG_CACHE


# ---------------------------------------------------------------------------
# 2. Security, Filename Sanitization & Constant-Memory Upload Streaming
# ---------------------------------------------------------------------------
def sanitize_media_filename(raw_filename: str | None) -> str:
    """Prevent path traversal, null bytes, and unsafe filenames; validate media extension."""
    if not raw_filename or "\x00" in raw_filename:
        raise AIError(400, "Invalid or missing media filename.")

    # Strip both Windows and POSIX directory traversal components
    base = posixpath.basename(ntpath.basename(raw_filename.strip()))
    if not base or base in {".", ".."}:
        raise AIError(400, "Unsafe media filename rejected.")

    ext = Path(base).suffix.lower()
    if ext not in ALLOWED_MEDIA_EXTENSIONS:
        allowed_str = ", ".join(sorted(ALLOWED_MEDIA_EXTENSIONS))
        raise AIError(
            400,
            f"Unsupported media file format '{ext or base}'. Allowed formats: {allowed_str}.",
        )

    stem = Path(base).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "lecture_media"
    return f"{safe_stem[:64]}{ext}"


def stream_upload_to_file(
    source_stream: BinaryIO,
    dest_dir: Path,
    raw_filename: str,
    max_bytes: int | None = None,
) -> Path:
    """Stream an uploaded media file to ``dest_dir`` in 1 MB chunks without loading into RAM."""
    safe_name = sanitize_media_filename(raw_filename)
    dest_dir_resolved = dest_dir.resolve()
    dest_path = (dest_dir_resolved / safe_name).resolve()

    if dest_dir_resolved not in dest_path.parents:
        raise AIError(400, "Unsafe file path rejected.")

    limit_bytes = max_bytes if max_bytes is not None else config.MAX_VIDEO_UPLOAD_BYTES
    total_written = 0

    try:
        with open(dest_path, "wb") as out_f:
            while True:
                buf = source_stream.read(_STREAM_COPY_BUFFER_BYTES)
                if not buf:
                    break
                total_written += len(buf)
                if total_written > limit_bytes:
                    raise AIError(
                        413,
                        f"Uploaded media exceeds maximum allowed size of {limit_bytes / (1024 * 1024):.0f} MB.",
                    )
                out_f.write(buf)
    except AIError:
        if dest_path.exists():
            try:
                dest_path.unlink()
            except OSError:
                pass
        raise
    except Exception as exc:
        if dest_path.exists():
            try:
                dest_path.unlink()
            except OSError:
                pass
        raise AIError(400, f"Failed while reading uploaded media stream: {exc}") from exc

    if total_written == 0:
        if dest_path.exists():
            try:
                dest_path.unlink()
            except OSError:
                pass
        raise AIError(400, "Uploaded media file is empty (0 bytes).")

    return dest_path


# ---------------------------------------------------------------------------
# 3. Media Inspection & Validation (Corrupted MP4 / Missing Audio Detection)
# ---------------------------------------------------------------------------
def probe_and_validate_media(
    input_path: Path,
    max_duration_sec: float | None = None,
) -> MediaProbeInfo:
    """Validate media container integrity and verify presence of an audio stream using ffprobe."""
    if not input_path.is_file():
        raise AIError(400, "Uploaded media file does not exist on disk.")

    size_bytes = input_path.stat().st_size
    if size_bytes == 0:
        raise AIError(400, "Uploaded media file is empty (0 bytes).")
    if size_bytes > config.MAX_VIDEO_UPLOAD_BYTES:
        raise AIError(
            413,
            f"Media file size ({size_bytes / (1024 * 1024):.1f} MB) exceeds maximum limit ({config.MAX_VIDEO_UPLOAD_MB:.0f} MB).",
        )

    _, ffprobe_bin = resolve_ffmpeg_binaries()
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "format=duration,format_name,size:stream=index,codec_type,codec_name,duration",
        "-of", "json",
        str(input_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise AIError(400, "Media validation timed out: the uploaded MP4 file may be severely corrupted.") from exc
    except Exception as exc:
        raise AIError(500, f"Failed to run ffprobe on uploaded media: {exc}") from exc

    if proc.returncode != 0:
        stderr_snippet = (proc.stderr or "").strip()[:240]
        raise AIError(
            400,
            f"Uploaded media file is corrupted or not a valid MP4/audio container ({stderr_snippet or 'invalid header'}).",
        )

    try:
        probe_data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AIError(400, "Uploaded media file could not be parsed (corrupted metadata).") from exc

    streams = probe_data.get("streams") or []
    fmt = probe_data.get("format") or {}
    if not streams and not fmt:
        raise AIError(400, "Uploaded media file contains no readable streams (corrupted file).")

    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    video_streams = [s for s in streams if s.get("codec_type") == "video"]

    if not audio_streams:
        raise AIError(
            400,
            "Uploaded MP4/video file does not contain an audio stream. "
            "Please upload a recording with spoken audio.",
        )

    # Determine duration from container format or audio stream
    duration = 0.0
    for raw_dur in [fmt.get("duration")] + [s.get("duration") for s in audio_streams]:
        if raw_dur is not None:
            try:
                val = float(raw_dur)
                if val > duration:
                    duration = val
            except (TypeError, ValueError):
                continue

    max_dur = max_duration_sec if max_duration_sec is not None else config.MAX_MEDIA_DURATION_SECONDS
    if duration > max_dur:
        raise AIError(
            400,
            f"Media duration ({duration / 60:.1f} min) exceeds maximum allowed duration ({max_dur / 60:.0f} min).",
        )

    return MediaProbeInfo(
        format_name=str(fmt.get("format_name") or "unknown"),
        duration_sec=duration,
        size_bytes=size_bytes,
        has_video=bool(video_streams),
        has_audio=True,
        audio_codec=audio_streams[0].get("codec_name"),
    )


# ---------------------------------------------------------------------------
# 4. FFmpeg Audio Extraction & Speech Recognition Optimization
# ---------------------------------------------------------------------------
def extract_preprocessed_audio(
    input_path: Path,
    output_dir: Path,
    *,
    sample_rate: int | None = None,
    channels: int | None = None,
    bitrate: str | None = None,
) -> tuple[Path, float]:
    """Extract speech-optimized audio from an MP4/media file using FFmpeg.

    * Strips video/subtitle/data streams (-vn -sn -dn).
    * Downmixes to mono (-ac 1) at 16 kHz (-ar 16000) default.
    * Applies gentle speech band-pass (80 Hz - 7600 Hz) + dynamic normalization.
    * Encodes to MP3 (supported by OpenAI Whisper and Gemini Audio STT).
    * Returns ``(extracted_audio_path, exact_audio_duration_sec)``.
    """
    ffmpeg_bin, _ = resolve_ffmpeg_binaries()
    sr = sample_rate or config.AUDIO_SAMPLE_RATE
    ch = channels or config.AUDIO_CHANNELS
    br = bitrate or config.AUDIO_BITRATE

    out_path = output_dir / "extracted_speech_16khz_mono.mp3"
    nyquist_lp = min(7600, int(sr * 0.475))
    audio_filter = f"highpass=f=80,lowpass=f={nyquist_lp},dynaudnorm=f=150:g=5"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(input_path),
        "-map", "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-ac", str(ch),
        "-ar", str(sr),
        "-af", audio_filter,
        "-c:a", "libmp3lame",
        "-b:a", br,
        str(out_path),
    ]

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired as exc:
        raise AIError(504, "FFmpeg audio extraction timed out on oversized or damaged file.") from exc
    except Exception as exc:
        raise AIError(500, f"FFmpeg audio extraction failed to start: {exc}") from exc

    if proc.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
        err_msg = (proc.stderr or "").strip()[:260]
        raise AIError(
            400,
            f"Audio extraction failed — the MP4 audio stream appears corrupted or unreadable ({err_msg or 'empty output'}).",
        )

    audio_info = probe_and_validate_media(out_path)
    if audio_info.duration_sec <= 0.05:
        raise AIError(400, "Extracted audio stream has zero playable duration.")

    elapsed = time.monotonic() - t0
    logger.info(
        "Extracted mono %dHz MP3: %.2f MB (duration %.1fs) in %.2fs",
        sr,
        out_path.stat().st_size / (1024 * 1024),
        audio_info.duration_sec,
        elapsed,
    )
    return out_path, audio_info.duration_sec


# ---------------------------------------------------------------------------
# 5. Silence-Aware Audio Chunking with Overlap (Respecting Provider Size Limit)
# ---------------------------------------------------------------------------
def get_effective_max_upload_bytes(cfg: AIConfig | None = None, max_upload_bytes_override: int | None = None) -> int:
    """Return the maximum per-request audio upload size in bytes with safety margin."""
    if max_upload_bytes_override is not None and max_upload_bytes_override > 0:
        limit = max_upload_bytes_override
    else:
        limit = config.STT_MAX_UPLOAD_BYTES

    # OpenAI Audio Transcriptions API enforces a strict 25 MB limit; cap at 23.5 MB for multipart safety margin
    stt_provider = cfg.provider if cfg else "openai"
    try:
        if cfg is not None:
            stt_provider = resolve_stt_config(cfg).provider
    except Exception:
        pass

    if stt_provider == "openai":
        openai_safe_cap = int(23.5 * 1024 * 1024)
        limit = min(limit, openai_safe_cap)

    return max(64 * 1024, limit)


def detect_silence_midpoints(audio_path: Path, noise_db: int = -35, min_silence_sec: float = 0.35) -> list[float]:
    """Run FFmpeg silencedetect filter to locate natural pause timestamps (in seconds)."""
    ffmpeg_bin, _ = resolve_ffmpeg_binaries()
    null_sink = "NUL" if os.name == "nt" else "/dev/null"
    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-f", "null",
        null_sink,
    ]
    try:
        proc = subprocess.run(
            cmd,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except Exception:
        return []

    stderr = proc.stderr or ""
    starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*([0-9.]+)", stderr)]
    ends = [float(m.group(1)) for m in re.finditer(r"silence_end:\s*([0-9.]+)", stderr)]

    midpoints: list[float] = []
    for s_val, e_val in zip(starts, ends):
        if e_val >= s_val:
            midpoints.append(round((s_val + e_val) / 2.0, 3))
    return sorted(midpoints)


def _slice_audio_segment(
    ffmpeg_bin: str,
    source_audio: Path,
    dest_chunk: Path,
    start_sec: float,
    end_sec: float,
    sample_rate: int,
    bitrate: str,
) -> None:
    duration = max(0.1, end_sec - start_sec)
    # Fast stream-copy slice first
    copy_cmd = [
        ffmpeg_bin,
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(source_audio),
        "-vn",
        "-c:a", "copy",
        str(dest_chunk),
    ]
    proc = subprocess.run(
        copy_cmd,
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode == 0 and dest_chunk.is_file() and dest_chunk.stat().st_size > 0:
        return

    # Fallback re-encode slice if frame-boundary copy failed
    enc_cmd = [
        ffmpeg_bin,
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(source_audio),
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "libmp3lame",
        "-b:a", bitrate,
        str(dest_chunk),
    ]
    proc2 = subprocess.run(
        enc_cmd,
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc2.returncode != 0 or not dest_chunk.is_file() or dest_chunk.stat().st_size == 0:
        raise AIError(500, f"Failed to slice audio segment [{start_sec:.1f}s - {end_sec:.1f}s].")


def split_audio_into_chunks(
    audio_path: Path,
    duration_sec: float,
    output_dir: Path,
    *,
    max_upload_bytes: int,
    target_chunk_sec: float | None = None,
    overlap_sec: float | None = None,
) -> list[AudioChunk]:
    """Split preprocessed audio into chronological chunks ONLY if it exceeds ``max_upload_bytes``.

    * Prefers natural silence boundaries near the target cut point.
    * Falls back to fixed-duration cuts when no silence boundary is found.
    * Includes ~1.5s overlap between consecutive chunks.
    * Guarantees every returned chunk has ``size_bytes <= max_upload_bytes``.
    """
    file_size = audio_path.stat().st_size
    if file_size <= max_upload_bytes:
        logger.info(
            "Audio size (%.2f MB) fits within upload limit (%.2f MB); skipping chunking.",
            file_size / (1024 * 1024),
            max_upload_bytes / (1024 * 1024),
        )
        return [
            AudioChunk(
                index=0,
                path=audio_path,
                start_sec=0.0,
                end_sec=duration_sec,
                nominal_start_sec=0.0,
                nominal_end_sec=duration_sec,
                size_bytes=file_size,
            )
        ]

    ffmpeg_bin, _ = resolve_ffmpeg_binaries()
    ov_sec = overlap_sec if overlap_sec is not None else config.AUDIO_CHUNK_OVERLAP_SECONDS
    ov_sec = max(0.0, min(ov_sec, 3.0))

    bytes_per_sec = max(1.0, file_size / max(0.5, duration_sec))
    # 82% safety margin so VBR fluctuations + overlap stay comfortably under max_upload_bytes
    max_sec_by_size = max(4.0, (max_upload_bytes * 0.82) / bytes_per_sec)
    cfg_target = target_chunk_sec if target_chunk_sec is not None else config.AUDIO_CHUNK_TARGET_SECONDS
    step_sec = min(cfg_target, max_sec_by_size) if cfg_target > 0 else max_sec_by_size
    step_sec = max(3.0, step_sec)
    if ov_sec >= step_sec * 0.4:
        ov_sec = round(step_sec * 0.15, 2)

    silences = detect_silence_midpoints(audio_path)
    logger.info(
        "Audio size (%.2f MB) exceeds limit (%.2f MB). Planning chunks (step=%.1fs, overlap=%.2fs, silences=%d)...",
        file_size / (1024 * 1024),
        max_upload_bytes / (1024 * 1024),
        step_sec,
        ov_sec,
        len(silences),
    )

    # Build nominal non-overlapping intervals [nom_start, nom_end], snapping to natural silences where available
    nominal_intervals: list[tuple[float, float]] = []
    cursor = 0.0
    while cursor < duration_sec - 0.05:
        if cursor + step_sec >= duration_sec - 0.25:
            nominal_intervals.append((cursor, duration_sec))
            break

        ideal_cut = cursor + step_sec
        search_min = cursor + step_sec * 0.60
        search_max = min(duration_sec - 0.5, cursor + max_sec_by_size * 0.95)
        if search_max < search_min:
            search_min = cursor + step_sec * 0.50
            search_max = ideal_cut

        candidates = [s for s in silences if search_min <= s <= search_max]
        if candidates:
            # Pick silence closest to ideal_cut
            cut = min(candidates, key=lambda s: abs(s - ideal_cut))
        else:
            cut = ideal_cut

        if cut <= cursor + 0.5:
            cut = min(duration_sec, cursor + step_sec)

        nominal_intervals.append((cursor, cut))
        cursor = cut

    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    raw_intervals: list[tuple[float, float, float, float]] = []
    for i, (nom_s, nom_e) in enumerate(nominal_intervals):
        actual_s = 0.0 if i == 0 else max(0.0, nom_s - ov_sec)
        actual_e = nom_e
        raw_intervals.append((actual_s, actual_e, nom_s, nom_e))

    final_chunks: list[AudioChunk] = []

    def _materialize_interval(act_s: float, act_e: float, nom_s: float, nom_e: float) -> None:
        idx = len(final_chunks)
        c_path = chunks_dir / f"chunk_{idx:04d}.mp3"
        _slice_audio_segment(
            ffmpeg_bin,
            audio_path,
            c_path,
            act_s,
            act_e,
            config.AUDIO_SAMPLE_RATE,
            config.AUDIO_BITRATE,
        )
        c_size = c_path.stat().st_size
        # Ironclad size invariant: if a VBR burst still exceeds max_upload_bytes, bisect it
        if c_size > max_upload_bytes and (act_e - act_s) > 2.5:
            c_path.unlink(missing_ok=True)
            mid_nom = round((nom_s + nom_e) / 2.0, 3)
            _materialize_interval(act_s, mid_nom, nom_s, mid_nom)
            _materialize_interval(max(0.0, mid_nom - ov_sec), act_e, mid_nom, nom_e)
            return

        final_chunks.append(
            AudioChunk(
                index=idx,
                path=c_path,
                start_sec=round(act_s, 3),
                end_sec=round(act_e, 3),
                nominal_start_sec=round(nom_s, 3),
                nominal_end_sec=round(nom_e, 3),
                size_bytes=c_size,
            )
        )

    for act_s, act_e, nom_s, nom_e in raw_intervals:
        _materialize_interval(act_s, act_e, nom_s, nom_e)

    logger.info("Created %d audio chunks (all <= %.2f MB).", len(final_chunks), max_upload_bytes / (1024 * 1024))
    return final_chunks


# ---------------------------------------------------------------------------
# 6. Per-Chunk Retryable Transcription + Memoization + Overlap Deduplication
# ---------------------------------------------------------------------------
def _chunk_cache_key(chunk: AudioChunk, language: str | None) -> str:
    h = hashlib.sha256()
    with open(chunk.path, "rb") as f:
        while True:
            b = f.read(128 * 1024)
            if not b:
                break
            h.update(b)
    return f"{chunk.index}:{language or 'auto'}:{h.hexdigest()[:24]}"


def format_timestamp(seconds: float) -> str:
    sec = max(0, int(round(seconds)))
    hrs, rem = divmod(sec, 3600)
    mins, s = divmod(rem, 60)
    return f"{hrs:02d}:{mins:02d}:{s:02d}"


_WORD_NORM_RE = re.compile(r"[^\w\u0590-\u05FF]+", flags=re.UNICODE)


def _normalize_word(word: str) -> str:
    return _WORD_NORM_RE.sub("", word).lower()


def _trim_overlap_prefix(prev_tail_text: str, next_head_text: str, max_overlap_words: int = 22) -> str:
    """Remove duplicated words/phrases at the start of ``next_head_text`` that already appeared at the end of ``prev_tail_text``."""
    prev_words = [w for w in prev_tail_text.strip().split() if _normalize_word(w)]
    next_words_raw = next_head_text.strip().split()
    if not prev_words or not next_words_raw:
        return next_head_text.strip()

    prev_norm = [_normalize_word(w) for w in prev_words[-max_overlap_words:]]
    next_norm = [_normalize_word(w) for w in next_words_raw[:max_overlap_words]]

    best_k = 0
    max_k = min(len(prev_norm), len(next_norm))
    for k in range(max_k, 0, -1):
        suffix = prev_norm[-k:]
        prefix = next_norm[:k]
        if suffix == prefix:
            best_k = k
            break
        # Also handle minor 1-word boundary jitter on overlaps >= 4 words
        if k >= 4:
            matches = sum(1 for a, b in zip(suffix, prefix) if a == b)
            if matches / k >= 0.80:
                best_k = k
                break

    if best_k > 0:
        return " ".join(next_words_raw[best_k:]).strip()
    return next_head_text.strip()


def merge_chunk_transcripts(
    chunks: list[AudioChunk],
    per_chunk_segments: dict[int, list[dict]],
) -> tuple[list[TranscriptSegment], str, str]:
    """Merge per-chunk segments in chronological order, deduplicating overlap windows and preserving timestamps."""
    merged: list[TranscriptSegment] = []

    for chunk in sorted(chunks, key=lambda c: c.index):
        raw_segs = per_chunk_segments.get(chunk.index) or []
        prev_chunk_end = merged[-1].end_sec if merged else -1.0

        for s_idx, seg in enumerate(raw_segs):
            txt = str(seg.get("text") or "").strip()
            if not txt:
                continue
            global_start = round(chunk.start_sec + max(0.0, float(seg.get("start") or 0.0)), 2)
            global_end = round(chunk.start_sec + max(0.0, float(seg.get("end") or 0.0)), 2)
            if global_end < global_start:
                global_end = global_start + 1.0

            # Overlap deduplication against the tail of the previous chunk
            if chunk.index > 0 and merged and global_start < chunk.nominal_start_sec + 3.0:
                recent_tail = " ".join(m.text for m in merged[-3:])
                # If this segment lies completely inside the already-covered time window, check if duplicate
                if global_end <= prev_chunk_end + 0.35:
                    seg_words = [_normalize_word(w) for w in txt.split() if _normalize_word(w)]
                    tail_words = {_normalize_word(w) for w in recent_tail.split() if _normalize_word(w)}
                    if seg_words and sum(1 for w in seg_words if w in tail_words) / len(seg_words) >= 0.75:
                        continue

                txt = _trim_overlap_prefix(recent_tail, txt)
                if not txt:
                    continue
                if global_start < merged[-1].end_sec and global_end > merged[-1].end_sec:
                    global_start = merged[-1].end_sec

            merged.append(
                TranscriptSegment(
                    start_sec=global_start,
                    end_sec=max(global_start, global_end),
                    text=txt,
                    chunk_index=chunk.index,
                )
            )

    formatted_lines = [
        f"[{format_timestamp(seg.start_sec)} - {format_timestamp(seg.end_sec)}] {seg.text}"
        for seg in merged
    ]
    plain_text = " ".join(seg.text for seg in merged).strip()
    return merged, "\n".join(formatted_lines), plain_text


def transcribe_chunks_with_retry(
    chunks: list[AudioChunk],
    ai_cfg: AIConfig,
    *,
    language: str | None = None,
    max_retries: int | None = None,
    retry_base_delay_sec: float = 1.0,
    memo_cache: dict[str, list[dict]] | None = None,
    transcribe_fn: Callable[..., list[dict]] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[dict[int, list[dict]], int]:
    """Transcribe chunks sequentially with independent per-chunk retry & memoization.

    * Never makes duplicate API calls for chunks already present in ``memo_cache``.
    * Handles partial transcription failures independently: if some chunks succeed and
      one chunk fails all retries (non-auth error), preserves succeeded chunks and marks
      the failed interval clearly. If ALL chunks fail, raises ``AIError``.
    """
    fn = transcribe_fn or transcribe_audio_segment
    retries = max(1, max_retries if max_retries is not None else config.TRANSCRIPTION_MAX_RETRIES)
    cache = memo_cache if memo_cache is not None else {}
    norm_lang = normalize_language_code(language) or config.TRANSCRIPTION_DEFAULT_LANGUAGE

    per_chunk_results: dict[int, list[dict]] = {}
    failed_chunks: list[tuple[AudioChunk, AIError]] = []
    total = len(chunks)

    for pos, chunk in enumerate(chunks, start=1):
        if progress_callback:
            progress_callback(
                pos,
                total,
                f"🎙️ Transcribing audio segment {pos}/{total} [{format_timestamp(chunk.start_sec)} - {format_timestamp(chunk.end_sec)}]...",
            )

        c_key = _chunk_cache_key(chunk, norm_lang)
        if c_key in cache:
            logger.info("Chunk %d/%d loaded from memoization cache (skipped duplicate API call).", pos, total)
            per_chunk_results[chunk.index] = cache[c_key]
            continue

        chunk_err: AIError | None = None
        for attempt in range(1, retries + 1):
            try:
                segs = fn(
                    ai_cfg,
                    audio_path=chunk.path,
                    language=norm_lang,
                    chunk_duration_sec=chunk.duration_sec,
                )
                cache[c_key] = segs
                per_chunk_results[chunk.index] = segs
                chunk_err = None
                logger.info(
                    "Chunk %d/%d transcribed on attempt %d (%d segment(s)).",
                    pos,
                    total,
                    attempt,
                    len(segs),
                )
                break
            except AIError as exc:
                chunk_err = exc
                # Auth / bad API key errors should fail immediately without retrying
                if exc.status_code in (401, 403):
                    raise
                logger.warning(
                    "Chunk %d/%d attempt %d/%d failed (%s): %s",
                    pos,
                    total,
                    attempt,
                    retries,
                    exc.status_code,
                    exc.message,
                )
                if attempt < retries and retry_base_delay_sec > 0:
                    time.sleep(retry_base_delay_sec * (2 ** (attempt - 1)))
            except Exception as exc:
                chunk_err = AIError(502, f"Unexpected transcription error on chunk {pos}: {exc}")
                logger.warning("Chunk %d/%d attempt %d/%d raised exception: %s", pos, total, attempt, retries, exc)
                if attempt < retries and retry_base_delay_sec > 0:
                    time.sleep(retry_base_delay_sec * (2 ** (attempt - 1)))

        if chunk_err is not None:
            failed_chunks.append((chunk, chunk_err))

    # If every single chunk failed, surface the error directly
    if len(failed_chunks) == total:
        raise failed_chunks[0][1]

    # Partial failure recovery: preserve succeeded chunks and mark any missing segment interval clearly
    for failed_chunk, _err in failed_chunks:
        marker = (
            "[קטע שמע לא פוענח לאחר ניסיונות חוזרים / Audio segment unavailable after retries]"
            if norm_lang == "he"
            else "[Audio segment transcription unavailable after retries]"
        )
        per_chunk_results[failed_chunk.index] = [
            {
                "start": max(0.0, failed_chunk.nominal_start_sec - failed_chunk.start_sec),
                "end": max(1.0, failed_chunk.duration_sec),
                "text": marker,
            }
        ]

    return per_chunk_results, len(failed_chunks)


# ---------------------------------------------------------------------------
# 7. End-to-End Isolated Job Orchestrator (Guaranteed Temp Cleanup)
# ---------------------------------------------------------------------------
def process_media_file_to_transcript(
    *,
    input_media_path: Path | None = None,
    upload_stream: BinaryIO | None = None,
    original_filename: str,
    ai_cfg: AIConfig,
    language: str | None = None,
    max_upload_bytes_override: int | None = None,
    target_chunk_sec_override: float | None = None,
    overlap_sec_override: float | None = None,
    max_retries_override: int | None = None,
    retry_base_delay_sec: float = 1.0,
    memo_cache: dict[str, list[dict]] | None = None,
    transcribe_fn: Callable[..., list[dict]] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> AudioPipelineResult:
    """Execute the full MP4 -> Preprocessed Audio -> Chunks -> STT -> Merged Transcript pipeline.

    Uses a unique temporary directory per job and guarantees cleanup of all intermediate files,
    including on failure.
    """
    job_uid = uuid.uuid4().hex[:12]
    job_dir = Path(tempfile.mkdtemp(prefix=f"classpulse_audio_{job_uid}_"))
    logger.info("[Job %s] Created isolated workspace %s for '%s'", job_uid, job_dir.name, original_filename)

    def _notify(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    try:
        # Step 1: Stream or copy input into isolated job workspace & sanitize filename
        safe_name = sanitize_media_filename(original_filename)
        if upload_stream is not None:
            _notify("📥 Streaming & validating uploaded media file...")
            staged_media = stream_upload_to_file(upload_stream, job_dir, safe_name)
        elif input_media_path is not None:
            _notify("🔍 Validating uploaded media container & audio stream...")
            if not input_media_path.is_file():
                raise AIError(400, "Uploaded media file was not found.")
            staged_media = job_dir / safe_name
            with open(input_media_path, "rb") as src_f:
                staged_media = stream_upload_to_file(src_f, job_dir, safe_name)
        else:
            raise AIError(400, "No media input provided.")

        # Step 2: Validate MP4 / media container & audio stream presence
        probe = probe_and_validate_media(staged_media)
        logger.info(
            "[Job %s] Validated media (%s, %.2f MB, duration=%.1fs, audio_codec=%s)",
            job_uid,
            probe.format_name,
            probe.size_bytes / (1024 * 1024),
            probe.duration_sec,
            probe.audio_codec,
        )

        # Step 3: Extract mono 16 kHz speech-optimized MP3 via FFmpeg (video removed completely)
        _notify("🎵 Extracting 16 kHz mono speech audio via FFmpeg...")
        extracted_audio, audio_duration = extract_preprocessed_audio(staged_media, job_dir)
        extracted_bytes = extracted_audio.stat().st_size

        # Free disk space immediately by removing the staged video copy once audio is extracted
        if staged_media.exists() and staged_media != extracted_audio:
            staged_media.unlink(missing_ok=True)

        # Step 4: Size check & silence-aware chunking (only if exceeding provider's max upload limit)
        effective_limit = get_effective_max_upload_bytes(ai_cfg, max_upload_bytes_override)
        _notify("✂️ Checking audio size & preparing segments...")
        chunks = split_audio_into_chunks(
            extracted_audio,
            audio_duration,
            job_dir,
            max_upload_bytes=effective_limit,
            target_chunk_sec=target_chunk_sec_override,
            overlap_sec=overlap_sec_override,
        )

        # Step 5: Transcribe audio chunks via Speech-to-Text with per-chunk retry & memoization
        per_chunk_segs, partial_failures = transcribe_chunks_with_retry(
            chunks,
            ai_cfg,
            language=language,
            max_retries=max_retries_override,
            retry_base_delay_sec=retry_base_delay_sec,
            memo_cache=memo_cache,
            transcribe_fn=transcribe_fn,
            progress_callback=lambda pos, tot, msg: _notify(msg),
        )

        # Step 6: Chronological merge & overlap deduplication
        _notify("🔗 Merging timestamped transcript segments...")
        segments, formatted_text, plain_text = merge_chunk_transcripts(chunks, per_chunk_segs)
        if not plain_text.strip():
            raise AIError(
                400,
                "No spoken words were detected in the uploaded media recording. "
                "Please verify the recording contains audible speech.",
            )

        is_heb = (normalize_language_code(language) == "he") or contains_hebrew(plain_text)
        chunk_meta = [
            {
                "index": c.index,
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "size_bytes": c.size_bytes,
            }
            for c in chunks
        ]

        logger.info(
            "[Job %s] Transcription complete (%d chunk(s), %d segment(s), %d chars, hebrew=%s).",
            job_uid,
            len(chunks),
            len(segments),
            len(plain_text),
            is_heb,
        )

        return AudioPipelineResult(
            formatted_transcript=formatted_text,
            plain_transcript=plain_text,
            segments=segments,
            duration_sec=audio_duration,
            extracted_audio_bytes=extracted_bytes,
            chunk_count=len(chunks),
            partial_failures=partial_failures,
            detected_hebrew=is_heb,
            chunk_metadata=chunk_meta,
        )
    finally:
        # Unconditional cleanup of the isolated job directory and all intermediate chunks/files
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.info("[Job %s] Cleaned up temporary workspace %s.", job_uid, job_dir.name)
