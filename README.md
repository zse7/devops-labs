# devops-labs

Репозиторий лабораторных работ по курсу **DevOps (ПМиФИ)**.

Скрапер на [Playwright](https://playwright.dev/) открывает [quotes.toscrape.com/scroll](https://quotes.toscrape.com/scroll) в Chromium и достаёт цитаты. Задание приходит из очереди SQS, готовый JSON ложится в бакет S3. Очередь и бакет описывает Terraform. Облако локальное: контейнер [Floci](https://floci.io/) `floci/floci:2.2.0` отвечает на те же запросы, что AWS. Настоящий аккаунт AWS не нужен.

Точка входа — `Makefile`. Команд, которых нет в Makefile, на защите нет.

## Структура

```text
.
├─ Dockerfile
├─ Makefile
├─ compose.yaml
├─ package.json / package-lock.json
├─ src/scrape.js
├─ infra/
│  ├─ versions.tf
│  ├─ provider.tf
│  ├─ main.tf
│  ├─ variables.tf
│  ├─ outputs.tf
│  └─ .terraform.lock.hcl
├─ out/                      # в git не коммитится
└─ .github/workflows/ci.yml
```

## Что нужно установить

1. [Docker Desktop](https://www.docker.com/products/docker-desktop/) со статусом *Engine running*.
2. [Terraform](https://developer.hashicorp.com/terraform/install) 1.5 или новее. В CI стоит 1.17.0.
3. `curl` и `python3` — они нужны команде `make run`, чтобы отправить задание и проверить JSON.

Playwright и Chromium на хост ставить не нужно: браузер ставится внутри образа при `make build`.

## Команды

Все команды запускаются из корня репозитория.

```bash
make provision   # поднять Floci и создать бакет с очередью через Terraform
make deploy      # собрать образ и запустить скрапер с параметрами из Terraform
make run         # отправить одно задание в SQS и дождаться JSON в бакете
make destroy     # удалить ресурсы Terraform и остановить окружение
make build       # только собрать образ quotes-scraper
make clean       # удалить образ и каталог out/
make ci          # fmt, validate и plan; то же запускает GitHub Actions
```

`make provision` можно запускать повторно: если бакет и очередь уже совпадают с файлами в `infra/`, Terraform напишет `No changes`.

`make deploy` берёт имя бакета и адрес очереди из выходов Terraform и передаёт их в контейнер переменными окружения. С хоста очередь доступна как `localhost:4566`, из контейнера скрапера — как `floci:4566`.

`make run` имеет смысл после `make deploy`. Скрапер уже ждёт сообщения. Команда кладёт в очередь одну записку, дожидается удаления этой записки и печатает число цитат в `result.json`.

`make destroy` тоже можно запускать повторно. Второй раз удалять уже нечего, команда завершается без ошибки и оставляет окружение выключенным.

Ключи в `infra/provider.tf` — фиктивные `test` / `test` для Floci, не ключи настоящего AWS.
