from __future__ import annotations

import unittest
from pathlib import Path

from .models import load_model_registry
from .upscale_model import (
    UPSCALE_MODEL_SAVE_NODE_ID,
    UPSCALE_MODELS,
    build_upscale_model_prompt,
    upscale_model_spec,
)

MANIFEST = Path(__file__).resolve().parents[2] / "config" / "models.lock.json"


class UpscaleModelPromptTests(unittest.TestCase):
    def test_graph_loads_the_pinned_weights_and_keeps_audio_and_fps(self) -> None:
        graph = build_upscale_model_prompt("job-1", "clip.mp4", "realesrgan_x2plus")
        self.assertEqual(graph["1"]["inputs"]["file"], "clip.mp4")
        self.assertEqual(graph["3"]["class_type"], "UpscaleModelLoader")
        self.assertEqual(graph["3"]["inputs"]["model_name"], "RealESRGAN_x2plus.pth")
        self.assertEqual(graph["4"]["inputs"]["image"], ["2", 0])
        self.assertEqual(graph["5"]["inputs"]["fps"], ["2", 2])
        self.assertEqual(graph["5"]["inputs"]["audio"], ["2", 1])
        self.assertEqual(graph[UPSCALE_MODEL_SAVE_NODE_ID]["class_type"], "SaveVideo")

    def test_every_model_points_to_a_manifest_asset_and_bundle(self) -> None:
        registry = load_model_registry(MANIFEST)
        assets = {asset.id: asset for asset in registry.assets}
        bundles = {bundle.id: bundle for bundle in registry.bundles}
        for spec in UPSCALE_MODELS.values():
            with self.subTest(model=spec.key):
                self.assertIn(spec.asset_id, assets)
                self.assertEqual(bundles[spec.bundle_id].asset_ids, (spec.asset_id,))
                self.assertIsNotNone(assets[spec.asset_id].url)

    def test_native_scales_match_the_models(self) -> None:
        self.assertEqual(upscale_model_spec("realesrgan_x2plus").scale, 2)
        self.assertEqual(upscale_model_spec("realesrgan_animevideo_v3").scale, 4)

    def test_rejects_unknown_model_and_unsafe_job_id(self) -> None:
        with self.assertRaises(ValueError):
            build_upscale_model_prompt("j", "c.mp4", "bad")
        with self.assertRaises(ValueError):
            build_upscale_model_prompt("../j", "c.mp4", "realesrgan_x2plus")


if __name__ == "__main__":
    unittest.main()
