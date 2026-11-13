from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

import aiohttp
from aiohttp import WSMsgType

from .comfy import (
    ComfyUIClient,
    ComfyUIError,
    ComfyVideoResult,
    as_string_mapping,
    stringify_error,
)
from .config import Settings
from .flashvsr import FLASHVSR_SAVE_NODE_ID, build_flashvsr_prompt, flashvsr_bundle_id
from .flashvsr_progress import parse_flashvsr_progress
from .frame_interpolation import (
    INTERPOLATION_BUNDLE_ID,
    INTERPOLATION_SAVE_NODE_ID,
    build_interpolation_prompt,
)
from .media import normalize_reference_video, probe_video
from .model_bundles import ModelBundleManager
from .pika import PIKA_SAVE_NODE_ID, build_pika_ref2va_prompt
from .readiness import ReadinessManager
from .uploads import UploadedFile, UploadStore
from .upscale_model import (
    UPSCALE_MODEL_SAVE_NODE_ID,
    build_upscale_model_prompt,
    upscale_model_spec,
)
from .workflows import (
    H3_DEFAULT_RESOLUTION,
    H3_MAX_REFERENCE_AUDIOS,
    H3_MAX_REFERENCE_SECONDS,
    H3_MIN_REFERENCE_SECONDS,
    ComfyPrompt,
    build_h3_ref2v_prompt,
    build_seedvr2_prompt,
)

JobMode = Literal[
    "generation", "upscale", "pika", "flashvsr", "upscale_model", "interpolate"
]
JobStatus = Literal["queued", "running", "complete", "error", "cancelled"]
TERMINAL_STATUSES = frozenset({"complete", "error", "cancelled"})
CANCEL_CONFIRM_ATTEMPTS = 30
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".m4v"})


class JobRequestError(Exception):
    pass


class JobNotReadyError(JobRequestError):
    pass


@dataclass(slots=True)
class WorkbenchJob:
    id: str
    mode: JobMode
    prompt: ComfyPrompt
    output_filename: str
    current_message: str
    input_ids: set[str]
    derived_paths: list[Path]
    model_bundle_id: str
    status: JobStatus = "queued"
    prompt_id: str | None = None
    current_node: str | None = None
    progress_percent: int | None = None
    progress_label: str | None = None
    source_frames: int | None = None
    log_cursor: str = ""
    result: ComfyVideoResult | None = None
    error: str | None = None
    monitor_task: asyncio.Task[None] | None = None
    execution_finished: bool = False
    cancel_requested: bool = False
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    def elapsed_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(end - self.started_at, 0.0)


class JobManager:
    def __init__(
        self,
        settings: Settings,
        readiness: ReadinessManager,
        uploads: UploadStore,
        comfy: ComfyUIClient,
        model_bundles: ModelBundleManager | None = None,
    ) -> None:
        self._settings: Settings = settings
        self._readiness: ReadinessManager = readiness
        self._uploads: UploadStore = uploads
        self._comfy: ComfyUIClient = comfy
        self._model_bundles: ModelBundleManager | None = model_bundles
        self._jobs: dict[str, WorkbenchJob] = {}
        self._submit_lock: asyncio.Lock = asyncio.Lock()

    async def start_generation(
        self,
        prompt: str,
        reference_image_ids: list[str],
        reference_video_id: str | None,
        duration_seconds: float,
        use_turbo_lora: bool,
        reference_audio_ids: list[str] | None = None,
        resolution: str = H3_DEFAULT_RESOLUTION,
    ) -> dict[str, object]:
        async with self._submit_lock:
            return await self._start_generation(
                prompt,
                reference_image_ids,
                reference_video_id,
                duration_seconds,
                use_turbo_lora,
                reference_audio_ids,
                resolution,
            )

    async def _start_generation(
        self,
        prompt: str,
        reference_image_ids: list[str],
        reference_video_id: str | None,
        duration_seconds: float,
        use_turbo_lora: bool,
        reference_audio_ids: list[str] | None = None,
        resolution: str = H3_DEFAULT_RESOLUTION,
    ) -> dict[str, object]:
        bundle_id = "h3_turbo" if use_turbo_lora else "h3_base"
        await self._require_ready(bundle_id)
        await self._require_gpu_idle()
        if not prompt.strip():
            raise JobRequestError("Для генерации нужен непустой промт.")
        if len(reference_image_ids) > 9:
            raise JobRequestError("H3 Ref2V поддерживает не больше 9 изображений.")
        if len(reference_audio_ids or []) > H3_MAX_REFERENCE_AUDIOS:
            raise JobRequestError(
                f"H3 Ref2V поддерживает не больше {H3_MAX_REFERENCE_AUDIOS} "
                "аудио-референсов."
            )

        job_id = uuid.uuid4().hex
        uploaded: list[UploadedFile] = []
        derived_paths: list[Path] = []
        try:
            images = [
                self._get_uploaded_file(identifier, "image")
                for identifier in reference_image_ids
            ]
            uploaded.extend(images)
            audios = [
                self._get_uploaded_file(identifier, "audio")
                for identifier in reference_audio_ids or []
            ]
            uploaded.extend(audios)
            reference_video = None
            if reference_video_id is not None:
                source_video = self._get_uploaded_file(reference_video_id, "video")
                uploaded.append(source_video)
                metadata = await probe_video(source_video.path)
                if (
                    not H3_MIN_REFERENCE_SECONDS
                    <= metadata.duration_seconds
                    <= H3_MAX_REFERENCE_SECONDS
                ):
                    raise JobRequestError(
                        "H3 Ref2V: reference-видео от 2 до 15 секунд."
                    )
                normalized_path = (
                    self._settings.input_root / f"workbench-ref-{job_id}.mp4"
                )
                normalized_path = await normalize_reference_video(
                    source_video.path, normalized_path
                )
                derived_paths.append(normalized_path)
                reference_video = normalized_path.name

            workflow = build_h3_ref2v_prompt(
                prompt,
                job_id,
                [image.filename for image in images],
                reference_video,
                duration_seconds,
                use_turbo_lora,
                secrets.randbits(64),
                reference_audios=[audio.filename for audio in audios],
                resolution=resolution,
            )
            return await self._submit(
                job_id,
                "generation",
                workflow,
                {file.id for file in uploaded},
                derived_paths,
                bundle_id,
            )
        except Exception:
            self._cleanup_inputs({file.id for file in uploaded}, derived_paths)
            raise

    async def start_pika_generation(
        self,
        prompt: str,
        reference_image_ids: list[str],
        reference_video_id: str | None,
        duration_seconds: float,
        seed: int,
        ref_image_size: str = "match",
        reference_audio_ids: list[str] | None = None,
        resolution: str = H3_DEFAULT_RESOLUTION,
    ) -> dict[str, object]:
        async with self._submit_lock:
            return await self._start_pika_generation(
                prompt,
                reference_image_ids,
                reference_video_id,
                duration_seconds,
                seed,
                ref_image_size,
                reference_audio_ids,
                resolution,
            )

    async def _start_pika_generation(
        self,
        prompt: str,
        reference_image_ids: list[str],
        reference_video_id: str | None,
        duration_seconds: float,
        seed: int,
        ref_image_size: str = "match",
        reference_audio_ids: list[str] | None = None,
        resolution: str = H3_DEFAULT_RESOLUTION,
    ) -> dict[str, object]:
        bundle_id = "pika_h3_flex_ref2va"
        await self._require_ready(bundle_id)
        await self._require_gpu_idle()
        if not prompt.strip():
            raise JobRequestError("Для Pika нужен непустой промт.")
        job_id = uuid.uuid4().hex
        uploaded: list[UploadedFile] = []
        derived_paths: list[Path] = []
        try:
            images = [
                self._get_uploaded_file(identifier, "image")
                for identifier in reference_image_ids
            ]
            uploaded.extend(images)
            audios = [
                self._get_uploaded_file(identifier, "audio")
                for identifier in reference_audio_ids or []
            ]
            uploaded.extend(audios)
            reference_video = None
            if reference_video_id is not None:
                source_video = self._get_uploaded_file(reference_video_id, "video")
                uploaded.append(source_video)
                metadata = await probe_video(source_video.path)
                if (
                    not H3_MIN_REFERENCE_SECONDS
                    <= metadata.duration_seconds
                    <= H3_MAX_REFERENCE_SECONDS
                ):
                    raise JobRequestError(
                        "Pika Ref2VA: reference-видео от 2 до 15 секунд."
                    )
                normalized_path = await normalize_reference_video(
                    source_video.path,
                    self._settings.input_root / f"workbench-ref-{job_id}.mp4",
                )
                derived_paths.append(normalized_path)
                reference_video = normalized_path.name
            try:
                workflow = build_pika_ref2va_prompt(
                    prompt,
                    job_id,
                    [image.filename for image in images],
                    reference_video,
                    duration_seconds,
                    seed,
                    ref_image_size,
                    reference_audios=[audio.filename for audio in audios],
                    resolution=resolution,
                )
            except ValueError as error:
                raise JobRequestError(str(error)) from error
            return await self._submit(
                job_id,
                "pika",
                workflow,
                {file.id for file in uploaded},
                derived_paths,
                bundle_id,
            )
        except Exception:
            self._cleanup_inputs({file.id for file in uploaded}, derived_paths)
            raise

    async def start_interpolation(
        self, video_id: str, model_key: str, multiplier: int
    ) -> dict[str, object]:
        async with self._submit_lock:
            return await self._start_interpolation(video_id, model_key, multiplier)

    async def _start_interpolation(
        self, video_id: str, model_key: str, multiplier: int
    ) -> dict[str, object]:
        await self._require_ready(INTERPOLATION_BUNDLE_ID)
        await self._require_gpu_idle()
        video = self._get_uploaded_file(video_id, "video")
        job_id = uuid.uuid4().hex
        try:
            metadata = await probe_video(video.path)
            try:
                workflow = build_interpolation_prompt(
                    job_id,
                    video.filename,
                    model_key,
                    multiplier,
                    metadata.width,
                    metadata.height,
                )
            except ValueError as error:
                raise JobRequestError(str(error)) from error
            return await self._submit(
                job_id, "interpolate", workflow, {video.id}, [], INTERPOLATION_BUNDLE_ID
            )
        except Exception:
            self._cleanup_inputs({video.id}, [])
            raise

    async def start_upscale_model(
        self, video_id: str, model_key: str
    ) -> dict[str, object]:
        async with self._submit_lock:
            return await self._start_upscale_model(video_id, model_key)

    async def _start_upscale_model(
        self, video_id: str, model_key: str
    ) -> dict[str, object]:
        try:
            spec = upscale_model_spec(model_key)
        except ValueError as error:
            raise JobRequestError(str(error)) from error
        await self._require_ready(spec.bundle_id)
        await self._require_gpu_idle()
        video = self._get_uploaded_file(video_id, "video")
        job_id = uuid.uuid4().hex
        try:
            try:
                workflow = build_upscale_model_prompt(job_id, video.filename, model_key)
            except ValueError as error:
                raise JobRequestError(str(error)) from error
            return await self._submit(
                job_id, "upscale_model", workflow, {video.id}, [], spec.bundle_id
            )
        except Exception:
            self._cleanup_inputs({video.id}, [])
            raise

    async def start_flashvsr(
        self, video_id: str, variant: str, scale: int
    ) -> dict[str, object]:
        async with self._submit_lock:
            return await self._start_flashvsr(video_id, variant, scale)

    async def _start_flashvsr(
        self, video_id: str, variant: str, scale: int
    ) -> dict[str, object]:
        try:
            bundle_id = flashvsr_bundle_id(variant)
        except ValueError as error:
            raise JobRequestError(str(error)) from error
        await self._require_ready(bundle_id)
        await self._require_gpu_idle()
        video = self._get_uploaded_file(video_id, "video")
        job_id = uuid.uuid4().hex
        try:
            metadata = await probe_video(video.path)
            try:
                workflow = build_flashvsr_prompt(
                    job_id, video.filename, variant, scale, secrets.randbits(32)
                )
            except ValueError as error:
                raise JobRequestError(str(error)) from error
            log_cursor = await self._latest_log_timestamp()
            result = await self._submit(
                job_id, "flashvsr", workflow, {video.id}, [], bundle_id
            )
            job = self._jobs[job_id]
            job.source_frames = round(metadata.duration_seconds * metadata.frame_rate)
            job.log_cursor = log_cursor
            return result
        except Exception:
            self._cleanup_inputs({video.id}, [])
            raise

    async def start_upscale(
        self,
        video_id: str,
        scale: int,
        model_key: str = "7b_sharp",
        batch_size: int = 1,
        temporal_overlap: int = 0,
    ) -> dict[str, object]:
        async with self._submit_lock:
            return await self._start_upscale(
                video_id, scale, model_key, batch_size, temporal_overlap
            )

    async def _start_upscale(
        self,
        video_id: str,
        scale: int,
        model_key: str,
        batch_size: int,
        temporal_overlap: int,
    ) -> dict[str, object]:
        bundle_id = "seedvr2_upscale"
        await self._require_ready(bundle_id)
        await self._require_gpu_idle()
        video = self._get_uploaded_file(video_id, "video")
        job_id = uuid.uuid4().hex
        try:
            metadata = await probe_video(video.path)
            try:
                workflow = build_seedvr2_prompt(
                    job_id,
                    video.filename,
                    scale,
                    metadata.short_edge,
                    model_key=model_key,
                    batch_size=batch_size,
                    temporal_overlap=temporal_overlap,
                )
            except ValueError as error:
                raise JobRequestError(str(error)) from error
            return await self._submit(
                job_id, "upscale", workflow, {video.id}, [], bundle_id
            )
        except Exception:
            self._cleanup_inputs({video.id}, [])
            raise

    def upload_in_use(self, upload_id: str) -> bool:
        return any(
            upload_id in job.input_ids
            and job.status != "complete"
            and not job.execution_finished
            for job in self._jobs.values()
        )

    def bundle_in_use(self, bundle_id: str) -> bool:
        return any(
            job.model_bundle_id == bundle_id
            and job.status != "complete"
            and not job.execution_finished
            for job in self._jobs.values()
        )

    def get(self, job_id: str) -> dict[str, object]:
        normalized_id = _normalize_job_id(job_id)
        job = self._jobs.get(normalized_id)
        if job is None:
            raise KeyError("Задача не найдена.")
        return self._snapshot(job)

    async def cancel(self, job_id: str) -> dict[str, object]:
        job = self._jobs.get(_normalize_job_id(job_id))
        if job is None:
            raise KeyError("Задача не найдена.")
        if job.status in TERMINAL_STATUSES:
            return self._snapshot(job)
        prompt_id = job.prompt_id
        if prompt_id is None:
            raise JobRequestError("У задачи ещё нет prompt ID в ComfyUI.")
        try:
            running, pending = await self._queue_ids()
            job.cancel_requested = True
            if prompt_id in pending:
                await self._comfy.delete_queued(prompt_id)
            elif prompt_id in running:
                if len(running) != 1:
                    raise JobRequestError(
                        "Выполняется несколько задач ComfyUI; отмена не отправлена."
                    )
                await self._comfy.interrupt()
            else:
                job.cancel_requested = False
                return self._snapshot(job)
            for _ in range(CANCEL_CONFIRM_ATTEMPTS):
                await asyncio.sleep(1)
                running, pending = await self._queue_ids()
                if prompt_id not in running and prompt_id not in pending:
                    break
            else:
                raise JobRequestError(
                    "Не удалось подтвердить отмену: задача всё ещё в очереди ComfyUI."
                )
        except (ComfyUIError, JobRequestError) as error:
            job.cancel_requested = False
            if isinstance(error, JobRequestError):
                raise
            raise JobRequestError(str(error)) from error
        job.status = "cancelled"
        job.current_message = "Отменено пользователем."
        job.progress_percent = None
        job.progress_label = None
        job.finished_at = time.monotonic()
        return self._snapshot(job)

    async def _queue_ids(self) -> tuple[list[str], list[str]]:
        queue = await self._comfy.queue()
        return _queue_prompt_ids(queue.get("queue_running")), _queue_prompt_ids(
            queue.get("queue_pending")
        )

    def get_output(self, job_id: str) -> tuple[ComfyVideoResult, str]:
        normalized_id = _normalize_job_id(job_id)
        job = self._jobs.get(normalized_id)
        if job is None:
            raise KeyError("Задача не найдена.")
        if job.status != "complete" or job.result is None:
            raise JobRequestError("Результат появится после завершения ComfyUI job.")
        return job.result, job.output_filename

    async def close(self) -> None:
        tasks = [
            job.monitor_task
            for job in self._jobs.values()
            if job.monitor_task is not None
        ]
        for job in self._jobs.values():
            if job.monitor_task is not None and not job.monitor_task.done():
                _ = job.monitor_task.cancel()
        if tasks:
            _ = await asyncio.gather(*tasks, return_exceptions=True)

    async def _submit(
        self,
        job_id: str,
        mode: JobMode,
        workflow: ComfyPrompt,
        input_ids: set[str],
        derived_paths: list[Path],
        model_bundle_id: str,
    ) -> dict[str, object]:
        await self._require_gpu_idle()
        client_id = uuid.uuid4().hex
        websocket = await self._comfy.connect_websocket(client_id)
        try:
            prompt_id = await self._comfy.submit_prompt(workflow, client_id)
        except Exception:
            _ = await websocket.close()
            self._cleanup_inputs(input_ids, derived_paths)
            raise

        output_filename = f"{mode}-{job_id}.mp4"
        job = WorkbenchJob(
            id=job_id,
            mode=mode,
            prompt=workflow,
            output_filename=output_filename,
            current_message="Задача добавлена в очередь ComfyUI.",
            input_ids=input_ids,
            derived_paths=derived_paths,
            model_bundle_id=model_bundle_id,
            prompt_id=prompt_id,
        )
        self._jobs[job_id] = job
        if self._model_bundles is not None:
            self._model_bundles.mark_in_use(model_bundle_id)
        job.monitor_task = asyncio.create_task(self._monitor(job, websocket))
        return self._snapshot(job)

    async def _monitor(
        self,
        job: WorkbenchJob,
        websocket: aiohttp.ClientWebSocketResponse,
    ) -> None:
        try:
            while job.status not in TERMINAL_STATUSES:
                try:
                    message = await websocket.receive(
                        timeout=self._settings.readiness_poll_seconds
                    )
                except TimeoutError:
                    message = None
                if message is not None:
                    await self._handle_websocket_message(job, message)
                if job.status in TERMINAL_STATUSES:
                    break
                await self._refresh_history(job)
                if job.status in TERMINAL_STATUSES:
                    break
                await self._refresh_flashvsr_progress(job)
                if message is not None and message.type in {
                    WSMsgType.CLOSE,
                    WSMsgType.CLOSED,
                    WSMsgType.CLOSING,
                }:
                    raise ComfyUIError(
                        "Соединение ComfyUI потеряно; GPU job может продолжаться."
                    )
                await self._refresh_queue_position(job)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            job.status = "error"
            job.error = _safe_error(error)
            job.current_message = job.error
        finally:
            _ = await websocket.close()
            if job.finished_at is None and job.status in TERMINAL_STATUSES:
                job.finished_at = time.monotonic()
            if job.status in {"complete", "cancelled"} or job.execution_finished:
                self._cleanup_inputs(job.input_ids, job.derived_paths)
                if self._model_bundles is not None:
                    self._model_bundles.mark_available(job.model_bundle_id)

    async def _handle_websocket_message(
        self,
        job: WorkbenchJob,
        message: aiohttp.WSMessage,
    ) -> None:
        if job.cancel_requested:
            return
        if message.type == WSMsgType.ERROR:
            websocket_error = cast(object, message.data)
            raise ComfyUIError(
                f"ComfyUI WebSocket завершился с ошибкой: {websocket_error}"
            )
        if message.type != WSMsgType.TEXT:
            return

        try:
            event_value: object = cast(object, json.loads(cast(str, message.data)))
        except json.JSONDecodeError:
            return
        event = as_string_mapping(event_value)
        if event is None:
            return
        data = as_string_mapping(event.get("data"))
        if data is None or not self._event_matches(job, data):
            return

        event_type = event.get("type")
        if event_type == "execution_start":
            job.status = "running"
            job.current_message = "ComfyUI начал выполнять workflow."
            return
        if event_type == "executing":
            self._handle_executing(job, data)
            return
        if event_type == "progress":
            self._handle_progress(job, data)
            return
        if event_type == "execution_error":
            job.execution_finished = True
            details = (
                data.get("exception_message")
                or data.get("exception_type")
                or "Ошибка узла ComfyUI."
            )
            job.status = "error"
            job.error = stringify_error(details)
            job.current_message = job.error
            return
        if event_type == "execution_success":
            job.status = "running"
            job.current_message = (
                "ComfyUI завершил обработку; проверяю сохранённый видеофайл."
            )
            job.progress_percent = None
            job.progress_label = None

    async def _latest_log_timestamp(self) -> str:
        try:
            entries = await self._comfy.log_entries()
        except ComfyUIError:
            return ""
        return max((entry.timestamp for entry in entries), default="")

    async def _refresh_flashvsr_progress(self, job: WorkbenchJob) -> None:
        if job.mode != "flashvsr" or job.execution_finished or job.cancel_requested:
            return
        try:
            entries = await self._comfy.log_entries()
        except ComfyUIError:
            return
        messages = [
            entry.message for entry in entries if entry.timestamp > job.log_cursor
        ]
        progress = parse_flashvsr_progress(messages, job.source_frames)
        if progress is None:
            return
        job.status = "running"
        job.current_message = progress.label
        job.progress_percent = progress.percent
        job.progress_label = "Прогресс FlashVSR+ по логу ComfyUI"

    async def _refresh_history(self, job: WorkbenchJob) -> None:
        if job.prompt_id is None or job.cancel_requested:
            return
        try:
            history = await self._comfy.history(job.prompt_id)
        except ComfyUIError:
            return
        execution_error = extract_execution_error(history, job.prompt_id)
        if execution_error is not None:
            job.execution_finished = True
            job.status = "error"
            job.error = execution_error
            job.current_message = execution_error
            return
        output_node_id = {
            "generation": "16",
            "pika": PIKA_SAVE_NODE_ID,
            "flashvsr": FLASHVSR_SAVE_NODE_ID,
            "upscale_model": UPSCALE_MODEL_SAVE_NODE_ID,
            "interpolate": INTERPOLATION_SAVE_NODE_ID,
        }.get(job.mode, "7")
        result = extract_video_result(history, job.prompt_id, output_node_id)
        if result is None:
            return
        job.result = result
        job.execution_finished = True
        job.status = "complete"
        job.current_message = "Видео сохранено ComfyUI и готово к просмотру."
        job.progress_percent = 100
        job.progress_label = "ComfyUI завершил сохранение результата."

    async def _refresh_queue_position(self, job: WorkbenchJob) -> None:
        if job.prompt_id is None:
            return
        try:
            queue = await self._comfy.queue()
        except ComfyUIError:
            return
        pending = queue.get("queue_pending")
        if not isinstance(pending, list):
            return
        for position, item in enumerate(cast(list[object], pending), start=1):
            if _queue_item_prompt_id(item) == job.prompt_id:
                job.status = "queued"
                job.current_message = f"В очереди ComfyUI: позиция {position}."
                return
        if job.status == "queued":
            job.status = "running"
            job.current_message = "ComfyUI выполняет задачу."

    def _handle_executing(self, job: WorkbenchJob, data: dict[str, object]) -> None:
        job.status = "running"
        node = data.get("node")
        job.current_node = node if isinstance(node, str) else None
        job.progress_percent = None
        job.progress_label = None
        if job.current_node is None:
            job.current_message = "Ожидаю сохранения результата ComfyUI."
            return
        job.current_message = _node_label(job.mode, job.current_node)

    def _handle_progress(self, job: WorkbenchJob, data: dict[str, object]) -> None:
        value = _finite_number(data.get("value"))
        maximum = _finite_number(data.get("max"))
        if value is None or maximum is None or maximum <= 0:
            return
        node = data.get("node")
        if isinstance(node, str):
            job.current_node = node
            job.current_message = _node_label(job.mode, node)
        job.status = "running"
        job.progress_percent = min(100, max(0, round(value / maximum * 100)))
        job.progress_label = "Прогресс текущего ComfyUI узла"

    def _event_matches(self, job: WorkbenchJob, data: dict[str, object]) -> bool:
        event_prompt_id = data.get("prompt_id")
        return not isinstance(event_prompt_id, str) or event_prompt_id == job.prompt_id

    def _get_uploaded_file(self, upload_id: str, expected_kind: str) -> UploadedFile:
        if self.upload_in_use(upload_id):
            raise JobRequestError("Файл уже используется другим ComfyUI job.")
        try:
            uploaded = self._uploads.get(upload_id)
        except ValueError as error:
            raise JobRequestError(str(error)) from error
        except KeyError as error:
            raise JobRequestError(str(error)) from error
        if uploaded.kind != expected_kind:
            raise JobRequestError(f"Ожидался файл типа {expected_kind}.")
        return uploaded

    async def _require_ready(self, bundle_id: str) -> None:
        if not self._settings.real_connection:
            raise JobRequestError("REAL_CONNECTION отключён.")
        if self._model_bundles is not None:
            blocked_reason = self._model_bundles.execution_blocked_reason(bundle_id)
            if blocked_reason is not None:
                raise JobNotReadyError(blocked_reason)
            if not await self._model_bundles.bundle_ready(bundle_id):
                raise JobNotReadyError(f"Модели комплекта {bundle_id} не проверены.")
        elif not self._readiness.ready:
            raise JobNotReadyError(
                "Сначала дождись проверки моделей и готовности ComfyUI."
            )
        if not await self._comfy.health():
            raise JobNotReadyError("ComfyUI недоступен; workflow не отправлен.")
        await self._require_nodes(bundle_id)

    async def _require_nodes(self, bundle_id: str, extra: tuple[str, ...] = ()) -> None:
        if self._model_bundles is None:
            return
        required = (*self._model_bundles.required_nodes(bundle_id), *extra)
        if not required:
            return
        try:
            available = await self._comfy.node_classes()
        except ComfyUIError as error:
            raise JobNotReadyError(
                "Не удалось проверить установленные узлы ComfyUI; запуск заблокирован."
            ) from error
        missing = [name for name in required if name not in available]
        if missing:
            raise JobNotReadyError(
                "В ComfyUI не установлены узлы: " + ", ".join(missing) + "."
            )

    async def _require_gpu_idle(self) -> None:
        try:
            queue = await self._comfy.queue()
        except ComfyUIError as error:
            raise JobNotReadyError(
                "Не удалось проверить GPU-очередь; workflow не отправлен."
            ) from error
        for key in ("queue_running", "queue_pending"):
            value = queue.get(key)
            if not isinstance(value, list):
                raise JobNotReadyError(
                    "ComfyUI вернул неполную очередь; запуск безопасно заблокирован."
                )
            if value:
                raise JobNotReadyError("GPU занят текущей или внешней ComfyUI-задачей.")
        try:
            stats = await self._comfy.system_stats()
        except ComfyUIError as error:
            raise JobNotReadyError(
                "Не удалось проверить свободную VRAM; workflow не отправлен."
            ) from error
        free_vram = _reported_gpu_vram_free(stats)
        if free_vram is None:
            raise JobNotReadyError(
                "ComfyUI не сообщил свободную VRAM GPU; запуск безопасно заблокирован."
            )
        if free_vram <= 0:
            raise JobNotReadyError("Свободной VRAM нет; workflow не отправлен.")

    def _snapshot(self, job: WorkbenchJob) -> dict[str, object]:
        return {
            "id": job.id,
            "mode": job.mode,
            "modelBundleId": job.model_bundle_id,
            "status": job.status,
            "currentMessage": job.current_message,
            "progressPercent": job.progress_percent,
            "progressLabel": job.progress_label,
            "resultUrl": f"/api/jobs/{job.id}/output"
            if job.status == "complete"
            else None,
            "filename": job.output_filename,
            "error": job.error,
            "elapsedSeconds": round(job.elapsed_seconds(), 1),
        }

    def _cleanup_inputs(self, upload_ids: set[str], derived_paths: list[Path]) -> None:
        self._uploads.discard_many(upload_ids)
        for path in derived_paths:
            path.unlink(missing_ok=True)


def extract_execution_error(history: object, prompt_id: str) -> str | None:
    root = as_string_mapping(history)
    item = as_string_mapping(root.get(prompt_id)) if root else None
    status = as_string_mapping(item.get("status")) if item else None
    if status is None or status.get("status_str") != "error":
        return None
    messages = status.get("messages")
    if isinstance(messages, list):
        for message in cast(list[object], messages):
            if isinstance(message, list):
                parts = cast(list[object], message)
                if len(parts) == 2 and parts[0] == "execution_error":
                    details = as_string_mapping(parts[1])
                    if details is not None:
                        return stringify_error(details.get("exception_message"))
    return "ComfyUI сообщил об ошибке выполнения workflow."


def extract_video_result(
    history: object,
    prompt_id: str,
    output_node_id: str,
) -> ComfyVideoResult | None:
    history_map = as_string_mapping(history)
    if history_map is None:
        return None
    prompt_history = as_string_mapping(history_map.get(prompt_id))
    if prompt_history is None:
        return None
    outputs = as_string_mapping(prompt_history.get("outputs"))
    if outputs is None:
        return None

    output_node = outputs.get(output_node_id)
    for candidate in _walk_mappings(output_node):
        filename = candidate.get("filename")
        if not isinstance(filename, str):
            continue
        if not any(
            filename.lower().endswith(extension) for extension in VIDEO_EXTENSIONS
        ):
            continue
        subfolder = candidate.get("subfolder", "")
        folder_type = candidate.get("type", "output")
        if not isinstance(subfolder, str) or not isinstance(folder_type, str):
            continue
        if folder_type != "output":
            continue
        return ComfyVideoResult(filename, subfolder, folder_type)
    return None


def _walk_mappings(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        mapping = as_string_mapping(cast(object, value))
        if mapping is None:
            return []
        nested = [
            candidate for item in mapping.values() for candidate in _walk_mappings(item)
        ]
        return [mapping, *nested]
    if isinstance(value, list):
        return [
            candidate
            for item in cast(list[object], value)
            for candidate in _walk_mappings(item)
        ]
    return []


def _reported_gpu_vram_free(stats: dict[str, object]) -> int | None:
    devices = stats.get("devices")
    if not isinstance(devices, list):
        return None
    free_values: list[int] = []
    for device_value in cast(list[object], devices):
        device = as_string_mapping(device_value)
        if device is None or device.get("type") not in {"cuda", "gpu"}:
            continue
        free_bytes = device.get("vram_free")
        if isinstance(free_bytes, int) and not isinstance(free_bytes, bool):
            free_values.append(free_bytes)
    return max(free_values, default=None)


def _queue_item_prompt_id(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    values = cast(list[object], value)
    if len(values) < 2:
        return None
    prompt_id = values[1]
    return prompt_id if isinstance(prompt_id, str) else None


def _finite_number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _node_label(mode: JobMode, node_id: str) -> str:
    if mode in {"generation", "pika"}:
        labels = {
            "1": "Загружаю diffusion model H3.",
            "2": "Загружаю H3 text encoder.",
            "3": "Загружаю H3 video VAE.",
            "4": "Загружаю H3 audio VAE.",
            "6": "Применяю H3 Turbo LoRA.",
            "7": "Кодирую H3 Ref2V prompt и references.",
            "12": "Генерирую видео и аудио MiniMax H3 Ref2V.",
            "13": "Декодирую кадры H3.",
            "14": "Декодирую звук H3.",
            "15": "Собираю выходное видео.",
            "16": "Сохраняю MP4.",
        }
    else:
        labels = {
            "1": "Читаю исходное видео.",
            "2": "Подготавливаю кадры и аудио.",
            "3": "Загружаю SeedVR2 Sharp 7B.",
            "4": "Загружаю SeedVR2 VAE.",
            "5": "Обрабатываю кадры SeedVR2.",
            "6": "Собираю видео и сохраняю аудио.",
            "7": "Сохраняю MP4.",
        }
    return labels.get(node_id, f"Выполняю ComfyUI node {node_id}.")


def _normalize_job_id(job_id: str) -> str:
    try:
        return uuid.UUID(job_id).hex
    except ValueError as error:
        raise KeyError("Неверный идентификатор задачи.") from error


def _safe_error(error: Exception) -> str:
    message = str(error).strip()
    return (
        message[:1_000] if message else "ComfyUI job завершился с неизвестной ошибкой."
    )


def _queue_prompt_ids(entries: object) -> list[str]:
    if not isinstance(entries, list):
        return []
    ids: list[str] = []
    for entry in cast(list[object], entries):
        if isinstance(entry, list):
            parts = cast(list[object], entry)
            if len(parts) > 1 and isinstance(parts[1], str):
                ids.append(parts[1])
    return ids
