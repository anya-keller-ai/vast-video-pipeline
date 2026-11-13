from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from .config import Settings

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".m4v"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"})
UPLOAD_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class UploadedFile:
    id: str
    filename: str
    path: Path
    kind: str


class UploadStore:
    def __init__(self, settings: Settings) -> None:
        self._settings: Settings = settings
        self._files: dict[str, UploadedFile] = {}

    async def store(
        self,
        upload_id: str,
        original_name: str,
        content_length: int | None,
        chunks: AsyncIterator[bytes],
    ) -> UploadedFile:
        normalized_id = _normalize_upload_id(upload_id)
        filename = _safe_original_name(original_name)
        extension = Path(filename).suffix.lower()
        kind = _file_kind(extension)
        self._settings.input_root.mkdir(parents=True, exist_ok=True)
        target = self._settings.input_root / f"workbench-{normalized_id}{extension}"

        available_bytes = shutil.disk_usage(self._settings.input_root).free
        if (
            content_length is not None
            and content_length + self._settings.min_free_bytes > available_bytes
        ):
            raise InsufficientStorageError(
                "Недостаточно места на диске с учётом системного резерва."
            )

        written_bytes = 0
        try:
            with target.open("xb") as output:
                async for chunk in chunks:
                    if (
                        len(chunk) + self._settings.min_free_bytes
                        > shutil.disk_usage(self._settings.input_root).free
                    ):
                        raise InsufficientStorageError(
                            "Достигнут резерв свободного места на диске."
                        )
                    written_bytes += len(chunk)
                    _ = await asyncio.to_thread(output.write, chunk)
        except FileExistsError as error:
            raise ValueError("Файл с таким upload ID уже существует.") from error
        except Exception:
            target.unlink(missing_ok=True)
            raise

        if written_bytes == 0:
            target.unlink(missing_ok=True)
            raise ValueError("Нельзя загрузить пустой файл.")
        if content_length is not None and written_bytes != content_length:
            target.unlink(missing_ok=True)
            raise ValueError("Получен неполный файл загрузки.")

        uploaded = UploadedFile(normalized_id, target.name, target, kind)
        self._files[normalized_id] = uploaded
        return uploaded

    def get(self, upload_id: str) -> UploadedFile:
        normalized_id = _normalize_upload_id(upload_id)
        uploaded = self._files.get(normalized_id)
        if uploaded is None:
            raise KeyError("Загруженный файл не найден; выбери и загрузи его повторно.")
        return uploaded

    def discard(self, upload_id: str) -> None:
        normalized_id = _normalize_upload_id(upload_id)
        uploaded = self._files.pop(normalized_id, None)
        if uploaded is not None:
            uploaded.path.unlink(missing_ok=True)

    def discard_many(self, upload_ids: set[str]) -> None:
        for upload_id in upload_ids:
            self.discard(upload_id)


def _normalize_upload_id(upload_id: str) -> str:
    try:
        return uuid.UUID(upload_id).hex
    except (ValueError, AttributeError) as error:
        raise ValueError("Upload id must be a UUID.") from error


def _safe_original_name(original_name: str) -> str:
    safe_name = Path(unquote(original_name)).name
    extension = Path(safe_name).suffix.lower()
    if extension not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
        raise ValueError(
            "Поддерживаются изображения JPG/PNG/WebP, видео MP4/WebM/MOV "
            "и аудио WAV/MP3/FLAC/M4A/OGG/AAC."
        )
    return safe_name


def _file_kind(extension: str) -> str:
    if extension in IMAGE_EXTENSIONS:
        return "image"
    return "audio" if extension in AUDIO_EXTENSIONS else "video"


class InsufficientStorageError(Exception):
    pass
