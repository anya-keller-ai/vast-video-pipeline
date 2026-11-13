from __future__ import annotations

import importlib.util
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType


def _load_chunking() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "comfy_nodes"
        / "workbench_interpolate"
        / "chunking.py"
    )
    spec = importlib.util.spec_from_file_location("workbench_chunking", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["workbench_chunking"] = module
    spec.loader.exec_module(module)
    return module


chunking = _load_chunking()


def _load_media() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "comfy_nodes"
        / "workbench_interpolate"
        / "media.py"
    )
    spec = importlib.util.spec_from_file_location("workbench_media", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["workbench_media"] = module
    spec.loader.exec_module(module)
    return module


media = _load_media()


def pairwise_interpolate(frames: Sequence[float], multiplier: int) -> list[float]:
    """Fake model: each in-between frame depends only on its two neighbours."""
    result: list[float] = []
    for before, after in zip(frames, frames[1:], strict=False):
        for step in range(multiplier):
            result.append(before + (after - before) * step / multiplier)
    result.append(frames[-1])
    return result


class ChunkedInterpolationTests(unittest.TestCase):
    def test_chunked_result_equals_processing_the_whole_video(self) -> None:
        for total in (2, 3, 7, 24, 25, 100):
            for chunk in (2, 3, 5, 24, 200):
                for multiplier in (2, 3):
                    with self.subTest(total=total, chunk=chunk, multiplier=multiplier):
                        frames = [float(index * index) for index in range(total)]
                        whole = pairwise_interpolate(frames, multiplier)
                        pieces = list(
                            chunking.interpolate_in_chunks(
                                iter(frames), pairwise_interpolate, multiplier, chunk
                            )
                        )
                        self.assertEqual(pieces, whole)

    def test_joined_video_has_the_exact_frame_count(self) -> None:
        frames = [float(index) for index in range(345)]
        pieces = list(
            chunking.interpolate_in_chunks(frames, pairwise_interpolate, 2, 64)
        )
        self.assertEqual(len(pieces), (345 - 1) * 2 + 1)
        self.assertEqual(len(pieces), chunking.output_frame_count(345, 2))

    def test_no_duplicates_or_gaps_at_chunk_boundaries(self) -> None:
        frames = [float(index) for index in range(21)]
        pieces = list(
            chunking.interpolate_in_chunks(frames, pairwise_interpolate, 2, 5)
        )
        # Linear fake model: the joined sequence must be strictly increasing.
        self.assertTrue(all(a < b for a, b in zip(pieces, pieces[1:], strict=False)))

    def test_calls_the_model_with_one_shared_frame_between_chunks(self) -> None:
        seen: list[list[float]] = []

        def recording(chunk: Sequence[float], multiplier: int) -> list[float]:
            seen.append(list(chunk))
            return pairwise_interpolate(chunk, multiplier)

        _ = list(
            chunking.interpolate_in_chunks(
                [float(i) for i in range(10)], recording, 2, 4
            )
        )
        self.assertEqual(
            seen, [[0.0, 1.0, 2.0, 3.0], [3.0, 4.0, 5.0, 6.0], [6.0, 7.0, 8.0, 9.0]]
        )

    def test_short_inputs_are_returned_unchanged(self) -> None:
        self.assertEqual(
            list(chunking.interpolate_in_chunks([], pairwise_interpolate, 2, 4)), []
        )
        self.assertEqual(
            list(chunking.interpolate_in_chunks([7.0], pairwise_interpolate, 2, 4)),
            [7.0],
        )

    def test_rejects_invalid_parameters_and_wrong_model_output(self) -> None:
        with self.assertRaises(ValueError):
            _ = list(
                chunking.interpolate_in_chunks([1.0, 2.0], pairwise_interpolate, 1, 4)
            )
        with self.assertRaises(ValueError):
            _ = list(
                chunking.interpolate_in_chunks([1.0, 2.0], pairwise_interpolate, 2, 1)
            )
        with self.assertRaises(ValueError):
            _ = list(
                chunking.interpolate_in_chunks(
                    [1.0, 2.0, 3.0], lambda chunk, multiplier: [1.0], 2, 3
                )
            )


class MediaProbeTests(unittest.TestCase):
    def test_parses_a_video_with_audio_and_a_frame_count(self) -> None:
        info = media.parse_ffprobe(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1344,
                        "height": 768,
                        "avg_frame_rate": "24/1",
                        "r_frame_rate": "24/1",
                        "nb_frames": "345",
                        "color_space": "bt709",
                    },
                    {"codec_type": "audio"},
                ]
            }
        )
        self.assertEqual((info.width, info.height), (1344, 768))
        self.assertEqual(float(info.fps), 24.0)
        self.assertEqual(info.estimated_frames, 345)
        self.assertTrue(info.has_audio)
        self.assertEqual(info.color_matrix, "bt709")

    def test_estimates_frames_from_duration_for_ntsc_without_audio(self) -> None:
        info = media.parse_ffprobe(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 640,
                        "height": 480,
                        "avg_frame_rate": "30000/1001",
                        "nb_frames": "N/A",
                        "duration": "10.01",
                    }
                ]
            }
        )
        self.assertEqual(info.estimated_frames, 300)
        self.assertFalse(info.has_audio)
        self.assertEqual(info.color_matrix, "bt601")

    def test_untagged_colour_follows_the_resolution_convention(self) -> None:
        self.assertEqual(media.choose_color_matrix(None, 1080), "bt709")
        self.assertEqual(media.choose_color_matrix("unknown", 480), "bt601")
        self.assertEqual(media.choose_color_matrix("smpte170m", 1080), "bt601")
        self.assertEqual(media.choose_color_matrix("bt709", 480), "bt709")

    def test_rejects_files_without_video_or_frame_rate(self) -> None:
        with self.assertRaises(ValueError):
            _ = media.parse_ffprobe({"streams": [{"codec_type": "audio"}]})
        with self.assertRaises(ValueError):
            _ = media.parse_ffprobe(
                {"streams": [{"codec_type": "video", "width": 10, "height": 10}]}
            )


if __name__ == "__main__":
    unittest.main()
