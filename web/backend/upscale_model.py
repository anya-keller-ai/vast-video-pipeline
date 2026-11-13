from __future__ import annotations

from dataclasses import dataclass

from .workflows import ComfyPrompt, _node, model_filename


@dataclass(frozen=True, slots=True)
class UpscaleModelSpec:
    key: str
    bundle_id: str
    asset_id: str
    scale: int


UPSCALE_MODELS: dict[str, UpscaleModelSpec] = {
    spec.key: spec
    for spec in (
        UpscaleModelSpec(
            "realesrgan_x2plus", "realesrgan_x2plus", "realesrgan_x2plus", 2
        ),
        UpscaleModelSpec(
            "realesrgan_animevideo_v3",
            "realesrgan_animevideo_v3",
            "realesrgan_animevideo_v3",
            4,
        ),
    )
}
UPSCALE_MODEL_SAVE_NODE_ID = "6"


def upscale_model_spec(key: str) -> UpscaleModelSpec:
    spec = UPSCALE_MODELS.get(key)
    if spec is None:
        raise ValueError("Модель апскейла должна быть из списка поддерживаемых.")
    return spec


def build_upscale_model_prompt(
    job_id: str, video_filename: str, key: str
) -> ComfyPrompt:
    spec = upscale_model_spec(key)
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        raise ValueError("Некорректный идентификатор задачи.")
    return {
        "1": _node("LoadVideo", {"file": video_filename}),
        "2": _node("GetVideoComponents", {"video": ["1", 0]}),
        "3": _node("UpscaleModelLoader", {"model_name": model_filename(spec.asset_id)}),
        "4": _node(
            "ImageUpscaleWithModel", {"upscale_model": ["3", 0], "image": ["2", 0]}
        ),
        "5": _node(
            "CreateVideo",
            {
                "images": ["4", 0],
                "fps": ["2", 2],
                "audio": ["2", 1],
                "bit_depth": ["2", 3],
                "color_space": ["2", 4],
                "codec": "none",
            },
        ),
        UPSCALE_MODEL_SAVE_NODE_ID: _node(
            "SaveVideo",
            {
                "video": ["5", 0],
                "filename_prefix": f"workbench/jobs/{job_id}/{key}-x{spec.scale}",
                "format": "mp4",
                "format.codec": "h264",
                "format.codec.encoding": "auto",
            },
        ),
    }
