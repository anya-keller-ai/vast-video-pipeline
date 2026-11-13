from __future__ import annotations

import unittest

from .negpip import (
    NEGATIVE_DEFAULT_STRENGTH,
    NEGATIVE_MAX_CHARS,
    NEGPIP_NODE_ID,
    NO_NEGATIVE,
    NegativePrompt,
)
from .pika import build_pika_ref2va_prompt
from .workflows import build_h3_ref2v_prompt

NEGATIVE = NegativePrompt("red dress, clothes", 2.5)


class NegativePromptValidationTests(unittest.TestCase):
    def test_default_is_disabled_with_documented_strength(self) -> None:
        self.assertFalse(NO_NEGATIVE.active)
        self.assertEqual(NO_NEGATIVE.strength, NEGATIVE_DEFAULT_STRENGTH)
        self.assertFalse(NegativePrompt("   ").active)

    def test_group_uses_negative_weight_syntax(self) -> None:
        self.assertEqual(NEGATIVE.weighted_group, "(red dress, clothes:-2.5)")
        self.assertEqual(
            NegativePrompt(" fog ", 3.0).weighted_group,
            "(fog:-3)",
        )

    def test_rejects_syntax_injection_length_and_bad_strength(self) -> None:
        for text in ("(a:-9)", "a:b", "a@2", "a\nb", "[x]", "{x}"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                _ = NegativePrompt(text)
        with self.assertRaises(ValueError):
            _ = NegativePrompt("x" * (NEGATIVE_MAX_CHARS + 1))
        for strength in (0.4, 5.1, float("nan"), float("inf")):
            with self.subTest(strength=strength), self.assertRaises(ValueError):
                _ = NegativePrompt("fog", strength)


class H3NegPiPGraphTests(unittest.TestCase):
    def build(
        self, turbo: bool, negative: NegativePrompt
    ) -> dict[str, dict[str, object]]:
        graph = build_h3_ref2v_prompt(
            "A scene", "job", ["a.png"], None, 5.0, turbo, 7, negative=negative
        )
        return {key: dict(node) for key, node in graph.items()}

    def test_graph_without_negative_is_unchanged(self) -> None:
        for turbo in (False, True):
            with self.subTest(turbo=turbo):
                baseline = build_h3_ref2v_prompt(
                    "A scene", "job", ["a.png"], None, 5.0, turbo, 7
                )
                explicit = build_h3_ref2v_prompt(
                    "A scene",
                    "job",
                    ["a.png"],
                    None,
                    5.0,
                    turbo,
                    7,
                    negative=NO_NEGATIVE,
                )
                self.assertEqual(baseline, explicit)
                self.assertNotIn(NEGPIP_NODE_ID, baseline)

    def test_base_routes_model_and_clip_through_negpip(self) -> None:
        graph = self.build(False, NEGATIVE)
        node = graph[NEGPIP_NODE_ID]
        self.assertEqual(node["class_type"], "ApplyMiniMaxH3NegPiP")
        self.assertEqual(node["inputs"]["model"], ["5", 0])
        self.assertEqual(node["inputs"]["clip"], ["2", 0])
        self.assertEqual(graph["8"]["inputs"]["model"], [NEGPIP_NODE_ID, 0])
        self.assertEqual(graph["9"]["inputs"]["model"], [NEGPIP_NODE_ID, 0])
        self.assertEqual(graph["7"]["inputs"]["clip"], [NEGPIP_NODE_ID, 1])
        self.assertEqual(
            graph["7"]["inputs"]["prompt"], "A scene\n(red dress, clothes:-2.5)"
        )

    def test_turbo_negpip_wraps_the_lora_model(self) -> None:
        graph = self.build(True, NEGATIVE)
        self.assertEqual(graph[NEGPIP_NODE_ID]["inputs"]["model"], ["6", 0])
        self.assertEqual(graph["8"]["inputs"]["model"], [NEGPIP_NODE_ID, 0])


class PikaNegPiPGraphTests(unittest.TestCase):
    def test_pika_negpip_wraps_the_pika_lora_and_keeps_manual_sigmas(self) -> None:
        graph = build_pika_ref2va_prompt(
            "A scene", "job", ["a.png"], None, 5.0, 7, negative=NEGATIVE
        )
        self.assertEqual(graph[NEGPIP_NODE_ID]["inputs"]["model"], ["6", 0])
        self.assertEqual(graph["8"]["inputs"]["model"], [NEGPIP_NODE_ID, 0])
        self.assertEqual(graph["9"]["class_type"], "ManualSigmas")
        self.assertNotIn("model", graph["9"]["inputs"])
        self.assertEqual(graph["7"]["inputs"]["clip"], [NEGPIP_NODE_ID, 1])
        self.assertTrue(
            str(graph["7"]["inputs"]["prompt"]).endswith("(red dress, clothes:-2.5)")
        )

    def test_pika_graph_without_negative_has_no_negpip_node(self) -> None:
        graph = build_pika_ref2va_prompt("A scene", "job", ["a.png"], None, 5.0, 7)
        self.assertNotIn(NEGPIP_NODE_ID, graph)


if __name__ == "__main__":
    _ = unittest.main()
