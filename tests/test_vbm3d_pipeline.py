from fractions import Fraction
from pathlib import Path

import pytest

from postprocess.vbm3d_pipeline import (
    StreamMismatchError,
    VideoInfo,
    build_vpy_script,
    parse_probe_output,
    probe_video,
    validate_matching_streams,
)


def test_parse_probe_output_reads_required_stream_fields() -> None:
    output = "\n".join(
        (
            "width=744",
            "height=488",
            "r_frame_rate=24/1",
            "nb_read_frames=124",
        )
    )

    assert parse_probe_output(output) == VideoInfo(744, 488, Fraction(24, 1), 124)


def test_parse_probe_output_rejects_unknown_frame_count() -> None:
    output = "width=744\nheight=488\nr_frame_rate=24/1\nnb_read_frames=N/A"

    with pytest.raises(ValueError, match="nb_read_frames"):
        parse_probe_output(output)


def test_probe_video_uses_ffprobe_without_touching_the_filesystem() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...] | list[str]) -> str:
        commands.append(tuple(command))
        return "width=744\nheight=488\nr_frame_rate=24/1\nnb_read_frames=124\n"

    result = probe_video(Path("/input/original.mp4"), runner=runner)

    assert result.frames == 124
    assert commands == [
        (
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_read_frames",
            "-of",
            "default=noprint_wrappers=1:nokey=0",
            "/input/original.mp4",
        )
    ]


def test_validate_matching_streams_rejects_frame_count_mismatch() -> None:
    original = VideoInfo(744, 488, Fraction(24, 1), 124)
    reference = VideoInfo(744, 488, Fraction(24, 1), 123)

    with pytest.raises(StreamMismatchError, match="frames 124 != 123"):
        validate_matching_streams(original, reference)


def test_build_vpy_script_uses_original_and_reference_for_final_pass() -> None:
    script = build_vpy_script(
        Path("/input/original.mp4"),
        Path("/input/flow-reference.mkv"),
        sigma=(3.0, 3.0, 3.0),
        radius=1,
    )

    assert "core.bm3d.VFinal" in script
    assert ".bm3d.VAggregate(radius=1, sample=1)" in script
    assert "flow-reference.mkv" in script
    assert "sigma=[3.0, 3.0, 3.0]" in script
    assert "matrix=100" in script
