from __future__ import annotations

import unittest

from .pika import (
    PIKA_MAX_REFERENCE_IMAGES,
    PIKA_SAMPLER,
    PIKA_SIGMAS,
    build_pika_ref2va_prompt,
)
from .workflows import model_filename


class PikaWorkflowTests(unittest.TestCase):
    def build(self, images: list[str] | None = None) -> dict[str, dict[str, object]]:
        graph = build_pika_ref2va_prompt(
            "A scene", "job-1", images or ["a.png", "b.png"], None, 5.0, 7
        )
        return {key: dict(node) for key, node in graph.items()}

    def test_uses_pika_lora_six_step_schedule_and_lcm_sampler(self) -> None:
        graph = self.build()
        lora = graph["6"]["inputs"]
        self.assertEqual(graph["6"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(lora["lora_name"], model_filename("pika_h3_flex_ref2va_lora"))
        self.assertEqual(graph["8"]["inputs"]["model"], ["6", 0])
        self.assertEqual(graph["9"]["class_type"], "ManualSigmas")
        self.assertEqual(graph["9"]["inputs"]["sigmas"], PIKA_SIGMAS)
        self.assertEqual(len(PIKA_SIGMAS.split(",")), 7)
        self.assertEqual(graph["10"]["inputs"]["sampler_name"], PIKA_SAMPLER)
        self.assertEqual(graph["12"]["inputs"]["sigmas"], ["9", 0])
        self.assertEqual(graph["11"]["inputs"]["noise_seed"], 7)

    def test_no_turbo_lora_and_output_prefix_marks_pika(self) -> None:
        graph = self.build()
        self.assertNotIn("h3_ref2v_turbo", str(graph))
        prefix = graph["16"]["inputs"]["filename_prefix"]
        self.assertEqual(prefix, "workbench/jobs/job-1/pika-ref2va")

    def test_accepts_up_to_twelve_references_and_rejects_more_or_none(self) -> None:
        names = [f"{index}.png" for index in range(PIKA_MAX_REFERENCE_IMAGES)]
        self.build(names)
        with self.assertRaises(ValueError):
            self.build([*names, "extra.png"])
        with self.assertRaises(ValueError):
            build_pika_ref2va_prompt("p", "j", [], None, 5.0, 1)

    def test_passes_audio_references_to_the_shared_reference_node(self) -> None:
        graph = build_pika_ref2va_prompt(
            "<Picture 1> <Audio 1>",
            "j",
            ["a.png"],
            None,
            5.0,
            1,
            reference_audios=["voice.wav"],
        )
        self.assertEqual(graph["50"]["class_type"], "LoadAudio")
        self.assertEqual(graph["7"]["inputs"]["ref_audios.ref_audio_0"], ["50", 0])

    def test_draft_resolution_is_available_in_pika_without_changing_steps(
        self,
    ) -> None:
        standard = build_pika_ref2va_prompt("p", "j", ["a.png"], None, 5.0, 1)
        draft = build_pika_ref2va_prompt(
            "p", "j", ["a.png"], None, 5.0, 1, resolution="draft"
        )
        self.assertEqual(draft["7"]["inputs"]["width"], 608)
        self.assertEqual(draft["7"]["inputs"]["height"], 352)
        self.assertEqual(draft["9"], standard["9"])
        self.assertEqual(draft["6"], standard["6"])

    def test_rejects_empty_prompt_and_bad_seed(self) -> None:
        with self.assertRaises(ValueError):
            build_pika_ref2va_prompt(" ", "j", ["a.png"], None, 5.0, 1)
        with self.assertRaises(ValueError):
            build_pika_ref2va_prompt("p", "j", ["a.png"], None, 5.0, -1)

    def test_accepts_motion_reference_video_with_or_without_images(self) -> None:
        with_video = build_pika_ref2va_prompt("p", "j", ["a.png"], "motion.mp4", 5.0, 1)
        self.assertEqual(with_video["40"]["inputs"]["file"], "motion.mp4")
        video_only = build_pika_ref2va_prompt("p", "j", [], "motion.mp4", 5.0, 1)
        self.assertEqual(video_only["40"]["class_type"], "LoadVideo")

    def test_ref_image_size_max_is_passed_and_validated(self) -> None:
        graph = build_pika_ref2va_prompt(
            "p", "j", ["a.png"], None, 5.0, 1, ref_image_size="max"
        )
        self.assertEqual(graph["7"]["inputs"]["ref_image_size"], "max")
        self.assertEqual(self.build()["7"]["inputs"]["ref_image_size"], "match")
        with self.assertRaises(ValueError):
            build_pika_ref2va_prompt(
                "p", "j", ["a.png"], None, 5.0, 1, ref_image_size="huge"
            )
