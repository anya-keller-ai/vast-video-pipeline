from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# Share of the whole run taken by decoding on the GPU; the rest is MP4 encoding.
FLASHVSR_DECODE_SHARE = 0.8

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_START = "FlashVSR+: Preparing file-backed VIDEO input"
_TILE = re.compile(r"FlashVSR\+: Spatial tile (\d+)/(\d+)")
_DECODE = re.compile(
    r"FlashVSR\+: Decode segment (\d+) · \d+ latent · (\d+) output frames"
)
_ENCODE = re.compile(r"FlashVSR\+: Encoding video frame (\d+)/(\d+)")


@dataclass(frozen=True, slots=True)
class FlashVsrProgress:
    percent: int | None
    label: str


def parse_flashvsr_progress(
    messages: Sequence[str], total_frames: int | None
) -> FlashVsrProgress | None:
    """Turn the FlashVSR+ node's log lines into a progress estimate.

    The node reports progress only as text in the ComfyUI log. The caller passes
    only the lines written since the job was submitted; if a start marker is
    present, only the lines after the newest marker are used.
    """
    lines = [_ANSI.sub("", message).strip() for message in messages]
    if not any("FlashVSR+:" in line for line in lines):
        return None
    starts = [index for index, line in enumerate(lines) if _START in line]
    first = starts[-1] + 1 if starts else 0

    tile, tiles = 1, 1
    decoded, segment = 0, 0
    encoded: tuple[int, int] | None = None
    for line in lines[first:]:
        if match := _TILE.search(line):
            tile, tiles = int(match[1]), max(int(match[2]), 1)
            decoded, segment = 0, 0
        elif match := _DECODE.search(line):
            segment = int(match[1])
            decoded += int(match[2])
        elif match := _ENCODE.search(line):
            encoded = (int(match[1]), max(int(match[2]), 1))

    decode_percent = round(FLASHVSR_DECODE_SHARE * 100)
    if encoded is not None:
        done, total = encoded
        percent = decode_percent + round(
            (100 - decode_percent) * min(done, total) / total
        )
        return FlashVsrProgress(
            min(percent, 100), f"FlashVSR+: кодирую видео, кадр {done}/{total}"
        )
    if segment == 0:
        return FlashVsrProgress(None, "FlashVSR+: подготавливаю видео и модель")
    tile_note = f", фрагмент {tile}/{tiles}" if tiles > 1 else ""
    if total_frames is None or total_frames <= 0:
        return FlashVsrProgress(
            None, f"FlashVSR+: декодирование, сегмент {segment}{tile_note}"
        )
    fraction = ((tile - 1) + min(decoded, total_frames) / total_frames) / tiles
    percent = min(round(fraction * decode_percent), decode_percent - 1)
    done = min(decoded, total_frames)
    label = f"FlashVSR+: декодирование, кадр {done}/{total_frames}{tile_note}"
    return FlashVsrProgress(percent, label)
