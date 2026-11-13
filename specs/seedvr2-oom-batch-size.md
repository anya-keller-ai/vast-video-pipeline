# Снижение пикового расхода VRAM в SeedVR2 upscale

## Контекст

`build_seedvr2_prompt` устанавливает `batch_size=5` для upscale ×2, но `1` для ×4. Примерный ComfyUI workflow задаёт `batch_size=33`. Большой batch повышает пиковое потребление GPU-памяти и может приводить к OOM; production graph и example должны использовать безопасное одинаковое значение.

## Цель

Задать `batch_size=1` для всех SeedVR2 upscale режимов, не меняя разрешение, tiled VAE, offload, аудио и остальные параметры workflow.

## Что изменится

1. `web/backend/workflows.py` — всегда генерировать `batch_size=1`.
2. `web/backend/test_workflows.py` — зафиксировать batch size для ×2 и ×4.
3. `workflows/seedvr2-sharp-video-example.json` — изменить batch size с 33 на 1.
4. `specs/seedvr2-oom-batch-size.md` — критерии проверки и deployment.

## Детали реализации

1. Сохранить динамическое разрешение upscale и все текущие loader/VAE/offload настройки; менять только `batch_size`.
2. Регрессионные tests должны проверять JSON graph для ×2 и ×4 и убеждаться, что каждый передаёт `batch_size=1` в `SeedVR2VideoUpscaler`.
3. Unit tests использовать mocks; не запускать и не имитировать скачивание model weights.
4. Перед GPU retry проверить пустую ComfyUI queue, положительную свободную VRAM, готовность SeedVR2 bundle и точный исходный upload. Повторять только исходный OOM-вызов с теми же scale/source, не перезаписывать outputs.
5. Если исходного upload или готовых SeedVR2 весов нет, остановиться до GPU job и запросить недостающую явную авторизацию; не подменять исходное видео и не скачивать веса автоматически.
6. Развернуть новую immutable SHA-256 release, проверить health/API и не перезапускать ComfyUI.

## Критерии приёмки

- [x] Backend-generated SeedVR2 graph задаёт `batch_size=1` для ×2 и ×4.
- [x] Static SeedVR2 example задаёт `batch_size=1`.
- [x] Backend 66 tests, Ruff check/format, basedpyright (0 warnings), JSON validation и `git diff --check` проходят без загрузки весов.
- [ ] Новая SHA-verified release активирована; backend health и ComfyUI health подтверждены.
- [ ] Исходный upscale повторён только при подтверждённых input/models/VRAM; результат проверен по ComfyUI history, размеру и видеопараметрам без перезаписи предыдущих outputs.
