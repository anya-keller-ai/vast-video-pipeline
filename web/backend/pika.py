from __future__ import annotations

from collections.abc import Sequence

from .negpip import NO_NEGATIVE, NegativePrompt, apply_negpip
from .workflows import (
    H3_DEFAULT_RESOLUTION,
    ComfyPrompt,
    _node,  # pyright: ignore[reportPrivateUsage]
    build_h3_ref2v_prompt,
    model_filename,
)

PIKA_MAX_REFERENCE_IMAGES = 9  # limit of MiniMaxH3ReferenceToVideo ref_images
PIKA_REF_IMAGE_SIZES = ("match", "max")
PIKA_LORA_ASSET = "pika_h3_flex_ref2va_lora"
PIKA_SAMPLER = "lcm"
# Exact six-step schedule from the official Pika H3 Flex ComfyUI template.
PIKA_SIGMAS = "1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0"
PIKA_OUTPUT_PREFIX = "pika-ref2va"
PIKA_SAVE_NODE_ID = "16"


def build_pika_ref2va_prompt(
    prompt: str,
    job_id: str,
    reference_images: Sequence[str],
    reference_video: str | None,
    duration_seconds: float,
    seed: int,
    ref_image_size: str = "match",
    reference_audios: Sequence[str] = (),
    resolution: str = H3_DEFAULT_RESOLUTION,
    negative: NegativePrompt = NO_NEGATIVE,
) -> ComfyPrompt:
    """Build the Pika H3 Flex Ref2VA graph on top of the shared H3 base graph."""
    if not reference_images and reference_video is None:
        raise ValueError("Pika Ref2VA требует изображение или видео-референс.")
    if ref_image_size not in PIKA_REF_IMAGE_SIZES:
        raise ValueError("ref_image_size должен быть match или max.")
    graph = build_h3_ref2v_prompt(
        prompt,
        job_id,
        reference_images,
        reference_video,
        duration_seconds,
        False,
        seed,
        max_reference_images=PIKA_MAX_REFERENCE_IMAGES,
        ref_image_size=ref_image_size,
        reference_audios=reference_audios,
        resolution=resolution,
    )
    graph["6"] = _node(
        "LoraLoaderModelOnly",
        {
            "model": ["5", 0],
            "lora_name": model_filename(PIKA_LORA_ASSET),
            "strength_model": 1.0,
        },
    )
    graph["8"]["inputs"]["model"] = ["6", 0]
    graph["9"] = _node("ManualSigmas", {"sigmas": PIKA_SIGMAS})
    graph["10"]["inputs"]["sampler_name"] = PIKA_SAMPLER
    graph[PIKA_SAVE_NODE_ID]["inputs"]["filename_prefix"] = (
        f"workbench/jobs/{job_id}/{PIKA_OUTPUT_PREFIX}"
    )
    # After the LoRA swap, so NegPiP wraps the final model.
    apply_negpip(graph, negative)
    return graph
