from __future__ import annotations

import unittest
from pathlib import Path

from .frame_interpolation import (
    INTERPOLATION_BUNDLE_ID,
    INTERPOLATION_MODEL_ASSETS,
    INTERPOLATION_SAVE_NODE_ID,
    build_interpolation_prompt,
    choose_chunk_frames,
)
from .models import load_model_registry

MANIFEST = Path(__file__).resolve().parents[2] / "config" / "models.lock.json"


class InterpolationPromptTests(unittest.TestCase):
    def test_graph_streams_the_file_through_the_chunked_node(self) -> None:
        graph = build_interpolation_prompt(
            "job-1", "clip.mp4", "rife_4_26", 2, 1344, 768
        )

        loader = graph["1"]
        self.assertEqual(loader["class_type"], "FrameInterpolationModelLoader")
        self.assertEqual(loader["inputs"]["model_name"], "rife_v4.26.safetensors")
        node = graph[INTERPOLATION_SAVE_NODE_ID]
        self.assertEqual(node["class_type"], "WorkbenchChunkedInterpolate")
        self.assertEqual(node["inputs"]["interp_model"], ["1", 0])
        self.assertEqual(node["inputs"]["file"], "clip.mp4")
        self.assertEqual(node["inputs"]["multiplier"], 2)
        self.assertIn(
            "job-1/interpolate-rife_4_26-x2", node["inputs"]["filename_prefix"]
        )

    def test_chunk_size_follows_the_resolution_and_multiplier(self) -> None:
        hd = choose_chunk_frames(1920, 1080, 2)
        uhd = choose_chunk_frames(3840, 2160, 3)
        self.assertGreater(hd, uhd)
        self.assertGreaterEqual(uhd, 2)
        self.assertEqual(choose_chunk_frames(64, 64, 2), 480)
        self.assertEqual(choose_chunk_frames(30_000, 30_000, 3), 2)
        graph = build_interpolation_prompt("j", "c.mp4", "film", 3, 3840, 2160)
        self.assertEqual(
            graph[INTERPOLATION_SAVE_NODE_ID]["inputs"]["chunk_frames"], uhd
        )
        with self.assertRaises(ValueError):
            _ = choose_chunk_frames(0, 100, 2)

    def test_every_model_points_to_a_manifest_asset_inside_the_bundle(self) -> None:
        registry = load_model_registry(MANIFEST)
        assets = {asset.id: asset for asset in registry.assets}
        bundle = next(b for b in registry.bundles if b.id == INTERPOLATION_BUNDLE_ID)
        self.assertIn("WorkbenchChunkedInterpolate", bundle.required_nodes)
        for asset_id in INTERPOLATION_MODEL_ASSETS.values():
            with self.subTest(asset=asset_id):
                self.assertIn(asset_id, bundle.asset_ids)
                self.assertTrue(
                    assets[asset_id].target.startswith("frame_interpolation/")
                )

    def test_rejects_unknown_model_multiplier_and_unsafe_job_id(self) -> None:
        for kwargs in (
            {"model_key": "bad"},
            {"multiplier": 4},
            {"multiplier": 1},
            {"job_id": "../x"},
        ):
            with self.subTest(kwargs=kwargs):
                params: dict[str, object] = {
                    "job_id": "j",
                    "model_key": "film",
                    "multiplier": 2,
                    **kwargs,
                }
                with self.assertRaises(ValueError):
                    build_interpolation_prompt(
                        str(params["job_id"]),
                        "c.mp4",
                        str(params["model_key"]),
                        int(str(params["multiplier"])),
                        1344,
                        768,
                    )


if __name__ == "__main__":
    unittest.main()
