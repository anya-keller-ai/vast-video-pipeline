from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import override

from .config import Settings
from .model_bundles import ModelBundleError, ModelBundleManager
from .models import ModelBundleSpec


@dataclass(slots=True)
class BundleTestContext:
    temp_dir: tempfile.TemporaryDirectory[str]
    root: Path
    model_root: Path
    manifest_path: Path
    project_root: Path
    settings: Settings
    manager: ModelBundleManager


class ModelBundleManagerTests(unittest.IsolatedAsyncioTestCase):
    _context: BundleTestContext | None = None

    @property
    def context(self) -> BundleTestContext:
        assert self._context is not None
        return self._context

    @override
    async def asyncSetUp(self) -> None:
        temp_dir: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        model_root = root / "models"
        manifest_path = root / "models.lock.json"
        project_root = root / "project"
        project_root.mkdir()
        _write_manifest(manifest_path, model_root)
        settings = Settings.from_environment(
            project_root,
            {
                "REAL_CONNECTION": "true",
                "MODEL_ROOT": str(model_root),
                "MANIFEST_PATH": str(manifest_path),
                "MIN_FREE_BYTES": "1",
            },
        )
        self._context = BundleTestContext(
            temp_dir,
            root,
            model_root,
            manifest_path,
            project_root,
            settings,
            ModelBundleManager(settings),
        )

    @override
    async def asyncTearDown(self) -> None:
        await self.context.manager.close()
        self.context.temp_dir.cleanup()

    async def test_snapshot_is_read_only_and_download_is_explicit(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        downloads: list[str] = []

        async def hold_download(bundle: ModelBundleSpec) -> None:
            downloads.append(bundle.id)
            _ = started.set()
            _ = await release.wait()
            _ = finished.set()

        context = self.context
        manager = ModelBundleManager(context.settings, download_runner=hold_download)
        context.manager = manager
        snapshot = await manager.snapshot()
        first = next(
            bundle for bundle in snapshot["bundles"] if bundle["id"] == "first"
        )

        self.assertEqual(first["phase"], "waiting")
        self.assertEqual(first["downloadProgress"], 0)
        self.assertIsNone(snapshot["activeDownloadBundleId"])
        self.assertEqual(downloads, [])

        _ = await manager.start_download("first")
        _ = await started.wait()
        with self.assertRaises(ModelBundleError):
            _ = await manager.start_download("second")
        _ = release.set()
        _ = await finished.wait()
        await asyncio.sleep(0)
        self.assertEqual(downloads, ["first"])

    async def test_download_is_blocked_when_connection_is_not_real(self) -> None:
        context = self.context
        manager = ModelBundleManager(
            Settings.from_environment(
                context.project_root,
                {
                    "REAL_CONNECTION": "false",
                    "MODEL_ROOT": str(context.model_root),
                    "MANIFEST_PATH": str(context.manifest_path),
                    "MIN_FREE_BYTES": "1",
                },
            )
        )
        with self.assertRaisesRegex(ModelBundleError, "REAL_CONNECTION"):
            _ = await manager.start_download("first")
        await manager.close()

    async def test_delete_preserves_shared_asset_and_removes_selected_files(
        self,
    ) -> None:
        model_root = self.context.model_root
        shared = model_root / "checkpoints" / "shared.safetensors"
        private = model_root / "checkpoints" / "private.safetensors"
        shared.parent.mkdir(parents=True)
        _ = shared.write_bytes(b"abc")
        _ = private.write_bytes(b"abc")

        result = await self.context.manager.delete_bundle("first")

        self.assertTrue(shared.exists())
        self.assertFalse(private.exists())
        self.assertEqual(result["preservedSharedAssets"], ["shared_asset"])
        self.assertEqual(result["freedBytes"], 3)

    async def test_sha_mismatch_is_invalid_after_background_verification(self) -> None:
        target = self.context.model_root / "checkpoints" / "shared.safetensors"
        target.parent.mkdir(parents=True)
        _ = target.write_bytes(b"bad")

        first = await self.context.manager.snapshot()
        first_bundle = next(
            bundle for bundle in first["bundles"] if bundle["id"] == "first"
        )
        self.assertEqual(first_bundle["assets"][0]["status"], "verifying")

        async def wait_until_invalid() -> None:
            while True:
                snapshot = await self.context.manager.snapshot()
                bundle = next(
                    item for item in snapshot["bundles"] if item["id"] == "first"
                )
                if bundle["assets"][0]["status"] == "invalid":
                    self.assertFalse(bundle["canDownload"])
                    return
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait_until_invalid(), timeout=2)

    async def test_delete_is_blocked_while_bundle_is_in_use(self) -> None:
        manager = self.context.manager
        manager.mark_in_use("first")

        with self.assertRaisesRegex(ModelBundleError, "активной GPU-задачей"):
            _ = await manager.delete_bundle("first")


def _write_manifest(path: Path, model_root: Path) -> None:
    good_hash = hashlib.sha256(b"abc").hexdigest()
    value = {
        "schema_version": 2,
        "model_root": str(model_root),
        "models": [
            {
                "id": "shared_asset",
                "repository": "org/repo",
                "revision": "abc123",
                "source": "weights/shared.safetensors",
                "target": "checkpoints/shared.safetensors",
                "bytes": 3,
                "sha256": good_hash,
                "format": "safetensors-bf16",
                "loader": "comfyui-checkpoint",
                "required": False,
            },
            {
                "id": "private_asset",
                "repository": "org/repo",
                "revision": "abc123",
                "source": "weights/private.safetensors",
                "target": "checkpoints/private.safetensors",
                "bytes": 3,
                "sha256": good_hash,
                "format": "safetensors-bf16",
                "loader": "comfyui-checkpoint",
                "required": False,
            },
        ],
        "bundles": [
            {
                "id": "first",
                "title": "First",
                "runtime": "comfyui",
                "workflows": ["text-to-video"],
                "assets": ["shared_asset", "private_asset"],
            },
            {
                "id": "second",
                "title": "Second",
                "runtime": "comfyui",
                "workflows": ["text-to-video"],
                "assets": ["shared_asset"],
            },
        ],
    }
    _ = path.write_text(json.dumps(value), encoding="utf-8")


if __name__ == "__main__":
    _ = unittest.main()
