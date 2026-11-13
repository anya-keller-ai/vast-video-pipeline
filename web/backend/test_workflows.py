from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import cast

from .workflows import (
    H3_GENERATION_CONFIG,
    H3_MAX_GENERATION_SECONDS,
    H3_MIN_GENERATION_SECONDS,
    SEEDVR2_MODEL,
    SEEDVR2_MODEL_ASSETS,
    build_h3_ref2v_prompt,
    build_seedvr2_prompt,
    h3_frame_count_for_duration,
    model_filename,
    seedvr2_target_resolution,
)


class H3WorkflowTests(unittest.TestCase):
    def test_builds_turbo_reference_workflow_at_fixed_production_resolution(
        self,
    ) -> None:
        prompt = build_h3_ref2v_prompt(
            "A cinematic scene with <Picture 1> and <Video 1>",
            "job-1",
            ["image-1.png", "image-2.png"],
            "reference.mp4",
            7.0,
            True,
            42,
        )

        self.assertEqual(prompt["1"]["class_type"], "UNETLoaderDisTorch2MultiGPU")
        self.assertEqual(
            prompt["1"]["inputs"]["unet_name"],
            "minimax_h3_ref2va_int8_convrot.safetensors",
        )
        self.assertEqual(
            prompt["1"]["inputs"]["expert_mode_allocations"],
            H3_GENERATION_CONFIG.turbo.allocation,
        )
        self.assertEqual(prompt["6"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(
            prompt["6"]["inputs"]["lora_name"], model_filename("h3_ref2v_turbo")
        )
        self.assertEqual(prompt["7"]["class_type"], "MiniMaxH3ReferenceToVideo")
        self.assertEqual(prompt["7"]["inputs"]["ref_images.ref_image_0"], ["21", 0])
        self.assertEqual(prompt["7"]["inputs"]["ref_images.ref_image_1"], ["22", 0])
        self.assertEqual(prompt["7"]["inputs"]["ref_videos.ref_video_0"], ["41", 0])
        self.assertEqual(
            prompt["7"]["inputs"]["ref_video_audios.ref_video_audio_0"], ["41", 1]
        )
        self.assertEqual(prompt["7"]["inputs"]["width"], 1344)
        self.assertEqual(prompt["7"]["inputs"]["height"], 768)
        self.assertEqual(prompt["7"]["inputs"]["length"], 175)
        self.assertEqual(prompt["8"]["inputs"]["model"], ["6", 0])
        self.assertEqual(prompt["9"]["inputs"]["steps"], 4)
        self.assertEqual(prompt["15"]["inputs"]["fps"], 24.0)
        self.assertEqual(prompt["16"]["inputs"]["format"], "mp4")
        self.assertEqual(prompt["16"]["inputs"]["format.codec"], "h264")
        self.assertEqual(prompt["16"]["inputs"]["format.codec.encoding"], "auto")
        self.assertNotIn("codec", prompt["16"]["inputs"])

    def test_uses_base_model_without_lora_and_with_twenty_steps(self) -> None:
        prompt = build_h3_ref2v_prompt("scene", "job-2", [], None, 5.0, False, 42)

        self.assertNotIn("6", prompt)
        self.assertEqual(prompt["7"]["inputs"]["length"], 124)
        self.assertEqual(prompt["8"]["inputs"]["model"], ["5", 0])
        self.assertEqual(prompt["9"]["inputs"]["model"], ["5", 0])
        self.assertEqual(prompt["9"]["inputs"]["steps"], 20)
        self.assertEqual(prompt["10"]["inputs"]["sampler_name"], "res_multistep")

    def test_supports_nine_reference_images(self) -> None:
        images = [f"image-{index}.png" for index in range(1, 10)]
        prompt = build_h3_ref2v_prompt(
            "<Picture 9>", "job-3", images, None, 5.0, True, 1
        )

        self.assertEqual(prompt["7"]["inputs"]["ref_images.ref_image_8"], ["29", 0])
        self.assertNotIn("40", prompt)

    def test_rejects_more_than_nine_reference_images(self) -> None:
        with self.assertRaisesRegex(ValueError, "9 изображений"):
            _ = build_h3_ref2v_prompt(
                "scene", "job-4", ["image.png"] * 10, None, 5.0, True, 1
            )

    def test_wires_standalone_audio_references_through_load_audio_nodes(self) -> None:
        prompt = build_h3_ref2v_prompt(
            "<Audio 1> <Audio 2>",
            "job-5",
            [],
            None,
            5.0,
            True,
            1,
            reference_audios=["voice.wav", "music.mp3"],
        )

        self.assertEqual(prompt["50"]["class_type"], "LoadAudio")
        self.assertEqual(prompt["50"]["inputs"]["audio"], "voice.wav")
        self.assertEqual(prompt["7"]["inputs"]["ref_audios.ref_audio_0"], ["50", 0])
        self.assertEqual(prompt["7"]["inputs"]["ref_audios.ref_audio_1"], ["51", 0])
        self.assertNotIn("52", prompt)

    def test_rejects_more_than_three_audio_references(self) -> None:
        with self.assertRaisesRegex(ValueError, "3 аудио"):
            _ = build_h3_ref2v_prompt(
                "scene", "job-6", [], None, 5.0, True, 1, reference_audios=["a.wav"] * 4
            )

    def test_snaps_requested_duration_up_to_model_frame_grid(self) -> None:
        self.assertEqual(h3_frame_count_for_duration(H3_MIN_GENERATION_SECONDS), 124)
        self.assertEqual(h3_frame_count_for_duration(7.0), 175)
        self.assertEqual(h3_frame_count_for_duration(H3_MAX_GENERATION_SECONDS), 362)
        self.assertEqual(h3_frame_count_for_duration(5.5), 141)

    def test_draft_resolution_changes_only_the_frame_size(self) -> None:
        standard = build_h3_ref2v_prompt("scene", "j", [], None, 5.0, True, 1)
        draft = build_h3_ref2v_prompt(
            "scene", "j", [], None, 5.0, True, 1, resolution="draft"
        )

        self.assertEqual(standard["7"]["inputs"]["width"], 1344)
        self.assertEqual(standard["7"]["inputs"]["height"], 768)
        self.assertEqual(draft["7"]["inputs"]["width"], 608)
        self.assertEqual(draft["7"]["inputs"]["height"], 352)
        self.assertEqual(
            draft["7"]["inputs"]["length"], standard["7"]["inputs"]["length"]
        )
        self.assertEqual(draft["9"], standard["9"])

    def test_rejects_unknown_resolution(self) -> None:
        with self.assertRaisesRegex(ValueError, "standard или draft"):
            _ = build_h3_ref2v_prompt(
                "scene", "j", [], None, 5.0, True, 1, resolution="4k"
            )

    def test_every_frame_grid_duration_maps_to_itself(self) -> None:
        for frames in range(124, 363, 17):
            with self.subTest(frames=frames):
                self.assertEqual(h3_frame_count_for_duration(frames / 24), frames)

    def test_generation_length_does_not_depend_on_reference_video_duration(
        self,
    ) -> None:
        without_reference = build_h3_ref2v_prompt(
            "scene", "job-5", [], None, 8.0, True, 1
        )
        with_reference = build_h3_ref2v_prompt(
            "scene", "job-6", [], "reference.mp4", 8.0, True, 1
        )

        self.assertEqual(
            without_reference["7"]["inputs"]["length"],
            with_reference["7"]["inputs"]["length"],
        )

    def test_rejects_generation_durations_outside_range_or_non_finite(self) -> None:
        for duration in (4.99, 15.1, float("nan"), float("inf")):
            with (
                self.subTest(duration=duration),
                self.assertRaises(ValueError),
            ):
                _ = h3_frame_count_for_duration(duration)


class SeedVR2WorkflowTests(unittest.TestCase):
    def test_builds_7b_sharp_x2_workflow_and_preserves_audio(self) -> None:
        prompt = build_seedvr2_prompt("job-8", "source.mp4", 2, 1_080)

        self.assertEqual(prompt["3"]["inputs"]["model"], SEEDVR2_MODEL)
        self.assertEqual(prompt["5"]["inputs"]["resolution"], 2_160)
        self.assertEqual(prompt["5"]["inputs"]["batch_size"], 1)
        self.assertEqual(prompt["6"]["inputs"]["audio"], ["2", 1])
        self.assertEqual(prompt["7"]["inputs"]["format"], "mp4")
        self.assertEqual(prompt["7"]["inputs"]["format.codec"], "h264")
        self.assertEqual(prompt["7"]["inputs"]["format.codec.encoding"], "auto")
        self.assertNotIn("codec", prompt["7"]["inputs"])

    def test_builds_x4_workflow_with_single_frame_batches(self) -> None:
        prompt = build_seedvr2_prompt("job-9", "source.mp4", 4, 1_080)

        self.assertEqual(prompt["5"]["inputs"]["resolution"], 4_320)
        self.assertEqual(prompt["5"]["inputs"]["batch_size"], 1)

    def test_static_example_uses_single_frame_batches(self) -> None:
        example_path = (
            Path(__file__).resolve().parents[2]
            / "workflows"
            / "seedvr2-sharp-video-example.json"
        )
        raw_workflow = cast(
            object, json.loads(example_path.read_text(encoding="utf-8"))
        )
        if not isinstance(raw_workflow, dict):
            self.fail("SeedVR2 example must be a JSON object.")
        workflow = cast(dict[str, object], raw_workflow)
        raw_nodes = workflow.get("nodes")
        if not isinstance(raw_nodes, list):
            self.fail("SeedVR2 example must contain a nodes list.")

        batch_sizes: list[object] = []
        for raw_node in cast(list[object], raw_nodes):
            if not isinstance(raw_node, dict):
                continue
            node = cast(dict[str, object], raw_node)
            if node.get("type") != "SeedVR2VideoUpscaler":
                continue
            raw_values = node.get("widgets_values")
            if isinstance(raw_values, list):
                values = cast(list[object], raw_values)
                if len(values) > 4:
                    batch_sizes.append(values[4])

        self.assertEqual(batch_sizes, [1])

    def test_rounds_target_resolution_to_even_pixel_count(self) -> None:
        self.assertEqual(seedvr2_target_resolution(721, 2), 1_442)

    def test_rejects_invalid_scale_and_excessive_resolution(self) -> None:
        with self.assertRaisesRegex(ValueError, "x2 и x4"):
            _ = seedvr2_target_resolution(1_080, 3)
        with self.assertRaisesRegex(ValueError, "16384"):
            _ = seedvr2_target_resolution(4_100, 4)


if __name__ == "__main__":
    _ = unittest.main()


class SeedVr2ModelSelectionTests(unittest.TestCase):
    def test_each_model_key_uses_its_own_locked_file(self) -> None:
        names = set()
        for key, asset_id in SEEDVR2_MODEL_ASSETS.items():
            prompt = build_seedvr2_prompt("j", "v.mp4", 2, 1_080, model_key=key)
            self.assertEqual(prompt["3"]["inputs"]["model"], model_filename(asset_id))
            self.assertEqual(prompt["3"]["inputs"]["blocks_to_swap"], 0)
            names.add(prompt["3"]["inputs"]["model"])
        self.assertEqual(len(names), 3)

    def test_default_is_sharp_7b_and_unknown_key_is_rejected(self) -> None:
        default = build_seedvr2_prompt("j", "v.mp4", 2, 1_080)
        self.assertEqual(default["3"]["inputs"]["model"], SEEDVR2_MODEL)
        with self.assertRaises(ValueError):
            build_seedvr2_prompt("j", "v.mp4", 2, 1_080, model_key="13b")


class SeedVr2BatchTests(unittest.TestCase):
    def test_batch_size_and_overlap_are_passed(self) -> None:
        prompt = build_seedvr2_prompt(
            "j", "v.mp4", 2, 1_080, batch_size=5, temporal_overlap=2
        )
        self.assertEqual(prompt["5"]["inputs"]["batch_size"], 5)
        self.assertEqual(prompt["5"]["inputs"]["temporal_overlap"], 2)

    def test_rejects_invalid_batch_size_and_overlap(self) -> None:
        for batch, overlap in ((2, 0), (0, 0), (37, 0), (5, 5), (5, -1), (1, 1)):
            with (
                self.subTest(batch=batch, overlap=overlap),
                self.assertRaises(ValueError),
            ):
                build_seedvr2_prompt(
                    "j", "v.mp4", 2, 1_080, batch_size=batch, temporal_overlap=overlap
                )
