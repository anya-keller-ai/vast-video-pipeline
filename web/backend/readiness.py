from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
from pathlib import Path
from typing import Literal

import aiohttp

from .config import Settings
from .models import ModelSpec, load_model_specs

ReadinessPhase = Literal[
    "starting",
    "downloading",
    "verifying",
    "starting-comfyui",
    "ready",
    "error",
]
ModelStatus = Literal["waiting", "downloading", "verifying", "ready"]
ComfyStatus = Literal["waiting", "starting", "ready"]


class ReadinessManager:
    def __init__(self, settings: Settings, session: aiohttp.ClientSession) -> None:
        self._settings: Settings = settings
        self._session: aiohttp.ClientSession = session
        self._models: tuple[ModelSpec, ...] = load_model_specs(
            settings.model_manifest_path
        )
        self._phase: ReadinessPhase = "starting"
        self._comfy_status: ComfyStatus = "waiting"
        self._current_message: str = "Ожидаю подключения к Vast."
        self._current_model_id: str | None = None
        self._current_model_progress: int = 0
        self._verifying_model: bool = False
        self._verified_model_ids: set[str] = set()
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._error: str | None = None
        self._cancel_requested: bool = False
        self._output_buffer: str = ""
        self._last_output_line: str = ""

    @property
    def ready(self) -> bool:
        return self._phase == "ready"

    async def start(self) -> dict[str, object]:
        if not self._settings.real_connection:
            raise RuntimeError("REAL_CONNECTION is disabled.")
        if self.ready:
            return self.snapshot()
        if self._task is None or self._task.done():
            self._reset_for_retry()
            self._task = asyncio.create_task(self._prepare())
        return self.snapshot()

    async def cancel(self) -> dict[str, object]:
        task = self._task
        process = self._process
        if (
            task is None
            or task.done()
            or process is None
            or process.returncode is not None
        ):
            raise RuntimeError("No model download is currently running.")

        self._cancel_requested = True
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        _ = await asyncio.gather(task, return_exceptions=True)
        self._set_cancelled()
        self._process = None
        return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        models: list[dict[str, object]] = []
        for model in self._models:
            status, progress = self._model_status(model)
            models.append(
                {
                    "id": model.id,
                    "repository": model.repository,
                    "target": model.target,
                    "bytes": model.bytes,
                    "sha256": model.sha256,
                    "required": True,
                    "status": status,
                    "progress": progress,
                    "downloadedBytes": model.bytes
                    if status == "ready"
                    else min(self._model_file_bytes(model), model.bytes),
                }
            )

        return {
            "phase": self._phase,
            "models": models,
            "comfyUi": self._comfy_status,
            "currentMessage": self._current_message,
            "error": self._error,
        }

    async def close(self) -> None:
        task = self._task
        process = self._process
        if task is None or task.done():
            return
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        _ = await asyncio.gather(task, return_exceptions=True)

    async def _prepare(self) -> None:
        try:
            self._current_message = "Проверяю доступность ComfyUI."
            self._comfy_status = "starting"
            if await self._comfy_is_ready():
                self._comfy_status = "ready"

            self._phase = "downloading"
            self._current_message = "Запускаю проверку и подготовку файлов из manifest."
            await self._run_model_script()
            if self._cancel_requested:
                self._set_cancelled()
                return
            if len(self._verified_model_ids) != len(self._models):
                raise RuntimeError(
                    "Скрипт завершился без проверки всех обязательных моделей."
                )

            if not await self._comfy_is_ready():
                self._phase = "starting-comfyui"
                self._comfy_status = "starting"
                self._current_message = "Запускаю ComfyUI предусмотренным скриптом."
                await self._start_comfyui()

            if self._comfy_status != "ready":
                raise RuntimeError("ComfyUI не ответил на health-check после запуска.")
            self._phase = "ready"
            self._current_message = "Модели проверены, ComfyUI готов."
            self._error = None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._cancel_requested:
                self._set_cancelled()
                return
            self._phase = "error"
            self._error = _safe_error_message(error)
            self._current_message = self._error

    async def _run_model_script(self) -> None:
        if not self._settings.prepare_models_script.is_file():
            raise RuntimeError("Не найден scripts/prepare-models.sh.")

        environment = os.environ.copy()
        environment["MANIFEST_PATH"] = str(self._settings.model_manifest_path)
        environment["MODEL_ROOT"] = str(self._settings.model_root)
        environment["HF_HUB_DISABLE_PROGRESS_BARS"] = "0"
        self._process = await asyncio.create_subprocess_exec(
            "bash",
            str(self._settings.prepare_models_script),
            cwd=self._settings.project_root,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        output = self._process.stdout
        if output is None:
            raise RuntimeError("Не удалось открыть журнал подготовки моделей.")

        while chunk := await output.read(8_192):
            self._consume_output(chunk.decode("utf-8", errors="replace"))
        self._consume_output("\n", flush=True)
        return_code = await self._process.wait()
        if return_code != 0:
            if self._cancel_requested:
                return
            detail = self._last_output_message()
            raise RuntimeError(
                detail or f"Подготовка моделей завершилась с кодом {return_code}."
            )

    def _consume_output(self, chunk: str, flush: bool = False) -> None:
        self._output_buffer += chunk
        lines = re.split(r"[\r\n]+", self._output_buffer)
        self._output_buffer = "" if flush else lines.pop()
        for line in lines:
            self.consume_script_output(line.strip())

    def consume_script_output(self, line: str) -> None:
        if not line:
            return
        self._last_output_line = line
        match = re.match(r"(?:GET|OK|DONE|VERIFY)\s+([A-Za-z0-9_.-]+)", line)
        if match is not None:
            model_id = match.group(1)
            if line.startswith("VERIFY "):
                self._current_model_id = model_id
                self._verifying_model = True
                self._phase = "verifying"
                self._current_message = (
                    f"Проверяю размер и SHA-256: {self._filename(model_id)}."
                )
            elif line.startswith("GET "):
                self._verifying_model = False
                self._current_model_id = model_id
                self._current_model_progress = 0
                self._phase = "downloading"
                self._current_message = f"Загружается {self._filename(model_id)}."
            else:
                self._verified_model_ids.add(model_id)
                self._current_model_id = None
                self._current_model_progress = 100
                self._current_message = (
                    f"Размер и SHA-256 проверены: {self._filename(model_id)}."
                )
            return

        if self._current_model_id is not None:
            progress_match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)%", line)
            if progress_match is not None:
                self._current_model_progress = min(
                    99, max(0, round(float(progress_match.group(1))))
                )
                model_name = self._filename(self._current_model_id)
                self._current_message = f"Загружается {model_name}."
            elif line.startswith("ERROR:"):
                self._current_message = line

    def _model_status(self, model: ModelSpec) -> tuple[ModelStatus, int]:
        if model.id in self._verified_model_ids:
            return "ready", 100
        if model.id != self._current_model_id:
            return "waiting", 0

        measured_bytes = self._model_file_bytes(model)
        measured_progress = round(min(measured_bytes, model.bytes) / model.bytes * 100)
        progress = measured_progress
        return ("verifying" if self._verifying_model else "downloading"), progress

    def _model_file_bytes(self, model: ModelSpec) -> int:
        staging_path = self._settings.model_root / ".staging" / model.source
        target_path = self._settings.model_root / model.target
        sizes: list[int] = []
        candidates = (
            staging_path,
            target_path,
            staging_path.with_name(f"{staging_path.name}.incomplete"),
        )
        cache_root = (
            self._settings.model_root
            / ".staging"
            / ".cache"
            / "huggingface"
            / "download"
            / Path(model.source).parent
        )
        source_filename = Path(model.source).name
        incomplete = tuple(
            path
            for path in cache_root.glob("*.incomplete")
            if path.name.startswith(f"{source_filename}.") or model.sha256 in path.name
        )
        for path in candidates:
            try:
                sizes.append(path.stat().st_size)
            except OSError:
                continue

        incomplete_sizes: list[tuple[int, int]] = []
        for path in incomplete:
            try:
                file_stat = path.stat()
                incomplete_sizes.append((file_stat.st_mtime_ns, file_stat.st_size))
            except OSError:
                continue

        active_incomplete_bytes = max(incomplete_sizes, default=(0, 0))[1]
        return max((*sizes, active_incomplete_bytes), default=0)

    async def _comfy_is_ready(self) -> bool:
        timeout = aiohttp.ClientTimeout(total=3)
        try:
            async with self._session.get(
                f"{self._settings.comfyui_url}/system_stats", timeout=timeout
            ) as response:
                return response.status == 200
        except (TimeoutError, aiohttp.ClientError):
            return False

    async def _start_comfyui(self) -> None:
        script = self._settings.start_comfyui_script
        if not script.is_file():
            raise RuntimeError("Не найден scripts/start.sh.")
        environment = os.environ.copy()
        environment["COMFYUI_ROOT"] = str(self._settings.comfyui_root)
        environment["DATA_ROOT"] = str(self._settings.data_root)
        environment["COMFYUI_LISTEN"] = "127.0.0.1"
        environment["COMFYUI_PORT"] = "8188"
        process = await asyncio.create_subprocess_exec(
            "bash",
            str(script),
            cwd=self._settings.project_root,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except TimeoutError as error:
            process.terminate()
            _ = await process.wait()
            raise RuntimeError(
                "Скрипт запуска ComfyUI не завершился вовремя."
            ) from error
        if process.returncode != 0:
            message = output.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or "Не удалось запустить ComfyUI.")

        deadline = asyncio.get_running_loop().time() + 120
        while asyncio.get_running_loop().time() < deadline:
            if await self._comfy_is_ready():
                self._comfy_status = "ready"
                return
            await asyncio.sleep(self._settings.readiness_poll_seconds)

    def _filename(self, model_id: str) -> str:
        model = next(
            (candidate for candidate in self._models if candidate.id == model_id), None
        )
        return model.filename if model is not None else model_id

    def _last_output_message(self) -> str | None:
        message = self._last_output_line.strip()
        return message[-1_000:] if message else None

    def _reset_for_retry(self) -> None:
        self._phase = "starting"
        self._comfy_status = "waiting"
        self._current_message = "Проверяю состояние моделей и ComfyUI."
        self._current_model_id = None
        self._current_model_progress = 0
        self._verified_model_ids.clear()
        self._error = None
        self._cancel_requested = False
        self._output_buffer = ""
        self._last_output_line = ""
        self._process = None

    def _set_cancelled(self) -> None:
        self._phase = "error"
        self._error = "Загрузка остановлена. Можно безопасно повторить подготовку."
        self._current_message = self._error
        self._current_model_id = None
        self._comfy_status = "ready" if self._comfy_status == "ready" else "waiting"


def _safe_error_message(error: Exception) -> str:
    message = str(error).strip()
    return (
        message[:1_000]
        if message
        else "Подготовка Vast завершилась с неизвестной ошибкой."
    )
