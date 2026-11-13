from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

import aiohttp
from aiohttp import web

from .comfy import ComfyUIClient, ComfyUIError, as_string_mapping
from .config import Settings
from .h3_guides import GuideRequest
from .jobs import JobManager, JobNotReadyError, JobRequestError
from .model_bundles import ModelBundleError, ModelBundleManager
from .readiness import ReadinessManager
from .uploads import UPLOAD_CHUNK_BYTES, InsufficientStorageError, UploadStore
from .workflows import (
    H3_DEFAULT_RESOLUTION,
    H3_MAX_GENERATION_SECONDS,
    H3_MIN_GENERATION_SECONDS,
    H3_RESOLUTIONS,
)

SETTINGS_KEY = web.AppKey("settings", Settings)
READINESS_KEY = web.AppKey("readiness", ReadinessManager)
UPLOADS_KEY = web.AppKey("uploads", UploadStore)
JOBS_KEY = web.AppKey("jobs", JobManager)
COMFY_KEY = web.AppKey("comfy", ComfyUIClient)
MODEL_BUNDLES_KEY = web.AppKey("model_bundles", ModelBundleManager)
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@web.middleware
async def api_errors(request: web.Request, handler: Handler) -> web.StreamResponse:
    if request.method not in {"GET", "HEAD"}:
        origin = request.headers.get("Origin")
        if (
            origin is not None
            and origin.rstrip("/") != f"{request.scheme}://{request.host}"
        ):
            return web.json_response(
                {"error": "Cross-origin requests are not allowed."}, status=403
            )
    try:
        return await handler(request)
    except JobNotReadyError as error:
        return web.json_response({"error": str(error)}, status=409)
    except ModelBundleError as error:
        return web.json_response({"error": str(error)}, status=409)
    except InsufficientStorageError as error:
        return web.json_response({"error": str(error)}, status=507)
    except (JobRequestError, ValueError, json.JSONDecodeError) as error:
        return web.json_response({"error": str(error)}, status=400)
    except KeyError as error:
        return web.json_response({"error": str(error)}, status=404)
    except ComfyUIError as error:
        return web.json_response({"error": str(error)}, status=502)
    except RuntimeError as error:
        return web.json_response({"error": str(error)}, status=409)


def create_app(settings: Settings) -> web.Application:
    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The workbench must listen on loopback only.")
    app = web.Application(middlewares=[api_errors], client_max_size=0)
    app[SETTINGS_KEY] = settings
    app.cleanup_ctx.append(runtime_context)
    _ = app.router.add_get("/api/config", runtime_config)
    _ = app.router.add_get("/api/health", health)
    _ = app.router.add_get("/api/readiness", get_readiness)
    _ = app.router.add_post("/api/readiness/start", start_readiness)
    _ = app.router.add_post("/api/readiness/cancel", cancel_readiness)
    _ = app.router.add_get("/api/model-bundles", get_model_bundles)
    _ = app.router.add_post(
        "/api/model-bundles/{bundle_id}/download", download_model_bundle
    )
    _ = app.router.add_post(
        "/api/model-bundles/{bundle_id}/cancel", cancel_model_bundle_download
    )
    _ = app.router.add_delete("/api/model-bundles/{bundle_id}", delete_model_bundle)
    _ = app.router.add_put("/api/uploads/{upload_id}", upload_file)
    _ = app.router.add_delete("/api/uploads/{upload_id}", delete_upload)
    _ = app.router.add_post("/api/jobs", start_job)
    _ = app.router.add_get("/api/jobs/{job_id}", get_job)
    _ = app.router.add_post("/api/jobs/{job_id}/cancel", cancel_job)
    _ = app.router.add_get("/api/jobs/{job_id}/output", get_output)
    _ = app.router.add_get("/{path:.*}", static_file)
    return app


async def runtime_context(app: web.Application) -> AsyncIterator[None]:
    settings = app[SETTINGS_KEY]
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        readiness = ReadinessManager(settings, session)
        uploads = UploadStore(settings)
        comfy = ComfyUIClient(settings.comfyui_url, session)
        model_bundles = ModelBundleManager(settings)
        jobs = JobManager(settings, readiness, uploads, comfy, model_bundles)
        app[READINESS_KEY] = readiness
        app[UPLOADS_KEY] = uploads
        app[COMFY_KEY] = comfy
        app[JOBS_KEY] = jobs
        app[MODEL_BUNDLES_KEY] = model_bundles
        yield
        await jobs.close()
        await model_bundles.close()
        await readiness.close()


async def runtime_config(request: web.Request) -> web.Response:
    return web.json_response(
        {"realConnection": request.app[SETTINGS_KEY].real_connection}
    )


async def health(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    return web.json_response(
        {
            "status": "ok",
            "realConnection": settings.real_connection,
            "staticReady": (settings.static_root / "index.html").is_file(),
        }
    )


async def get_readiness(request: web.Request) -> web.Response:
    require_real(request)
    return web.json_response(request.app[READINESS_KEY].snapshot())


async def start_readiness(request: web.Request) -> web.Response:
    require_real(request)
    return web.json_response(await request.app[READINESS_KEY].start())


async def cancel_readiness(request: web.Request) -> web.Response:
    require_real(request)
    return web.json_response(await request.app[READINESS_KEY].cancel())


async def get_model_bundles(request: web.Request) -> web.Response:
    return web.json_response(await request.app[MODEL_BUNDLES_KEY].snapshot())


async def download_model_bundle(request: web.Request) -> web.Response:
    require_real(request)
    snapshot = await request.app[MODEL_BUNDLES_KEY].start_download(
        request.match_info["bundle_id"]
    )
    return web.json_response(snapshot, status=202)


async def cancel_model_bundle_download(request: web.Request) -> web.Response:
    require_real(request)
    snapshot = await request.app[MODEL_BUNDLES_KEY].cancel_download(
        request.match_info["bundle_id"]
    )
    return web.json_response(snapshot)


async def delete_model_bundle(request: web.Request) -> web.Response:
    require_real(request)
    bundle_id = request.match_info["bundle_id"]
    jobs = request.app[JOBS_KEY]
    if jobs.bundle_in_use(bundle_id):
        raise JobNotReadyError("Модель используется выполняющейся GPU-задачей.")
    queue = await request.app[COMFY_KEY].queue()
    if _queue_has_work(queue):
        raise JobNotReadyError("ComfyUI занят; дождись завершения GPU-очереди.")
    result = await request.app[MODEL_BUNDLES_KEY].delete_bundle(bundle_id)
    return web.json_response(result)


def _queue_has_work(queue: dict[str, object]) -> bool:
    for key in ("queue_running", "queue_pending"):
        value = queue.get(key)
        if not isinstance(value, list) or value:
            return True
    return False


async def upload_file(request: web.Request) -> web.Response:
    require_real(request)
    uploaded = await request.app[UPLOADS_KEY].store(
        request.match_info["upload_id"],
        request.headers.get("X-Filename", ""),
        request.content_length,
        request.content.iter_chunked(UPLOAD_CHUNK_BYTES),
    )
    return web.json_response({"id": uploaded.id, "filename": uploaded.filename})


async def delete_upload(request: web.Request) -> web.Response:
    require_real(request)
    upload_id = request.match_info["upload_id"]
    if request.app[JOBS_KEY].upload_in_use(upload_id):
        raise JobNotReadyError("Файл используется активным ComfyUI job.")
    request.app[UPLOADS_KEY].discard(upload_id)
    return web.json_response({"deleted": True})


def _optional_id_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in cast(list[object], value)
    ):
        raise ValueError(f"{key} must be an array of strings.")
    return cast(list[str], value)


def _resolution(payload: dict[str, object]) -> str:
    value = payload.get("resolution", H3_DEFAULT_RESOLUTION)
    if not isinstance(value, str) or value not in H3_RESOLUTIONS:
        raise ValueError("resolution must be standard or draft.")
    return value


def _guides(payload: dict[str, object]) -> list[GuideRequest]:
    raw = payload.get("guides", [])
    if not isinstance(raw, list):
        raise ValueError("guides must be an array.")
    guides: list[GuideRequest] = []
    for item in cast(list[object], raw):
        entry = as_string_mapping(item)
        image_id = None if entry is None else entry.get("imageId")
        frame = None if entry is None else entry.get("frame")
        if (
            not isinstance(image_id, str)
            or not isinstance(frame, int)
            or isinstance(frame, bool)
        ):
            raise ValueError("Each guide needs a string imageId and an integer frame.")
        guides.append(GuideRequest(image_id, frame))
    return guides


async def start_job(request: web.Request) -> web.Response:
    require_real(request)
    payload = as_string_mapping(cast(object, await request.json()))
    if payload is None:
        raise ValueError("Job body must be a JSON object.")
    manager = request.app[JOBS_KEY]
    mode = payload.get("mode")
    if mode == "generation":
        prompt = payload.get("prompt")
        images = payload.get("referenceImageIds", [])
        video = payload.get("referenceVideoId")
        duration_seconds = payload.get("durationSeconds")
        turbo_lora = payload.get("turboLora")
        audio_ids = _optional_id_list(payload, "referenceAudioIds")
        resolution = _resolution(payload)
        if not isinstance(prompt, str):
            raise ValueError("Prompt must be a string.")
        if not isinstance(images, list):
            raise ValueError("Reference image ids must be an array.")
        image_values = cast(list[object], images)
        if not all(isinstance(value, str) for value in image_values):
            raise ValueError("Reference image ids must be strings.")
        if video is not None and not isinstance(video, str):
            raise ValueError("Reference video id must be a string or null.")
        if (
            not isinstance(duration_seconds, (int, float))
            or isinstance(duration_seconds, bool)
            or not math.isfinite(duration_seconds)
            or not H3_MIN_GENERATION_SECONDS
            <= duration_seconds
            <= H3_MAX_GENERATION_SECONDS
        ):
            duration_range = (
                f"{H3_MIN_GENERATION_SECONDS:g}–{H3_MAX_GENERATION_SECONDS:.3f}"
            )
            raise ValueError(f"Generation duration must be {duration_range} seconds.")
        if not isinstance(turbo_lora, bool):
            raise ValueError("Turbo LoRA setting must be boolean.")
        result = await manager.start_generation(
            prompt,
            cast(list[str], image_values),
            video,
            float(duration_seconds),
            turbo_lora,
            audio_ids,
            resolution,
            _guides(payload),
        )
    elif mode == "pika":
        prompt = payload.get("prompt")
        images = payload.get("referenceImageIds", [])
        video = payload.get("referenceVideoId")
        duration_seconds = payload.get("durationSeconds")
        seed = payload.get("seed")
        ref_image_size = payload.get("refImageSize", "match")
        audio_ids = _optional_id_list(payload, "referenceAudioIds")
        resolution = _resolution(payload)
        if not isinstance(ref_image_size, str):
            raise ValueError("refImageSize must be a string.")
        if not isinstance(prompt, str):
            raise ValueError("Prompt must be a string.")
        if not isinstance(images, list) or not all(
            isinstance(value, str) for value in cast(list[object], images)
        ):
            raise ValueError("Reference image ids must be an array of strings.")
        if video is not None and not isinstance(video, str):
            raise ValueError("Reference video id must be a string or null.")
        if (
            not isinstance(duration_seconds, (int, float))
            or isinstance(duration_seconds, bool)
            or not math.isfinite(duration_seconds)
        ):
            raise ValueError("Pika duration must be a finite number.")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError("Pika seed must be an integer.")
        result = await manager.start_pika_generation(
            prompt,
            cast(list[str], images),
            video,
            float(duration_seconds),
            seed,
            ref_image_size,
            audio_ids,
            resolution,
            _guides(payload),
        )
    elif mode == "interpolate":
        video = payload.get("videoId")
        model_key = payload.get("model")
        multiplier = payload.get("multiplier")
        if (
            not isinstance(video, str)
            or not isinstance(model_key, str)
            or not isinstance(multiplier, int)
            or isinstance(multiplier, bool)
        ):
            raise ValueError(
                "Interpolation requires video id, model key and integer multiplier."
            )
        result = await manager.start_interpolation(video, model_key, multiplier)
    elif mode == "upscale_model":
        video = payload.get("videoId")
        model_key = payload.get("model")
        if not isinstance(video, str) or not isinstance(model_key, str):
            raise ValueError("Upscale model requires a video id and a model key.")
        result = await manager.start_upscale_model(video, model_key)
    elif mode == "flashvsr":
        video = payload.get("videoId")
        variant = payload.get("variant")
        scale = payload.get("scale")
        if (
            not isinstance(video, str)
            or not isinstance(variant, str)
            or not isinstance(scale, int)
            or isinstance(scale, bool)
        ):
            raise ValueError("FlashVSR+ requires video id, variant and integer scale.")
        result = await manager.start_flashvsr(video, variant, scale)
    elif mode == "upscale":
        video = payload.get("videoId")
        scale = payload.get("scale")
        model_key = payload.get("model", "7b_sharp")
        batch_size = payload.get("batchSize", 1)
        temporal_overlap = payload.get("temporalOverlap", 0)
        if not isinstance(model_key, str):
            raise ValueError("SeedVR2 model must be a string.")
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not isinstance(temporal_overlap, int)
            or isinstance(temporal_overlap, bool)
        ):
            raise ValueError("SeedVR2 batchSize and temporalOverlap must be integers.")
        if (
            not isinstance(video, str)
            or not isinstance(scale, int)
            or isinstance(scale, bool)
        ):
            raise ValueError("Upscale requires a video id and integer scale.")
        result = await manager.start_upscale(
            video, scale, model_key, batch_size, temporal_overlap
        )
    else:
        raise ValueError("Unknown job mode.")
    return web.json_response(result, status=202)


async def cancel_job(request: web.Request) -> web.Response:
    require_real(request)
    result = await request.app[JOBS_KEY].cancel(request.match_info["job_id"])
    return web.json_response(result)


async def get_job(request: web.Request) -> web.Response:
    require_real(request)
    return web.json_response(request.app[JOBS_KEY].get(request.match_info["job_id"]))


async def get_output(request: web.Request) -> web.StreamResponse:
    require_real(request)
    result, filename = request.app[JOBS_KEY].get_output(request.match_info["job_id"])
    upstream = await request.app[COMFY_KEY].open_video(
        result, request.headers.get("Range")
    )
    try:
        headers = {
            key: upstream.headers[key]
            for key in (
                "Content-Type",
                "Content-Length",
                "Content-Range",
                "Accept-Ranges",
            )
            if key in upstream.headers
        }
        headers["Content-Disposition"] = f'inline; filename="{filename}"'
        response = web.StreamResponse(status=upstream.status, headers=headers)
        _ = await response.prepare(request)
        async for chunk in upstream.content.iter_chunked(UPLOAD_CHUNK_BYTES):
            await response.write(chunk)
        await response.write_eof()
        return response
    finally:
        upstream.release()


async def static_file(request: web.Request) -> web.FileResponse:
    relative = request.match_info["path"] or "index.html"
    if relative.startswith("api/"):
        raise web.HTTPNotFound()
    root = request.app[SETTINGS_KEY].static_root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "no-cache"})


def require_real(request: web.Request) -> None:
    if not request.app[SETTINGS_KEY].real_connection:
        raise JobRequestError(
            "REAL_CONNECTION отключён; доступен только локальный mock."
        )


def main() -> None:
    settings = Settings.from_environment()
    web.run_app(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
