# Реальный режим видеомастерской на Vast.ai

## Статус

Утверждено Аней: реальная мастерская на Vast, MiniMax H3 Ref2V, загрузка моделей, GPU smoke и изменение исходного pipeline. Уточнение от Ани: менять исходный манифест, не создавать отдельный Ref2V pipeline.

Последний согласованный endpoint: `root@83.233.229.248:28698`, ключ `~/.ssh/anya-sky`, `IdentitiesOnly=yes`. При последней успешной preflight-проверке: RTX 5090 с 32 607 MiB VRAM, около 60 GiB RAM, Python 3.12.14, aiohttp 3.14.3, ComfyUI 0.36.0 на loopback `8188`; все 7 файлов manifest готовы и SHA-256 проверены. Реальный unpruned H3 Ref2V GPU/browser smoke и SeedVR2 ×2/×4 media/download проверки завершены. Ошибочный pruned-файл удалён только после успешного unpruned smoke. Последний известный `current` — предыдущий health-checked release `20261006T181433Z-269e2987`. После локальной реализации новых H3 controls SSH к согласованному endpoint и `localhost:8080` стали отвечать `Connection refused`; новая сборка не staged/deployed, причины отказа не установлены, ComfyUI не перезапускался. Дальнейший remote доступ ждёт проверки статуса/endpoint Аней. Приёмка остаётся открытой. `136.148.216.185:21171` удалён Аней; старые endpoints не использовать.

## Контекст

На старте задачи `web/` была локальным SolidJS mock. Реализованы оба режима. Первый H3 smoke выявил ошибочную pruned diffusion модель; общий manifest и workflows переведены на официальный unpruned Ref2V INT8. Новый checkpoint прошёл независимую проверку размера/SHA и использован реальным ComfyUI loader с DisTorch CPU-offload в завершённой H3 generation job. Старый pruned smoke не засчитывается как proof. SeedVR2 ×2/×4 fixture smoke/media/download также прошли. Подтверждение Аней остаётся открытым.

Историческая read-only проверка прежнего инстанса `54311196` показала: `RTX 5090` с 32 607 MiB VRAM, Python 3.12.14, Hugging Face CLI установлен, свободно около 150 GiB. ComfyUI 0.36.0 уже запущен на loopback: `GET /system_stats` возвращает HTTP 200, очередь пуста. API node `MiniMaxH3ReferenceToVideo` поддерживает до 9 изображений и до 3 reference-видео; видеореференс принимает кадры 24 fps длительностью 2–15 секунд. Все 7 файлов текущего `config/models.lock.json` отсутствуют. Node.js и pnpm на инстансе нет; интерфейс соберётся локально, backend будет работать на имеющемся Python.

## Цель

`REAL_CONNECTION=false` сохраняет текущий полностью локальный mock. При `REAL_CONNECTION=true` мастерская показывает реальный прогресс проверки/скачивания моделей, реальную готовность ComfyUI и использует GPU-workflows для генерации и upscale. На Vast ничего не публикуется в интернет: страница доступна через SSH-forward на `localhost:8080`.

## Что изменится

1. `web/backend/` — Python backend для readiness, загрузок, ComfyUI API и реальных jobs; исходные config/workflows переводятся на Ref2V, denoise/V-BM3D код сохраняется.
2. `web/src/` — runtime-переключение mock/real, получение статусов и результатов через backend.
3. `web/.env.example` и приватный `web/.env` на инстансе — настройка `REAL_CONNECTION`; секреты в браузер не передаются.
4. `web/e2e/` и backend tests — проверки обоих режимов и обработки ошибок.
5. `web/README.md` — инструкция запуска и SSH-туннеля.
6. Отдельный deploy/health-check для приложения в `/workspace/video-creation`; не удалять, не заменять и не останавливать существующий ComfyUI.

Исходный `config/models.lock.json` переводится на H3 Ref2V; config/presets и основной API graph используют эти же веса. Backend использует существующие scripts/prepare-models.sh и scripts/start.sh, без изменения denoise/V-BM3D postprocess. Три исходных генерационных workflow также переведены на Ref2V INT8/4-step; старые имена файлов сохранены для совместимости. Мастерская строит API graph динамически из исходного manifest.

## Утверждённая поправка весов Ref2V

Источник: [официальный H3 R2V guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native#minimax-h3-reference-to-video-r2v) и Hugging Face API `Comfy-Org/MiniMax-H3?blobs=true`.

Аня уточнила требование: Ref2V diffusion нужна именно unpruned. Официальный каталог Hugging Face `Comfy-Org/MiniMax-H3` содержит `diffusion_models/minimax_h3_ref2va_int8_convrot.safetensors`, 34 038 894 550 байт, LFS SHA-256 `9eef934046a0671bc8a5daf87100705e1478419c574cfde70c50fbe6885f76a9`.

- diffusion: `minimax_h3_ref2va_int8_convrot.safetensors` (unpruned INT8);
- Ref2V Turbo LoRA: `loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors`, тот же repository, 1 956 193 000 байт, SHA-256 `5b9ab5ade15d0775676d01a907268a69a1468dc6033b3b0d3ded5502f3ebb84c`.

Unpruned diffusion превышает 32 GB VRAM RTX 5090; DisTorch распределяет модель через `cuda:0,13gb;cpu,*`, CPU — donor. Остальные пять файлов сохраняются. Sampler `KSamplerSelect/res_multistep`, Ref2V LoRA через `LoraLoaderModelOnly`, 4 шага. Dynamic inputs взяты из official template (`ref_images.ref_image_0`, `ref_videos.ref_video_0`). Итоговый объём семи файлов — 77 917 114 158 байт (72,57 GiB).

## Детали реализации

1. Режим берётся из `REAL_CONNECTION` при старте backend. Значение по умолчанию — `false`; `true` — только булев флаг, не секрет. Runtime-config endpoint возвращает только выбранный режим и никогда не отдаёт содержимое `.env`.
2. Backend и UI обслуживаются с `127.0.0.1:8080`. ComfyUI остаётся доступен только на loopback `127.0.0.1:8188`. Локальный доступ — через SSH-forward с ключом `~/.ssh/anya-sky`, портом `28698`; никаких публичных web-портов не открывать.
3. Readiness сверяет размер и SHA-256 обязательных файлов прямо из манифеста. Для недостающих/повреждённых файлов используется `scripts/prepare-models.sh`; UI получает фактический прогресс текущего файла, общий прогресс по ожидаемым байтам, фазу SHA-проверки и завершённость каждого файла. Модель не считается готовой до проверки размера и SHA-256. Повторный запуск не создаёт параллельных загрузок и не обходит существующий lock.
4. После готовности файлов backend проверяет ComfyUI через реальный API. Если API уже отвечает, процесс не перезапускается. Если он не отвечает, запускать только предусмотренный проектом `scripts/start.sh` и ждать health-check с таймаутом и понятной ошибкой.
5. «Генерация» ставит workflow с `MiniMaxH3ReferenceToVideo`, передавая промт, до 9 изображений и одно видео из интерфейса как последовательность кадров 24 fps (допустимая длительность 2–15 секунд). Видеовход валидируется/преобразуется в CFR 24 fps с сохранением аудиопотока. CFR-опция выбирается по фактическим возможностям установленного ffmpeg: `-fps_mode cfr` либо legacy `-vsync cfr`. «Upscale» ставит SeedVR2 Sharp workflow и применяет выбранный x2/x4 к разрешению входного видео. UI показывает статус очереди, реальные этапы выполнения, ошибки ComfyUI и playable-файл, полученный от сервера.
6. API-адаптеры для real workflows размещаются в `web/backend/`; исходные config/workflows согласуются с Ref2V, демонстрационные mock-функции и denoise/V-BM3D не подменяются. Перед генерацией проверяются обязательные входы и готовность моделей.
7. Деплой — в новый каталог `/workspace/video-creation`, через staging и manifest; `.env`, модели, входы и результаты не синхронизируются и не удаляются. После health-check доступен откат приложения без перезапуска ComfyUI.
8. Туннель для работы: `ssh -N -i ~/.ssh/anya-sky -o IdentitiesOnly=yes -p 28698 -L 127.0.0.1:8080:127.0.0.1:8080 root@83.233.229.248`, затем открыть `http://localhost:8080`. Предпочтителен единый отслеживаемый foreground SSH job, запускающий backend и держащий forward; не использовать detach.

## Настройки H3 generation

Уточнённые требования и H3 frame-grid зафиксированы в [спецификации настроек H3](h3-generation-controls.md). Выходное разрешение неизменно — 1344×768, UI выбора разрешения нет. Для запрошенной длины 5–15 секунд workflow округляет кадры вверх до native grid `17*k+5` при 24 fps и сохраняет полный результат без обрезки. Turbo LoRA включена по умолчанию (4 шага); при снятой галочке node исключается, используется `hero_base` с 20 шагами. Reference-видео продолжает отдельно валидироваться/нормализоваться и не задаёт длительность результата.

## Критерии приёмки

- [x] При `REAL_CONNECTION=false` прежний mock и его Playwright/unit-тесты продолжают работать без внешних вызовов.
- [x] При `REAL_CONNECTION=true` страница через SSH-туннель обращается к backend на инстансе; публичные web-порты не открыты.
- [x] Все 7 файлов unpruned manifest проверены по размеру/SHA; UI progress сверён с bytes активного HF partial, stale transfer не маскирует его.
- [x] ComfyUI readiness определяется реальным HTTP API; уже работающий ComfyUI не перезапускается.
- [x] Unpruned H3 Ref2V выполнил реальный ComfyUI job с 9 images + video; SeedVR2 Sharp 7B реально обработал ×2/×4. Результаты воспроизводятся и скачиваются через UI.
- [x] Тестируются ошибка сети/API, недостаток диска, отмена/повтор загрузки и повторный запуск без дублирования процессов.
- [x] Backend/frontend tests, Ruff, basedpyright, lint, typecheck, build и 5 E2E проходят; на Vast health-check успешен, H3/SeedVR2 GPU и media checks пройдены.
- [x] Локально проверены duration/UI/API и workflow branching: Turbo graph — 4 шага, no-LoRA graph — 20 шагов; выходная длина не зависит от reference-видео. Последний regression: 52 backend tests, 18 frontend tests, 5 E2E, Ruff, basedpyright, ESLint, typecheck и production build прошли.
- [ ] Текущие изменения H3 controls безопасно staged/deployed через SHA manifest, lock, atomic current и health-check; SSH отказал до staging, предыдущий release не менялся.
- [ ] Отдельный deployed Vast GPU smoke no-LoRA/20-step, native frame-count/media/Range/download verification и полный decode.
- [ ] Аня открыла мастерскую и подтвердила результат.

## Проверенные изменения и оставшиеся риски

- Исходные generation workflows и backend используют locked Ref2V INT8/Ref2V 4-step LoRA и `res_multistep`; SeedVR2 example согласован с Sharp 7B. Dotted inputs подтверждены template и установленным ComfyUI V3 (`ref_images.ref_image_0..8`, `ref_videos.ref_video_0`).
- Новый объём семи моделей — 77 917 114 158 байт (72,57 GiB). Unpruned checkpoint `diffusion_models/minimax_h3_ref2va_int8_convrot.safetensors`: 34 038 894 550 байт, SHA-256 `9eef934046a0671bc8a5daf87100705e1478419c574cfde70c50fbe6885f76a9`; проверен readiness и отдельным хешированием. Старый pruned checkpoint удалён после GPU-подтверждения замены; ComfyUI не останавливался.
- SaveVideo V3 использует API choices `format: mp4`, `format.codec: h264`, `format.codec.encoding: auto`; nested dict собирает ComfyUI runtime. Реальные CPU SaveVideo и H3 MP4 подтвердили этот формат.
- Временные `/tmp` helpers после паузы исчезли; для возобновления используется сохранённый результат и заново создаются служебные deploy/smoke artifacts. Доказательства хранятся в ignored `outputs/workbench-checks/`.
- Первый H3 result `generation-f085ff389c694b398e155946dd8a5391.mp4` был создан с pruned weights и не доказывает unpruned. Отдельный unpruned job `d3238c19392e46a6a91c0ce808784787` зафиксирован в ComfyUI history: loader node 1 использовал unpruned имя и `cuda:0,13gb;cpu,*`, graph содержал 9 images + video, 1344×768, 124 frames, 24 fps, 4 steps `res_multistep`. MP4 2 316 592 bytes, H.264 1344×768/24fps/124 кадра + AAC stereo 32kHz/5.167s; browser playback, Range 206, локальный download SHA/size и полный decode совпали. SeedVR2 fixture outputs `upscale-f3d48d1f9cee4e2ebf601835714e5060.mp4` и `upscale-34eb7cd8abc948c99989a636b65c57f4.mp4` прошли Range 206, download SHA, ffprobe и полный decode. Доказательства: ignored `outputs/workbench-checks/2026-10-06/`.
- Две отдельные SSH-сессии ранее завершились с timeout при живом удалённом app. Причина не установлена; текущий отслеживаемый запуск объединяет app и forward в один сеанс. Живость подтверждается HTTP и job status, а не фактом запуска команды.
