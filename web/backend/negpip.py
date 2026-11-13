from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .workflows import ComfyPrompt

NEGPIP_NODE_ID = "60"
NEGPIP_NODE_CLASS = "ApplyMiniMaxH3NegPiP"

# NegPiP syntax: an unescaped group "(text:-weight)" with an optional "@time"
# range. Escaped parentheses (\( \)) are literal text and never a weight group.
_NEGATIVE_WEIGHT_GROUP = re.compile(
    r"(?<!\\)\([^()\\]*:\s*-\s*\d+(?:\.\d+)?(?:@[^()\\]*)?\)"
)

# Ids of the shared H3 graph nodes that NegPiP is wired into.
_PROMPT_NODE_ID = "7"
_GUIDER_NODE_ID = "8"
_SCHEDULER_NODE_ID = "9"


def has_negative_weights(prompt: str) -> bool:
    """Tell whether the prompt carries a NegPiP group such as `(fog:-3)`."""
    return _NEGATIVE_WEIGHT_GROUP.search(prompt) is not None


def apply_negpip(graph: ComfyPrompt) -> None:
    """Route model and CLIP through NegPiP when the prompt has a negative group.

    Does nothing otherwise, so graphs without negative weights stay identical to
    the previous ones (without the node ComfyUI would read `(word:-1)` as text).
    """
    prompt_inputs = graph[_PROMPT_NODE_ID]["inputs"]
    prompt = prompt_inputs["prompt"]
    if not isinstance(prompt, str) or not has_negative_weights(prompt):
        return
    guider_inputs = graph[_GUIDER_NODE_ID]["inputs"]
    graph[NEGPIP_NODE_ID] = {
        "class_type": NEGPIP_NODE_CLASS,
        "inputs": {
            "model": guider_inputs["model"],
            "clip": prompt_inputs["clip"],
            "value_strength": 1.0,
            # Keep any other weights in the prompt unscaled.
            "apply_positive_weights": False,
            # Subtract only from the generated rows: untimed negative weights
            # otherwise tend to invert (the concept gets stronger).
            "protect_text_rows": True,
        },
    }
    guider_inputs["model"] = [NEGPIP_NODE_ID, 0]
    scheduler_inputs = graph[_SCHEDULER_NODE_ID]["inputs"]
    if "model" in scheduler_inputs:
        scheduler_inputs["model"] = [NEGPIP_NODE_ID, 0]
    prompt_inputs["clip"] = [NEGPIP_NODE_ID, 1]
