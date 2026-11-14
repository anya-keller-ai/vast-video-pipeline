from __future__ import annotations

import unittest

from .h3_guides import MAX_GUIDES, GuideFrame, validate_guides
from .pika import build_pika_ref2va_prompt
from .workflows import build_h3_ref2v_prompt

FRAMES = 124  # 5.167 s at 24 fps
GUIDES = [GuideFrame("a.png", 0), GuideFrame("b.png", 60), GuideFrame("c.png", 123)]


class GuideValidationTests(unittest.TestCase):
    def test_accepts_up_to_the_limit_within_the_clip(self) -> None:
        validate_guides(
            [GuideFrame(f"{i}.png", i * 10) for i in range(MAX_GUIDES)], FRAMES
        )
        validate_guides([], FRAMES)

    def test_rejects_too_many_out_of_range_and_duplicate_frames(self) -> None:
        too_many = [GuideFrame(f"{i}.png", i) for i in range(MAX_GUIDES + 1)]
        for guides in (
            too_many,
            [GuideFrame("a.png", -1)],
            [GuideFrame("a.png", FRAMES)],
            [GuideFrame("a.png", 5), GuideFrame("b.png", 5)],
        ):
            with self.subTest(guides=guides), self.assertRaises(ValueError):
                validate_guides(guides, FRAMES)


class H3GuideGraphTests(unittest.TestCase):
    def build(
        self, turbo: bool, guides: list[GuideFrame]
    ) -> dict[str, dict[str, object]]:
        graph = build_h3_ref2v_prompt(
            "A scene", "job", ["r.png"], None, FRAMES / 24, turbo, 7, guides=guides
        )
        return {key: dict(node) for key, node in graph.items()}

    def test_graph_without_guides_is_unchanged(self) -> None:
        baseline = build_h3_ref2v_prompt(
            "A scene", "job", ["r.png"], None, 5.0, False, 7
        )
        self.assertEqual(baseline["8"]["inputs"]["conditioning"], ["7", 0])
        self.assertNotIn("70", baseline)
        self.assertNotIn("80", baseline)

    def test_guides_are_chained_after_the_reference_conditioning(self) -> None:
        graph = self.build(False, GUIDES)
        expected_inputs = {"70": ["7", 0], "71": ["70", 0], "72": ["71", 0]}
        for index, (node_id, positive) in enumerate(expected_inputs.items()):
            node = graph[node_id]
            inputs = node["inputs"]
            self.assertEqual(node["class_type"], "MiniMaxH3AddGuide")
            self.assertEqual(inputs["positive"], positive)
            self.assertEqual(inputs["latent"], ["7", 1])
            self.assertEqual(inputs["vae"], ["3", 0])
            self.assertEqual(inputs["image"], [str(80 + index), 0])
            self.assertEqual(inputs["frame_idx"], GUIDES[index].frame)
            self.assertEqual(graph[str(80 + index)]["class_type"], "LoadImage")
            self.assertEqual(
                graph[str(80 + index)]["inputs"]["image"], GUIDES[index].filename
            )
        self.assertEqual(graph["8"]["inputs"]["conditioning"], ["72", 0])
        self.assertEqual(graph["12"]["inputs"]["latent_image"], ["7", 1])

    def test_turbo_keeps_its_lora_and_guides_together(self) -> None:
        graph = self.build(True, GUIDES[:1])
        self.assertEqual(graph["8"]["inputs"]["model"], ["6", 0])
        self.assertEqual(graph["8"]["inputs"]["conditioning"], ["70", 0])

    def test_guide_frame_is_checked_against_the_requested_duration(self) -> None:
        with self.assertRaises(ValueError):
            build_h3_ref2v_prompt(
                "A scene",
                "job",
                [],
                None,
                5.0,
                False,
                7,
                guides=[GuideFrame("a.png", FRAMES)],
            )


class PikaGuideGraphTests(unittest.TestCase):
    def test_pika_guides_keep_its_lora_and_sigmas(self) -> None:
        graph = build_pika_ref2va_prompt(
            "A scene", "job", ["r.png"], None, FRAMES / 24, 7, guides=GUIDES[:2]
        )
        self.assertEqual(graph["8"]["inputs"]["conditioning"], ["71", 0])
        self.assertEqual(graph["8"]["inputs"]["model"], ["6", 0])
        self.assertEqual(graph["9"]["class_type"], "ManualSigmas")
        self.assertEqual(graph["70"]["inputs"]["frame_idx"], 0)


if __name__ == "__main__":
    _ = unittest.main()
