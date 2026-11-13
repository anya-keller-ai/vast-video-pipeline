from __future__ import annotations

import unittest

from .negpip import NEGPIP_NODE_ID, has_negative_weights
from .pika import build_pika_ref2va_prompt
from .workflows import build_h3_ref2v_prompt

SCENE = "A scene.\n<d>[Russian] Привет \\(S1\\)</d>"
WITH_NEGATIVE = f"{SCENE}\n(lit candle, candle flame, burning wick:-3)"


class NegativeWeightDetectionTests(unittest.TestCase):
    def test_detects_negative_weight_groups(self) -> None:
        for prompt in (
            "(fog:-3)",
            "text (red dress, clothes:-2.5) text",
            "(neon:-1.5@2.5-4.0)",
            "(neon:-1.2@v2.5-)",
            "(fog: -3)",
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(has_negative_weights(prompt))

    def test_ignores_plain_text_positive_weights_and_escaped_parentheses(self) -> None:
        for prompt in (
            "A scene without groups",
            "(fog:3)",
            "(fog:1.3)",
            "Veigar \\(S1\\) speaks \\(a:-3\\)",
            "ratio 16:9 or -3 degrees",
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(has_negative_weights(prompt))


class H3NegPiPGraphTests(unittest.TestCase):
    def build(self, turbo: bool, prompt: str) -> dict[str, dict[str, object]]:
        graph = build_h3_ref2v_prompt(prompt, "job", ["a.png"], None, 5.0, turbo, 7)
        return {key: dict(node) for key, node in graph.items()}

    def test_graph_without_negative_group_has_no_negpip_node(self) -> None:
        for turbo in (False, True):
            with self.subTest(turbo=turbo):
                graph = self.build(turbo, SCENE)
                self.assertNotIn(NEGPIP_NODE_ID, graph)
                self.assertEqual(graph["7"]["inputs"]["clip"], ["2", 0])

    def test_base_routes_model_and_clip_through_negpip(self) -> None:
        graph = self.build(False, WITH_NEGATIVE)
        node = graph[NEGPIP_NODE_ID]
        self.assertEqual(node["class_type"], "ApplyMiniMaxH3NegPiP")
        self.assertEqual(node["inputs"]["model"], ["5", 0])
        self.assertEqual(node["inputs"]["clip"], ["2", 0])
        self.assertIs(node["inputs"]["protect_text_rows"], True)
        self.assertIs(node["inputs"]["apply_positive_weights"], False)
        self.assertEqual(graph["8"]["inputs"]["model"], [NEGPIP_NODE_ID, 0])
        self.assertEqual(graph["9"]["inputs"]["model"], [NEGPIP_NODE_ID, 0])
        self.assertEqual(graph["7"]["inputs"]["clip"], [NEGPIP_NODE_ID, 1])
        self.assertEqual(graph["7"]["inputs"]["prompt"], WITH_NEGATIVE)

    def test_turbo_negpip_wraps_the_lora_model(self) -> None:
        graph = self.build(True, WITH_NEGATIVE)
        self.assertEqual(graph[NEGPIP_NODE_ID]["inputs"]["model"], ["6", 0])
        self.assertEqual(graph["8"]["inputs"]["model"], [NEGPIP_NODE_ID, 0])


class PikaNegPiPGraphTests(unittest.TestCase):
    def test_pika_negpip_wraps_the_pika_lora_and_keeps_manual_sigmas(self) -> None:
        graph = build_pika_ref2va_prompt(WITH_NEGATIVE, "job", ["a.png"], None, 5.0, 7)
        self.assertEqual(graph["6"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(graph[NEGPIP_NODE_ID]["inputs"]["model"], ["6", 0])
        self.assertEqual(graph["8"]["inputs"]["model"], [NEGPIP_NODE_ID, 0])
        self.assertEqual(graph["9"]["class_type"], "ManualSigmas")
        self.assertNotIn("model", graph["9"]["inputs"])
        self.assertEqual(graph["7"]["inputs"]["clip"], [NEGPIP_NODE_ID, 1])

    def test_pika_graph_without_negative_group_has_no_negpip_node(self) -> None:
        graph = build_pika_ref2va_prompt(SCENE, "job", ["a.png"], None, 5.0, 7)
        self.assertNotIn(NEGPIP_NODE_ID, graph)
        self.assertEqual(graph["8"]["inputs"]["model"], ["6", 0])


if __name__ == "__main__":
    _ = unittest.main()
