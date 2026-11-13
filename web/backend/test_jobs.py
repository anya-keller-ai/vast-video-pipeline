from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from .comfy import ComfyUIClient, ComfyVideoResult
from .config import Settings
from .jobs import (
    JobManager,
    JobNotReadyError,
    JobRequestError,
    WorkbenchJob,
    extract_execution_error,
    extract_video_result,
)
from .readiness import ReadinessManager
from .uploads import UploadedFile, UploadStore
from .workflows import ComfyPrompt


class JobResultTests(unittest.TestCase):
    def test_reads_history_error_for_expected_prompt(self) -> None:
        history = {
            "prompt": {
                "status": {
                    "status_str": "error",
                    "messages": [
                        [
                            "execution_error",
                            {"exception_message": "GPU memory exhausted"},
                        ],
                    ],
                }
            }
        }
        self.assertEqual(
            extract_execution_error(history, "prompt"), "GPU memory exhausted"
        )
        self.assertIsNone(extract_execution_error(history, "another-prompt"))

    def test_extracts_saved_video_from_expected_output_node(self) -> None:
        history = {
            "prompt-1": {
                "outputs": {
                    "41": {
                        "videos": [
                            {
                                "filename": "reference.mp4",
                                "subfolder": "",
                                "type": "input",
                            }
                        ]
                    },
                    "16": {
                        "videos": [
                            {
                                "filename": "generation_00001_.mp4",
                                "subfolder": "workbench/jobs/abc",
                                "type": "output",
                            }
                        ]
                    },
                }
            }
        }

        result = extract_video_result(history, "prompt-1", "16")

        self.assertEqual(
            result,
            ComfyVideoResult("generation_00001_.mp4", "workbench/jobs/abc", "output"),
        )

    def test_does_not_treat_reference_video_as_job_output(self) -> None:
        history = {
            "prompt-2": {
                "outputs": {
                    "41": {"videos": [{"filename": "reference.mp4", "type": "input"}]}
                }
            }
        }

        self.assertIsNone(extract_video_result(history, "prompt-2", "16"))

    def test_waits_until_expected_save_node_has_output(self) -> None:
        history: object = {"prompt-3": {"outputs": {"16": {"images": []}}}}

        self.assertIsNone(extract_video_result(history, "prompt-3", "16"))


class JobConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_comfy_queue_blocks_a_second_gpu_submission(self) -> None:
        with (
            patch("web.backend.config._load_environment", return_value={}),
            patch(
                "web.backend.config._manifest_model_root",
                return_value=Path("/virtual/models"),
            ),
        ):
            settings = Settings.from_environment(environ={"REAL_CONNECTION": "true"})
        comfy = Mock(spec=ComfyUIClient)
        comfy.health = AsyncMock(return_value=True)
        comfy.queue = AsyncMock(
            return_value={"queue_running": [[0, "external-job"]], "queue_pending": []}
        )
        comfy.system_stats = AsyncMock(return_value={})
        submit_prompt: AsyncMock = AsyncMock()
        comfy.submit_prompt = submit_prompt
        manager = JobManager(
            settings,
            cast(ReadinessManager, Mock(ready=True)),
            cast(UploadStore, Mock(spec=UploadStore)),
            cast(ComfyUIClient, comfy),
        )

        with self.assertRaisesRegex(JobNotReadyError, "GPU занят"):
            _ = await manager.start_generation("scene", [], None, 5.0, True)
        submit_prompt.assert_not_awaited()

    async def test_unknown_gpu_memory_blocks_workflow_submission(self) -> None:
        with (
            patch("web.backend.config._load_environment", return_value={}),
            patch(
                "web.backend.config._manifest_model_root",
                return_value=Path("/virtual/models"),
            ),
        ):
            settings = Settings.from_environment(environ={"REAL_CONNECTION": "true"})
        comfy = Mock(spec=ComfyUIClient)
        comfy.health = AsyncMock(return_value=True)
        comfy.queue = AsyncMock(return_value={"queue_running": [], "queue_pending": []})
        comfy.system_stats = AsyncMock(return_value={"devices": []})
        submit_prompt: AsyncMock = AsyncMock()
        comfy.submit_prompt = submit_prompt
        manager = JobManager(
            settings,
            cast(ReadinessManager, Mock(ready=True)),
            cast(UploadStore, Mock(spec=UploadStore)),
            cast(ComfyUIClient, comfy),
        )

        with self.assertRaisesRegex(JobNotReadyError, "не сообщил свободную VRAM"):
            _ = await manager.start_generation("scene", [], None, 5.0, True)
        submit_prompt.assert_not_awaited()

    async def run_closed_socket(
        self, history: dict[str, object]
    ) -> tuple[dict[str, object], Mock]:
        with (
            patch("web.backend.config._load_environment", return_value={}),
            patch(
                "web.backend.config._manifest_model_root",
                return_value=Path("/virtual/models"),
            ),
        ):
            settings = Settings.from_environment(environ={"REAL_CONNECTION": "true"})
        uploads = Mock(spec=UploadStore)
        uploads.get = Mock(
            return_value=UploadedFile(
                "image", "image.png", Path("/virtual/image.png"), "image"
            )
        )
        discard = Mock()
        uploads.discard_many = discard
        socket = Mock(spec=aiohttp.ClientWebSocketResponse)
        socket.receive = AsyncMock(
            return_value=aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)
        )
        socket.close = AsyncMock(return_value=True)
        comfy = Mock(spec=ComfyUIClient)
        comfy.connect_websocket = AsyncMock(return_value=socket)
        comfy.submit_prompt = AsyncMock(return_value="prompt")
        comfy.history = AsyncMock(return_value=history)
        comfy.queue = AsyncMock(return_value={"queue_running": [], "queue_pending": []})
        comfy.system_stats = AsyncMock(
            return_value={"devices": [{"type": "cuda", "vram_free": 8 * 1024**3}]}
        )
        comfy.health = AsyncMock(return_value=True)
        workflow: ComfyPrompt = {"16": {"class_type": "SaveVideo", "inputs": {}}}
        manager = JobManager(
            settings,
            cast(ReadinessManager, Mock(ready=True)),
            cast(UploadStore, uploads),
            cast(ComfyUIClient, comfy),
        )
        with patch("web.backend.jobs.build_h3_ref2v_prompt", return_value=workflow):
            job = await manager.start_generation("scene", ["image"], None, 5.0, True)
        job_id = cast(str, job["id"])
        for _ in range(16):
            await asyncio.sleep(0)
            if manager.get(job_id)["status"] == "error":
                break
        await manager.close()
        return manager.get(job_id), discard

    async def test_closed_socket_reports_error_without_deleting_running_inputs(
        self,
    ) -> None:
        job, discard = await self.run_closed_socket({})
        self.assertEqual(job["status"], "error")
        self.assertIn("потеряно", str(job["error"]))
        discard.assert_not_called()

    async def test_history_execution_error_cleans_only_finished_job_inputs(
        self,
    ) -> None:
        history: dict[str, object] = {
            "prompt": {
                "status": {
                    "status_str": "error",
                    "messages": [
                        ["execution_error", {"exception_message": "GPU out of memory"}],
                    ],
                }
            }
        }
        job, discard = await self.run_closed_socket(history)
        self.assertEqual(job["error"], "GPU out of memory")
        discard.assert_called_once_with({"image"})


if __name__ == "__main__":
    _ = unittest.main()


class JobCancelTests(unittest.IsolatedAsyncioTestCase):
    def make_manager(self, queue: dict[str, object]) -> tuple[JobManager, Mock]:
        with (
            patch("web.backend.config._load_environment", return_value={}),
            patch(
                "web.backend.config._manifest_model_root",
                return_value=Path("/virtual/models"),
            ),
        ):
            settings = Settings.from_environment(environ={"REAL_CONNECTION": "true"})
        comfy = Mock(spec=ComfyUIClient)
        comfy.queue = AsyncMock(return_value=queue)
        comfy.delete_queued = AsyncMock()
        comfy.interrupt = AsyncMock()
        manager = JobManager(
            settings,
            cast(ReadinessManager, Mock(ready=True)),
            cast(UploadStore, Mock(spec=UploadStore)),
            cast(ComfyUIClient, comfy),
        )
        return manager, comfy

    def add_job(self, manager: JobManager, prompt_id: str) -> str:
        job_id = "a" * 32
        manager._jobs[job_id] = WorkbenchJob(  # pyright: ignore[reportPrivateUsage]
            id=job_id,
            mode="upscale",
            prompt={},
            output_filename="x.mp4",
            current_message="",
            input_ids=set(),
            derived_paths=[],
            model_bundle_id="seedvr2_upscale",
            prompt_id=prompt_id,
        )
        return job_id

    async def test_running_own_job_is_interrupted_and_marked_cancelled(self) -> None:
        manager, comfy = self.make_manager(
            {"queue_running": [[1, "mine", {}]], "queue_pending": []}
        )
        job_id = self.add_job(manager, "mine")
        comfy.interrupt.side_effect = lambda: comfy.queue.configure_mock(
            return_value={"queue_running": [], "queue_pending": []}
        )
        with patch("web.backend.jobs.asyncio.sleep", AsyncMock()):
            result = await manager.cancel(job_id)
        comfy.interrupt.assert_awaited_once()
        comfy.delete_queued.assert_not_awaited()
        self.assertEqual(result["status"], "cancelled")
        self.assertIsInstance(result["elapsedSeconds"], float)

    async def test_pending_job_is_deleted_without_interrupt(self) -> None:
        manager, comfy = self.make_manager(
            {"queue_running": [[1, "other", {}]], "queue_pending": [[2, "mine", {}]]}
        )
        job_id = self.add_job(manager, "mine")
        comfy.delete_queued.side_effect = lambda _id: comfy.queue.configure_mock(
            return_value={"queue_running": [[1, "other", {}]], "queue_pending": []}
        )
        with patch("web.backend.jobs.asyncio.sleep", AsyncMock()):
            result = await manager.cancel(job_id)
        comfy.delete_queued.assert_awaited_once_with("mine")
        comfy.interrupt.assert_not_awaited()
        self.assertEqual(result["status"], "cancelled")

    async def test_never_interrupts_when_several_jobs_run(self) -> None:
        manager, comfy = self.make_manager(
            {
                "queue_running": [[1, "mine", {}], [2, "foreign", {}]],
                "queue_pending": [],
            }
        )
        job_id = self.add_job(manager, "mine")
        with self.assertRaises(JobRequestError):
            _ = await manager.cancel(job_id)
        comfy.interrupt.assert_not_awaited()
        self.assertEqual(manager.get(job_id)["status"], "queued")

    async def test_unconfirmed_cancel_reports_error_not_cancelled(self) -> None:
        manager, comfy = self.make_manager(
            {"queue_running": [[1, "mine", {}]], "queue_pending": []}
        )
        job_id = self.add_job(manager, "mine")
        with (
            patch("web.backend.jobs.asyncio.sleep", AsyncMock()),
            self.assertRaises(JobRequestError),
        ):
            _ = await manager.cancel(job_id)
        comfy.interrupt.assert_awaited_once()
        self.assertEqual(manager.get(job_id)["status"], "queued")

    async def test_cancel_is_idempotent_for_finished_job(self) -> None:
        manager, comfy = self.make_manager({"queue_running": [], "queue_pending": []})
        job_id = self.add_job(manager, "mine")
        manager._jobs[job_id].status = "complete"  # pyright: ignore[reportPrivateUsage]
        result = await manager.cancel(job_id)
        self.assertEqual(result["status"], "complete")
        comfy.interrupt.assert_not_awaited()


class NodePreflightTests(unittest.IsolatedAsyncioTestCase):
    async def make_manager(
        self, required: tuple[str, ...], available: frozenset[str]
    ) -> JobManager:
        with (
            patch("web.backend.config._load_environment", return_value={}),
            patch(
                "web.backend.config._manifest_model_root",
                return_value=Path("/virtual/models"),
            ),
        ):
            settings = Settings.from_environment(environ={"REAL_CONNECTION": "true"})
        comfy = Mock(spec=ComfyUIClient)
        comfy.node_classes = AsyncMock(return_value=available)
        bundles = Mock()
        bundles.required_nodes = Mock(return_value=required)
        return JobManager(
            settings,
            cast(ReadinessManager, Mock(ready=True)),
            cast(UploadStore, Mock(spec=UploadStore)),
            cast(ComfyUIClient, comfy),
            bundles,
        )

    async def test_missing_custom_nodes_block_the_job_with_their_names(self) -> None:
        manager = await self.make_manager(
            ("NodeA", "NodeB"),
            frozenset({"NodeB"}),
        )
        with self.assertRaisesRegex(JobNotReadyError, "NodeA"):
            await manager._require_nodes("h3_base")  # pyright: ignore[reportPrivateUsage]

    async def test_installed_nodes_pass_and_empty_requirements_skip_the_check(
        self,
    ) -> None:
        manager = await self.make_manager(("A",), frozenset({"A", "B"}))
        await manager._require_nodes("pika_h3_flex_ref2va")  # pyright: ignore[reportPrivateUsage]
        empty = await self.make_manager((), frozenset())
        await empty._require_nodes("h3_base")  # pyright: ignore[reportPrivateUsage]
