# Шаблон Vast.ai

Статус: приватный template создан в Vast.ai; staged H3 repair workflow проверен на RTX 5090. Для новых инстансов image нужно пересобрать после добавления custom staged-latent node.

## Идентификация

- Имя: `H3 Video Pipeline — RTX 3090/4090`
- Описание: `MiniMax H3/Turbo → FlowDenoise → V-BM3D → SeedVR2 Sharp на одной GPU`
- Видимость template: приватный

## Docker

- Image Path:Tag: `anyakeller/vast-video-pipeline:sha-91fd633`
- Registry: Docker Hub, репозиторий public по явному решению Ани
- Важное: текущий опубликованный image не содержит staged-latent node; перед новым запуском нужен новый build/publish.
- Проверенный manifest digest: `sha256:3213fd0cb4aec53c11fd76fdbd7db76d8f96aeb4b2805c1f8b9b2daf859a1598`
- Docker Repository Authentication: не требуется для public image; credentials в template не внесены
- Launch mode: Interactive shell server, SSH
- On-start script:

```bash
/opt/vast/scripts/start.sh
```

Не передавать пароль, токен или access token в это поле.

## Ресурсы

- GPU: 1 × RTX 3090 24 GB или RTX 4090 24 GB
- RAM: минимум 64 GB
- CPU: x86_64
- Диск template: 150 GB
- Сеть: быстрая загрузка Hugging Face

## Доступ

ComfyUI не публикуется на внешнем интерфейсе. Скрипт запускает его на `127.0.0.1:8188`. Доступ с Mac:

```bash
ssh -p <SSH_PORT> root@<HOST> -L 8188:localhost:8188
```

Затем открыть `http://localhost:8188`.

## Проверка перед Rent

- [x] Docker Hub image существует и manifest digest проверен независимо для тегов `latest` и `sha-91fd633`.
- [x] Public image credentials не внесены в template.
- [ ] Выбран x86_64 host с одной RTX 3090/4090 и RAM от 64 GB.
- [x] Размер диска template — 150 GB; покрытие моделей и промежуточных lossless-файлов нужно подтвердить на host.
- [ ] Цена/час и стоимость диска проверены.
- [ ] Максимальный бюджет и аренда отдельно согласованы.
- [ ] Опубликован image с `SaveH3StagedLatent`/`LoadH3StagedLatent`.

## После запуска

```bash
/opt/vast/scripts/doctor.sh
hf auth login
/opt/vast/scripts/prepare-models.sh
/opt/vast/scripts/doctor.sh --models --gpu
```

Для H3 1344x768 сначала queue sampling workflow, затем отдельный staged decode workflow; stock `SaveLatent` для H3 NestedTensor не использовать. До отдельного согласования максимальной стоимости GPU аренду не запускать.
