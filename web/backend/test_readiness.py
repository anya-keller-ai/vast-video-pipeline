from __future__ import annotations

import asyncio
import signal
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from .config import Settings
from .models import ModelSpec
from .readiness import ReadinessManager


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    def new_manager(self) -> ReadinessManager:
        with patch("web.backend.config._load_environment", return_value={}):
            settings = Settings.from_environment(environ={"REAL_CONNECTION": "true"})
        model = ModelSpec(
            "model", "owner/repo", "foo/model.bin", "foo/model.bin", 100, "0" * 64
        )
        with patch("web.backend.readiness.load_model_specs", return_value=(model,)):
            return ReadinessManager(settings, cast(aiohttp.ClientSession, Mock()))

    def emit(self, manager: ReadinessManager, line: str) -> None:
        manager.consume_script_output(line)

    async def wait(self, manager: ReadinessManager) -> None:
        for _ in range(32):
            if manager.snapshot()["phase"] in ("ready", "error"):
                return
            await asyncio.sleep(0)
        self.fail("Mock readiness did not finish")

    async def test_duplicate_start_uses_single_download_and_requires_done(self) -> None:
        manager = self.new_manager()
        observed: list[dict[str, object]] = []

        async def download() -> None:
            self.emit(manager, "GET model")
            self.emit(manager, "99%")
            observed.append(manager.snapshot())
            self.emit(manager, "VERIFY model")
            observed.append(manager.snapshot())
            self.emit(manager, "DONE model")

        run = AsyncMock(side_effect=download)
        with (
            patch.object(manager, "_comfy_is_ready", AsyncMock(return_value=True)),
            patch.object(manager, "_run_model_script", run),
            patch.object(manager, "_model_file_bytes", return_value=90),
        ):
            _ = await manager.start()
            _ = await manager.start()
            await self.wait(manager)
            self.assertTrue(manager.ready)
        run.assert_awaited_once()
        self.assertEqual(observed[0]["phase"], "downloading")
        self.assertEqual(observed[1]["phase"], "verifying")
        self.assertIn("'progress': 90", str(observed[0]["models"]))

    async def test_checksum_failure_can_retry_without_false_ready(self) -> None:
        manager = self.new_manager()

        async def success() -> None:
            self.emit(manager, "DONE model")

        with (
            patch.object(manager, "_comfy_is_ready", AsyncMock(return_value=True)),
            patch.object(manager, "_model_file_bytes", return_value=100),
            patch.object(
                manager,
                "_run_model_script",
                AsyncMock(side_effect=RuntimeError("SHA mismatch")),
            ),
        ):
            _ = await manager.start()
            await self.wait(manager)
            self.assertFalse(manager.ready)
            self.assertEqual(manager.snapshot()["phase"], "error")
        with (
            patch.object(manager, "_comfy_is_ready", AsyncMock(return_value=True)),
            patch.object(manager, "_model_file_bytes", return_value=100),
            patch.object(manager, "_run_model_script", AsyncMock(side_effect=success)),
        ):
            _ = await manager.start()
            await self.wait(manager)
            self.assertTrue(manager.ready)

    async def test_cancellation_terminates_owned_process_group(self) -> None:
        manager = self.new_manager()
        finished = asyncio.Event()
        process = Mock(spec=asyncio.subprocess.Process)
        process.pid = 123
        process.returncode = None

        def terminate(pid: int, received_signal: int) -> None:
            self.assertEqual(pid, 123)
            self.assertEqual(received_signal, signal.SIGTERM)
            finished.set()

        with (
            patch.object(manager, "_comfy_is_ready", AsyncMock(return_value=True)),
            patch.object(
                manager, "_run_model_script", AsyncMock(side_effect=finished.wait)
            ),
            patch.object(manager, "_model_file_bytes", return_value=0),
            patch("web.backend.readiness.os.killpg", side_effect=terminate) as kill,
        ):
            _ = await manager.start()
            await asyncio.sleep(0)
            with patch.object(manager, "_process", process):
                snapshot = await manager.cancel()
            self.assertEqual(snapshot["phase"], "error")
            self.assertFalse(manager.ready)
            kill.assert_called_once_with(123, signal.SIGTERM)

    def test_huggingface_cache_uses_newest_matching_sha_partial(self) -> None:
        manager = self.new_manager()
        model_sha = "0" * 64
        stale = Path(f"/virtual/cache/opaque.{model_sha}.old.incomplete")
        active = Path(f"/virtual/cache/opaque.{model_sha}.new.incomplete")
        unrelated = Path(f"/virtual/cache/opaque.{'1' * 64}.new.incomplete")

        def list_incomplete(directory: Path, pattern: str) -> tuple[Path, ...]:
            self.assertEqual(directory.name, "foo")
            self.assertEqual(pattern, "*.incomplete")
            return stale, active, unrelated

        def measure_file(path: Path) -> Mock:
            if path == stale:
                return Mock(st_size=999, st_mtime_ns=1)
            if path == active:
                return Mock(st_size=45, st_mtime_ns=2)
            if path == unrelated:
                return Mock(st_size=1_200, st_mtime_ns=3)
            raise OSError

        with (
            patch.object(Path, "glob", autospec=True, side_effect=list_incomplete),
            patch.object(Path, "stat", autospec=True, side_effect=measure_file),
        ):
            self.emit(manager, "GET model")
            snapshot = manager.snapshot()

        self.assertIn("'downloadedBytes': 45", str(snapshot["models"]))


if __name__ == "__main__":
    _ = unittest.main()
