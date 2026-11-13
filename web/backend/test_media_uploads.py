from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from .config import Settings
from .media import normalize_reference_video, parse_video_metadata
from .uploads import InsufficientStorageError, UploadStore


class VideoMetadataTests(unittest.TestCase):
    def test_parses_video_dimensions_audio_and_fractional_fps(self) -> None:
        metadata = parse_video_metadata(
            {
                "format": {"duration": "7.25"},
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1_920,
                        "height": 1_080,
                        "avg_frame_rate": "30000/1001",
                    },
                    {"codec_type": "audio"},
                ],
            }
        )

        self.assertEqual(metadata.duration_seconds, 7.25)
        self.assertEqual(metadata.short_edge, 1_080)
        self.assertAlmostEqual(metadata.frame_rate, 29.97, places=2)
        self.assertTrue(metadata.has_audio)

    def test_rejects_metadata_without_video_stream(self) -> None:
        with self.assertRaisesRegex(ValueError, "video stream"):
            _ = parse_video_metadata({"streams": [{"codec_type": "audio"}]})


class VideoNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapts_cfr_option_to_installed_ffmpeg_capabilities(self) -> None:
        for help_text, option in (
            (b"-fps_mode set fps mode", "-fps_mode"),
            (b"-vsync set sync mode", "-vsync"),
        ):
            help_process = Mock(spec=asyncio.subprocess.Process)
            help_process.returncode = 0
            help_process.communicate = AsyncMock(return_value=(help_text, b""))
            normalize_process = Mock(spec=asyncio.subprocess.Process)
            normalize_process.returncode = 0
            normalize_process.communicate = AsyncMock(return_value=(b"", b""))
            spawn = AsyncMock(side_effect=[help_process, normalize_process])
            with (
                self.subTest(option=option),
                patch("web.backend.media.shutil.which", return_value="ffmpeg"),
                patch("web.backend.media.asyncio.create_subprocess_exec", spawn),
                patch.object(Path, "mkdir"),
                patch.object(Path, "is_file", return_value=True),
                patch.object(Path, "stat", return_value=Mock(st_size=4)),
            ):
                target = Path("/virtual/prepared.mp4")
                self.assertEqual(
                    await normalize_reference_video(Path("/virtual/input.mp4"), target),
                    target,
                )
                arguments = cast(tuple[object, ...], spawn.await_args_list[1].args)
                self.assertIn(option, arguments)
                self.assertIn("fps=24", arguments)
                self.assertIn("0:a?", arguments)


class UploadStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_stores_file_under_generated_name_and_discards_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config").mkdir()
            (root / "scripts").mkdir()
            _ = (root / "config" / "models.lock.json").write_text(
                json.dumps({"model_root": str(root / "models"), "models": []}),
                encoding="utf-8",
            )
            settings = Settings.from_environment(
                root,
                environ={
                    "COMFYUI_INPUT_ROOT": str(root / "data" / "input"),
                    "MIN_FREE_BYTES": "1",
                },
            )
            store = UploadStore(settings)
            upload_id = str(uuid.uuid4())
            uploaded = await store.store(
                upload_id,
                "../../portrait.PNG",
                4,
                _chunks(b"png!"),
            )

            self.assertEqual(uploaded.kind, "image")
            self.assertTrue(uploaded.filename.endswith(".png"))
            self.assertEqual(uploaded.path.read_bytes(), b"png!")
            self.assertEqual(store.get(upload_id), uploaded)
            with self.assertRaisesRegex(ValueError, "уже существует"):
                _ = await store.store(upload_id, "portrait.PNG", 4, _chunks(b"next"))
            self.assertEqual(uploaded.path.read_bytes(), b"png!")
            store.discard(upload_id)
            self.assertFalse(uploaded.path.exists())

    async def test_accepts_audio_references_and_marks_them_as_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config").mkdir()
            _ = (root / "config" / "models.lock.json").write_text(
                json.dumps({"model_root": str(root / "models"), "models": []}),
                encoding="utf-8",
            )
            settings = Settings.from_environment(
                root,
                environ={
                    "COMFYUI_INPUT_ROOT": str(root / "data" / "input"),
                    "MIN_FREE_BYTES": "1",
                },
            )
            store = UploadStore(settings)
            uploaded = await store.store(
                str(uuid.uuid4()), "voice.WAV", 4, _chunks(b"riff")
            )

            self.assertEqual(uploaded.kind, "audio")
            self.assertTrue(uploaded.filename.endswith(".wav"))

    async def test_rejects_unsupported_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config").mkdir()
            _ = (root / "config" / "models.lock.json").write_text(
                json.dumps({"model_root": str(root / "models"), "models": []}),
                encoding="utf-8",
            )
            settings = Settings.from_environment(
                root, environ={"COMFYUI_INPUT_ROOT": str(root / "data" / "input")}
            )
            store = UploadStore(settings)

            with self.assertRaisesRegex(ValueError, "Поддерживаются изображения"):
                _ = await store.store(str(uuid.uuid4()), "payload.exe", 0, _chunks(b""))

    async def test_rejects_upload_larger_than_available_disk_reserve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config").mkdir()
            _ = (root / "config" / "models.lock.json").write_text(
                json.dumps({"model_root": str(root / "models"), "models": []}),
                encoding="utf-8",
            )
            settings = Settings.from_environment(
                root,
                environ={
                    "COMFYUI_INPUT_ROOT": str(root / "data" / "input"),
                    "MIN_FREE_BYTES": "999999999999999999",
                },
            )
            store = UploadStore(settings)

            with self.assertRaises(InsufficientStorageError):
                _ = await store.store(
                    str(uuid.uuid4()), "video.mp4", 4, _chunks(b"data")
                )


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data
    await asyncio.sleep(0)


if __name__ == "__main__":
    _ = unittest.main()
