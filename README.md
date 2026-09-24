# Vast Video Pipeline

Подготовленное окружение для одного инстанса Vast.ai с RTX 3090 или RTX 4090 24 GB:

```text
MiniMax H3/Turbo
  → FlowDenoise
  → reference-based V-BM3D
  → SeedVR2 7B Sharp mixed FP8
  → возврат оригинального аудио
```

## Состояние

Локальные текстовые файлы подготовлены. Пустые приватные репозитории уже созданы:

- GitHub: `anya-keller/vast-video-pipeline`;
- Docker Hub: `anyakeller/vast-video-pipeline`.

Образ ещё не собран и не опубликован. GPU-тест ещё не запускался. Docker на Mac не нужен и не устанавливается.

## Сборка окружения

GitHub Actions собирает только `linux/amd64`. В Docker-образ входят ComfyUI, custom nodes, FFmpeg, VapourSynth, V-BM3D и зависимости. Веса моделей в образ не входят.

На GitHub в репозитории нужно добавить:

- variable `DOCKER_USERNAME` со значением `anyakeller`;
- secret `DOCKER_PASSWORD` со значением Docker Hub access token, имеющим право push в `vast-video-pipeline`.

Workflow собирает образ на pull request и push в `main`. Публикация выполняется только через `workflow_dispatch` с `publish=true`. До запуска проверь квоту GitHub Actions: стандартный private Linux runner имеет ограниченные CPU/RAM/SSD, а платное превышение не включается автоматически.

## После публикации образа

1. Зафиксировать опубликованный digest.
2. В Vast создать приватный template на основе `anyakeller/vast-video-pipeline:<tag>`.
3. Добавить Docker Hub read-only credentials через поле Docker Repository Authentication.
4. Выбрать SSH launch mode, минимум 64 GB RAM, одну RTX 3090/4090 и диск, рассчитанный по `config/models.lock.json` и промежуточным FFV1-файлам.
5. В on-start script вызвать:

```bash
/opt/vast/scripts/start.sh
```

ComfyUI слушает только `127.0.0.1:8188`. Открывать его нужно через SSH-туннель:

```bash
ssh -p <SSH_PORT> root@<HOST> -L 8188:localhost:8188
```

Открыть на Mac: `http://localhost:8188`.

## Подготовка моделей

После запуска инстанса:

```bash
/opt/vast/scripts/doctor.sh
hf auth login
/opt/vast/scripts/prepare-models.sh
/opt/vast/scripts/doctor.sh --models --gpu
```

`prepare-models.sh` проверяет размер и SHA-256, скачивает недостающие файлы с возобновлением и блокирует параллельные запуски. На время скачивания не запускай генерацию.

Модели занимают примерно 55 GiB. Нужен запас под кэш, входы, промежуточные PNG/FFV1 и результаты; точный размер диска рассчитывается перед арендой.

## Workflow

- `workflows/01-text-to-video-24gb.json` — H3 на 24 GB, адаптированный под v4 Turbo.
- `workflows/02-text-to-video-1344-q4-tiled.json` — первый staged-run: Q4 H3 4-step sampling на 1344x768 с сохранением video/audio latent.
- `workflows/02-text-to-video-1344-q4-8step-staged.json` — staged-run для 8-step Turbo sampling на 1344x768.
- `workflows/03-h3-staged-video-audio-decode.json` — второй staged-run: tiled video VAE, audio VAE и mux в MP4 с lossless H.264 CRF 0.
- `postprocess/h3_staged_latent.py` — custom nodes `SaveH3StagedLatent`/`LoadH3StagedLatent`; stock `SaveLatent` не подходит NestedTensor H3.
- `workflows/flowdenoise-raft-example.json` — пример FlowDenoise.
- `workflows/seedvr2-sharp-video-example.json` — пример SeedVR2.
- `postprocess/vbm3d_pipeline.py` — безопасный reference-based V-BM3D: сначала сверяет resolution/FPS/frame count, затем запускает lossless FFV1.

Параметры хранятся в `config/presets.json`. Для первого теста используй один 5-секундный шот и сравни:

1. Original → SeedVR2;
2. FlowDenoise → SeedVR2;
3. Original + FlowDenoise reference → V-BM3D → SeedVR2.

Production-вариант выбирай только после side-by-side проверки.

## Проверки

В локальной среде без Docker доступны проверки Python-файлов:

```bash
python3 -m compileall -q postprocess
ruff check postprocess tests
ruff format --check postprocess tests
pytest
```

Shell, Dockerfile и GitHub Actions проверяются удалённо в CI. Отсутствие GPU в CI не считается проверкой GPU-пайплайна.

## Важные ограничения

- Не коммить модели, видео, токены и пароли.
- Не хранить секреты в Dockerfile, workflow, template или логах.
- Local volume Vast привязан к конкретному физическому хосту.
- Не запускать аренду GPU, пока не согласованы стоимость и размер диска.
- Не считать образ готовым до отдельного smoke-test на RTX 3090/4090.
