from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str
    repository: str
    source: str
    target: str
    bytes: int
    sha256: str
    revision: str | None = None
    file_format: str = "safetensors"
    loader: str = "comfyui"
    url: str | None = None

    @property
    def filename(self) -> str:
        return Path(self.target).name


@dataclass(frozen=True, slots=True)
class ModelCheck:
    bytes_present: int
    size_matches: bool
    checksum_matches: bool | None

    @property
    def ready(self) -> bool:
        return self.size_matches and self.checksum_matches is True


@dataclass(frozen=True, slots=True)
class ModelBundleSpec:
    id: str
    title: str
    asset_ids: tuple[str, ...]
    runtime: str
    workflows: tuple[str, ...]
    enabled: bool
    blocked_reason: str | None
    execution_enabled: bool
    execution_blocked_reason: str | None
    required_nodes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    assets: tuple[ModelSpec, ...]
    bundles: tuple[ModelBundleSpec, ...]


def load_model_specs(manifest_path: Path) -> tuple[ModelSpec, ...]:
    registry = _load_model_registry_data(manifest_path)
    required_ids = {
        _required_string(entry, "id")
        for raw_entry in cast(list[object], registry["models"])
        if (entry := _as_string_mapping(raw_entry)) is not None
        and entry.get("required", True) is True
    }
    models = tuple(
        asset
        for asset in _parse_model_assets(registry["models"])
        if asset.id in required_ids
    )
    if not models:
        raise ValueError("Model manifest contains no required files.")
    return models


def load_model_registry(manifest_path: Path) -> ModelRegistry:
    raw = _load_model_registry_data(manifest_path)
    assets = _parse_model_assets(raw["models"])
    bundles_value = raw.get("bundles", [])
    if not isinstance(bundles_value, list):
        raise ValueError("Model manifest bundles must be an array.")

    assets_by_id = {asset.id: asset for asset in assets}
    bundles: list[ModelBundleSpec] = []
    seen_bundle_ids: set[str] = set()
    for raw_bundle in cast(list[object], bundles_value):
        entry = _as_string_mapping(raw_bundle)
        if entry is None:
            raise ValueError("Each model bundle must be an object.")
        identifier = _required_string(entry, "id")
        if identifier in seen_bundle_ids:
            raise ValueError(f"Duplicate model bundle id: {identifier}")
        seen_bundle_ids.add(identifier)
        raw_asset_ids = entry.get("assets")
        if not isinstance(raw_asset_ids, list) or not raw_asset_ids:
            raise ValueError(f"Model bundle {identifier} must list assets.")
        raw_ids = cast(list[object], raw_asset_ids)
        if not all(isinstance(asset_id, str) for asset_id in raw_ids):
            raise ValueError(f"Model bundle {identifier} asset ids must be strings.")
        asset_ids = tuple(cast(list[str], raw_ids))
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError(f"Model bundle {identifier} contains duplicate assets.")
        missing_ids = set(asset_ids).difference(assets_by_id)
        if missing_ids:
            missing_list = ", ".join(sorted(missing_ids))
            raise ValueError(
                f"Bundle {identifier} references unknown assets: {missing_list}"
            )
        raw_workflows = entry.get("workflows", [])
        workflow_ids = _parse_string_list(raw_workflows, identifier, "workflows")
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"Model bundle {identifier} enabled flag must be boolean.")
        blocked_reason = entry.get("blocked_reason")
        if blocked_reason is not None and not isinstance(blocked_reason, str):
            raise ValueError(f"Model bundle {identifier} blocked_reason must be text.")
        if enabled and blocked_reason is not None:
            raise ValueError(f"Enabled model bundle {identifier} cannot be blocked.")
        execution_enabled = entry.get("execution_enabled", True)
        if not isinstance(execution_enabled, bool):
            raise ValueError(
                f"Model bundle {identifier} execution_enabled must be boolean."
            )
        execution_blocked_reason = entry.get("execution_blocked_reason")
        if execution_blocked_reason is not None and not isinstance(
            execution_blocked_reason, str
        ):
            raise ValueError(
                f"Model bundle {identifier} execution_blocked_reason must be text."
            )
        if execution_enabled and execution_blocked_reason is not None:
            raise ValueError(
                f"Executable model bundle {identifier} cannot have a blocked reason."
            )
        if not execution_enabled and not execution_blocked_reason:
            raise ValueError(
                f"Blocked model bundle {identifier} requires an execution reason."
            )
        bundles.append(
            ModelBundleSpec(
                identifier,
                _required_string(entry, "title"),
                asset_ids,
                _required_string(entry, "runtime"),
                workflow_ids,
                enabled,
                blocked_reason,
                execution_enabled,
                execution_blocked_reason,
                _parse_string_list(
                    entry.get("required_nodes", []), identifier, "required_nodes"
                ),
            )
        )

    target_owners: dict[str, ModelSpec] = {}
    for asset in assets:
        target = Path(asset.target)
        if target.is_absolute() or ".." in target.parts:
            raise ValueError(f"Model {asset.id} target must stay under model_root.")
        previous = target_owners.get(asset.target)
        if previous is not None and (
            previous.sha256 != asset.sha256 or previous.bytes != asset.bytes
        ):
            raise ValueError(f"Conflicting model assets share target: {asset.target}")
        target_owners[asset.target] = asset
    return ModelRegistry(assets, tuple(bundles))


def _load_model_registry_data(manifest_path: Path) -> dict[str, object]:
    try:
        raw_manifest = cast(
            object, json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read model manifest: {manifest_path}") from error
    manifest = _as_string_mapping(raw_manifest)
    if manifest is None:
        raise ValueError("Model manifest must be a JSON object.")
    raw_models = manifest.get("models")
    if not isinstance(raw_models, list):
        raise ValueError("Model manifest must contain a models array.")
    return manifest


def _parse_model_assets(raw_models: object) -> tuple[ModelSpec, ...]:
    if not isinstance(raw_models, list):
        raise ValueError("Model manifest must contain a models array.")
    models: list[ModelSpec] = []
    seen_ids: set[str] = set()
    for raw_model in cast(list[object], raw_models):
        entry = _as_string_mapping(raw_model)
        if entry is None:
            raise ValueError("Each model manifest entry must be an object.")
        required = entry.get("required", True)
        if not isinstance(required, bool):
            raise ValueError("Model required flags must be boolean.")
        identifier = _required_string(entry, "id")
        repository = _required_string(entry, "repository")
        source = _safe_relative_path(entry, "source", identifier)
        target = _safe_relative_path(entry, "target", identifier)
        checksum = _required_string(entry, "sha256").lower()
        size = entry.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError(f"Model {identifier} must have a positive byte size.")
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise ValueError(f"Model {identifier} must have a SHA-256 checksum.")
        if identifier in seen_ids:
            raise ValueError(f"Duplicate model id: {identifier}")
        seen_ids.add(identifier)
        revision = entry.get("revision")
        file_format = entry.get("format", "safetensors")
        loader = entry.get("loader", "comfyui")
        if revision is not None and (not isinstance(revision, str) or not revision):
            raise ValueError(f"Model {identifier} revision must be non-empty text.")
        if not isinstance(file_format, str) or not file_format:
            raise ValueError(f"Model {identifier} format must be non-empty text.")
        if not isinstance(loader, str) or not loader:
            raise ValueError(f"Model {identifier} loader must be non-empty text.")
        url = entry.get("url")
        if url is not None and (
            not isinstance(url, str) or not url.startswith("https://")
        ):
            raise ValueError(f"Model {identifier} url must be an https link.")
        models.append(
            ModelSpec(
                identifier,
                repository,
                source,
                target,
                size,
                checksum,
                revision,
                file_format,
                loader,
                url,
            )
        )
    if not models:
        raise ValueError("Model manifest contains no files.")
    return tuple(models)


def _safe_relative_path(entry: dict[str, object], key: str, model_id: str) -> str:
    value = _required_string(entry, key)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Model {model_id} {key} path must stay relative.")
    return value


def _parse_string_list(value: object, bundle_id: str, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"Model bundle {bundle_id} {field} must be a list.")
    result: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item:
            raise ValueError(f"Model bundle {bundle_id} {field} must be strings.")
        result.append(item)
    return tuple(result)


def check_model(path: Path, model: ModelSpec) -> ModelCheck:
    try:
        present_bytes = path.stat().st_size
    except OSError:
        return ModelCheck(0, False, None)

    if present_bytes != model.bytes:
        return ModelCheck(present_bytes, False, None)

    digest = sha256_file(path)
    return ModelCheck(present_bytes, True, digest == model.sha256)


def sha256_file(path: Path, chunk_size: int = 8 * 1024**2) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_string_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    entries = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in entries):
        return None
    return cast(dict[str, object], entries)


def _required_string(entry: dict[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Required model field {key} must be a non-empty string.")
    return value
