# Контекст проекта

## Ошибки и решения

[2026-09-20] Проблема: shell-проверка upstream SHA использовала разбор строки с именем репозитория и веткой через `set --`, из-за чего сформировался некорректный URL → Решение: разбирать элементы через явный разделитель `:` и отдельные переменные `repo`/`branch`.

[2026-09-20] Проблема: zsh считает переменную `path` специальным отражением `PATH`; присваивание пути файла переменной `path` удалило каталоги из PATH, поэтому `curl` и `sed` стали недоступны → Решение: не использовать имя `path` в shell-скриптах, применять `file_path`.

[2026-09-20] Проблема: hadolint обнаружил непинованные системные пакеты и Bash-команду `local` в Dockerfile → Решение: зафиксировать базовый image digest, явно объяснить динамическую резолюцию пакетов комментариями hadolint и подавить только DL3008/SC3043; перепроверить hadolint, shellcheck, shfmt и actionlint.

[2026-09-20] Проблема: в zsh цикл проверки Secret Agent использовал read-only переменную `status`, поэтому shell остановился до postcondition snapshot → Решение: не использовать имена zsh special parameters (`status`, `path`); применять `page_state` и после навигации отдельно проверять status/snapshot.

[2026-09-20] Проблема: GitHub Actions после push завершился Startup failure, потому что аккаунт GitHub billing locked и требует обновления payment information → Решение: не менять платёжные данные и не включать платный runner без отдельного согласования; зафиксировать блокер, использовать только доступную бесплатную квоту или согласовать альтернативный бесплатный remote builder.

[2026-09-20] Проблема: после перевода репозитория в public GitHub Actions quality job прошёл, но Docker build остановился на `vapoursynth==80`: PyTorch runtime Python ниже 3.12, а VapourSynth 80+ требует Python>=3.12 → Решение: определить ABI базового образа и выбрать совместимую комбинацию Python/VapourSynth/BM3D/BestSource, не удаляя обязательный V-BM3D стек.

[2026-09-20] Проблема: после установки Python 3.12 Docker build дошёл до `pip check`, но base package `conda-lockfiles 0.2.0` требует `pydantic>=2.12.5`, а зависимости ComfyUI оставили pydantic 2.11.10 → Решение: зафиксировать совместимую pydantic-версию после установки upstream requirements и повторить `pip check`.

[2026-09-20] Проблема: zsh registry verification использовала read-only переменную `status`, поэтому проверка manifest остановилась до вывода digest → Решение: использовать `http_status`/`page_state` и не использовать zsh special parameters; повторить проверку без вывода временного registry token.

[2026-09-20] Проблема: диагностический Python one-liner для локальных CDP targets потерял кавычки ключей внутри shell f-string и завершился `NameError` → Решение: для JSON diagnostics использовать heredoc или безопасные JSON-ключи, не смешивать shell/Python quoting.

[2026-09-20] Проблема: heredoc Python был подключён после pipe от curl и перекрыл stdin, поэтому CDP JSON не попал в `json.load` → Решение: сохранять diagnostics response во временный файл с безопасным именем (`json_file`) или использовать `python -c` без heredoc после pipe.

[2026-09-20] Проблема: Vast preview показывал `$0.050/ч`, но после готовности instance фактическая ставка стала `$0.410/ч`; диалог Stop также сообщил о storage charge `$1.2/день` → Решение: после создания всегда проверять active/stopped billing rate; при отсутствии согласованного бюджета и при `0.0/150 GB`, `No Volumes` уничтожить пустой instance для остановки начислений; зафиксировать фактически списанную сумму.

[2026-09-20] Проблема: RTX 5090 instance `51774140` за `$0.028/ч` оставался `Loading...` на 13-й минуте, Vast logs заканчивались `Pull complete`, direct/proxy SSH отвечал `Connection refused`, VRAM/RAM/disk были нулевыми → Решение: не тратить лимит на бесконечное ожидание; уничтожить instance без данных, зафиксировать около `$0.02` расхода и перейти к другому host/GPU.

[2026-09-20] Проблема: запуск `pytest -q` из `/Users/a/Space` собрал чужие тесты LifeLine/executorch и завершился fatal abort → Решение: запускать pytest из корня `/Users/a/Space/Projects/AI/vast-video-pipeline` или указывать только `tests/`.

[2026-09-20] Проблема: Hugging Face CLI 1.32.0 отверг одновременные `--local-dir` и `--cache-dir` в `prepare-models.sh` → Решение: выставить `HF_HOME`/`HF_XET_CACHE` и передавать только `--local-dir`; проверить bash/shellcheck/shfmt и pytest из корня проекта, затем установить исправленный script на remote instance.

[2026-09-20] Проблема: Secret Agent `upload` завершился `unknown flag: --path` → Решение: использовать документированный флаг `--file` для загрузки workflow через `input#comfy-file-input`.

[2026-09-20] Проблема: H3 124-frame 1344x768 sampling завершился, но ComfyUI process исчез во время/после VAE decode на host с 31.2 GB RAM; log не содержит traceback и output video отсутствует → Решение: не уничтожать instance; перезапустить ComfyUI и повторить 5.2-секундный граф на 608x352 с теми же 124 frames для снижения decode memory.

[2026-09-21] Проблема: stock ComfyUI `SaveLatent` вызывает `.contiguous()` у H3 `NestedTensor`, поэтому node 16 падает после успешного sampling → Решение: добавить `SaveH3StagedLatent`, который вызывает `unbind()`, проверяет две video/audio части и сохраняет их отдельными CPU-тензорами в `.h3latent.pt`; парный `LoadH3StagedLatent` восстанавливает два обычных `LATENT` выхода.

[2026-09-21] Проблема: первая версия custom node проверяла `isinstance(raw_samples, torch.Tensor)`, но фактический H3 NestedTensor не прошёл эту проверку → Решение: использовать duck typing через `is_nested` и вызываемый `unbind()`, сохраняя строгую проверку частей после распаковки.

[2026-09-21] Проблема: объединённый sampling + VAE decode на 1344x768 сбрасывал ComfyUI из-за пикового потребления памяти → Решение: разделить pipeline на `02-text-to-video-1344-q4-tiled.json` (sampling/staging) и `03-h3-staged-video-audio-decode.json` (temporal-tiled video VAE, audio VAE, mux); staged run успешно дал 1344x768, 124 frames, 24 fps и AAC audio.

[2026-09-21] Проблема: zsh интерпретировал optional FFmpeg map `0:a:0?` как glob и остановил перекодирование до запуска → Решение: заключать такой map-спецификатор в одинарные кавычки (`'0:a:0?'`).

[2026-09-21] Проблема: `SaveVideo` с `auto` дал низкий H.264 bitrate около 3.59 Mbps → Решение: сохранить VAE settings `512/64/16/4`, а в `SaveVideo` явно выбрать MP4 → H.264 → re-encode → CRF `10`; проверенный промежуточный output получился около 24.61 Mbps.

[2026-09-21] Решение: по подтверждённой настройке Ани decode workflow переведён на lossless H.264 CRF `0`; VAE settings не менялись. Проверенный output получился 45,242,033 bytes и около 70.05 Mbps, с 1344x768, 124 frames, 24 fps и AAC audio.

[2026-09-21] Проблема: remote image verification использовала отсутствующие в image `identify` и `file`, поэтому shell-команда завершилась ошибкой после успешного scp → Решение: проверять remote PNG через встроенный `/opt/conda/bin/python` и PIL, а размер файла — через `stat`.

[2026-09-21] Проблема: reference PNG были скопированы в `/workspace/ComfyUI/input`, но ComfyUI `LoadImage` читает `/opt/ComfyUI/input`, поэтому API validation отвергла файлы как invalid image → Решение: копировать пользовательские input files в фактический `folder_paths.get_input_directory()` (`/opt/ComfyUI/input`).

[2026-09-21] Проблема: SeedVR2 `LoadVideo` показал symlinked V-BM3D MKV в options, но validation отклонила его как invalid video → Решение: использовать обычную копию V-BM3D MKV в `/opt/ComfyUI/input`, а не symlink; перед `cp` удалить прежний symlink, иначе `cp` считает source и destination одним файлом.

[2026-09-21] Проблема: ComfyUI process завершился/reset во время второго длительного SeedVR2 upscale; второй output не был сохранён → Решение: проверить process/queue, перезапустить ComfyUI без удаления уже сохранённых outputs и повторить variant.

[2026-09-21] Проблема: параллельный rsync трёх больших output-каталогов получил `Connection reset by peer`/код 12 после передачи около 1.3 GB на поток → Решение: повторять resumable-синхронизацию по одному каталогу с `--partial`, SSH keepalive и проверкой размеров/хэшей.

[2026-09-21] Проблема: после SeedVR2 non-pruned no-Turbo SSH proxy `ssh8.vast.ai:19980` и direct `180.189.55.43:42518` стали отвечать `Connection refused`; remote instance/output недоступны для завершения rsync → Решение: сохранить и проверить уже скачанные локальные outputs; оставшиеся partial files не считать готовыми и не удалять до восстановления доступа.

[2026-09-21] Проблема: финальная локальная команда `python3 -m pytest -q tests` не запустилась, потому что активный Python 3.14 не содержит pytest (`No module named pytest`); временная `uv`-среда с pytest остановилась на отсутствии `torch` → Решение: не устанавливать тяжёлые зависимости в системный Python; ранее тесты проекта проходили из корректного окружения/корня, текущую финальную проверку считать `ruff`/`git diff --check` успешной, pytest — не выполненным в этой среде.

[2026-09-21] Проблема: Vast instance `51902318` на host `402342` оставался в `Loading` со статусом `Pulling` более 54 минут, хотя Docker-образ занимал 8.67 GB; SSH был недоступен → Решение: не ждать бесконечно и не тратить instance без данных, уничтожить его и выбрать другой RTX 5090 host `149637` (`51909085`).

[2026-09-21] Проблема: `scp` получил `stat local "29084": No such file or directory`, потому что SSH-аргумент `-p` был передан scp вместо порта → Решение: для scp использовать отдельный параметр `-P 29084`, а `-p` оставлять только для ssh.

[2026-09-21] Проблема: Hugging Face CLI 1.32 остановил `prepare-models.sh` с `Cannot use both --local-dir and --cache-dir at the same time`, потому что `HF_HOME`/`HF_XET_CACHE` задавали cache-dir одновременно с `--local-dir` → Решение: перед вызовом CLI с `--local-dir` не экспортировать эти cache-переменные.

[2026-09-21] Проблема: direct SeedVR2 graph получил `Invalid video/audio file`, потому что `LoadVideo`/`LoadAudio` валидируют файлы только в ComfyUI input, а H3 intermediate был сохранён в output → Решение: скопировать промежуточный MP4 в `/opt/ComfyUI/input/` и передать его basename в оба loader node.

[2026-09-21] Проблема: попытка возобновить скачивание через rsync получила `Permission denied (publickey)`, потому что в SSH-аргументах rsync использовался `-P` вместо порта `-p` → Решение: для `rsync -e ssh` использовать `-p 29084`; для scp по-прежнему использовать `-P 29084`.

[2026-09-21] Проблема: исправленный rsync-сеанс скачивания финального MP4 завершился `Connection closed by 54.211.20.77 port 29084` после частичной передачи → Решение: скачивать MP4 независимыми проверяемыми `dd`-чанками через несколько SSH-сеансов, затем собрать файл и проверить размер/SHA-256.

[2026-09-21] Проблема: первый HTTP-over-SSH range test стартовал до готовности локального tunnel и получил `curl (7) Failed to connect to 127.0.0.1:18188` → Решение: добавить retry с задержкой после запуска туннеля.

[2026-09-21] Проблема: aria2c отклонил тестовый параметр `--timeout=900`, потому что допустимый диапазон — 1–600 секунд → Решение: использовать `--timeout=600`.

[2026-09-21] Проблема: aria2c с 8 HTTP connections через один SSH tunnel держал общий поток около 20–30 KiB/s (как один поток), поэтому многопоточность не ускорила proxy → Решение: не считать aria2c ускорением через один tunnel; проверить прямой SSH по публичному IP или независимые tunnels.

[2026-09-21] Проблема: при первом direct scp использован `-p 48863`, и `scp` воспринял `48863` как путь (порт у scp задаётся только `-P`) → Решение: использовать `scp -P 48863`.

[2026-09-21] Проблема: direct SSH оказался доступен только по `137.175.76.24:48863` (порт 22 закрыт), но aria2c через один и через восемь direct tunnels удерживал суммарно примерно 10–35 KiB/s; raw SSH и scp дали тот же порядок и обрывались → Решение: остановить transfer и не удалять instance до выбора отдельного cloud-sync маршрута.

[2026-09-21] Холодная загрузка 7 моделей на instance `51909085` завершена с кодом 0: начало `2026-09-21T13:05:37Z`, конец `2026-09-21T13:39:50Z`, длительность `2053 s` (`34:13`), ожидаемый объём `76,740,768,758` bytes. `prepare-models.sh` проверил размер и SHA-256 каждого файла перед перемещением; HF CLI сообщил, что запросы были без авторизации, токен в логи не попадал.

## Результаты H3 reference variants и постобработки

[2026-09-21] Проверены четыре reference-first H3 постановки через ComfyUI API: `pruned-int4-turbo-8`, `pruned-int4-base-20`, `nonpruned-int8-turbo-8`, `nonpruned-int8-base-20`. Для каждой sampling history завершилась успешно, staged latent и 1344x768 H.264 CRF0 decode были сохранены; граф использовал только `first_frame` из начальной reference PNG.

[2026-09-21] SeedVR2 Sharp 7B успешно завершил все четыре 4K graph history: prompt IDs `2d7053f2-e652-44c5-83f6-9612d1913050` (pruned Turbo), `7ddf905d-50f8-4a08-815b-3769ed0fd367` (pruned no-Turbo), `1348f146-b667-437b-b35a-398c90d6074e` (non-pruned Turbo), `12389379-72b1-4d08-ab90-a12c65f6622b` (non-pruned no-Turbo). Все graph использовали оригинальный H3 audio через `LoadAudio`, FPS из `GetVideoComponents` и H.264 CRF `0`.

[2026-09-21] Через `ffprobe` проверены все доступные финальные SeedVR2 paths: pruned Turbo remote complete и оба non-pruned local complete имеют `3780x2160`, 124 frames, 24 fps, H.264, AAC, длительность 5.167 s; pruned no-Turbo local partial структурно показывает те же streams, но из-за размера 376307712 вместо ожидаемых 473737727 не считается целым output. SeedVR2 сохраняет aspect ratio при заданной высоте 2160, поэтому ширина 3780, а не 3840. Локально также проверены H3 1344x768 MP4 и Flow/V-BM3D FFV1 outputs: 124 frames, 24 fps, длительность 5.167 s; audio-preserving Flow outputs имеют FLAC stream.

[2026-09-21] SHA-256 локальных проверенных outputs: reference non-pruned Turbo `f9a936147b273f45718bf30d11430616cc6cfab5bb42158b0bf872ec7e2f24cd`; reference non-pruned no-Turbo `613baa42e7ac2cf3d5ca83f21b5646f05d76ed2d4de231afde746cf9203c7e9c`; SeedVR2 non-pruned Turbo `1a7a94c772283f6be642315b2be47551d4e90499d701d9c43f3fce5a5293257e`; SeedVR2 non-pruned no-Turbo `ffccb95b48ed969e3cdd897413e55d858e42a0834530404207239ef994db63ec`.

[2026-09-21] Готовые локальные outputs находятся в `/Users/a/Downloads/h3-pipeline-output/`: две non-pruned reference MP4, две non-pruned SeedVR2 4K MP4, три полных V-BM3D MKV (`pruned base`, `non-pruned Turbo`, `non-pruned no-Turbo`) и полные FlowDenoise MKV для двух non-pruned вариантов. Три недокачанных файла перенесены в `incomplete/` с ожидаемыми remote-размерами; они не считаются готовыми.

## Правила

- Docker на Mac не устанавливать, не запускать и не собирать.
- Не включать секреты, токены, модели и результаты в Git или Docker image.
- Перед каждым изменением запускать доступные проверки; после ошибки добавлять запись выше.
- Коммиты не выполнять самостоятельно.
