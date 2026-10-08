# devops-labs

Репозиторий лабораторных работ по курсу **DevOps (ПМиФИ)**.

## Лаба 1 — Quotes scraper

Скрапер на [Playwright](https://playwright.dev/): открывает [quotes.toscrape.com/scroll](https://quotes.toscrape.com/scroll) в Chromium, дожидается подгрузки цитат через JS, сохраняет результат в `out/result.json`.

Приложение упаковано в Docker (multistage-сборка от `node:22-bookworm`). Точка входа — `Makefile`.

### Структура

```text
.
├─ Dockerfile
├─ Makefile
├─ package.json / package-lock.json
├─ src/scrape.js
├─ out/                      # результат make run (в git не коммитится)
└─ .github/workflows/ci.yml
```

### Локальное окружение для разработки

1. Установите [Docker Desktop](https://www.docker.com/products/docker-desktop/) и дождитесь статуса *Engine running*.
2. (Опционально) Node.js 22+, если хотите править/гонять скрипт вне Docker.
3. Клонируйте репозиторий и перейдите в каталог проекта.

Локально ставить Playwright/Chromium на хост **не требуется** — браузер ставится внутри образа при `make build`.

### Сборка production-образа и запуск

Все команды — только через Make (ручные `docker …` не нужны):

```bash
make build   # собрать образ quotes-scraper
make run     # запустить скрапер → out/result.json
make clean   # удалить образ и каталог out/
make ci      # build + run (то же, что в GitHub Actions)
```

`make run` монтирует локальную папку `out/` в контейнер, поэтому JSON появляется на вашей машине.
