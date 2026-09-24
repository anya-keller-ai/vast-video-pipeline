"""Stage MiniMax H3 video and audio latents across separate ComfyUI runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import folder_paths
import torch

_LATENT_SUFFIX = ".h3latent.pt"


def _nested_parts(samples: dict[str, Any]) -> tuple[torch.Tensor, torch.Tensor]:
    raw_samples = samples.get("samples")
    if not getattr(raw_samples, "is_nested", False):
        raise ValueError("MiniMax H3 latent must contain a nested video/audio tensor")

    unbind = getattr(raw_samples, "unbind", None)
    if not callable(unbind):
        raise TypeError("Nested H3 latent does not support unbind()")
    parts = tuple(unbind())
    if len(parts) != 2:
        raise ValueError(f"Expected two H3 latent parts, got {len(parts)}")
    video, audio = parts
    if not isinstance(video, torch.Tensor) or not isinstance(audio, torch.Tensor):
        raise TypeError("H3 latent parts must be torch tensors")
    return video.detach().cpu(), audio.detach().cpu()


def _latent_files() -> list[str]:
    output_root = Path(folder_paths.get_output_directory()).resolve()
    return sorted(
        str(path.resolve().relative_to(output_root))
        for path in output_root.rglob(f"*{_LATENT_SUFFIX}")
        if path.is_file()
    )


def _safe_output_path(filename: str) -> Path:
    output_root = Path(folder_paths.get_output_directory()).resolve()
    path = (output_root / filename).resolve()
    if path.parent != output_root and output_root not in path.parents:
        raise ValueError("Latent file must stay inside the ComfyUI output directory")
    return path


class SaveH3StagedLatent:
    """Persist nested MiniMax H3 video/audio latents as independent tensors."""

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, dict[str, tuple[str, dict[str, str]]]]:
        return {
            "required": {
                "samples": ("LATENT", {}),
                "filename_prefix": ("STRING", {"default": "h3/staged"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filename",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "latent/staging"

    def save(self, samples: dict[str, Any], filename_prefix: str) -> dict[str, Any]:
        video, audio = _nested_parts(samples)
        output_root = Path(folder_paths.get_output_directory())
        folder, filename, counter, _, _ = folder_paths.get_save_image_path(
            filename_prefix,
            str(output_root),
        )
        path = Path(folder) / f"{filename}_{counter:05d}{_LATENT_SUFFIX}"
        torch.save({"video": video, "audio": audio}, path)
        relative_path = str(path.resolve().relative_to(output_root.resolve()))
        return {"ui": {"text": [relative_path]}, "result": (relative_path,)}


class LoadH3StagedLatent:
    """Load staged MiniMax H3 video and audio latents for separate decoding."""

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, dict[str, list[list[str]]]]:
        return {"required": {"filename": [_latent_files()]}}

    RETURN_TYPES = ("LATENT", "LATENT")
    RETURN_NAMES = ("video_samples", "audio_samples")
    FUNCTION = "load"
    CATEGORY = "latent/staging"

    def load(
        self, filename: str
    ) -> tuple[dict[str, torch.Tensor], dict[str, torch.Tensor]]:
        path = _safe_output_path(filename)
        payload = torch.load(path, map_location="cpu", weights_only=True)
        video = payload.get("video")
        audio = payload.get("audio")
        if not isinstance(video, torch.Tensor) or not isinstance(audio, torch.Tensor):
            raise ValueError("Staged latent file has invalid video/audio tensors")
        return ({"samples": video}, {"samples": audio})


NODE_CLASS_MAPPINGS = {
    "SaveH3StagedLatent": SaveH3StagedLatent,
    "LoadH3StagedLatent": LoadH3StagedLatent,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SaveH3StagedLatent": "Save H3 Staged Latent",
    "LoadH3StagedLatent": "Load H3 Staged Latent",
}
