from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .workflows import ComfyPrompt

NEGPIP_NODE_ID = "60"
NEGPIP_NODE_CLASS = "ApplyMiniMaxH3NegPiP"
NEGATIVE_MAX_CHARS = 300
NEGATIVE_MIN_STRENGTH = 0.5
NEGATIVE_MAX_STRENGTH = 5.0  # upper bound recommended by the node author
NEGATIVE_DEFAULT_STRENGTH = 3.0
# Characters of the NegPiP weight syntax; users must not be able to inject them.
_FORBIDDEN_CHARS = frozenset("()[]{}:@\n\r\t")

# Ids of the shared H3 graph nodes that NegPiP is wired into.
_PROMPT_NODE_ID = "7"
_GUIDER_NODE_ID = "8"
_SCHEDULER_NODE_ID = "9"


@dataclass(frozen=True, slots=True)
class NegativePrompt:
    """Concepts to subtract from the result with NegPiP (empty text = disabled)."""

    text: str = ""
    strength: float = NEGATIVE_DEFAULT_STRENGTH

    def __post_init__(self) -> None:
        if len(self.text) > NEGATIVE_MAX_CHARS:
            raise ValueError(
                f"Негативный промт: не больше {NEGATIVE_MAX_CHARS} символов."
            )
        if any(char in _FORBIDDEN_CHARS for char in self.text):
            raise ValueError(
                "Негативный промт: нельзя использовать скобки, двоеточие, @ и "
                "переносы строк."
            )
        if (
            not math.isfinite(self.strength)
            or not NEGATIVE_MIN_STRENGTH <= self.strength <= NEGATIVE_MAX_STRENGTH
        ):
            raise ValueError(
                "Сила негативного промта должна быть от "
                f"{NEGATIVE_MIN_STRENGTH:g} до {NEGATIVE_MAX_STRENGTH:g}."
            )

    @property
    def active(self) -> bool:
        return bool(self.text.strip())

    @property
    def weighted_group(self) -> str:
        return f"({self.text.strip()}:-{self.strength:g})"


NO_NEGATIVE = NegativePrompt()


def apply_negpip(graph: ComfyPrompt, negative: NegativePrompt) -> None:
    """Route model and CLIP through NegPiP and append the negative group.

    Does nothing when the negative prompt is empty, so graphs without it stay
    byte-identical to the previous ones.
    """
    if not negative.active:
        return
    prompt_inputs = graph[_PROMPT_NODE_ID]["inputs"]
    guider_inputs = graph[_GUIDER_NODE_ID]["inputs"]
    graph[NEGPIP_NODE_ID] = {
        "class_type": NEGPIP_NODE_CLASS,
        "inputs": {
            "model": guider_inputs["model"],
            "clip": prompt_inputs["clip"],
            "value_strength": 1.0,
            # Keep any parentheses in the main prompt unweighted.
            "apply_positive_weights": False,
        },
    }
    guider_inputs["model"] = [NEGPIP_NODE_ID, 0]
    scheduler_inputs = graph[_SCHEDULER_NODE_ID]["inputs"]
    if "model" in scheduler_inputs:
        scheduler_inputs["model"] = [NEGPIP_NODE_ID, 0]
    prompt_inputs["clip"] = [NEGPIP_NODE_ID, 1]
    prompt_inputs["prompt"] = f"{prompt_inputs['prompt']}\n{negative.weighted_group}"
