# Удаление HY-OmniWeaving из видеомастерской

## Контекст

Локальная веб-мастерская `/web` показывает HY-OmniWeaving как одну из пяти вкладок, а `config/models.lock.json` содержит отдельный HY Q5 bundle и его assets. Пользователь просит убрать HY именно из видеомастерской; Vast сейчас недоступен.

## Цель

Удалить HY-OmniWeaving из активной локальной мастерской: вкладки, выбора workflow/model bundle, manifest, связанных проверок и актуальной документации. Не выполнять удалённый деплой.

## Что изменится

1. `config/models.lock.json` — удалить только HY Q5 bundle и принадлежащие только ему lock-listed assets.
2. `web/src/App.tsx` — убрать HY из типов, списка вкладок, bundle selection, workflow preview и HY-специфичных полей.
3. `web/backend/test_config_models.py`, `web/backend/test_server.py` — обновить ожидаемый набор bundle/assets и число API bundles, проверить отсутствие HY.
4. `web/e2e/workbench.spec.ts` и релевантные unit tests — проверять оставшиеся четыре вкладки и отсутствие HY.
5. `README.md`, `web/README.md`, `specs/multi-model-workbench.md` — привести список активных моделей и количество bundles/assets к новому составу.
6. `CONTEXT.md` — записать решение и границы удаления.
7. `specs/remove-hy-omniweaving-workbench.md` — отметить проверяемые результаты после реализации.

## Детали реализации

1. Удалить HY Q5 entry из `bundles` и только те `assets`, которые больше не используются другими bundle. Общие assets сохранить.
2. Удалить из UI только HY-специфичную ветку; оставить MiniMax H3, LTX 2.5, Wan 2.1 VACE и SeedVR2 без изменения их поведения.
3. Обновить тесты так, чтобы manifest/API не объявляли HY, а UI показывал оставшиеся четыре вкладки и не показывал HY.
4. Обновить активную документацию и проверенные количества по итоговому manifest.
5. Не удалять веса с диска, partial downloads, inputs, outputs или пользовательские данные. Не менять unrelated незакоммиченные файлы и не создавать коммит. Изменения остаются локальными, пока Vast недоступен.

## Критерии приёмки

- [x] В model lock нет HY bundle или HY-only assets; общие assets остальных моделей сохранены.
- [x] UI показывает ровно четыре вкладки и не содержит HY-specific workflow/control path.
- [x] Backend/API и unit tests подтверждают отсутствие HY bundle.
- [x] Playwright E2E подтверждает четыре доступные вкладки и отсутствие HY.
- [x] README и спецификация описывают актуальный набор моделей и manifest counts.
- [x] Backend/frontend тесты, линтеры, typecheck, сборка, E2E и `git diff --check` проходят.
- [x] Никакие model files, partials, inputs, outputs или remote resources не удалены.
