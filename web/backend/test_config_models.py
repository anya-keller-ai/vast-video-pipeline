from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import cast

from .config import Settings
from .models import check_model, load_model_registry, load_model_specs


class SettingsTests(unittest.TestCase):
    def test_real_connection_defaults_to_mock(self) -> None:
        settings = Settings.from_environment(environ={})
        self.assertFalse(settings.real_connection)
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8080)

    def test_reads_runtime_environment_without_vite_prefix(self) -> None:
        settings = Settings.from_environment(environ={"REAL_CONNECTION": "true"})
        self.assertTrue(settings.real_connection)

    def test_rejects_invalid_real_connection_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "REAL_CONNECTION"):
            _ = Settings.from_environment(environ={"REAL_CONNECTION": "yes"})

    def test_upload_root_defaults_to_comfyui_input_and_allows_override(self) -> None:
        settings = Settings.from_environment(
            environ={"COMFYUI_ROOT": "/opt/custom-comfy", "DATA_ROOT": "/data"}
        )
        self.assertEqual(settings.input_root, Path("/opt/custom-comfy/input"))

        overridden = Settings.from_environment(
            environ={
                "COMFYUI_ROOT": "/opt/custom-comfy",
                "COMFYUI_INPUT_ROOT": "/data/managed-input",
            }
        )
        self.assertEqual(overridden.input_root, Path("/data/managed-input"))

    def test_loads_dotenv_without_shell_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            web_directory = root / "web"
            web_directory.mkdir()
            (root / "config").mkdir()
            _ = (root / "config" / "models.lock.json").write_text(
                json.dumps({"model_root": "/data/models", "models": []}),
                encoding="utf-8",
            )
            _ = (web_directory / ".env").write_text(
                "REAL_CONNECTION='true'\nWORKBENCH_PORT=9090\n",
                encoding="utf-8",
            )

            settings = Settings.from_environment(root, environ={})

        self.assertTrue(settings.real_connection)
        self.assertEqual(settings.port, 9090)
        self.assertEqual(settings.model_root, Path("/data/models"))


class ModelManifestTests(unittest.TestCase):
    def test_loads_required_models_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "models.json"
            _ = path.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "id": "required",
                                "repository": "owner/repo",
                                "source": "source.bin",
                                "target": "folder/model.bin",
                                "bytes": 4,
                                "sha256": hashlib.sha256(b"data").hexdigest(),
                                "required": True,
                            },
                            {
                                "id": "optional",
                                "repository": "owner/repo",
                                "source": "optional.bin",
                                "target": "optional.bin",
                                "bytes": 3,
                                "sha256": hashlib.sha256(b"opt").hexdigest(),
                                "required": False,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            models = load_model_specs(path)

        self.assertEqual([model.id for model in models], ["required"])
        self.assertEqual(models[0].filename, "model.bin")

    def test_loads_bundle_assets_independently_from_required_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "models.json"
            entries = [
                {
                    "id": identifier,
                    "repository": "owner/repo",
                    "source": f"{identifier}.safetensors",
                    "target": f"models/{identifier}.safetensors",
                    "bytes": 4,
                    "sha256": hashlib.sha256(b"data").hexdigest(),
                    "required": required,
                    "format": "safetensors-int8",
                    "loader": "comfyui-unet",
                }
                for identifier, required in (("base", True), ("optional", False))
            ]
            manifest = {
                "models": entries,
                "bundles": [
                    {
                        "id": "workflow-base",
                        "title": "Base workflow",
                        "assets": ["base", "optional"],
                        "runtime": "comfyui",
                        "workflows": ["t2v"],
                    }
                ],
            }
            _ = path.write_text(json.dumps(manifest), encoding="utf-8")

            registry = load_model_registry(path)
            required_models = load_model_specs(path)

        self.assertEqual([asset.id for asset in registry.assets], ["base", "optional"])
        self.assertEqual([asset.id for asset in required_models], ["base"])
        self.assertEqual(registry.assets[1].file_format, "safetensors-int8")
        self.assertEqual(registry.assets[1].loader, "comfyui-unet")
        self.assertEqual(registry.bundles[0].asset_ids, ("base", "optional"))

    def test_rejects_bundle_with_unknown_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "models.json"
            entry = {
                "id": "model",
                "repository": "owner/repo",
                "source": "source.bin",
                "target": "model.bin",
                "bytes": 4,
                "sha256": hashlib.sha256(b"data").hexdigest(),
            }
            manifest = {
                "models": [entry],
                "bundles": [
                    {
                        "id": "broken",
                        "title": "Broken bundle",
                        "assets": ["missing"],
                        "runtime": "comfyui",
                    }
                ],
            }
            _ = path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unknown assets"):
                _ = load_model_registry(path)

    def test_rejects_asset_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "models.json"
            entry = {
                "id": "model",
                "repository": "owner/repo",
                "source": "../outside.bin",
                "target": "model.bin",
                "bytes": 4,
                "sha256": hashlib.sha256(b"data").hexdigest(),
            }
            _ = path.write_text(json.dumps({"models": [entry]}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "source path must stay relative"):
                _ = load_model_registry(path)

    def test_project_manifest_pins_independent_workflow_bundles(self) -> None:
        manifest_path = (
            Path(__file__).resolve().parents[2] / "config" / "models.lock.json"
        )
        registry = load_model_registry(manifest_path)
        bundles_by_id = {bundle.id: bundle for bundle in registry.bundles}

        self.assertEqual(
            set(bundles_by_id),
            {
                "h3_base",
                "h3_turbo",
                "seedvr2_upscale",
                "pika_h3_flex_ref2va",
                "flashvsr_plus_turbo",
                "flashvsr_plus_base",
                "realesrgan_x2plus",
                "realesrgan_animevideo_v3",
                "frame_interpolation",
            },
        )
        self.assertTrue(
            all(
                asset.revision is not None and asset.file_format and asset.loader
                for asset in registry.assets
            )
        )
        self.assertTrue(bundles_by_id["h3_base"].execution_enabled)
        self.assertTrue(bundles_by_id["seedvr2_upscale"].execution_enabled)
        self.assertEqual(
            set(bundles_by_id["seedvr2_upscale"].asset_ids),
            {
                "seedvr2_3b_fp8",
                "seedvr2_7b_mixed_fp8",
                "seedvr2_sharp_7b_mixed_fp8",
                "seedvr2_vae_fp16",
            },
        )
        pika = bundles_by_id["pika_h3_flex_ref2va"]
        self.assertTrue(pika.execution_enabled)
        self.assertEqual(
            set(pika.asset_ids) - set(bundles_by_id["h3_base"].asset_ids),
            {"pika_h3_flex_ref2va_lora"},
        )
        self.assertFalse(
            any(asset.id.startswith(("ltx25_", "wan21_")) for asset in registry.assets)
        )

        self.assertNotIn("hy_omniweaving_q5", bundles_by_id)
        self.assertFalse(
            any(asset.id.startswith("hy_q5_") for asset in registry.assets)
        )

    def test_locks_official_unpruned_ref2v_int8_checkpoint(self) -> None:
        manifest_path = (
            Path(__file__).resolve().parents[2] / "config" / "models.lock.json"
        )
        models = load_model_specs(manifest_path)
        diffusion = next(
            model for model in models if model.id == "h3_diffusion_ref2v_int8"
        )

        self.assertEqual(
            diffusion.source,
            "diffusion_models/minimax_h3_ref2va_int8_convrot.safetensors",
        )
        self.assertEqual(diffusion.target, diffusion.source)
        self.assertEqual(diffusion.bytes, 34_038_894_550)
        self.assertEqual(
            diffusion.sha256,
            "9eef934046a0671bc8a5daf87100705e1478419c574cfde70c50fbe6885f76a9",
        )

    def test_saved_generation_workflows_use_unpruned_checkpoint_and_offload(
        self,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        workflow_names = (
            "01-text-to-video-24gb.json",
            "02-text-to-video-1344-q4-tiled.json",
            "02-text-to-video-1344-q4-8step-staged.json",
        )
        expected_model = "minimax_h3_ref2va_int8_convrot.safetensors"

        for workflow_name in workflow_names:
            with self.subTest(workflow=workflow_name):
                workflow_path = project_root / "workflows" / workflow_name
                workflow = cast(
                    dict[str, object],
                    json.loads(workflow_path.read_text(encoding="utf-8")),
                )
                nodes = cast(list[object], workflow["nodes"])
                loader = next(
                    cast(dict[str, object], node)
                    for node in nodes
                    if isinstance(node, dict)
                    and cast(dict[str, object], node).get("type")
                    == "UNETLoaderDisTorch2MultiGPU"
                )
                widgets = cast(list[object], loader["widgets_values"])

                self.assertEqual(widgets[0], expected_model)
                self.assertIn("cuda:0,13gb;cpu,*", widgets)

    def test_rejects_duplicate_model_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "models.json"
            entry = {
                "id": "duplicate",
                "repository": "owner/repo",
                "source": "source.bin",
                "target": "model.bin",
                "bytes": 4,
                "sha256": hashlib.sha256(b"data").hexdigest(),
            }
            _ = path.write_text(
                json.dumps({"models": [entry, entry]}), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "Duplicate model id"):
                _ = load_model_specs(path)

    def test_checks_size_before_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "model.bin"
            _ = path.write_bytes(b"bad")
            model_manifest = Path(temporary_directory) / "models.json"
            _ = model_manifest.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "id": "model",
                                "repository": "owner/repo",
                                "source": "model.bin",
                                "target": "model.bin",
                                "bytes": 4,
                                "sha256": hashlib.sha256(b"data").hexdigest(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            model = load_model_specs(model_manifest)[0]

            check = check_model(path, model)

        self.assertFalse(check.ready)
        self.assertFalse(check.size_matches)
        self.assertIsNone(check.checksum_matches)

    def test_validates_sha256_for_exact_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "model.bin"
            _ = path.write_bytes(b"data")
            model_manifest = Path(temporary_directory) / "models.json"
            _ = model_manifest.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "id": "model",
                                "repository": "owner/repo",
                                "source": "model.bin",
                                "target": "model.bin",
                                "bytes": 4,
                                "sha256": hashlib.sha256(b"data").hexdigest(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            model = load_model_specs(model_manifest)[0]

            check = check_model(path, model)

        self.assertTrue(check.ready)
        self.assertEqual(check.checksum_matches, True)


if __name__ == "__main__":
    _ = unittest.main()
