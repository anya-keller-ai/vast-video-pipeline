"""ffprobe parsing and colour handling for the chunked interpolation node.

Pure functions only (no ComfyUI imports) so they can be tested anywhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

# ffmpeg matrix name -> (-colorspace, -color_primaries, -color_trc) tags
COLOR_TAGS: dict[str, tuple[str, str, str]] = {
    "bt709": ("bt709", "bt709", "bt709"),
    "bt601": ("smpte170m", "smpte170m", "smpte170m"),
}
_BT601_TAGS = frozenset({"bt601", "smpte170m", "bt470bg", "bt470m"})


@dataclass(frozen=True, slots=True)
class VideoInfo:
    width: int
    height: int
    fps: Fraction
    estimated_frames: int | None
    has_audio: bool
    color_matrix: str


def choose_color_matrix(tag: str | None, height: int) -> str:
    """Matrix used both to decode the source and to encode the result.

    A tagged source keeps its matrix; an untagged one follows the common player
    convention (BT.709 from 720 lines up, BT.601 below).
    """
    if tag == "bt709":
        return "bt709"
    if tag in _BT601_TAGS:
        return "bt601"
    return "bt709" if height >= 720 else "bt601"


def parse_fraction(text: object) -> Fraction | None:
    if not isinstance(text, str) or "/" not in text:
        return None
    numerator, _, denominator = text.partition("/")
    try:
        value = Fraction(int(numerator), int(denominator))
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def parse_ffprobe(payload: dict[str, object]) -> VideoInfo:
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ValueError("ffprobe не вернул потоки.")
    video = next(
        (s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise ValueError("В файле нет видеопотока.")
    width, height = video.get("width"), video.get("height")
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or min(width, height) <= 0
    ):
        raise ValueError("Не удалось определить размер видео.")
    fps = parse_fraction(video.get("avg_frame_rate")) or parse_fraction(
        video.get("r_frame_rate")
    )
    if fps is None:
        raise ValueError("Не удалось определить частоту кадров видео.")

    estimated: int | None = None
    frames_text = video.get("nb_frames")
    if isinstance(frames_text, str) and frames_text.isdigit() and int(frames_text) > 0:
        estimated = int(frames_text)
    else:
        try:
            duration = float(str(video.get("duration")))
        except ValueError:
            duration = math.nan
        if math.isfinite(duration) and duration > 0:
            estimated = max(1, round(duration * float(fps)))

    has_audio = any(
        isinstance(s, dict) and s.get("codec_type") == "audio" for s in streams
    )
    tag = video.get("color_space")
    return VideoInfo(
        width=width,
        height=height,
        fps=fps,
        estimated_frames=estimated,
        has_audio=has_audio,
        color_matrix=choose_color_matrix(tag if isinstance(tag, str) else None, height),
    )
