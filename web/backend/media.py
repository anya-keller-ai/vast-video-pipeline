from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    duration_seconds: float
    width: int
    height: int
    frame_rate: float
    has_audio: bool

    @property
    def short_edge(self) -> int:
        return min(self.width, self.height)


def parse_video_metadata(payload: object) -> VideoMetadata:
    root = _as_string_mapping(payload)
    if root is None:
        raise ValueError("ffprobe returned invalid video metadata.")
    streams_value = root.get("streams")
    if not isinstance(streams_value, list):
        raise ValueError("The uploaded file does not contain a readable video stream.")

    streams = [
        mapping
        for stream in cast(list[object], streams_value)
        if (mapping := _as_string_mapping(stream)) is not None
    ]
    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    if video is None:
        raise ValueError("The uploaded file does not contain a video stream.")

    width = _positive_int(video.get("width"))
    height = _positive_int(video.get("height"))
    duration = _duration(root, video)
    frame_rate = _frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    return VideoMetadata(duration, width, height, frame_rate, has_audio)


async def probe_video(path: Path) -> VideoMetadata:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise RuntimeError("ffprobe is required to inspect uploaded videos.")
    process = await asyncio.create_subprocess_exec(
        executable,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
        "-show_streams",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(message[-1_000:] or "ffprobe could not read this video.")
    try:
        payload = cast(object, json.loads(stdout.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("ffprobe returned invalid JSON for this video.") from error
    return parse_video_metadata(payload)


async def normalize_reference_video(source: Path, target: Path) -> Path:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("ffmpeg is required to prepare H3 video references.")

    frame_rate_option = await ffmpeg_frame_rate_option(executable)
    target.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        "fps=24",
        *frame_rate_option,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(target),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        target.unlink(missing_ok=True)
        message = stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(
            message[-1_000:] or "Не удалось преобразовать reference-видео в 24 fps."
        )
    if not target.is_file() or target.stat().st_size == 0:
        raise ValueError("ffmpeg не создал подготовленный reference-файл.")
    return target


async def ffmpeg_frame_rate_option(executable: str) -> tuple[str, str]:
    process = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        "-h",
        "full",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError("Cannot inspect installed ffmpeg capabilities.")
    help_text = (stdout + stderr).decode("utf-8", errors="replace")
    has_fps_mode = any(
        line.lstrip().startswith("-fps_mode") for line in help_text.splitlines()
    )
    return ("-fps_mode" if has_fps_mode else "-vsync", "cfr")


def _as_string_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    entries = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in entries):
        return None
    return cast(dict[str, object], entries)


def _duration(root: dict[str, object], video: dict[str, object]) -> float:
    format_value = root.get("format")
    format_data = _as_string_mapping(format_value) if format_value is not None else None
    candidates = (
        format_data.get("duration") if format_data is not None else None,
        video.get("duration"),
    )
    for value in candidates:
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            duration = float(value)
            if duration > 0:
                return duration
    raise ValueError("Could not determine the video duration.")


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("ffprobe returned invalid video dimensions.")
    return value


def _frame_rate(value: object) -> float:
    if not isinstance(value, str) or not value:
        raise ValueError("ffprobe returned an invalid frame rate.")
    numerator, separator, denominator = value.partition("/")
    try:
        rate = float(numerator) / float(denominator) if separator else float(numerator)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("ffprobe returned an invalid frame rate.") from error
    if rate <= 0:
        raise ValueError("ffprobe returned an invalid frame rate.")
    return rate
