# Таб Pika H3 Flex (Ref2VA) и удаление Wan 2.1 VACE и LTX 2.5

## Дополнение (решение Ани)

Vast-инстанс удалён Аней; сервера нет, все серверные шаги (деплой, загрузка, смоук) выполняются позже на новом инстансе. LTX 2.5 тоже удаляется целиком. Итоговые вкладки (подтверждено Аней): 1) MiniMax H3 (Base, без переключателя Turbo), 2) MiniMax H3 Turbo (отдельная вкладка с Turbo LoRA, тот же граф и bundle `h3_turbo`), 3) Pika H3 Flex (Ref2VA), 4) SeedVR2. В вкладке H3 Base галка Turbo убирается.

Дополнительно к списку изменений: удалить bundles `ltx25_distilled`, `ltx25_dev` и их ассеты, которые не используются другими bundles (проверить общий доступ по id перед удалением); удалить `web/backend/ltx_reference.py`, `test_ltx_reference.py`, `specs/ltx25-reference-pipelines.md`, форму LTX в UI и e2e-проверки LTX; HF-токен на сервере больше не нужен. Удаление не затрагивает пользовательские данные.

## Контекст

Аня решила: таб Wan 2.1 VACE удалить, вместо него добавить таб Pika H3 Flex. Нужен только режим Ref2VA. Turbo-LoRA не нужны: Pika H3 Flex дообучена на шесть шагов сэмплирования, точное расписание шагов записано в манифесте адаптера и является частью модели. Сторонний репозиторий `Pika-H3-Flex-Turbo-LoRA` не используется.

Источник: `Pika-Labs/Pika-H3-Flex` (лицензия MiniMax H3 Community License, `license:other`, база `MiniMaxAI/MiniMax-H3`). Для ComfyUI в репозитории лежат BF16 LoRA `comfy/loras/pika_h3_flex_ref2va_comfy_bf16.safetensors` (1 956 172 176 байт). Официальный ComfyUI-шаблон: `Comfy-Org/workflow_templates`, `templates/video_pika_h3_flex_ref2va.json`; туториал: docs.comfy.org/tutorials/video/pika/pika-h3-flex.

Не подтверждено и проверяется в ходе работы: совместима ли эта LoRA с нашими INT8 ConvRot файлами H3 (`minimax_h3_ref2va_int8_convrot`, текст-энкодер INT8), которые уже в манифесте. Полный diffusers-репозиторий (около 210 ГБ) не используется и не скачивается.

## Цель

Заменить вкладку VACE вкладкой «Pika H3 Flex»: референсы (до 12), промт, шесть шагов, результат в MP4 со звуком. Пока совместимость LoRA не доказана реальным запуском, выполнение остаётся заблокированным.

## Что изменится

1. `config/models.lock.json`: удалить bundle `wan21_vace_control` и ассеты `wan21_vace_14b_fp16`, `wan21_umt5_fp8`, `wan21_vae`; добавить ассет `pika_h3_flex_ref2va_lora` (репозиторий, закреплённый commit `3ff166447d5edb1406107ab8370c42000ce173e4`, размер, SHA-256 из `comfy/SHA256SUMS`, формат `safetensors-bf16-lora`, загрузчик `comfyui-lora`) и bundle `pika_h3_flex_ref2va` с ассетами базы H3 (общие `h3_audio_vae`, `h3_video_vae`, `h3_diffusion_ref2v_int8`, `h3_text_encoder_int8`) и этой LoRA. `execution_enabled: false` до успешного смоука.
2. `workflows/wan21-vace-control-api.json`, `web/backend/vace.py`, `web/backend/test_vace.py`: удалить. Добавить `workflows/pika-h3-flex-ref2va-api.json` и `web/backend/pika.py` с билдером и тестами.
3. `web/backend/jobs.py`, `server.py`: убрать режим `vace`; добавить режим `pika`; выходной узел определять по метке узла, а не по номеру (исправляет класс ошибки с зависанием результата).
4. `web/src/App.tsx`, `real-connection.ts`, e2e: вкладка «Pika H3 Flex» вместо VACE (референсы, промт, seed, длительность); убрать VACE-форму и тесты.
5. `config.py`, README, `CONTEXT.md`, `specs/multi-model-workbench.md`: обновить документацию.
6. Сервер: релиз заливается атомарно; модели не скачиваются без нажатия кнопки Ани.

## Детали реализации

- Граф строится на основе официального шаблона `video_pika_h3_flex_ref2va`: шаблон скачивается в репозиторий как есть, переводится в API-формат; имена узлов и входов сверяются с живым `/object_info`; точное расписание шагов и сэмплер берутся из манифеста адаптера (`ref2va/manifest.json`), а не подбираются.
- Загруженные референсы (до 12) передаются как image conditioning; режим FL2VA не реализуется.
- Токен Hugging Face не нужен, если репозиторий не закрыт; проверяется HTTP-статусом файла до загрузки. Если закрыт, загрузка блокируется с понятной причиной.
- Общая очередь GPU, проверка VRAM и защита общих ассетов при удалении сохраняются.

## Критерии приёмки

- [ ] В UI четыре вкладки: MiniMax H3, MiniMax H3 Turbo, Pika H3 Flex, SeedVR2; VACE и LTX нигде не упоминаются в активном коде, bundle и тестах (кроме исторических записей CONTEXT.md).
- [ ] Манифест: SHA-256 и размер LoRA совпадают с `comfy/SHA256SUMS` на закреплённом commit.
- [ ] Граф Pika сверен с живым `/object_info`; builder и тесты прошли; backend/frontend проверки, lint, типизация, build, E2E проходят.
- [ ] Реальный GPU-смоук Ани или мой по её разрешению показывает совместимость LoRA с INT8 H3. Пока этого нет, `execution_enabled: false`, и это написано в UI.
- [ ] Выходной файл находится по метке выходного узла и показывается в UI.
- [ ] Не удалены ни входы, ни результаты, ни токен без отдельной просьбы; коммитов нет.
