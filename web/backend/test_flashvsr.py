from __future__ import annotations

import unittest

from .flashvsr import (
    FLASHVSR_SAVE_NODE_ID,
    build_flashvsr_prompt,
    flashvsr_bundle_id,
)


class FlashVsrPromptTests(unittest.TestCase):
    def test_graph_uses_local_weights_only_and_the_chosen_variant(self) -> None:
        graph = build_flashvsr_prompt("job-1", "clip.mp4", "base", 2, 5)
        loader = graph["2"]["inputs"]
        self.assertEqual(graph["2"]["class_type"], "FlashVSRPlusLoader")
        self.assertEqual(loader["mode"], "base")
        self.assertIs(loader["auto_download"], False)
        self.assertEqual(loader["decoder"], "tiny")

    def test_upscale_node_gets_the_scale_the_video_and_keeps_audio(self) -> None:
        graph = build_flashvsr_prompt("job-1", "clip.mp4", "turbo", 4, 5)
        upscale = graph["3"]["inputs"]
        self.assertEqual(graph["1"]["inputs"]["file"], "clip.mp4")
        self.assertEqual(upscale["scale"], 4.0)
        self.assertEqual(upscale["pipe"], ["2", 0])
        self.assertEqual(upscale["video"], ["1", 0])
        self.assertIs(upscale["keep_audio"], True)
        self.assertEqual(graph[FLASHVSR_SAVE_NODE_ID]["class_type"], "SaveVideo")
        self.assertEqual(graph[FLASHVSR_SAVE_NODE_ID]["inputs"]["video"], ["3", 0])

    def test_bundle_id_matches_the_variant(self) -> None:
        self.assertEqual(flashvsr_bundle_id("turbo"), "flashvsr_plus_turbo")
        self.assertEqual(flashvsr_bundle_id("base"), "flashvsr_plus_base")

    def test_rejects_unknown_variant_scale_and_unsafe_job_id(self) -> None:
        for kwargs in (
            {"variant": "v1", "scale": 2},
            {"variant": "turbo", "scale": 3},
            {"variant": "turbo", "scale": 2, "job_id": "../x"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_flashvsr_prompt(
                    kwargs.get("job_id", "j"),
                    "c.mp4",
                    str(kwargs["variant"]),
                    int(kwargs["scale"]),
                )
        with self.assertRaises(ValueError):
            flashvsr_bundle_id("v1")


if __name__ == "__main__":
    unittest.main()
