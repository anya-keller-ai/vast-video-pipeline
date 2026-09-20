"""Reference-based V-BM3D refinement with strict stream validation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import cast


class StreamMismatchError(ValueError):
    """Raised when the source and reference streams cannot be aligned safely."""


@dataclass(frozen=True)
class VideoInfo:
    """The stream properties required by reference-based V-BM3D."""

    width: int
    height: int
    fps: Fraction
    frames: int


CommandRunner = Callable[[Sequence[str]], str]


def parse_probe_output(output: str) -> VideoInfo:
    """Parse the stable key/value output produced by ffprobe."""

    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()

    required = ("width", "height", "r_frame_rate", "nb_read_frames")
    missing = [key for key in required if not values.get(key) or values[key] == "N/A"]
    if missing:
        raise ValueError(f"ffprobe output is missing: {', '.join(missing)}")

    try:
        fps = Fraction(values["r_frame_rate"])
        return VideoInfo(
            width=int(values["width"]),
            height=int(values["height"]),
            fps=fps,
            frames=int(values["nb_read_frames"]),
        )
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("ffprobe output contains invalid stream values") from error


def default_probe_runner(command: Sequence[str]) -> str:
    """Run a read-only ffprobe command and return stdout."""

    result = subprocess.run(
        list(command),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def probe_video(path: Path, runner: CommandRunner = default_probe_runner) -> VideoInfo:
    """Read video geometry, frame rate, and frame count with ffprobe."""

    command = (
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
        str(path),
    )
    return parse_probe_output(runner(command))


def validate_matching_streams(original: VideoInfo, reference: VideoInfo) -> None:
    """Reject any source/reference mismatch before V-BM3D allocates buffers."""

    mismatches: list[str] = []
    if (original.width, original.height) != (reference.width, reference.height):
        mismatches.append(
            f"resolution {original.width}x{original.height} != "
            f"{reference.width}x{reference.height}"
        )
    if original.fps != reference.fps:
        mismatches.append(f"fps {original.fps} != {reference.fps}")
    if original.frames != reference.frames:
        mismatches.append(f"frames {original.frames} != {reference.frames}")
    if mismatches:
        raise StreamMismatchError("; ".join(mismatches))


def build_vpy_script(
    original: Path,
    reference: Path,
    sigma: Sequence[float] = (3.0, 3.0, 3.0),
    radius: int = 1,
) -> str:
    """Build a deterministic VapourSynth script for the reference refinement."""

    if len(sigma) != 3:
        raise ValueError("sigma must contain exactly three values")
    if radius < 1:
        raise ValueError("radius must be at least 1")
    sigma_literal = json.dumps([float(value) for value in sigma])
    original_literal = json.dumps(str(original.resolve()))
    reference_literal = json.dumps(str(reference.resolve()))
    return f"""import vapoursynth as vs
from vapoursynth import core

original = core.bs.VideoSource(source={original_literal})
reference = core.bs.VideoSource(source={reference_literal})

original = core.resize.Bicubic(original, format=vs.RGBS, matrix_in_s="709")
reference = core.resize.Bicubic(reference, format=vs.RGBS, matrix_in_s="709")

original_opp = core.bm3d.RGB2OPP(original, sample=1)
reference_opp = core.bm3d.RGB2OPP(reference, sample=1)

refined = core.bm3d.VFinal(
    original_opp,
    reference_opp,
    profile="fast",
    sigma={sigma_literal},
    radius={radius},
    matrix=100,
).bm3d.VAggregate(radius={radius}, sample=1)

refined = core.bm3d.OPP2RGB(refined, sample=1)
refined = core.resize.Bicubic(refined, format=vs.YUV444P16, matrix_s="709")
refined.set_output()
"""


def refine_video(
    original: Path,
    reference: Path,
    output: Path,
    sigma: Sequence[float] = (3.0, 3.0, 3.0),
    radius: int = 1,
    runner: CommandRunner = default_probe_runner,
) -> None:
    """Validate streams, run V-BM3D, and write a lossless FFV1 output."""

    validate_matching_streams(
        probe_video(original, runner),
        probe_video(reference, runner),
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vbm3d-") as temporary_directory:
        script_path = Path(temporary_directory) / "refine.vpy"
        script_path.write_text(
            build_vpy_script(original, reference, sigma=sigma, radius=radius),
            encoding="utf-8",
        )
        vspipe = subprocess.Popen(
            ["vspipe", "--y4m", str(script_path), "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert vspipe.stdout is not None
        ffmpeg = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "yuv4mpegpipe",
                "-i",
                "-",
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-g",
                "1",
                "-pix_fmt",
                "yuv444p16le",
                str(output),
            ],
            stdin=vspipe.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        vspipe.stdout.close()
        _, ffmpeg_stderr = ffmpeg.communicate()
        vspipe_stderr = vspipe.stderr.read() if vspipe.stderr is not None else b""
        vspipe_return_code = vspipe.wait()
        if ffmpeg.returncode != 0 or vspipe_return_code != 0:
            details = b"\n".join((vspipe_stderr, ffmpeg_stderr)).decode(
                "utf-8", errors="replace"
            )
            raise RuntimeError(f"V-BM3D render failed:\n{details}")


@dataclass(frozen=True)
class Arguments:
    original: Path
    reference: Path
    output: Path
    sigma: tuple[float, float, float]
    radius: int


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sigma", type=float, nargs=3, default=(3.0, 3.0, 3.0))
    parser.add_argument("--radius", type=int, default=1)
    return parser


def _parse_arguments(argv: Sequence[str] | None) -> Arguments:
    namespace = _build_parser().parse_args(argv)
    sigma = cast(list[float], namespace.sigma)
    if len(sigma) != 3:
        raise ValueError("sigma must contain exactly three values")
    return Arguments(
        original=cast(Path, namespace.original),
        reference=cast(Path, namespace.reference),
        output=cast(Path, namespace.output),
        sigma=(sigma[0], sigma[1], sigma[2]),
        radius=cast(int, namespace.radius),
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_arguments(argv)
        refine_video(
            args.original,
            args.reference,
            args.output,
            sigma=args.sigma,
            radius=args.radius,
        )
    except (OSError, RuntimeError, StreamMismatchError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
