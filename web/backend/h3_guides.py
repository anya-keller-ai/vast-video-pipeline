from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .workflows import ComfyPrompt

GUIDE_NODE_CLASS = "MiniMaxH3AddGuide"
# The node itself has no limit; the official template uses four anchor images.
MAX_GUIDES = 4

# Node ids: guide nodes 70+, their LoadImage inputs 80+ (ids 20-29, 40-41, 50-52
# and 60 belong to references, reference video, audio and NegPiP).
_GUIDE_NODE_BASE_ID = 70
_GUIDE_IMAGE_BASE_ID = 80

# Ids of the shared H3 graph nodes the guides are wired into.
_PROMPT_NODE_ID = "7"
_VAE_NODE_ID = "3"
_GUIDER_NODE_ID = "8"


@dataclass(frozen=True, slots=True)
class GuideRequest:
    """An uploaded image and the frame of the clip it must stand on."""

    image_id: str
    frame: int


@dataclass(frozen=True, slots=True)
class GuideFrame:
    """A guide image already placed in ComfyUI's input directory."""

    filename: str
    frame: int


def validate_guides(guides: Sequence[GuideFrame], frame_count: int) -> None:
    if len(guides) > MAX_GUIDES:
        raise ValueError(f"Опорных кадров не больше {MAX_GUIDES}.")
    frames = [guide.frame for guide in guides]
    for frame in frames:
        if not 0 <= frame < frame_count:
            raise ValueError(
                f"Опорный кадр {frame} вне клипа: допустимо 0–{frame_count - 1}."
            )
    if len(set(frames)) != len(frames):
        raise ValueError("Два опорных изображения не могут стоять на одном кадре.")


def apply_guides(
    graph: ComfyPrompt, guides: Sequence[GuideFrame], frame_count: int
) -> None:
    """Chain MiniMaxH3AddGuide nodes after the reference conditioning.

    Does nothing without guides, so graphs without them stay unchanged.
    """
    validate_guides(guides, frame_count)
    previous_output = [_PROMPT_NODE_ID, 0]
    for index, guide in enumerate(guides):
        node_id = str(_GUIDE_NODE_BASE_ID + index)
        image_id = str(_GUIDE_IMAGE_BASE_ID + index)
        graph[image_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": guide.filename},
        }
        graph[node_id] = {
            "class_type": GUIDE_NODE_CLASS,
            "inputs": {
                "positive": previous_output,
                "vae": [_VAE_NODE_ID, 0],
                "latent": [_PROMPT_NODE_ID, 1],
                "image": [image_id, 0],
                "frame_idx": guide.frame,
            },
        }
        previous_output = [node_id, 0]
    if guides:
        graph[_GUIDER_NODE_ID]["inputs"]["conditioning"] = previous_output
