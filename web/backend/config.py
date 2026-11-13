from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

DEFAULT_MIN_FREE_BYTES = 20 * 1024**3


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    real_connection: bool
    host: str
    port: int
    comfyui_url: str
    comfyui_root: Path
    data_root: Path
    model_root: Path
    input_root: Path
    output_root: Path
    model_manifest_path: Path
    prepare_models_script: Path
    start_comfyui_script: Path
    static_root: Path
    min_free_bytes: int
    readiness_poll_seconds: float

    @classmethod
    def from_environment(
        cls,
        project_root: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> Settings:
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        values = _load_environment(root / "web" / ".env")
        values.update(dict(os.environ if environ is None else environ))

        real_value = values.get("REAL_CONNECTION", "false").strip().lower()
        if real_value not in {"true", "false"}:
            raise ValueError("REAL_CONNECTION must be 'true' or 'false'.")

        data_root = Path(values.get("DATA_ROOT", "/workspace")).expanduser()
        comfyui_root = Path(values.get("COMFYUI_ROOT", "/opt/ComfyUI")).expanduser()
        manifest_path = Path(
            values.get("MANIFEST_PATH", str(root / "config" / "models.lock.json"))
        ).expanduser()
        manifest_model_root = _manifest_model_root(manifest_path)
        model_root = Path(
            values.get("MODEL_ROOT", str(manifest_model_root))
        ).expanduser()
        min_free_bytes = _positive_int(
            values.get("MIN_FREE_BYTES"), DEFAULT_MIN_FREE_BYTES
        )
        poll_seconds = _positive_float(values.get("READINESS_POLL_SECONDS"), 1.0)

        return cls(
            project_root=root,
            real_connection=real_value == "true",
            host=values.get("WORKBENCH_HOST", "127.0.0.1"),
            port=_positive_int(values.get("WORKBENCH_PORT"), 8080),
            comfyui_url=values.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188").rstrip(
                "/"
            ),
            comfyui_root=comfyui_root,
            data_root=data_root,
            model_root=model_root,
            input_root=Path(
                values.get("COMFYUI_INPUT_ROOT", str(comfyui_root / "input"))
            ),
            output_root=Path(
                values.get("COMFYUI_OUTPUT_ROOT", str(data_root / "ComfyUI" / "output"))
            ),
            model_manifest_path=manifest_path,
            prepare_models_script=root / "scripts" / "prepare-models.sh",
            start_comfyui_script=root / "scripts" / "start.sh",
            static_root=root / "web" / "dist",
            min_free_bytes=min_free_bytes,
            readiness_poll_seconds=poll_seconds,
        )


def _load_environment(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        key, separator, value = stripped.partition("=")
        if not separator or not key.strip():
            continue
        parsed = shlex.split(value, comments=True, posix=True) if value.strip() else []
        values[key.strip()] = parsed[0] if parsed else ""
    return values


def _manifest_model_root(path: Path) -> Path:
    try:
        manifest_value = cast(object, json.loads(path.read_text(encoding="utf-8")))
        if not isinstance(manifest_value, dict):
            return Path("/workspace/ComfyUI/models")
        manifest = cast(dict[str, object], manifest_value)
        model_root = manifest.get("model_root")
        if isinstance(model_root, str) and model_root:
            return Path(model_root).expanduser()
    except (OSError, ValueError, AttributeError):
        pass
    return Path("/workspace/ComfyUI/models")


def _positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("Configuration values must be positive integers.")
    return parsed


def _positive_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    parsed = float(value)
    if parsed <= 0:
        raise ValueError("Configuration values must be positive numbers.")
    return parsed
