# Видеомастерская

SolidJS + Tailwind CSS v4 + `tailwind-variants`. Девять независимых вкладок: MiniMax H3 (Base), MiniMax H3 Turbo, Pika H3 Flex (Ref2VA), SeedVR2, FlashVSR+ Turbo, FlashVSR+ Base, RealESRGAN x2plus, RealESRGAN AnimeVideo-v3 и Повышение FPS. Отдельно доступны локальный mock и реальные поддержанные jobs.

## Локальный mock

Из корня проекта:

```sh
pnpm --dir web install
pnpm --dir web dev
```

Адрес: `http://127.0.0.1:4173`. Vite dev-server возвращает `realConnection: false` в `/api/config` и manifest-derived waiting states в `/api/model-bundles`; скачивание/удаление недоступны, модели и GPU не вызываются. Demo WebM создаётся в браузере после явного открытия mock-формы.

## Серверный режим

Backend требует Python 3.12+ и aiohttp 3.12+ (<4). На новом проверенном Vast aiohttp уже установлен; Node.js там не нужен. Адрес ComfyUI задаётся серверной переменной `COMFYUI_BASE_URL`.

```sh
pnpm --dir web build
cp web/.env.example web/.env
python3 -m web.backend.server
```

`web/.env` считывается только сервером. Для GPU установить `REAL_CONNECTION=true`, согласовать `MODEL_ROOT`, `COMFYUI_INPUT_ROOT` и `COMFYUI_OUTPUT_ROOT` с реальным ComfyUI. Default — mock. Не использовать `VITE_*` для runtime-флага или секретов.

HTTP слушает только loopback. `/api/config` раскрывает только boolean режима. `/api/health` проверяет наличие сборки. `/api/model-bundles` возвращает scoped readiness всех lock-listed bundles. `/api/model-bundles/{id}/download` запускает только выбранный bundle, `/cancel` останавливает только его downloader, `DELETE /api/model-bundles/{id}` удаляет файлы после подтверждения UI и проверки активной GPU-очереди.

## Реальные операции

- Manifest содержит 8 lock-listed assets и четыре workflow bundle: H3 Base, H3 Turbo, Pika H3 Flex Ref2VA и SeedVR2. Вкладка показывает только выбранный bundle; общие файлы H3 перечислены в нескольких bundle и не скачиваются повторно.
- Открытие страницы и переключение вкладок не запускают transfer. Только кнопка «Загрузить этот комплект» создаёт downloader; повторный клик не запускает параллельный процесс. Progress и active SHA verification опрашиваются только пока операция действительно активна.
- `ready` означает точный размер и SHA-256 из manifest. Скачивание адресовано pinned repository revision и точным lock-listed paths; неизвестный формат не передаётся в loader.
- Hugging Face Xet включён по умолчанию; `HF_XET_HIGH_PERFORMANCE=1` просит клиент эффективнее использовать доступные CPU/disk/network. Это не гарантирует скорость источника/CDN. `HF_HUB_DISABLE_XET=1` оставлен явным fallback; прогресс UI берётся из текущих байтов/CLI.
- Диск проверяется до старта с конфигурируемым резервом `MIN_FREE_BYTES` (по умолчанию 20 GiB). Cancel сохраняет partial/staged files для ручного повтора. Файлы никогда не удаляются автоматически.
- Удаление доступно только вручную через кнопку с browser confirmation. Показаны конкретные removable targets/occupancy; общие targets сохраняются, активные checks/downloads/jobs блокируют удаление. Backend также требует пустую ComfyUI queue.
- ComfyUI queue проверяется перед и непосредственно перед job submission. ComfyUI system stats должны сообщить положительную free VRAM; если очередь или VRAM неизвестны, workflow блокируется. Точные минимальные пороги VRAM по всем workflow пока не закреплены, поэтому positive-free check не гарантирует отсутствия OOM.
- Отмена завершает только собственную process group. Hugging Face Hub может сохранять partials; управляй ими через ручное удаление bundle после завершения активной операции.
- H3 Ref2V: обязательный промт, до девяти изображений, одно reference-видео длительностью 2–15 секунд. Промт может ссылаться на `<Picture 1>`, `<Video 1>` и далее. Reference-видео нормализуется в кадры 24 fps с аудио, не заменяется первым кадром.
- Длительность выходного ролика задаётся отдельно в диапазоне 5–15 секунд, дробные значения разрешены. H3 округляет длину вверх по нативной сетке `17*k+5` при 24 fps; интерфейс показывает ожидаемую длину, кадры и аудио не обрезаются. Максимальный native output — 362 кадра (примерно 15.083 секунды).
- Разрешение generation фиксировано на production 1344×768; переключателя разрешения и 2K/API пути нет. Turbo LoRA включена по умолчанию (4 шага); без неё workflow обходит LoRA loader и использует 20 шагов.
- SeedVR2 Sharp 7B: ×2/×4 от короткой стороны входа. Сохраняются FPS и аудио; округление до чётного числа пикселей.
- Процент GPU относится только к текущему узлу. Загрузка модели без измерений показывает статус, не выдуманный общий процент.
- Preview и download получают сохранённый ComfyUI MP4 через backend; поддерживаются Range-запросы.
- Потеря связи — ошибка, не успешное завершение. Входы ещё выполняющегося job сохраняются. Закрытие браузера не отменяет ComfyUI job.
- Состояние задач хранится в памяти backend; после его перезапуска результаты остаются в ComfyUI output, но job IDs API не восстанавливаются.

## Подключение Vast

Текущий согласованный endpoint:

```sh
ssh -i ~/.ssh/anya-sky -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes \
    -p 28698 -N -L 127.0.0.1:8080:127.0.0.1:8080 root@83.233.229.248
```

Прежний инстанс удалён Аней. Модели и release на новом endpoint проверяются отдельно, их наличие не предполагается.

Браузер: `http://localhost:8080`. Туннель и приложение должны оставаться отслеживаемыми foreground jobs. Новые публичные порты и новый инстанс не нужны; работающий ComfyUI не перезапускается.

Для уже развёрнутой установки единый запуск backend + forward из отслеживаемого `job`:

```sh
ssh -i ~/.ssh/anya-sky -o IdentitiesOnly=yes -o BatchMode=yes \
    -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=5 -o ServerAliveCountMax=12 \
    -p 28698 -L 127.0.0.1:8080:127.0.0.1:8080 root@83.233.229.248 \
    'cd /workspace/video-creation/current && umask 077 && echo $$ >/workspace/video-creation/runtime/workbench.pid && exec python3 -u -m web.backend.server'
```

Перед повторным запуском проверить `/proc/<pid>/cwd`, `cmdline`, HTTP и ComfyUI queue. EXIT локального SSH job не доказывает, что удалённый backend остановился. Если порт уже занят пользовательским туннелем, не останавливать его и не запускать конкурирующий forward.

Деплой: новая staging/release директория, SHA-256 manifest, deploy lock, атомарный `current` symlink, health-check и возврат прежнего symlink при ошибке. Приватный `.env`, модели, input/output находятся вне заменяемой сборки; `rsync --delete` к ним запрещён.

`manifest.sha256` и `deploy-state.json` хранятся в release; `shared/workbench.env` имеет mode `0600`. При откате сначала проверить `previous` в deploy-state, владение PID и пустую GPU queue, остановить только свой idle backend, атомарно вернуть `current` к проверенному previous и запустить тот же foreground job. Если `previous: null`, прежнего релиза нет — не обещать восстановление несуществующей сборки. Отказный rollback намеренно не провоцировался.

## Статус workflow

- MiniMax H3 Ref2V и SeedVR2 Sharp 7B используют существующие ComfyUI API workflows.
- Pika H3 Flex (Ref2VA): база H3 плюс LoRA `pika_h3_flex_ref2va_comfy_bf16`, 6 шагов, до 12 референсов. Выполнение заблокировано (`execution_enabled: false`), пока совместимость LoRA с INT8 H3 не подтверждена реальным запуском. LTX 2.5 и Wan 2.1 VACE удалены.

## Проверки

```sh
PYTHONPATH=. uv run --no-project --with pytest --with aiohttp python3 -m unittest discover -s web/backend -t .
uv run --no-project --with ruff ruff check web/backend
uv run --no-project --with ruff ruff format --check web/backend
uv run --no-project --with basedpyright --with aiohttp --with pytest basedpyright web/backend
pnpm --dir web test
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web test:e2e
pnpm --dir web build
git diff --check
```

Если bundled Chromium отсутствует, E2E можно запустить на установленном Chrome без скачивания браузера:

```sh
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' pnpm --dir web test:e2e
```

Unit и mocked E2E не доказывают работу GPU. Реальные H3 Ref2V, SeedVR2 ×2/×4, медиа-проверка и download выполняются отдельно на согласованном инстансе.

## Проверено на новом инстансе 2026-10-06

- Все 7 файлов текущего unpruned Ref2V/SeedVR2 manifest прошли проверку размера и SHA-256. Unpruned diffusion `minimax_h3_ref2va_int8_convrot.safetensors`: 34 038 894 550 байт, SHA-256 `9eef934046a0671bc8a5daf87100705e1478419c574cfde70c50fbe6885f76a9`.
- Исторический H3 smoke использовал ошибочный pruned checkpoint и не считается proof unpruned. После успешного unpruned smoke удалён только точный старый `minimax_h3_ref2va_pruned_int8_convrot.safetensors`; пользовательские результаты не удалялись.
- Реальный unpruned H3 job `d3238c19392e46a6a91c0ce808784787`: ComfyUI history подтверждает loader `UNETLoaderDisTorch2MultiGPU`, нужное unpruned имя, `cuda:0,13gb;cpu,*`, 9 reference images + video, 1344×768, 124 frames, 24 fps, 4 steps `res_multistep`. RTX 5090 достигала 100% GPU utilization и 31 804/32 607 MiB.
- MP4: 2 316 592 байта, H.264 1344×768/24fps/124 frames + stereo AAC 32 kHz, 5.167 s. Проверены браузерное воспроизведение, Range 206 (1 024 байта), реальное скачивание, совпадение локального/удалённого SHA-256 `fb86ac1092d3e50c9f55bacb4181dffdb22936b8b3d4646079c3d67dd5cdc768` и полный video/audio decode.
- SeedVR2 Sharp 7B на fixture 320×192: ×2 → 640×384, ×4 → 1280×768; по 51 кадру/24fps, исходные mono AAC 48 kHz сохранены. Для обоих outputs проверены playback, Range/download SHA и полный decode.
- Исправлен readiness progress: Hugging Face Hub 1.32 создаёт process-unique `.incomplete`; устаревший более крупный partial не должен маскировать свежий активный. Regression фиксирует большой stale и меньший active partial. Скриншот и JSON сверки UI/API лежат в `outputs/workbench-checks/2026-10-06/ui-progress-counter.png` и `.json`.
- ComfyUI не перезапускался, публичные порты и новые инстансы не создавались. После согласованного уточнения настройки 5–15 секунд, fixed 1344×768 и Turbo LoRA реализованы локально; 52 backend tests, 18 frontend tests, 5 E2E, Ruff, basedpyright, ESLint, typecheck и build проходят. Текущий удалённый `current` всё ещё указывает на предыдущий release `20261006T181433Z-269e2987`: новая сборка не была staged/deployed, потому что SSH `root@83.233.229.248:28698` и старый local forward получили `Connection refused`. Отдельный real no-LoRA/20-step GPU output ещё не проверен. Readiness, прежние GPU/media results и локальная regression сводка лежат в ignored `outputs/workbench-checks/2026-10-06/`; smoke helper для возобновления — `h3-no-lora-smoke.mjs`. Приёмка Аней остаётся открытой; коммитов нет.

## FlashVSR+ Turbo и Base

- Две отдельные вкладки на узлах `kkget/ComfyUI-FlashVSR_Plus` (Apache-2.0, commit `23fef7c3bf` в `config/custom-nodes.lock.json`); граф в `web/backend/flashvsr.py` повторяет официальный `streaming_video_api.json` узла: `LoadVideo` → `FlashVSRPlusLoader` → `FlashVSRPlusVideoUpscale` → `SaveVideo`. Исходные FPS и звук сохраняются.
- Веса `ruchangyao/FlashVSR_plus` @ `46b7952` с SHA-256 в `config/models.lock.json`: на вкладку около 6,8 ГБ (DiT 5,7 ГБ + `lq_proj_in` 1,2 ГБ) плюс общий `TCDecoder.ckpt` 0,19 ГБ. Скачиваются только кнопкой вкладки; у загрузчика `auto_download` выключен.
- Масштаб ×2 или ×4 (множитель по каждой стороне: ×4 это примерно 16× пикселей, см. README узла); тяжёлые входы могут упереться в память.
- Узел нужно установить на сервере (`pip install -r requirements.txt` узла); backend перед запуском проверяет наличие узлов через `/object_info`. Не проверено на GPU: работа на Blackwell (Triton компилируется при первом запуске), скорость и качество.

## RealESRGAN x2plus и AnimeVideo-v3

- Две вкладки на встроенных узлах ComfyUI (`UpscaleModelLoader` + `ImageUpscaleWithModel`), без Docker и TensorRT; граф в `web/backend/upscale_model.py`. Масштаб фиксирован моделью: x2plus ×2, AnimeVideo-v3 ×4. Исходные FPS и звук сохраняются.
- Веса `.pth` скачиваются по прямой ссылке (`url` в `config/models.lock.json`, `curl` в `scripts/prepare-models.sh`) с обязательной проверкой размера и SHA-256: RealESRGAN_x2plus (релиз `v0.2.1`, 67 МБ), realesr-animevideov3 (релиз `v0.2.5.0`, 2,5 МБ).
- Лицензия RealESRGAN: BSD-3-Clause. Модели без коммерческой лицензии (AnimeJaNai, SPAN 2xLiveActionV1) не используются по решению Ани.
- Скорость значительно ниже, чем у TensorRT из таблицы `trtvideo` (та измерена на RTX 4090). Не проверено на GPU: загрузка этих `.pth` через spandrel в ComfyUI 0.36.0 и память на длинных входах: узел обрабатывает все кадры сразу.

## Аудио-референсы (H3, H3 Turbo, Pika)

- В формах H3, H3 Turbo и Pika есть блок «Аудио-референсы»: до 3 файлов WAV, MP3, FLAC, M4A, OGG или AAC с предпрослушиванием. В промте они помечаются `<Audio 1>`…`<Audio 3>` (как `<Picture N>` и `<Video N>`).
- Файлы загружаются тем же `PUT /api/uploads/{id}` (тип `audio`), в запросе `POST /api/jobs` передаются как `referenceAudioIds`; граф добавляет `LoadAudio` (узлы 50–52) и подключает их к `MiniMaxH3ReferenceToVideo` через `ref_audios.ref_audio_N`. Звук референс-видео по-прежнему идёт отдельно (`ref_video_audios`).
- Не проверено на GPU: влияние аудио-референса на результат и ограничения модели на длительность аудио (узел их не декларирует).

## Негативный промт (NegPiP; H3, H3 Turbo, Pika)

- Отдельных полей нет: негатив пишется прямо в единственном промте группой `(фраза, синоним:-3)` (вес отрицательный, по README автора работает до около -5). Backend (`has_negative_weights` в `web/backend/negpip.py`) сам видит такую группу в промте; экранированные скобки `\(S1\)`, положительные веса `(текст:3)` и обычный текст группой не считаются. Диапазоны `(…:-N@сек)` тоже распознаются.
- При нахождении группы backend вставляет узел `ApplyMiniMaxH3NegPiP` (`hako-mikan/comfyui-minimax-h3-negpip`, **AGPL-3.0**, commit `f725718b…` в `config/custom-nodes.lock.json`) между моделью (после LoRA) и сэмплером; его `clip` идёт в `MiniMaxH3ReferenceToVideo`. У Pika узел встаёт после её LoRA. Без группы узел в граф не добавляется и граф не меняется.
- Нода ставится оператором (backend её не устанавливает); без неё запуск с негативом вернёт ошибку ComfyUI о недостающем узле.
- Не проверено на GPU: совместимость узла с INT8-моделью, Turbo LoRA и референсами, поведение тегов `<d>…</d>` и экранированных скобок при включённом узле, достаточность веса -3. Диапазоны времени `@сек` автор описывает как нестрогие.

## Загрузка весов

`scripts/prepare-models.sh` скачивает файлы Hugging Face клиентом `hf download` (с проверкой размера и SHA-256), а файлы с полем `url` в манифесте (RealESRGAN) прямым `curl`. Недокачанные файлы лежат в `<model_root>/.staging/<комплект>/`.

## Повышение FPS (интерполяция кадров)

- Отдельная вкладка на встроенных узлах ComfyUI `FrameInterpolationModelLoader` и `FrameInterpolate` (кастомные узлы не нужны); граф в `web/backend/frame_interpolation.py`. Модели: RIFE 4.26 (по умолчанию), RIFE 4.25 и FILM; множитель ×2 или ×3 (24 → 48 или 72 fps). Длительность и звук сохраняются, FPS выхода = исходный × множитель (исходный берётся из ffprobe).
- Веса `Comfy-Org/frame_interpolation` @ `aa7009be4d` (FILM 69 МБ, RIFE по 23 МБ, SHA-256 в `config/models.lock.json`), один комплект `frame_interpolation` около 114 МБ. Лицензии: FILM Apache-2.0, RIFE MIT.
- Режим API `interpolate`: `{videoId, model: film|rife_4_26|rife_4_25, multiplier: 2|3}`.
- Из 24 fps ровно 60 не получить (множитель целый). Узел держит все кадры в памяти: для очень длинных или больших видео возможна нехватка памяти. Не проверено на GPU: скорость и качество FILM против RIFE.
