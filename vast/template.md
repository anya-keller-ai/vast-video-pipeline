# Шаблон Vast.ai

Статус: поля подготовлены, шаблон Vast ещё не создан.

## Идентификация

- Имя: `H3 Video Pipeline — RTX 3090/4090`
- Описание: `MiniMax H3/Turbo → FlowDenoise → V-BM3D → SeedVR2 Sharp на одной GPU`
- Видимость: приватный

## Docker

- Image Path: `anyakeller/vast-video-pipeline:<проверенный-тег>`
- Registry: Docker Hub
- Docker Repository Authentication: добавить credentials с минимальным правом чтения приватного репозитория
- Launch mode: SSH
- On-start script:

```bash
/opt/vast/scripts/start.sh
```

Не передавать пароль, токен или access token в это поле.

## Ресурсы

- GPU: 1 × RTX 3090 24 GB или RTX 4090 24 GB
- RAM: минимум 64 GB
- CPU: x86_64
- Диск: пересчитать после публикации по `config/models.lock.json`; ориентир от 150 GB с запасом под FFV1 и результаты
- Сеть: быстрая загрузка Hugging Face

## Доступ

ComfyUI не публикуется на внешнем интерфейсе. Скрипт запускает его на `127.0.0.1:8188`. Доступ с Mac:

```bash
ssh -p <SSH_PORT> root@<HOST> -L 8188:localhost:8188
```

Затем открыть `http://localhost:8188`.

## Проверка перед Rent

- [ ] Docker Hub image существует и доступен по digest.
- [ ] Registry credentials внесены в поле Vast, а не в публичный template.
- [ ] Выбран x86_64 host с одной RTX 3090/4090 и RAM от 64 GB.
- [ ] Размер диска покрывает модели и промежуточные lossless-файлы.
- [ ] Цена/час и стоимость диска проверены.
- [ ] Аренда отдельно согласована.

## После запуска

```bash
/opt/vast/scripts/doctor.sh
hf auth login
/opt/vast/scripts/prepare-models.sh
/opt/vast/scripts/doctor.sh --models --gpu
```

Данные о созданном template, image digest, host и проверенном GPU-прогоне нужно дописать сюда после фактической проверки. До этого template нельзя считать рабочим.
