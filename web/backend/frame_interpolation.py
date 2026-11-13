from __future__ import annotations

from .workflows import (  # pyright: ignore[reportPrivateUsage]
    ComfyPrompt,
    _node,
    model_filename,
)

INTERPOLATION_BUNDLE_ID = "frame_interpolation"
INTERPOLATION_NODE = "WorkbenchChunkedInterpolate"
INTERPOLATION_SAVE_NODE_ID = "2"
INTERPOLATION_MULTIPLIERS = (2, 3)
INTERPOLATION_MODEL_ASSETS = {
    "film": "interp_film_net_fp16",
    "rife_4_26": "interp_rife_v4_26",
    "rife_4_25": "interp_rife_v4_25",
}
INTERPOLATION_DEFAULT_MODEL = "rife_4_26"
# Working memory per chunk; the container limit is 171 GiB, a wide margin.
INTERPOLATION_CHUNK_MEMORY_BYTES = 24 * 1024**3
INTERPOLATION_MIN_CHUNK_FRAMES = 2
INTERPOLATION_MAX_CHUNK_FRAMES = 480


def choose_chunk_frames(width: int, height: int, multiplier: int) -> int:
    """Largest chunk of source frames whose working memory fits the budget.

    Per source frame the node holds the float32 input plus the interpolated frames
    with their working copies, so the cost grows with the multiplier.
    """
    if width <= 0 or height <= 0 or multiplier < 2:
        raise ValueError("Некорректные параметры для расчёта размера куска.")
    bytes_per_frame = width * height * 3 * (4 + 4 * multiplier)
    fits = INTERPOLATION_CHUNK_MEMORY_BYTES // bytes_per_frame
    return max(
        INTERPOLATION_MIN_CHUNK_FRAMES, min(INTERPOLATION_MAX_CHUNK_FRAMES, fits)
    )


def build_interpolation_prompt(
    job_id: str,
    video_filename: str,
    model_key: str,
    multiplier: int,
    width: int,
    height: int,
) -> ComfyPrompt:
    if model_key not in INTERPOLATION_MODEL_ASSETS:
        raise ValueError("Модель интерполяции: film, rife_4_26 или rife_4_25.")
    if multiplier not in INTERPOLATION_MULTIPLIERS:
        raise ValueError("Множитель частоты кадров: 2 или 3.")
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        raise ValueError("Некорректный идентификатор задачи.")
    return {
        "1": _node(
            "FrameInterpolationModelLoader",
            {"model_name": model_filename(INTERPOLATION_MODEL_ASSETS[model_key])},
        ),
        INTERPOLATION_SAVE_NODE_ID: _node(
            INTERPOLATION_NODE,
            {
                "interp_model": ["1", 0],
                "file": video_filename,
                "multiplier": multiplier,
                "chunk_frames": choose_chunk_frames(width, height, multiplier),
                "filename_prefix": (
                    f"workbench/jobs/{job_id}/interpolate-{model_key}-x{multiplier}"
                ),
                "crf": 18,
            },
        ),
    }
