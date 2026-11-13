from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast, override
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from .config import Settings
from .h3_guides import GuideRequest
from .models import ModelSpec
from .server import JOBS_KEY, MODEL_BUNDLES_KEY, READINESS_KEY, create_app


class ServerTests(unittest.IsolatedAsyncioTestCase):
    _client: TestClient[web.Request, web.Application] | None = None
    _settings: Settings | None = None

    @property
    def client(self) -> TestClient[web.Request, web.Application]:
        assert self._client is not None
        return self._client

    @property
    def settings(self) -> Settings:
        assert self._settings is not None
        return self._settings

    @override
    async def asyncSetUp(self) -> None:
        with patch("web.backend.config._load_environment", return_value={}):
            self._settings = Settings.from_environment(
                environ={"REAL_CONNECTION": "true"}
            )
        model = ModelSpec("model", "owner/repo", "model.bin", "model.bin", 4, "0" * 64)
        with patch("web.backend.readiness.load_model_specs", return_value=(model,)):
            app = create_app(self.settings)
            self._client = TestClient(TestServer(app))
            await self.client.start_server()

    @override
    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_cannot_delete_input_of_active_gpu_job(self) -> None:
        manager = self.client.app[JOBS_KEY]
        with patch.object(manager, "upload_in_use", return_value=True):
            response = await self.client.delete("/api/uploads/active")
        self.assertEqual(response.status, 409)

    async def test_runtime_config_only_exposes_mode(self) -> None:
        response = await self.client.get("/api/config")
        payload = cast(object, await response.json())
        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"realConnection": True})

    async def test_readiness_reports_server_snapshot(self) -> None:
        manager = self.client.app[READINESS_KEY]
        snapshot: dict[str, object] = {
            "phase": "downloading",
            "models": [],
            "comfyUi": "ready",
            "currentMessage": "Measured server progress",
        }
        with patch.object(manager, "snapshot", return_value=snapshot):
            response = await self.client.get("/api/readiness")
            self.assertEqual(cast(object, await response.json()), snapshot)

    async def test_model_bundle_status_does_not_start_downloads(self) -> None:
        response = await self.client.get("/api/model-bundles")
        payload = cast(object, await response.json())
        self.assertEqual(response.status, 200)
        self.assertIsInstance(payload, dict)
        bundle_data = cast(dict[str, object], payload)
        self.assertEqual(bundle_data["activeDownloadBundleId"], None)
        bundles = cast(list[object], bundle_data["bundles"])
        self.assertEqual(len(bundles), 9)

    async def test_model_download_requires_explicit_endpoint_action(self) -> None:
        manager = self.client.app[MODEL_BUNDLES_KEY]
        start = AsyncMock(return_value={"id": "h3_base", "phase": "downloading"})
        with patch.object(manager, "start_download", start):
            response = await self.client.post("/api/model-bundles/h3_base/download")
        self.assertEqual(response.status, 202)
        start.assert_awaited_once_with("h3_base")

    async def test_start_uses_one_backend_readiness_operation(self) -> None:
        manager = self.client.app[READINESS_KEY]
        start = AsyncMock(return_value={"phase": "starting"})
        with patch.object(manager, "start", start):
            response = await self.client.post("/api/readiness/start")
        self.assertEqual(response.status, 200)
        start.assert_awaited_once()

    async def test_generation_is_rejected_until_models_are_ready(self) -> None:
        response = await self.client.post(
            "/api/jobs",
            json={
                "mode": "generation",
                "prompt": "A scene",
                "referenceImageIds": [],
                "durationSeconds": 5.0,
                "turboLora": True,
            },
        )
        self.assertEqual(response.status, 409)

    async def test_pika_passes_references_duration_and_seed_to_manager(self) -> None:
        manager = self.client.app[JOBS_KEY]
        start = AsyncMock(return_value={"id": "abc", "status": "queued"})
        with patch.object(manager, "start_pika_generation", start):
            response = await self.client.post(
                "/api/jobs",
                json={
                    "mode": "pika",
                    "prompt": "<Picture 1>",
                    "referenceImageIds": ["one", "two"],
                    "referenceVideoId": "motion",
                    "referenceAudioIds": ["voice"],
                    "resolution": "draft",
                    "durationSeconds": 5,
                    "seed": 42,
                    "refImageSize": "max",
                },
            )
        self.assertEqual(response.status, 202)
        start.assert_awaited_once_with(
            "<Picture 1>",
            ["one", "two"],
            "motion",
            5.0,
            42,
            "max",
            ["voice"],
            "draft",
            [],
        )

    async def test_guides_reach_the_manager_and_are_validated(self) -> None:
        manager = self.client.app[JOBS_KEY]
        start = AsyncMock(return_value={"id": "abc", "status": "queued"})
        body: dict[str, object] = {
            "mode": "generation",
            "prompt": "scene",
            "referenceImageIds": [],
            "durationSeconds": 5.0,
            "turboLora": False,
            "guides": [{"imageId": "a", "frame": 0}, {"imageId": "b", "frame": 60}],
        }
        with patch.object(manager, "start_generation", start):
            response = await self.client.post("/api/jobs", json=body)
            self.assertEqual(response.status, 202)
            self.assertEqual(
                start.await_args.args[-1],
                [GuideRequest("a", 0), GuideRequest("b", 60)],
            )
            for bad in (
                "x",
                [{"imageId": 1, "frame": 0}],
                [{"imageId": "a", "frame": "0"}],
                [{"imageId": "a", "frame": True}],
                [{"imageId": "a"}],
                ["a"],
            ):
                with self.subTest(guides=bad):
                    rejected = await self.client.post(
                        "/api/jobs", json={**body, "guides": bad}
                    )
                    self.assertEqual(rejected.status, 400)
        self.assertEqual(start.await_count, 1)

    async def test_cancel_endpoint_calls_manager(self) -> None:
        manager = self.client.app[JOBS_KEY]
        cancel = AsyncMock(return_value={"id": "abc", "status": "cancelled"})
        with patch.object(manager, "cancel", cancel):
            response = await self.client.post("/api/jobs/abc/cancel")
        self.assertEqual(response.status, 200)
        cancel.assert_awaited_once_with("abc")

    async def test_generation_passes_all_reference_ids_to_manager(self) -> None:
        manager = self.client.app[JOBS_KEY]
        start = AsyncMock(return_value={"id": "abc", "status": "queued"})
        with patch.object(manager, "start_generation", start):
            response = await self.client.post(
                "/api/jobs",
                json={
                    "mode": "generation",
                    "prompt": "<Picture 1>",
                    "referenceImageIds": ["one", "two"],
                    "referenceVideoId": "video",
                    "referenceAudioIds": ["voice", "music"],
                    "resolution": "draft",
                    "durationSeconds": 7.5,
                    "turboLora": False,
                },
            )
        self.assertEqual(response.status, 202)
        start.assert_awaited_once_with(
            "<Picture 1>",
            ["one", "two"],
            "video",
            7.5,
            False,
            ["voice", "music"],
            "draft",
            [],
        )

    async def test_rejects_invalid_generation_duration_and_turbo_type(self) -> None:
        manager = self.client.app[JOBS_KEY]
        start = AsyncMock(return_value={"id": "abc", "status": "queued"})
        valid_payload: dict[str, object] = {
            "mode": "generation",
            "prompt": "scene",
            "referenceImageIds": [],
            "durationSeconds": 5.0,
            "turboLora": True,
        }
        for update in (
            {"durationSeconds": 4.99},
            {"durationSeconds": 15.1},
            {"durationSeconds": float("nan")},
            {"durationSeconds": True},
            {"turboLora": 1},
            {"resolution": "4k"},
            {"resolution": 1080},
        ):
            with self.subTest(update=update):
                with patch.object(manager, "start_generation", start):
                    response = await self.client.post(
                        "/api/jobs", json={**valid_payload, **update}
                    )
                self.assertEqual(response.status, 400)
        start.assert_not_awaited()

    async def test_rejects_unknown_mode_and_invalid_reference_ids(self) -> None:
        for body in (
            {"mode": "unknown"},
            {"mode": "generation", "prompt": "scene", "referenceImageIds": [42]},
            {"mode": "upscale", "videoId": "one", "scale": True},
        ):
            response = await self.client.post("/api/jobs", json=body)
            self.assertEqual(response.status, 400)

    async def test_cross_origin_mutation_is_rejected(self) -> None:
        response = await self.client.post(
            "/api/readiness/start", headers={"Origin": "https://untrusted.example"}
        )
        self.assertEqual(response.status, 403)

    async def test_mock_mode_does_not_start_remote_operations(self) -> None:
        with patch("web.backend.readiness.load_model_specs", return_value=()):
            app = create_app(replace(self.settings, real_connection=False))
            client = TestClient(TestServer(app))
            await client.start_server()
        try:
            start = AsyncMock()
            with patch.object(client.app[READINESS_KEY], "start", start):
                response = await client.post("/api/readiness/start")
            self.assertEqual(response.status, 400)
            start.assert_not_awaited()
        finally:
            await client.close()

    async def test_rejects_unsupported_upload_without_writing_file(self) -> None:
        with patch.object(Path, "open", Mock()) as write:
            response = await self.client.put(
                "/api/uploads/00000000-0000-0000-0000-000000000001",
                data=b"payload",
                headers={"X-Filename": "payload.exe"},
            )
            write.assert_not_called()
        self.assertEqual(response.status, 400)

    def test_public_bind_is_not_allowed(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            _ = create_app(replace(self.settings, host="0.0.0.0"))


if __name__ == "__main__":
    _ = unittest.main()
