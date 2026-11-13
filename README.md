# Vast Video Pipeline

Подготовленное окружение для одного инстанса Vast.ai с RTX 3090 или RTX 4090 24 GB:

```text
MiniMax H3 Ref2V / Ref2V Turbo
  → FlowDenoise
  → reference-based V-BM3D
  → SeedVR2 7B Sharp mixed FP8
  → возврат оригинального аудио
```

## Состояние

Локальные текстовые файлы подготовлены. Пустые приватные репозитории уже созданы:

- GitHub: `anya-keller/vast-video-pipeline`;
- Docker Hub: `anyakeller/vast-video-pipeline`.

Образ ещё не собран и не опубликован. Его GPU-проверка остаётся отдельной задачей. Docker на Mac не нужен и не устанавливается.

Мастерская `web/` предоставляет независимые вкладки MiniMax H3, MiniMax H3 Turbo, Pika H3 Flex (Ref2VA) и SeedVR2; поддерживает локальный mock и реальный `REAL_CONNECTION`. Реальный unpruned H3 Ref2V GPU smoke, SeedVR2 ×2/×4 и playback/Range/download/media проверки выполнены на Vast; исторический smoke на pruned checkpoint не засчитывался. В генерации разрешение фиксировано 1344×768, желаемая длительность — 5–15 секунд с дробной частью; H3 округляет вверх по native frame-grid `17*k+5` при 24 fps без обрезки кадров. Turbo LoRA вынесена в отдельную вкладку «MiniMax H3 Turbo» (4 шага); вкладка «MiniMax H3» использует 20 шагов без LoRA. UI выбора разрешения и 2K/API пути нет. Полная спецификация — [specs/h3-generation-controls.md](specs/h3-generation-controls.md); подключение и деплой — [web/README.md](web/README.md) и [specs/real-vast-workbench-connection.md](specs/real-vast-workbench-connection.md). Новая ветка no-LoRA/20 шагов пока ждёт отдельного GPU smoke; последний локальный regression прошёл, но этот локальный release не развёрнут, поскольку SSH к существующему инстансу отказал.

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
/opt/vast/scripts/prepare-models.sh --bundle h3_base
/opt/vast/scripts/doctor.sh --models --gpu
```

Передача всегда относится только к явно выбранному bundle. Доступные ID: `h3_base`, `h3_turbo`, `pika_h3_flex_ref2va`, `seedvr2_upscale`. UI не начинает скачивания при открытии или переключении вкладок. Скрипт закрепляет repository revisions, принимает только lock-listed paths, проверяет disk space, размер и SHA-256; invalid files/symlinks не перезаписываются автоматически. Cancel сохраняет partial data; удаление доступно отдельно и только вручную. Hugging Face Hub 1.32 пишет новую передачу в уникальный `.incomplete`, поэтому возобновление после cancel не гарантируется.

Объём диска и свободное место показываются для выбранного bundle, а полный lock находится в `config/models.lock.json`. Нужен дополнительный запас под кэш, входы, промежуточные PNG/FFV1 и результаты. Подготовка модели сама по себе не означает, что соответствующий workflow runtime уже поддержан: генерация Pika gated в интерфейсе до подтверждающего GPU smoke.

## Workflow

- `workflows/01-text-to-video-24gb.json` — H3 Ref2V unpruned INT8, Ref2V 4-step Turbo LoRA, sampler `res_multistep` и DisTorch CPU-offload.
- `workflows/02-text-to-video-1344-q4-tiled.json` — staged Ref2V unpruned INT8 4-step sampling на 1344x768 с сохранением video/audio latent.
- `workflows/02-text-to-video-1344-q4-8step-staged.json` — тот же Ref2V unpruned 4-step набор весов; имя файла историческое. Q4/v4 LoRA/8-step больше не используются этими графами.
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
