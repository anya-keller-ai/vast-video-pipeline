from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

from .h3_guides import GuideFrame, apply_guides
from .models import load_model_registry
from .negpip import apply_negpip

type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class WorkflowNode(TypedDict):
    class_type: str
    inputs: dict[str, JsonValue]


type ComfyPrompt = dict[str, WorkflowNode]


@dataclass(frozen=True, slots=True)
class H3Preset:
    width: int
    height: int
    fps: int
    steps: int
    allocation: str


@dataclass(frozen=True, slots=True)
class H3GenerationConfig:
    min_duration_seconds: float
    max_duration_seconds: float
    min_frames: int
    max_frames: int
    frame_step: int
    frame_offset: int
    turbo: H3Preset
    base: H3Preset
    draft: H3Preset

    @property
    def fps(self) -> int:
        return self.turbo.fps


H3_MIN_REFERENCE_SECONDS = 2.0
H3_DEFAULT_RESOLUTION = "standard"
H3_RESOLUTIONS = ("standard", "draft")
H3_MAX_REFERENCE_AUDIOS = 3  # limit of MiniMaxH3ReferenceToVideo ref_audios
H3_MAX_REFERENCE_SECONDS = 15.0


def model_filename(identifier: str) -> str:
    manifest = Path(__file__).resolve().parents[2] / "config" / "models.lock.json"
    registry = load_model_registry(manifest)
    model = next((item for item in registry.assets if item.id == identifier), None)
    if model is None:
        raise ValueError(f"Missing model id: {identifier}")
    return model.filename


SEEDVR2_MODEL_ASSETS = {
    "3b": "seedvr2_3b_fp8",
    "7b": "seedvr2_7b_mixed_fp8",
    "7b_sharp": "seedvr2_sharp_7b_mixed_fp8",
}
SEEDVR2_DEFAULT_MODEL = "7b_sharp"
SEEDVR2_MAX_BATCH_SIZE = 33
SEEDVR2_MAX_TEMPORAL_OVERLAP = 4
SEEDVR2_MODEL = model_filename(SEEDVR2_MODEL_ASSETS[SEEDVR2_DEFAULT_MODEL])
SEEDVR2_VAE = model_filename("seedvr2_vae_fp16")


def h3_frame_count_for_duration(duration_seconds: float) -> int:
    if not math.isfinite(duration_seconds):
        raise ValueError("Длительность H3 должна быть конечным числом.")
    if not H3_MIN_GENERATION_SECONDS <= duration_seconds <= H3_MAX_GENERATION_SECONDS:
        duration_range = (
            f"{H3_MIN_GENERATION_SECONDS:g}–{H3_MAX_GENERATION_SECONDS:.3f}"
        )
        raise ValueError(f"Длительность H3: {duration_range} секунд.")

    # The tolerance keeps exact grid durations (e.g. 328 frames / 24 fps) from
    # rounding up to the next grid step through floating-point error.
    requested_frames = math.ceil(
        duration_seconds * H3_VIDEO_FRAMES_PER_SECOND - FRAME_ROUNDING_TOLERANCE
    )
    increments = max(
        0,
        math.ceil(
            (requested_frames - H3_GENERATION_CONFIG.min_frames)
            / H3_GENERATION_CONFIG.frame_step
        ),
    )
    return min(
        H3_GENERATION_CONFIG.min_frames + increments * H3_GENERATION_CONFIG.frame_step,
        H3_GENERATION_CONFIG.max_frames,
    )


def h3_resolution_size(resolution: str) -> tuple[int, int]:
    if resolution == "standard":
        preset = H3_GENERATION_CONFIG.turbo
    elif resolution == "draft":
        preset = H3_GENERATION_CONFIG.draft
    else:
        raise ValueError("Разрешение H3: standard или draft.")
    return preset.width, preset.height


def _load_h3_generation_config() -> H3GenerationConfig:
    presets_path = Path(__file__).resolve().parents[2] / "config" / "presets.json"
    try:
        raw_presets: object = cast(
            object, json.loads(presets_path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read H3 presets: {presets_path}") from error

    presets = _as_string_mapping(raw_presets)
    if presets is None:
        raise ValueError("H3 presets must be a JSON object.")
    gpu_profiles = _required_mapping(presets, "gpu_profiles")
    active_profile = _required_string(presets, "active_gpu_profile")
    gpu_profile = _required_mapping(gpu_profiles, active_profile)
    h3_profiles = _required_mapping(gpu_profile, "h3")
    turbo_profile = _read_h3_preset(_required_mapping(h3_profiles, "production"))
    base_profile = _read_h3_preset(_required_mapping(h3_profiles, "hero_base"))
    draft_profile = _read_h3_preset(_required_mapping(h3_profiles, "draft"))
    if draft_profile.fps != turbo_profile.fps:
        raise ValueError("H3 draft preset must share the FPS of the production preset.")
    if (
        turbo_profile.width != base_profile.width
        or turbo_profile.height != base_profile.height
        or turbo_profile.fps != base_profile.fps
        or turbo_profile.allocation != base_profile.allocation
    ):
        raise ValueError(
            "H3 Turbo and base presets must share resolution, FPS and allocation."
        )

    turbo_lora = _required_string(
        _required_mapping(h3_profiles, "production"), "turbo_lora"
    )
    if Path(turbo_lora).name != model_filename("h3_ref2v_turbo"):
        raise ValueError("H3 production preset Turbo LoRA must match the model lock.")
    base_lora = _required_nullable_string(
        _required_mapping(h3_profiles, "hero_base"), "turbo_lora"
    )
    if base_lora is not None:
        raise ValueError("H3 hero_base preset must not use Turbo LoRA.")

    rules = _required_mapping(presets, "rules")
    duration = _required_mapping(rules, "h3_generation_duration")
    result = H3GenerationConfig(
        min_duration_seconds=_required_number(duration, "min_seconds"),
        max_duration_seconds=_required_number(duration, "max_seconds"),
        min_frames=_required_positive_int(duration, "min_frames"),
        max_frames=_required_positive_int(duration, "max_frames"),
        frame_step=_required_positive_int(duration, "frame_step"),
        frame_offset=_required_nonnegative_int(duration, "frame_offset"),
        turbo=turbo_profile,
        base=base_profile,
        draft=draft_profile,
    )
    if (
        result.min_duration_seconds <= 0
        or result.min_duration_seconds > result.max_duration_seconds
        or result.min_frames > result.max_frames
        or result.min_frames % result.frame_step
        != result.frame_offset % result.frame_step
        or result.max_frames % result.frame_step
        != result.frame_offset % result.frame_step
    ):
        raise ValueError("H3 generation duration configuration is inconsistent.")
    return result


def _read_h3_preset(entry: dict[str, object]) -> H3Preset:
    return H3Preset(
        width=_required_positive_int(entry, "width"),
        height=_required_positive_int(entry, "height"),
        fps=_required_positive_int(entry, "fps"),
        steps=_required_positive_int(entry, "steps"),
        allocation=_required_string(entry, "allocation"),
    )


def _required_mapping(entry: dict[str, object], key: str) -> dict[str, object]:
    value = _as_string_mapping(entry.get(key))
    if value is None:
        raise ValueError(f"H3 preset field {key} must be an object.")
    return value


def _required_string(entry: dict[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"H3 preset field {key} must be a non-empty string.")
    return value


def _required_nullable_string(entry: dict[str, object], key: str) -> str | None:
    value = entry.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"H3 preset field {key} must be a string or null.")
    return value


def _required_positive_int(entry: dict[str, object], key: str) -> int:
    value = entry.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"H3 preset field {key} must be a positive integer.")
    return value


def _required_nonnegative_int(entry: dict[str, object], key: str) -> int:
    value = entry.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"H3 preset field {key} must be a nonnegative integer.")
    return value


def _required_number(entry: dict[str, object], key: str) -> float:
    value = entry.get(key)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise ValueError(f"H3 preset field {key} must be a finite number.")
    return float(value)


def _as_string_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    entries = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in entries):
        return None
    return cast(dict[str, object], entries)


H3_GENERATION_CONFIG = _load_h3_generation_config()
H3_VIDEO_FRAMES_PER_SECOND = H3_GENERATION_CONFIG.fps
H3_MIN_GENERATION_SECONDS = H3_GENERATION_CONFIG.min_duration_seconds
# The last frame-grid step (362 frames) lasts a little over the nominal 15 s.
H3_MAX_GENERATION_SECONDS = H3_GENERATION_CONFIG.max_frames / H3_GENERATION_CONFIG.fps
FRAME_ROUNDING_TOLERANCE = 1e-6


def seedvr2_target_resolution(short_edge: int, scale: int) -> int:
    if short_edge <= 0:
        raise ValueError("Не удалось определить короткую сторону исходного видео.")
    if scale not in {2, 4}:
        raise ValueError("Поддерживаются только коэффициенты upscale x2 и x4.")

    resolution = math.ceil(short_edge * scale / 2) * 2
    if resolution > 16_384:
        raise ValueError("Целевое разрешение превышает предел SeedVR2: 16384 пикселя.")
    return resolution


def build_h3_ref2v_prompt(
    prompt: str,
    job_id: str,
    reference_images: Sequence[str],
    reference_video: str | None,
    duration_seconds: float,
    use_turbo_lora: bool,
    seed: int,
    max_reference_images: int = 9,
    ref_image_size: str = "match",
    reference_audios: Sequence[str] = (),
    resolution: str = H3_DEFAULT_RESOLUTION,
    wire_negpip: bool = True,
    guides: Sequence[GuideFrame] = (),
) -> ComfyPrompt:
    if not prompt.strip():
        raise ValueError("Для генерации нужен непустой промт.")
    if len(reference_audios) > H3_MAX_REFERENCE_AUDIOS:
        raise ValueError(
            f"H3 Ref2V поддерживает не больше {H3_MAX_REFERENCE_AUDIOS} "
            "аудио-референсов."
        )
    if len(reference_images) > max_reference_images:
        raise ValueError(
            f"H3 Ref2V поддерживает не больше {max_reference_images} изображений."
        )
    frame_count = h3_frame_count_for_duration(duration_seconds)
    width, height = h3_resolution_size(resolution)
    if not 0 <= seed <= 18_446_744_073_709_551_615:
        raise ValueError("Seed вне диапазона ComfyUI.")

    selected_preset = (
        H3_GENERATION_CONFIG.turbo if use_turbo_lora else H3_GENERATION_CONFIG.base
    )
    model_path: JsonValue = ["6", 0] if use_turbo_lora else ["5", 0]
    graph: ComfyPrompt = {
        "1": _node(
            "UNETLoaderDisTorch2MultiGPU",
            {
                "unet_name": model_filename("h3_diffusion_ref2v_int8"),
                "weight_dtype": "default",
                "compute_device": "cuda:0",
                "virtual_vram_gb": 0.0,
                "donor_device": "cpu",
                "expert_mode_allocations": selected_preset.allocation,
                "eject_models": True,
            },
        ),
        "2": _node(
            "CLIPLoaderDisTorch2MultiGPU",
            {
                "clip_name": model_filename("h3_text_encoder_int8"),
                "type": "minimax",
                "device": "cuda:0",
                "virtual_vram_gb": 0.0,
                "donor_device": "cpu",
                "expert_mode_allocations": "cuda:0,8gb;cpu,*",
                "eject_models": False,
            },
        ),
        "3": _node(
            "VAELoaderMultiGPU",
            {
                "vae_name": model_filename("h3_video_vae"),
                "device": "cuda:0",
            },
        ),
        "4": _node(
            "VAELoaderMultiGPU",
            {
                "vae_name": model_filename("h3_audio_vae"),
                "device": "cuda:0",
            },
        ),
        "5": _node(
            "PathchSageAttentionKJ",
            {
                "model": ["1", 0],
                "sage_attention": "auto",
                "allow_compile": False,
            },
        ),
        "7": _node(
            "MiniMaxH3ReferenceToVideo",
            {
                "clip": ["2", 0],
                "vae": ["3", 0],
                "audio_vae": ["4", 0],
                "prompt": prompt.strip(),
                "width": width,
                "height": height,
                "length": frame_count,
                "ref_image_size": ref_image_size,
            },
        ),
        "8": _node(
            "BasicGuider",
            {
                "model": model_path,
                "conditioning": ["7", 0],
            },
        ),
        "9": _node(
            "BasicScheduler",
            {
                "model": model_path,
                "scheduler": "simple",
                "steps": selected_preset.steps,
                "denoise": 1.0,
            },
        ),
        "10": _node("KSamplerSelect", {"sampler_name": "res_multistep"}),
        "11": _node("RandomNoise", {"noise_seed": seed}),
        "12": _node(
            "SamplerCustomAdvanced",
            {
                "noise": ["11", 0],
                "guider": ["8", 0],
                "sampler": ["10", 0],
                "sigmas": ["9", 0],
                "latent_image": ["7", 1],
            },
        ),
        "13": _node(
            "VAEDecode",
            {
                "samples": ["12", 0],
                "vae": ["3", 0],
            },
        ),
        "14": _node(
            "VAEDecodeAudio",
            {
                "samples": ["12", 0],
                "vae": ["4", 0],
            },
        ),
        "15": _node(
            "CreateVideo",
            {
                "images": ["13", 0],
                "fps": float(selected_preset.fps),
                "audio": ["14", 0],
                "codec": "none",
            },
        ),
        "16": _node(
            "SaveVideo",
            {
                "video": ["15", 0],
                "filename_prefix": f"workbench/jobs/{job_id}/generation",
                "format": "mp4",
                "format.codec": "h264",
                "format.codec.encoding": "auto",
            },
        ),
    }

    if use_turbo_lora:
        graph["6"] = _node(
            "LoraLoaderModelOnly",
            {
                "model": ["5", 0],
                "lora_name": model_filename("h3_ref2v_turbo"),
                "strength_model": 1.0,
            },
        )

    for index, filename in enumerate(reference_images, start=1):
        node_id = str(20 + index)
        graph[node_id] = _node("LoadImage", {"image": filename})
        graph["7"]["inputs"][f"ref_images.ref_image_{index - 1}"] = [node_id, 0]

    for index, filename in enumerate(reference_audios):
        node_id = str(50 + index)
        graph[node_id] = _node("LoadAudio", {"audio": filename})
        graph["7"]["inputs"][f"ref_audios.ref_audio_{index}"] = [node_id, 0]

    if reference_video is not None:
        graph["40"] = _node("LoadVideo", {"file": reference_video})
        graph["41"] = _node("GetVideoComponents", {"video": ["40", 0]})
        graph["7"]["inputs"]["ref_videos.ref_video_0"] = ["41", 0]
        graph["7"]["inputs"]["ref_video_audios.ref_video_audio_0"] = ["41", 1]

    apply_guides(graph, guides, frame_count)
    # Callers that swap the model afterwards (Pika LoRA) wire NegPiP themselves.
    if wire_negpip:
        apply_negpip(graph)
    return graph


def build_seedvr2_prompt(
    job_id: str,
    video_filename: str,
    scale: int,
    short_edge: int,
    seed: int = 42,
    model_key: str = SEEDVR2_DEFAULT_MODEL,
    batch_size: int = 1,
    temporal_overlap: int = 0,
) -> ComfyPrompt:
    if (
        batch_size < 1
        or (batch_size - 1) % 4 != 0
        or batch_size > SEEDVR2_MAX_BATCH_SIZE
    ):
        raise ValueError("SeedVR2: batch_size должен иметь вид 4n+1 (1, 5, 9, 13…).")
    if not 0 <= temporal_overlap <= SEEDVR2_MAX_TEMPORAL_OVERLAP:
        raise ValueError("SeedVR2: temporal_overlap должен быть от 0 до 4.")
    if temporal_overlap >= batch_size:
        raise ValueError("SeedVR2: temporal_overlap должен быть меньше batch_size.")
    if model_key not in SEEDVR2_MODEL_ASSETS:
        raise ValueError("SeedVR2: модель должна быть 3b, 7b или 7b_sharp.")
    resolution = seedvr2_target_resolution(short_edge, scale)
    return {
        "1": _node("LoadVideo", {"file": video_filename}),
        "2": _node("GetVideoComponents", {"video": ["1", 0]}),
        "3": _node(
            "SeedVR2LoadDiTModel",
            {
                "model": model_filename(SEEDVR2_MODEL_ASSETS[model_key]),
                "device": "cuda:0",
                "blocks_to_swap": 0,
                "swap_io_components": False,
                "offload_device": "cpu",
                "cache_model": False,
                "attention_mode": "sdpa",
            },
        ),
        "4": _node(
            "SeedVR2LoadVAEModel",
            {
                "model": SEEDVR2_VAE,
                "device": "cuda:0",
                "encode_tiled": True,
                "encode_tile_size": 1_024,
                "encode_tile_overlap": 128,
                "decode_tiled": True,
                "decode_tile_size": 768,
                "decode_tile_overlap": 128,
                "tile_debug": "false",
                "offload_device": "cpu",
                "cache_model": False,
            },
        ),
        "5": _node(
            "SeedVR2VideoUpscaler",
            {
                "image": ["2", 0],
                "dit": ["3", 0],
                "vae": ["4", 0],
                "seed": seed,
                "resolution": resolution,
                "max_resolution": 0,
                "batch_size": batch_size,
                "uniform_batch_size": False,
                "color_correction": "lab",
                "temporal_overlap": temporal_overlap,
                "prepend_frames": 0,
                "input_noise_scale": 0.0,
                "latent_noise_scale": 0.0,
                "offload_device": "cpu",
                "enable_debug": False,
            },
        ),
        "6": _node(
            "CreateVideo",
            {
                "images": ["5", 0],
                "fps": ["2", 2],
                "audio": ["2", 1],
                "bit_depth": ["2", 3],
                "color_space": ["2", 4],
                "codec": "none",
            },
        ),
        "7": _node(
            "SaveVideo",
            {
                "video": ["6", 0],
                "filename_prefix": f"workbench/jobs/{job_id}/upscale-x{scale}",
                "format": "mp4",
                "format.codec": "h264",
                "format.codec.encoding": "auto",
            },
        ),
    }


def _node(class_type: str, inputs: dict[str, JsonValue]) -> WorkflowNode:
    return {"class_type": class_type, "inputs": inputs}
