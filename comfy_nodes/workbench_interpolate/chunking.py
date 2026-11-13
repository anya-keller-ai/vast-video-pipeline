"""Chunked frame interpolation without seams.

Frame interpolation models (RIFE, FILM) build every in-between frame from exactly
two neighbouring source frames, so a long video can be processed in chunks that
share one frame: each chunk starts with the last frame of the previous chunk and
that duplicate is dropped when the output is joined. The joined result contains
every in-between frame exactly once, i.e. it equals processing the whole video.

This module has no ComfyUI or torch imports so the logic can be tested anywhere.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence

MIN_CHUNK_FRAMES = 2


def output_frame_count(source_frames: int, multiplier: int) -> int:
    """Frames produced for a video of ``source_frames`` frames (one frame: itself)."""
    if source_frames < 2:
        return source_frames
    return (source_frames - 1) * multiplier + 1


def interpolate_in_chunks[Frame](
    frames: Iterable[Frame],
    interpolate: Callable[[Sequence[Frame], int], Sequence[Frame]],
    multiplier: int,
    chunk_frames: int,
) -> Iterator[Frame]:
    """Yield the interpolated video chunk by chunk.

    ``interpolate(chunk, multiplier)`` must return ``(len(chunk) - 1) * multiplier + 1``
    frames and start/end with the first/last chunk frame.
    """
    if multiplier < 2:
        raise ValueError("Множитель кадров должен быть не меньше 2.")
    if chunk_frames < MIN_CHUNK_FRAMES:
        raise ValueError("В куске должно быть не меньше двух кадров.")

    source = iter(frames)
    chunk: list[Frame] = []
    for frame in source:
        chunk.append(frame)
        if len(chunk) == chunk_frames:
            break
    if len(chunk) < 2:
        yield from chunk
        return

    first = True
    while True:
        result = interpolate(chunk, multiplier)
        expected = output_frame_count(len(chunk), multiplier)
        if len(result) != expected:
            raise ValueError(
                f"Интерполяция вернула {len(result)} кадров, ожидалось {expected}."
            )
        yield from result if first else result[1:]
        first = False

        # The next chunk starts with the last frame of this one (the shared frame).
        shared = chunk[-1]
        chunk = [shared]
        for frame in source:
            chunk.append(frame)
            if len(chunk) == chunk_frames:
                break
        if len(chunk) < 2:
            return
