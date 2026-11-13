from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import signal
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Literal, TypedDict

from .config import Settings
from .models import ModelBundleSpec, ModelSpec, check_model, load_model_registry

BundlePhase = Literal["waiting", "downloading", "verifying", "ready", "error"]
AssetPhase = Literal["waiting", "downloading", "verifying", "ready", "invalid"]


class AssetSnapshot(TypedDict):
    id: str
    repository: str
    revision: str | None
    source: str
    target: str
    bytes: int
    sha256: str
    format: str
    loader: str
    status: AssetPhase
    progress: int
    downloadedBytes: int
    storageBytes: int
    error: str | None
    shared: bool


class BundleSnapshot(TypedDict):
    id: str
    title: str
    runtime: str
    workflows: list[str]
    enabled: bool
    blockedReason: str | None
    executionEnabled: bool
    executionBlockedReason: str | None
    phase: BundlePhase
    assets: list[AssetSnapshot]
    totalBytes: int
    readyBytes: int
    pendingBytes: int
    downloadProgress: int
    freeDiskBytes: int
    diskRequiredBytes: int
    diskSufficient: bool
    canDownload: bool
    canDelete: bool
    deleteBlockedReason: str | None
    currentMessage: str | None
    error: str | None


class BundlesSnapshot(TypedDict):
    bundles: list[BundleSnapshot]
    activeDownloadBundleId: str | None
    freeDiskBytes: int
    minimumFreeBytes: int


class BundleDeleteResult(TypedDict):
    bundleId: str
    deletedFiles: list[str]
    preservedSharedAssets: list[str]
    freedBytes: int


class ModelBundleError(Exception):
    pass


class ModelBundleManager:
    def __init__(
        self,
        settings: Settings,
        download_runner: Callable[[ModelBundleSpec], Coroutine[object, object, None]]
        | None = None,
    ) -> None:
        registry = load_model_registry(settings.model_manifest_path)
        self._settings: Settings = settings
        self._download_runner: Callable[
            [ModelBundleSpec], Coroutine[object, object, None]
        ] = download_runner or self._download
        self._assets: dict[str, ModelSpec] = {
            asset.id: asset for asset in registry.assets
        }
        self._bundles: dict[str, ModelBundleSpec] = {
            bundle.id: bundle for bundle in registry.bundles
        }
        self._active_bundle_id: str | None = None
        self._current_asset_id: str | None = None
        self._current_progress: int = 0
        self._current_phase: Literal["downloading", "verifying"] = "downloading"
        self._current_message: str | None = None
        self._error: str | None = None
        self._error_bundle_id: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._download_task: asyncio.Task[None] | None = None
        self._verified_signatures: dict[str, tuple[tuple[int, int, int], bool]] = {}
        self._verification_tasks: dict[str, asyncio.Task[None]] = {}
        self._bundle_in_use: set[str] = set()
        self._output_buffer: str = ""
        self._last_output_line: str = ""
        self._cancel_requested: bool = False
        self._snapshot_lock: asyncio.Lock = asyncio.Lock()
        self._operation_lock: asyncio.Lock = asyncio.Lock()

    async def snapshot(self) -> BundlesSnapshot:
        if not self._settings.real_connection:
            bundles = [
                self._mock_bundle_snapshot(bundle) for bundle in self._bundles.values()
            ]
            return {
                "bundles": bundles,
                "activeDownloadBundleId": None,
                "freeDiskBytes": 0,
                "minimumFreeBytes": self._settings.min_free_bytes,
            }
        async with self._snapshot_lock:
            bundle_values: list[BundleSnapshot] = []
            for bundle in self._bundles.values():
                bundle_values.append(await self._bundle_snapshot(bundle))
            free_bytes = self._available_disk_bytes()
            return {
                "bundles": bundle_values,
                "activeDownloadBundleId": self._active_bundle_id,
                "freeDiskBytes": free_bytes,
                "minimumFreeBytes": self._settings.min_free_bytes,
            }

    async def bundle_ready(self, bundle_id: str) -> bool:
        bundle = self._get_bundle(bundle_id)
        if not bundle.enabled:
            return False
        snapshot = await self._bundle_snapshot(bundle)
        return snapshot["phase"] == "ready"

    async def start_download(self, bundle_id: str) -> BundleSnapshot:
        async with self._operation_lock:
            return await self._start_download(bundle_id)

    async def _start_download(self, bundle_id: str) -> BundleSnapshot:
        bundle = self._get_bundle(bundle_id)
        if not self._settings.real_connection:
            raise ModelBundleError("REAL_CONNECTION отключён; загрузка недоступна.")
        if not bundle.enabled:
            raise ModelBundleError(bundle.blocked_reason or "Этот комплект недоступен.")
        if self._active_bundle_id is not None:
            raise ModelBundleError(
                f"Уже загружается {self._active_bundle_id}; дождись завершения."
            )
        if bundle_id in self._bundle_in_use:
            raise ModelBundleError(
                "Модели комплекта используются активной GPU-задачей."
            )

        bundle_snapshot = await self._bundle_snapshot(bundle)
        asset_values = bundle_snapshot["assets"]
        invalid = [
            asset["id"] for asset in asset_values if asset["status"] == "invalid"
        ]
        if invalid:
            names = ", ".join(str(identifier) for identifier in invalid)
            raise ModelBundleError(f"Повреждены файлы ({names}); удали их вручную.")
        if any(asset["status"] == "verifying" for asset in asset_values):
            raise ModelBundleError(
                "Сначала дождись проверки SHA-256 существующих файлов."
            )
        if bundle_snapshot["phase"] == "ready":
            return bundle_snapshot

        required_bytes = sum(
            asset["bytes"] - asset["downloadedBytes"]
            for asset in asset_values
            if asset["status"] != "ready"
        )
        free_bytes = self._available_disk_bytes()
        if free_bytes < required_bytes + self._settings.min_free_bytes:
            message = (
                f"Недостаточно места: нужно {required_bytes} байт плюс запас "
                f"{self._settings.min_free_bytes}; свободно {free_bytes}."
            )
            raise ModelBundleError(message)

        self._active_bundle_id = bundle_id
        self._current_asset_id = None
        self._current_progress = 0
        self._current_phase = "downloading"
        self._current_message = "Запускаю загрузку выбранного комплекта."
        self._error = None
        self._error_bundle_id = None
        self._cancel_requested = False
        self._output_buffer = ""
        self._last_output_line = ""
        self._download_task = asyncio.create_task(self._download_runner(bundle))
        return await self._bundle_snapshot(bundle)

    async def cancel_download(self, bundle_id: str) -> BundleSnapshot:
        async with self._operation_lock:
            return await self._cancel_download(bundle_id)

    async def _cancel_download(self, bundle_id: str) -> BundleSnapshot:
        if self._active_bundle_id != bundle_id:
            raise ModelBundleError("Этот комплект сейчас не загружается.")
        process = self._process
        task = self._download_task
        if process is None or task is None or task.done():
            raise ModelBundleError("Загрузка уже завершилась.")
        self._cancel_requested = True
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        _ = await asyncio.gather(task, return_exceptions=True)
        self._active_bundle_id = None
        self._process = None
        self._download_task = None
        self._current_asset_id = None
        self._current_message = "Загрузка остановлена; частичные файлы сохранены."
        self._error = None
        self._error_bundle_id = None
        return await self._bundle_snapshot(self._get_bundle(bundle_id))

    async def delete_bundle(self, bundle_id: str) -> BundleDeleteResult:
        async with self._operation_lock:
            return await self._delete_bundle(bundle_id)

    async def _delete_bundle(self, bundle_id: str) -> BundleDeleteResult:
        bundle = self._get_bundle(bundle_id)
        if self._active_bundle_id is not None:
            raise ModelBundleError("Сначала дождись завершения текущей загрузки.")
        if bundle_id in self._bundle_in_use:
            raise ModelBundleError(
                "Модели комплекта используются активной GPU-задачей."
            )
        if any(
            (task := self._verification_tasks.get(asset_id)) is not None
            and not task.done()
            for asset_id in bundle.asset_ids
        ):
            raise ModelBundleError(
                "Дождись завершения проверки файлов перед удалением."
            )

        deleted_files: list[str] = []
        preserved_shared: list[str] = []
        freed_bytes = 0
        for asset_id in bundle.asset_ids:
            asset = self._assets[asset_id]
            owners = [
                other.id
                for other in self._bundles.values()
                if other.id != bundle_id
                and any(
                    self._assets[other_asset_id].target == asset.target
                    for other_asset_id in other.asset_ids
                )
            ]
            if owners:
                preserved_shared.append(asset_id)
                continue
            for path in self._asset_paths(bundle, asset):
                existed = path.exists() or path.is_symlink()
                freed_bytes += _unlink_file(path)
                if existed:
                    deleted_files.append(str(path))
            _ = self._verified_signatures.pop(asset_id, None)

        return {
            "bundleId": bundle_id,
            "deletedFiles": deleted_files,
            "preservedSharedAssets": preserved_shared,
            "freedBytes": freed_bytes,
        }

    def mark_in_use(self, bundle_id: str) -> None:
        _ = self._get_bundle(bundle_id)
        self._bundle_in_use.add(bundle_id)

    def mark_available(self, bundle_id: str) -> None:
        self._bundle_in_use.discard(bundle_id)

    def bundle_in_use(self, bundle_id: str) -> bool:
        return bundle_id in self._bundle_in_use

    def required_nodes(self, bundle_id: str) -> tuple[str, ...]:
        return self._get_bundle(bundle_id).required_nodes

    def execution_blocked_reason(self, bundle_id: str) -> str | None:
        bundle = self._get_bundle(bundle_id)
        return bundle.execution_blocked_reason if not bundle.execution_enabled else None

    async def close(self) -> None:
        task = self._download_task
        process = self._process
        if task is not None and not task.done():
            if process is not None and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
            _ = await asyncio.gather(task, return_exceptions=True)
        for verify_task in self._verification_tasks.values():
            if not verify_task.done():
                _ = verify_task.cancel()
        if self._verification_tasks:
            _ = await asyncio.gather(
                *self._verification_tasks.values(), return_exceptions=True
            )

    def _mock_bundle_snapshot(self, bundle: ModelBundleSpec) -> BundleSnapshot:
        assets: list[AssetSnapshot] = []
        for asset_id in bundle.asset_ids:
            asset = self._assets[asset_id]
            assets.append(
                {
                    "id": asset.id,
                    "repository": asset.repository,
                    "revision": asset.revision,
                    "source": asset.source,
                    "target": asset.target,
                    "bytes": asset.bytes,
                    "sha256": asset.sha256,
                    "format": asset.file_format,
                    "loader": asset.loader,
                    "status": "waiting",
                    "progress": 0,
                    "downloadedBytes": 0,
                    "storageBytes": 0,
                    "error": None,
                    "shared": self._asset_is_shared(bundle, asset),
                }
            )
        total_bytes = sum(asset["bytes"] for asset in assets)
        return {
            "id": bundle.id,
            "title": bundle.title,
            "runtime": bundle.runtime,
            "workflows": list(bundle.workflows),
            "enabled": bundle.enabled,
            "blockedReason": bundle.blocked_reason,
            "executionEnabled": bundle.execution_enabled,
            "executionBlockedReason": bundle.execution_blocked_reason,
            "phase": "waiting",
            "assets": assets,
            "totalBytes": total_bytes,
            "readyBytes": 0,
            "pendingBytes": total_bytes,
            "downloadProgress": 0,
            "freeDiskBytes": 0,
            "diskRequiredBytes": total_bytes + self._settings.min_free_bytes,
            "diskSufficient": False,
            "canDownload": False,
            "canDelete": False,
            "deleteBlockedReason": "Mock-режим не меняет файлы моделей.",
            "currentMessage": None,
            "error": None,
        }

    async def _bundle_snapshot(self, bundle: ModelBundleSpec) -> BundleSnapshot:
        assets = [
            await self._asset_snapshot(bundle, self._assets[asset_id])
            for asset_id in bundle.asset_ids
        ]
        ready_bytes = sum(
            asset["bytes"] for asset in assets if asset["status"] == "ready"
        )
        total_bytes = sum(asset["bytes"] for asset in assets)
        downloaded_bytes = sum(asset["downloadedBytes"] for asset in assets)
        pending_bytes = max(0, total_bytes - downloaded_bytes)
        invalid = any(asset["status"] == "invalid" for asset in assets)
        verifying = any(asset["status"] == "verifying" for asset in assets)
        all_ready = bool(assets) and all(asset["status"] == "ready" for asset in assets)
        if not bundle.enabled:
            phase: BundlePhase = "error"
        elif self._active_bundle_id == bundle.id:
            phase = "verifying" if self._current_phase == "verifying" else "downloading"
        elif self._error is not None and self._error_bundle_id == bundle.id:
            phase = "error"
        elif all_ready:
            phase = "ready"
        elif verifying:
            phase = "verifying"
        else:
            phase = "waiting"

        free_bytes = self._available_disk_bytes()
        disk_sufficient = free_bytes >= pending_bytes + self._settings.min_free_bytes
        can_download = (
            bundle.enabled
            and self._active_bundle_id is None
            and not invalid
            and not verifying
            and not all_ready
            and disk_sufficient
        )
        return {
            "id": bundle.id,
            "title": bundle.title,
            "runtime": bundle.runtime,
            "workflows": list(bundle.workflows),
            "enabled": bundle.enabled,
            "blockedReason": bundle.blocked_reason,
            "executionEnabled": bundle.execution_enabled,
            "executionBlockedReason": bundle.execution_blocked_reason,
            "phase": phase,
            "assets": assets,
            "totalBytes": total_bytes,
            "readyBytes": ready_bytes,
            "pendingBytes": pending_bytes,
            "downloadProgress": (
                min(100, round(downloaded_bytes / total_bytes * 100))
                if total_bytes > 0
                else 0
            ),
            "freeDiskBytes": free_bytes,
            "diskRequiredBytes": pending_bytes + self._settings.min_free_bytes,
            "diskSufficient": disk_sufficient,
            "canDownload": can_download,
            "canDelete": (
                self._active_bundle_id is None
                and bundle.id not in self._bundle_in_use
                and not verifying
            ),
            "deleteBlockedReason": (
                "Дождись завершения загрузки моделей."
                if self._active_bundle_id is not None
                else "Модели используются активной GPU-задачей."
                if bundle.id in self._bundle_in_use
                else "Дождись завершения проверки файлов."
                if verifying
                else None
            ),
            "currentMessage": (
                self._current_message
                if self._active_bundle_id == bundle.id
                else bundle.blocked_reason
                if not bundle.enabled
                else None
            ),
            "error": (self._error if self._error_bundle_id == bundle.id else None),
        }

    async def _asset_snapshot(
        self, bundle: ModelBundleSpec, asset: ModelSpec
    ) -> AssetSnapshot:
        target = self._model_path(asset)
        if target.is_symlink():
            status: AssetPhase = "invalid"
            downloaded_bytes = 0
            error = "Целевой путь модели является символической ссылкой."
        else:
            try:
                stat = await asyncio.to_thread(target.stat)
            except OSError:
                stat = None
            if stat is None:
                downloaded_bytes = self._partial_bytes(bundle, asset)
                status = "waiting"
                error = None
            elif not target.is_file() or stat.st_size != asset.bytes:
                downloaded_bytes = min(stat.st_size, asset.bytes)
                status = "invalid"
                error = "Размер файла не совпадает с manifest."
            else:
                signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
                cached = self._verified_signatures.get(asset.id)
                if cached is not None and cached[0] == signature:
                    status = "ready" if cached[1] else "invalid"
                    downloaded_bytes = asset.bytes
                    error = None if cached[1] else "SHA-256 не совпадает с manifest."
                else:
                    self._schedule_verification(asset, target, signature)
                    status = "verifying"
                    downloaded_bytes = asset.bytes
                    error = None

        if self._active_bundle_id == bundle.id and self._current_asset_id == asset.id:
            status = self._current_phase
            downloaded_bytes = max(
                downloaded_bytes,
                round(asset.bytes * self._current_progress / 100),
            )
            error = None
        return {
            "id": asset.id,
            "repository": asset.repository,
            "revision": asset.revision,
            "source": asset.source,
            "target": asset.target,
            "bytes": asset.bytes,
            "sha256": asset.sha256,
            "format": asset.file_format,
            "loader": asset.loader,
            "status": status,
            "progress": min(100, round(downloaded_bytes / asset.bytes * 100)),
            "downloadedBytes": min(asset.bytes, downloaded_bytes),
            "storageBytes": self._asset_storage_bytes(bundle, asset),
            "error": error,
            "shared": self._asset_is_shared(bundle, asset),
        }

    def _schedule_verification(
        self, asset: ModelSpec, path: Path, signature: tuple[int, int, int]
    ) -> None:
        task = self._verification_tasks.get(asset.id)
        if task is None or task.done():
            self._verification_tasks[asset.id] = asyncio.create_task(
                self._verify_asset(asset, path, signature)
            )

    async def _verify_asset(
        self, asset: ModelSpec, path: Path, signature: tuple[int, int, int]
    ) -> None:
        try:
            check = await asyncio.to_thread(check_model, path, asset)
            stat = await asyncio.to_thread(path.stat)
            if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != signature:
                return
            self._verified_signatures[asset.id] = (signature, check.ready)
        except OSError:
            _ = self._verified_signatures.pop(asset.id, None)

    async def _download(self, bundle: ModelBundleSpec) -> None:
        try:
            if not self._settings.prepare_models_script.is_file():
                raise RuntimeError("Не найден scripts/prepare-models.sh.")
            environment = os.environ.copy()
            environment["MANIFEST_PATH"] = str(self._settings.model_manifest_path)
            environment["MODEL_ROOT"] = str(self._settings.model_root)
            environment["MIN_FREE_BYTES"] = str(self._settings.min_free_bytes)
            self._process = await asyncio.create_subprocess_exec(
                "bash",
                str(self._settings.prepare_models_script),
                "--bundle",
                bundle.id,
                cwd=self._settings.project_root,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            output = self._process.stdout
            if output is None:
                raise RuntimeError("Не удалось открыть журнал загрузки моделей.")
            while chunk := await output.read(8_192):
                self._consume_output(chunk.decode("utf-8", errors="replace"))
            self._consume_output("\n", flush=True)
            return_code = await self._process.wait()
            if return_code != 0:
                if self._cancel_requested:
                    return
                raise RuntimeError(
                    self._last_output_line[-1_000:]
                    or f"Загрузка завершилась с кодом {return_code}."
                )
            self._current_message = "Загрузка и проверка комплекта завершены."
            self._error = None
            self._error_bundle_id = None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._cancel_requested:
                self._current_message = (
                    "Загрузка остановлена; частичные файлы сохранены."
                )
                return
            self._error = _safe_error(error)
            self._error_bundle_id = bundle.id
            self._current_message = self._error
        finally:
            self._process = None
            self._active_bundle_id = None
            self._current_asset_id = None
            self._download_task = None

    def _consume_output(self, chunk: str, flush: bool = False) -> None:
        self._output_buffer += chunk
        lines = re.split(r"[\r\n]+", self._output_buffer)
        self._output_buffer = "" if flush else lines.pop()
        for line in lines:
            self._consume_line(line.strip())

    def _consume_line(self, line: str) -> None:
        if not line:
            return
        self._last_output_line = line
        match = re.match(r"(?:GET|DONE|VERIFY|OK)\s+([A-Za-z0-9_.-]+)", line)
        if match is not None:
            asset_id = match.group(1)
            if line.startswith("GET "):
                self._current_asset_id = asset_id
                self._current_phase = "downloading"
                self._current_progress = 0
                self._current_message = f"Загружается {self._asset_filename(asset_id)}."
            elif line.startswith("VERIFY "):
                self._current_asset_id = asset_id
                self._current_phase = "verifying"
                self._current_progress = 100
                self._current_message = (
                    f"Проверяю SHA-256: {self._asset_filename(asset_id)}."
                )
            elif line.startswith(("DONE ", "OK ")):
                self._current_asset_id = None
                self._current_progress = 100
                self._current_message = f"Проверен: {self._asset_filename(asset_id)}."
                asset = self._assets.get(asset_id)
                if asset is not None:
                    self._cache_verified_asset(asset)
            return
        if self._current_asset_id is not None:
            progress_match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)%", line)
            if progress_match is not None:
                self._current_progress = min(
                    99, max(0, round(float(progress_match.group(1))))
                )
                self._current_message = (
                    f"Загружается {self._asset_filename(self._current_asset_id)}."
                )

    def _cache_verified_asset(self, asset: ModelSpec) -> None:
        path = self._model_path(asset)
        try:
            stat = path.stat()
        except OSError:
            return
        signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
        if stat.st_size == asset.bytes:
            self._verified_signatures[asset.id] = (signature, True)

    def _partial_bytes(self, bundle: ModelBundleSpec, asset: ModelSpec) -> int:
        stage = self._staging_path(bundle, asset)
        try:
            sizes = [stage.stat().st_size]
        except OSError:
            sizes = []
        cache_dir = (
            self._settings.model_root
            / ".staging"
            / bundle.id
            / ".cache"
            / "huggingface"
            / "download"
            / Path(asset.source).parent
        )
        for path in cache_dir.glob("*.incomplete"):
            if (
                path.name.startswith(f"{Path(asset.source).name}.")
                or asset.sha256 in path.name
            ):
                with contextlib.suppress(OSError):
                    sizes.append(path.stat().st_size)
        return min(asset.bytes, max(sizes, default=0))

    def _asset_paths(
        self, bundle: ModelBundleSpec, asset: ModelSpec
    ) -> tuple[Path, ...]:
        target = self._model_path(asset)
        staging = self._staging_path(bundle, asset)
        root = self._settings.model_root.resolve()
        paths = (target, staging)
        cache_dir = (
            self._settings.model_root
            / ".staging"
            / bundle.id
            / ".cache"
            / "huggingface"
            / "download"
            / Path(asset.source).parent
        )
        cache_paths = tuple(
            path
            for path in cache_dir.glob("*.incomplete")
            if path.name.startswith(f"{Path(asset.source).name}.")
            or asset.sha256 in path.name
        )
        paths = (*paths, *cache_paths)
        for path in paths:
            if not path.resolve(strict=False).is_relative_to(root):
                raise ModelBundleError("Model path resolves outside model_root.")
        return paths

    def _model_path(self, asset: ModelSpec) -> Path:
        return self._settings.model_root / asset.target

    def _staging_path(self, bundle: ModelBundleSpec, asset: ModelSpec) -> Path:
        return self._settings.model_root / ".staging" / bundle.id / asset.source

    def _available_disk_bytes(self) -> int:
        path = self._settings.model_root
        while not path.exists() and path != path.parent:
            path = path.parent
        return shutil.disk_usage(path).free

    def _asset_storage_bytes(self, bundle: ModelBundleSpec, asset: ModelSpec) -> int:
        total = 0
        try:
            paths = self._asset_paths(bundle, asset)
        except ModelBundleError:
            return 0
        for path in paths:
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def _asset_is_shared(self, bundle: ModelBundleSpec, asset: ModelSpec) -> bool:
        return any(
            other.id != bundle.id
            and any(
                self._assets[other_asset_id].target == asset.target
                for other_asset_id in other.asset_ids
            )
            for other in self._bundles.values()
        )

    def _asset_filename(self, asset_id: str) -> str:
        asset = self._assets.get(asset_id)
        return asset.filename if asset is not None else asset_id

    def _get_bundle(self, bundle_id: str) -> ModelBundleSpec:
        bundle = self._bundles.get(bundle_id)
        if bundle is None:
            raise KeyError("Комплект моделей не найден.")
        return bundle


def _unlink_file(path: Path) -> int:
    try:
        if path.is_symlink() or path.is_file():
            size = 0 if path.is_symlink() else path.stat().st_size
            path.unlink()
            return size
        if path.exists():
            raise ModelBundleError(f"Не могу удалить не-файл модели: {path.name}")
    except OSError as error:
        raise ModelBundleError(
            f"Не удалось удалить файл модели {path.name}."
        ) from error
    return 0


def _safe_error(error: Exception) -> str:
    message = str(error).strip()
    if not message:
        return f"Загрузка моделей завершилась с ошибкой ({type(error).__name__})."
    return message[-1_000:]
