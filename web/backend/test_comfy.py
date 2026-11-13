from __future__ import annotations

import unittest

from .comfy import ComfyVideoResult, as_string_mapping, stringify_error


class ComfyResponseParsingTests(unittest.TestCase):
    def test_accepts_only_string_keyed_json_objects(self) -> None:
        self.assertEqual(as_string_mapping({"prompt_id": "abc"}), {"prompt_id": "abc"})
        self.assertIsNone(as_string_mapping(["not", "a", "mapping"]))

    def test_formats_validation_errors_without_unbounded_output(self) -> None:
        self.assertEqual(stringify_error("invalid node"), "invalid node")
        self.assertLessEqual(len(stringify_error({"error": "x" * 2_000})), 1_000)

    def test_video_result_keeps_comfyui_location_fields(self) -> None:
        result = ComfyVideoResult("render.mp4", "workbench/job", "output")

        self.assertEqual(result.filename, "render.mp4")
        self.assertEqual(result.subfolder, "workbench/job")
        self.assertEqual(result.folder_type, "output")


if __name__ == "__main__":
    _ = unittest.main()
