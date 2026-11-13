from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

import aiohttp
from yarl import URL

from .workflows import ComfyPrompt


@dataclass(frozen=True, slots=True)
class ComfyVideoResult:
    filename: str
    subfolder: str
    folder_type: str


class ComfyUIError(Exception):
    pass


class ComfyUIClient:
    def __init__(self, base_url: str, session: aiohttp.ClientSession) -> None:
        self._base_url: str = base_url.rstrip("/")
        self._session: aiohttp.ClientSession = session

    async def health(self) -> bool:
        try:
            async with self._session.get(
                f"{self._base_url}/system_stats",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as response:
                return response.status == 200
        except (TimeoutError, aiohttp.ClientError):
            return False

    async def submit_prompt(self, prompt: ComfyPrompt, client_id: str) -> str:
        try:
            async with self._session.post(
                f"{self._base_url}/prompt",
                json={"prompt": prompt, "client_id": client_id},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                payload = await _response_json(response)
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(
                f"Не удалось отправить workflow в ComfyUI: {error}"
            ) from error

        result = as_string_mapping(payload)
        if result is None:
            raise ComfyUIError(
                "ComfyUI вернул некорректный ответ при постановке workflow."
            )
        prompt_id = result.get("prompt_id")
        if response.status >= 400 or not isinstance(prompt_id, str) or not prompt_id:
            error = result.get("error")
            details = (
                stringify_error(error) if error is not None else "workflow отклонён"
            )
            raise ComfyUIError(f"ComfyUI: {details}")
        return prompt_id

    async def connect_websocket(
        self, client_id: str
    ) -> aiohttp.ClientWebSocketResponse:
        base = URL(self._base_url)
        scheme = "wss" if base.scheme == "https" else "ws"
        websocket_url = (
            base.with_scheme(scheme)
            .with_path("/ws")
            .with_query({"clientId": client_id})
        )
        try:
            return await self._session.ws_connect(websocket_url, heartbeat=30)
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(
                f"Не удалось открыть ComfyUI WebSocket: {error}"
            ) from error

    async def history(self, prompt_id: str) -> object:
        try:
            async with self._session.get(
                f"{self._base_url}/history/{prompt_id}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                return await _response_json(response)
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(
                f"Не удалось получить историю ComfyUI: {error}"
            ) from error

    async def log_entries(self) -> list[LogEntry]:
        try:
            async with self._session.get(
                f"{self._base_url}/internal/logs/raw",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                return parse_log_entries(await _response_json(response))
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(f"Не удалось получить лог ComfyUI: {error}") from error

    async def system_stats(self) -> dict[str, object]:
        try:
            async with self._session.get(
                f"{self._base_url}/system_stats",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                payload = await _response_json(response)
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(
                f"Не удалось проверить память GPU ComfyUI: {error}"
            ) from error
        result = as_string_mapping(payload)
        if response.status >= 400 or result is None:
            raise ComfyUIError("ComfyUI вернул некорректную диагностику GPU.")
        return result

    async def queue(self) -> dict[str, object]:
        try:
            async with self._session.get(
                f"{self._base_url}/queue",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                payload = await _response_json(response)
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(
                f"Не удалось проверить очередь ComfyUI: {error}"
            ) from error
        result = as_string_mapping(payload)
        if result is None:
            raise ComfyUIError("ComfyUI вернул некорректную очередь.")
        return result

    async def node_classes(self) -> frozenset[str]:
        try:
            async with self._session.get(
                f"{self._base_url}/object_info",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                payload = await _response_json(response)
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(
                f"Не удалось получить список узлов ComfyUI: {error}"
            ) from error
        mapping = as_string_mapping(payload)
        if response.status >= 400 or mapping is None:
            raise ComfyUIError("ComfyUI вернул некорректный список узлов.")
        return frozenset(mapping)

    async def delete_queued(self, prompt_id: str) -> None:
        try:
            async with self._session.post(
                f"{self._base_url}/queue",
                json={"delete": [prompt_id]},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status >= 400:
                    raise ComfyUIError(
                        f"ComfyUI не удалил задачу из очереди: HTTP {response.status}."
                    )
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(
                f"Не удалось удалить задачу из очереди: {error}"
            ) from error

    async def interrupt(self) -> None:
        try:
            async with self._session.post(
                f"{self._base_url}/interrupt",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status >= 400:
                    raise ComfyUIError(
                        f"ComfyUI не принял прерывание: HTTP {response.status}."
                    )
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(f"Не удалось прервать задачу: {error}") from error

    async def open_video(
        self,
        result: ComfyVideoResult,
        byte_range: str | None = None,
    ) -> aiohttp.ClientResponse:
        try:
            response = await self._session.get(
                f"{self._base_url}/view",
                params={
                    "filename": result.filename,
                    "subfolder": result.subfolder,
                    "type": result.folder_type,
                },
                headers={"Range": byte_range} if byte_range else {},
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
            )
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ComfyUIError(
                f"Не удалось открыть результат ComfyUI: {error}"
            ) from error
        if response.status not in {200, 206}:
            response.release()
            raise ComfyUIError(f"ComfyUI не отдаёт результат: HTTP {response.status}.")
        return response


async def _response_json(response: aiohttp.ClientResponse) -> object:
    try:
        payload: object = cast(object, await response.json(content_type=None))
    except (aiohttp.ContentTypeError, json.JSONDecodeError) as error:
        text = await response.text()
        raise ComfyUIError(f"ComfyUI вернул не-JSON ответ: {text[:1_000]}") from error
    return payload


@dataclass(frozen=True, slots=True)
class LogEntry:
    timestamp: str
    message: str


def parse_log_entries(payload: object) -> list[LogEntry]:
    mapping = as_string_mapping(payload)
    entries = mapping.get("entries") if mapping is not None else None
    if not isinstance(entries, list):
        return []
    result: list[LogEntry] = []
    for entry in cast(list[object], entries):
        item = as_string_mapping(entry)
        if item is None:
            continue
        timestamp, message = item.get("t"), item.get("m")
        if isinstance(timestamp, str) and isinstance(message, str):
            result.append(LogEntry(timestamp, message))
    return result


def as_string_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    entries = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in entries):
        return None
    return cast(dict[str, object], entries)


def stringify_error(value: object) -> str:
    if isinstance(value, str):
        return value[:1_000]
    try:
        return json.dumps(value, ensure_ascii=False)[:1_000]
    except (TypeError, ValueError):
        return "workflow rejected"
