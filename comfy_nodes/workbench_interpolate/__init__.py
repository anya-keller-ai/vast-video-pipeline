"""ComfyUI node: seamless, memory-bounded frame interpolation of a video file.

The core ``FrameInterpolate`` node keeps every source and interpolated frame in RAM,
which is killed by the container memory limit on long videos. This node streams the
video through ffmpeg, interpolates it in chunks that share one frame (see
``chunking.py``) and encodes the result on the fly, keeping the source audio.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import override

import comfy.utils
import folder_paths
import numpy as np
import torch
from comfy import model_management
from comfy_api.latest import ComfyExtension, io, ui
from comfy_extras.nodes_frame_interpolation import FrameInterpolate

from .chunking import interpolate_in_chunks, output_frame_count
from .media import COLOR_TAGS, VideoInfo, parse_ffprobe

log = logging.getLogger(__name__)

InterpolationModel = io.Custom("INTERP_MODEL")
STDERR_TAIL_BYTES = 2_000


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise RuntimeError("ffmpeg не найден на сервере.")
    return path


def _passthrough_option(ffmpeg: str) -> list[str]:
    """Keep every source frame (no duplication or dropping) in any ffmpeg version."""
    banner = subprocess.run(
        [ffmpeg, "-version"], capture_output=True, text=True, check=False
    ).stdout
    match = re.search(r"version\s+n?(\d+)\.(\d+)", banner)
    if match is not None and (int(match[1]), int(match[2])) >= (5, 1):
        return ["-fps_mode", "passthrough"]
    return ["-vsync", "0"]


def _probe(path: str) -> VideoInfo:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe не найден на сервере.")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,nb_frames,"
            "duration,color_space",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe не смог прочитать файл: {completed.stderr[-500:]}")
    return parse_ffprobe(json.loads(completed.stdout))


def _decode(path: str, info: VideoInfo):
    """Yield source frames as uint8 [H, W, 3] tensors, one at a time."""
    ffmpeg = _ffmpeg()
    command = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        path,
        "-map",
        "0:v:0",
        *_passthrough_option(ffmpeg),
        "-vf",
        f"scale=in_color_matrix={info.color_matrix}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    frame_bytes = info.width * info.height * 3
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    try:
        assert process.stdout is not None
        while True:
            data = process.stdout.read(frame_bytes)
            while 0 < len(data) < frame_bytes:
                more = process.stdout.read(frame_bytes - len(data))
                if not more:
                    break
                data += more
            if len(data) < frame_bytes:
                return
            array = np.frombuffer(data, dtype=np.uint8).reshape(
                info.height, info.width, 3
            )
            yield torch.from_numpy(array.copy())
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def _encoder_command(
    ffmpeg: str, source: str, target: str, info: VideoInfo, multiplier: int, crf: int
) -> list[str]:
    fps = info.fps * multiplier
    colorspace, primaries, trc = COLOR_TAGS[info.color_matrix]
    command = [
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{info.width}x{info.height}",
        "-r",
        f"{fps.numerator}/{fps.denominator}",
        "-i",
        "-",
    ]
    if info.has_audio:
        command += [
            "-i",
            source,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    command += [
        "-vf",
        f"scale=out_color_matrix={info.color_matrix}:out_range=tv,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        colorspace,
        "-color_primaries",
        primaries,
        "-color_trc",
        trc,
        "-color_range",
        "tv",
        "-movflags",
        "+faststart",
        target,
    ]
    return command


class WorkbenchChunkedInterpolate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="WorkbenchChunkedInterpolate",
            display_name="Workbench: Chunked Frame Interpolation",
            category="video",
            description=(
                "Interpolates a video file chunk by chunk (one shared frame per join, "
                "no seams), keeps the source audio and writes an MP4."
            ),
            inputs=[
                InterpolationModel.Input("interp_model"),
                io.String.Input(
                    "file", tooltip="Video file in the ComfyUI input folder."
                ),
                io.Int.Input("multiplier", default=2, min=2, max=16),
                io.Int.Input(
                    "chunk_frames",
                    default=240,
                    min=2,
                    max=4096,
                    tooltip="Source frames per chunk, including the shared frame.",
                ),
                io.String.Input("filename_prefix", default="video/interpolated"),
                io.Int.Input("crf", default=18, min=0, max=40),
            ],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(
        cls, interp_model, file, multiplier, chunk_frames, filename_prefix, crf
    ) -> io.NodeOutput:
        source = folder_paths.get_annotated_filepath(file)
        info = _probe(source)
        folder, name, counter, subfolder, _ = folder_paths.get_save_image_path(
            filename_prefix,
            folder_paths.get_output_directory(),
            info.width,
            info.height,
        )
        os.makedirs(folder, exist_ok=True)
        output_name = f"{name}_{counter:05}_.mp4"
        target = os.path.join(folder, output_name)

        total = info.estimated_frames or 1
        progress = comfy.utils.ProgressBar(total)
        state = {"done": 0, "chunks": 0, "source_frames": 0}

        def run_model(chunk, factor):
            model_management.throw_exception_if_processing_interrupted()
            stack = torch.stack(list(chunk)).float().div_(255)
            result = FrameInterpolate.execute(interp_model, stack, factor).result[0]
            encoded = (result.float().clamp_(0.0, 1.0) * 255.0).round().to(torch.uint8)
            state["chunks"] += 1
            state["done"] += len(chunk) - (0 if state["done"] == 0 else 1)
            progress.update_absolute(min(state["done"], total), total)
            log.info(
                "Workbench interpolation: chunk %d, %d source frames done (~%d total)",
                state["chunks"],
                state["done"],
                total,
            )
            return list(encoded.unbind(0))

        def counted(frames):
            for frame in frames:
                state["source_frames"] += 1
                yield frame

        ffmpeg = _ffmpeg()
        encoder = subprocess.Popen(
            _encoder_command(ffmpeg, source, target, info, multiplier, crf),
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        written = 0
        try:
            assert encoder.stdin is not None
            for frame in interpolate_in_chunks(
                counted(_decode(source, info)), run_model, multiplier, chunk_frames
            ):
                encoder.stdin.write(frame.numpy().tobytes())
                written += 1
            encoder.stdin.close()
            stderr = encoder.stderr.read() if encoder.stderr is not None else b""
            if encoder.wait() != 0:
                raise RuntimeError(
                    "ffmpeg не смог закодировать результат: "
                    + stderr[-STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")
                )
        except BaseException:
            if encoder.poll() is None:
                encoder.kill()
            encoder.wait()
            if os.path.exists(target):
                os.remove(target)
            raise

        expected = output_frame_count(state["source_frames"], multiplier)
        if written != expected:
            raise RuntimeError(
                f"Записано {written} кадров, ожидалось {expected}: "
                "проверь исходное видео."
            )
        log.info(
            "Workbench interpolation finished: %d -> %d frames in %d chunks",
            state["source_frames"],
            written,
            state["chunks"],
        )
        return io.NodeOutput(
            ui=ui.PreviewVideo(
                [ui.SavedResult(output_name, subfolder, io.FolderType.output)]
            )
        )


class WorkbenchInterpolateExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [WorkbenchChunkedInterpolate]


async def comfy_entrypoint() -> WorkbenchInterpolateExtension:
    return WorkbenchInterpolateExtension()
