# Переход H3 Ref2V на unpruned diffusion

## Контекст
Мастерская и три сохранённых generation-workflow используют `minimax_h3_ref2va_pruned_int8_convrot.safetensors`. Аня уточнила, что нужен unpruned checkpoint. Официальный каталог `Comfy-Org/MiniMax-H3` содержит вариант Ref2V `minimax_h3_ref2va_int8_convrot.safetensors`: 34 038 894 550 байт; опубликованный LFS SHA-256 — `9eef934046a0671bc8a5daf87100705e1478419c574cfde70c50fbe6885f76a9`.

RTX 5090 имеет 32 ГБ VRAM; файл unpruned весит больше, поэтому генератор должен сохранить фактически поддерживаемое DisTorch распределение `cuda:0,13gb;cpu,*` и приёмка должна доказать загрузку и генерацию на GPU с offload, а не предполагать, что checkpoint целиком помещается в VRAM.

## Цель
Перевести серверную readiness, генератор и сохранённые ComfyUI workflow с pruned на официальный unpruned Ref2V INT8 checkpoint, сохранив отдельную Ref2V Turbo LoRA и работающий CPU-offload. Подтвердить размер/SHA модели, фактическое имя загруженного workflow и успешный GPU MP4.

## Что изменится
1. `config/models.lock.json` — официальный unpruned checkpoint, размер и SHA-256.
2. `workflows/01-text-to-video-24gb.json`, `workflows/02-text-to-video-1344-q4-tiled.json`, `workflows/02-text-to-video-1344-q4-8step-staged.json` — выбрать unpruned файл; сохранить loader, offload, Ref2V LoRA и sampler.
3. Тесты конфигурации/workflow — подтвердить manifest и graph используют один и тот же unpruned filename и CPU-offload.
4. `specs/real-vast-workbench-connection.md`, `web/README.md`, `CONTEXT.md` и handoff — зафиксировать исправленную модель и только реально выполненные проверки.

## Детали реализации
1. Сверить официальный Hugging Face tree/LFS SHA и фактические свободные ресурсы GPU, RAM и диска.
2. До проверки нового checkpoint не удалять pruned файл и результаты предыдущего smoke.
3. Скачать новый файл staging-сценарием проекта; измерить фактические байты и SHA-256, затем атомарно поместить по manifest target. Не перезапускать ComfyUI.
4. Развернуть manifest/workflow транзакцией staging + SHA-256 manifest + deploy lock + атомарный `current` + health-check. Сохранить предыдущий релиз для rollback; подтвердить отсутствие активных ComfyUI jobs до любой замены backend.
5. Подать настоящий Ref2V prompt и проверить сохранённую ComfyUI history: filename UNet loader должен быть именно unpruned. Проверить статус, размер/FPS/dimensions и полный decode MP4.
6. Во время загрузки readiness должна показывать измеренный размер реального Hugging Face `.incomplete` для текущего model SHA, а не нулевой прогресс и не bytes чужого cache-файла.
7. Удалять неправильный pruned checkpoint только после успеха unpruned-проверки, при пустой ComfyUI queue и после повторной проверки пути/имени удаляемого файла.

## Критерии приёмки
- [ ] В manifest задан `minimax_h3_ref2va_int8_convrot.safetensors`, 34 038 894 550 байт и официальный SHA-256.
- [ ] Все три generation-workflow и серверный API prompt выбирают unpruned файл, но сохраняют CPU-offload `cuda:0,13gb;cpu,*`, Ref2V LoRA, четыре шага и sampler `res_multistep`.
- [ ] Readiness показывает measured bytes текущего HF cache `.incomplete`, отфильтрованного по SHA модели, и не считает чужие partial-файлы.
- [ ] Новый checkpoint на Vast полностью совпадает с размером и SHA-256; старый checkpoint не удалён до успешной GPU-проверки.
- [ ] Полные backend/frontend tests, линтеры, typecheck, build, E2E и `git diff --check` проходят.
- [ ] ComfyUI history доказывает фактический unpruned loader и successful GPU H3 Ref2V MP4; MP4 декодируется целиком и доступен для preview/download.
- [ ] Доказательства/инструкции обновлены, коммита нет. Финальная приёмка Ани остаётся открытой.
