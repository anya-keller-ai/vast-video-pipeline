from __future__ import annotations

import unittest

from .comfy import LogEntry, parse_log_entries
from .flashvsr_progress import parse_flashvsr_progress

START = "\x1b[32m[INFO]\x1b[0m FlashVSR+: Preparing file-backed VIDEO input\n"


def _decode(count: int) -> list[str]:
    lines = []
    for index in range(1, count + 1):
        frames = 13 if index == 1 else 16
        lines.append(
            f"[INFO] FlashVSR+: Decode segment {index} · 4 latent · "
            f"{frames} output frames"
        )
    return lines


class FlashVsrProgressTests(unittest.TestCase):
    def test_no_progress_before_the_node_starts(self) -> None:
        self.assertIsNone(parse_flashvsr_progress(["[INFO] something else"], 345))
        self.assertIsNone(parse_flashvsr_progress([], 345))

    def test_works_without_the_start_marker_when_it_scrolled_out_of_the_log(
        self,
    ) -> None:
        result = parse_flashvsr_progress(_decode(5), 345)
        assert result is not None
        self.assertIn("77/345", result.label)

    def test_waiting_for_the_first_segment(self) -> None:
        result = parse_flashvsr_progress([START], 345)
        assert result is not None
        self.assertIsNone(result.percent)
        self.assertIn("подготавливаю", result.label)

    def test_decode_progress_counts_output_frames_against_source_frames(self) -> None:
        result = parse_flashvsr_progress([START, *_decode(18)], 345)
        assert result is not None
        self.assertIn("285/345", result.label)
        self.assertEqual(result.percent, round(285 / 345 * 80))

    def test_decode_without_known_total_reports_segment_only(self) -> None:
        result = parse_flashvsr_progress([START, *_decode(3)], None)
        assert result is not None
        self.assertIsNone(result.percent)
        self.assertIn("сегмент 3", result.label)

    def test_encoding_fills_the_last_share_and_reaches_one_hundred(self) -> None:
        lines = [START, *_decode(22), "[INFO] FlashVSR+: Encoding video frame 129/345"]
        result = parse_flashvsr_progress(lines, 345)
        assert result is not None
        self.assertEqual(result.percent, 80 + round(20 * 129 / 345))
        self.assertIn("129/345", result.label)
        done = parse_flashvsr_progress(
            [*lines, "FlashVSR+: Encoding video frame 345/345"], 345
        )
        assert done is not None
        self.assertEqual(done.percent, 100)

    def test_only_the_newest_run_is_used(self) -> None:
        old = [START, *_decode(22), "FlashVSR+: Encoding video frame 345/345"]
        result = parse_flashvsr_progress([*old, START, *_decode(2)], 345)
        assert result is not None
        self.assertIn("29/345", result.label)

    def test_tiles_split_the_decode_range(self) -> None:
        lines = [START, "FlashVSR+: Spatial tile 2/2", *_decode(2)]
        result = parse_flashvsr_progress(lines, 100)
        assert result is not None
        self.assertEqual(result.percent, round((1 + 29 / 100) / 2 * 80))
        self.assertIn("2/2", result.label)


class LogPayloadTests(unittest.TestCase):
    def test_extracts_entries_and_ignores_malformed_ones(self) -> None:
        payload = {
            "entries": [
                {"t": "1", "m": "a"},
                {"t": "2"},
                "x",
                {"t": "3", "m": 5},
                {"t": "4", "m": "b"},
            ]
        }
        self.assertEqual(
            parse_log_entries(payload), [LogEntry("1", "a"), LogEntry("4", "b")]
        )
        self.assertEqual(parse_log_entries({"entries": "bad"}), [])
        self.assertEqual(parse_log_entries(["not", "a", "mapping"]), [])


if __name__ == "__main__":
    unittest.main()
