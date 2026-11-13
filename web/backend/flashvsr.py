from __future__ import annotations

from .workflows import ComfyPrompt, WorkflowNode, _node, model_filename  # noqa: F401

FLASHVSR_VARIANTS = ("turbo", "base")
FLASHVSR_SCALES = (1, 2, 4)
FLASHVSR_SAVE_NODE_ID = "4"
FLASHVSR_MODEL_DIRECTORY = "FlashVSR_Plus"
FLASHVSR_BUNDLE_PREFIX = "flashvsr_plus_"


def flashvsr_bundle_id(variant: str) -> str:
    if variant not in FLASHVSR_VARIANTS:
        raise ValueError("FlashVSR+: режим должен быть turbo или base.")
    return f"{FLASHVSR_BUNDLE_PREFIX}{variant}"


def build_flashvsr_prompt(
    job_id: str,
    video_filename: str,
    variant: str,
    scale: int,
    seed: int = 1,
) -> ComfyPrompt:
    flashvsr_bundle_id(variant)
    if scale not in FLASHVSR_SCALES:
        raise ValueError("FlashVSR+: масштаб должен быть 1, 2 или 4.")
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        raise ValueError("Некорректный идентификатор задачи.")
    return {
        "1": _node("LoadVideo", {"file": video_filename}),
        "2": _node(
            "FlashVSRPlusLoader",
            {
                "mode": variant,
                "profile": "auto",
                "attn_backend": "auto",
                "offload": "auto",
                "decoder": "tiny",
                # Weights come only from the per-tab download button.
                "auto_download": False,
                "model_directory": "",
                "reload_token": 0,
            },
        ),
        "3": _node(
            "FlashVSRPlusVideoUpscale",
            {
                "pipe": ["2", 0],
                "video": ["1", 0],
                "scale": float(scale),
                "height": 0,
                "width": 0,
                "temporal_compression": 4,
                "tiled": "auto",
                "tile_size": 512,
                "tile_overlap": 128,
                "color_fix": False,
                "color_correction_levels": 5,
                "seed": seed,
                "cache_offload": "auto",
                "decode_every": 4,
                "vae_tiled": True,
                "vae_tile_size": 256,
                "vae_tile_overlap": 64,
                "oom_retry": True,
                "crf": 18,
                "keep_audio": True,
            },
        ),
        FLASHVSR_SAVE_NODE_ID: _node(
            "SaveVideo",
            {
                "video": ["3", 0],
                "filename_prefix": (
                    f"workbench/jobs/{job_id}/flashvsr-{variant}-x{scale}"
                ),
                "format": "mp4",
                "format.codec": "h264",
                "format.codec.encoding": "auto",
            },
        ),
    }
