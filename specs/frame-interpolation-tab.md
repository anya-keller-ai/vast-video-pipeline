# Вкладка «Повышение FPS» (интерполяция кадров)

Статус: **реализовано** (backend, frontend, тесты); не развёрнуто и не проверено на GPU.

## Цель

Отдельная вкладка мастерской: готовое видео на входе, на выходе MP4 с большей частотой кадров, той же длительностью и исходным звуком.

## Решения Ани

- Множители **×2 и ×3** (24 → 48 или 72 fps). Точные 60 fps из 24 не нужны.
- Модели: **FILM**, **RIFE 4.26**, **RIFE 4.25**.
- Только отдельная вкладка для готового видео (без галочки «после генерации» в H3, Turbo, Pika).
- Правило проекта: модели без коммерческой лицензии не берём.

## Проверенные факты

- Встроенные узлы ComfyUI `FrameInterpolationModelLoader` и `FrameInterpolate` (входы `interp_model`, `images`, `multiplier` 2–16); кадров на выходе `(N−1)×множитель+1`. Кастомные узлы не нужны; на сервере ComfyUI 0.36 они есть.
- Веса `Comfy-Org/frame_interpolation` @ `aa7009be4d` (в папку `models/frame_interpolation/`): `film_net_fp16.safetensors` 68 882 302 байт, `rife_v4.25.safetensors` и `rife_v4.26.safetensors` по 22 674 688 байт, SHA-256 закреплены в манифесте.
- Лицензии: FILM Apache-2.0 (`google-research/frame-interpolation`), RIFE MIT (`hzwer/Practical-RIFE`); карточка репозитория: «mit-and-apache-2.0». Коммерческое использование допустимо.

## Дизайн

1. **Манифест:** три ассета и один комплект `frame_interpolation` (около 114 МБ), скачивание только кнопкой вкладки.
2. **Backend:** `web/backend/frame_interpolation.py` (граф: `LoadVideo` → `GetVideoComponents` → `FrameInterpolationModelLoader` → `FrameInterpolate` → `CreateVideo` (FPS = исходный × множитель, звук из исходника) → `SaveVideo`), режим API `interpolate` с полями `videoId`, `model`, `multiplier`. Исходный FPS берётся из ffprobe.
3. **Frontend:** вкладка «Повышение FPS»: список моделей, множитель ×2 / ×3, загрузка видео, предупреждение о памяти на длинных или больших видео (узел держит все кадры сразу).
4. **Проверки:** тесты backend и frontend, E2E, граф сверяется с `/object_info` живого ComfyUI.

## Ограничения и не проверено на GPU

- Из-за целого множителя при 30 fps получится 60 (×2) и 90 (×3).
- Звук сохраняется целиком; видео на выходе короче на долю секунды (`(m−1)/(m·fps)`).
- Скорость и качество FILM против RIFE на нашей карте не измерены.
