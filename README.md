# devops-labs

Репозиторий лабораторных работ по курсу **DevOps (ПМиФИ)**.

Ветка `feat/ansible-deploy` содержит лабораторную № 3. Доступны `make setup`, `make provision`
(создание Ubuntu VM, настройка SSH и установка Docker), `make vm-status`,
`make ssh-check` и `make docker-check`. `make deploy` разворачивает
Redis + скрапер + Nginx на VM; `make app-check` проверяет их готовность и настройки,
`make http-check` проверяет доступ с Mac к HTTP-порту 8080.
Лабораторные № 1 и № 2 сохранены в ветках `feat/quotes-scrapper` и
`feat/terraform-infra`; их конфигурации и зависимости в этой ветке не используются.
`make` без аргументов показывает краткую справку.

Обработчик использует Redis Streams. `make scraper-test` запускает проверки
очереди, файлов, повторной доставки и падений в отдельных контейнерах Docker
Desktop; браузер заменён тестовыми данными. Node.js на Mac не требуется.
Реальный сбор с quotes.toscrape.com/scroll уже проверен: 100 записей с цитатами.
`make run URL=https://quotes.toscrape.com/scroll` отправляет ровно одно задание с
новым job_id. `make verify` ожидает именно его результат (по умолчанию до 180 с),
проверяет JSON через HTTP, совпадение SHA-256 с файлом, отсутствие tmp и пустой
XPENDING. Последнее задание сохраняется в `.lab/last-job.json`, проверенный JSON —
в `.lab/last-result.json`. Ожидание можно изменить: `make verify VERIFY_TIMEOUT=60`.
`make app-logs` показывает последние 40 строк лога worker. Отдельный
`make http-isolation-check` проверяет HTTP 404 для существующего читаемого tmp и
удаляет созданный проверкой файл.

При медленной первой сборке на VM можно после `make provision` выполнить
`make image-import`, затем `make deploy`: образ соберётся в Docker Desktop с
локальным кешем и будет перенесён в VM через Ansible с проверкой архитектуры.
Обычный `make deploy` также умеет собирать образ непосредственно на VM. Образы
помечаются хешем исходников; неизменный deploy не пересобирает и не перезапускает
контейнеры.

## Лабораторная № 3: требования к компьютеру

- POSIX-окружение: на этой работе проверен macOS Apple Silicon. На Windows
  Make/Ansible нужно запускать из WSL2; клиент можно выбрать через
  `MULTIPASS=multipass.exe`. Windows-вариант в этой сессии не проверялся.
- Multipass (проверен 1.17.1), Python 3.14, Make, SSH/ssh-keygen и curl.
- Ресурсы для Ubuntu 24.04 VM: 2 CPU, 4 ГиБ RAM, диск 20 ГиБ. Дополнительное
  место требуется для образов Docker и архива при необязательном image-import.
- Docker Desktop/Engine на управляющем компьютере нужен для контейнерных
  тестов `make ci` и локального кеша образов. Рабочие сервисы запускаются в VM.
- Python-инструменты устанавливаются в `.venv`; Node.js и Chromium на хост
  устанавливать не нужно. Архитектура VM выбирается Multipass, apt — через dpkg;
  amd64/arm64 в конфигурации не фиксируются.

## Архитектура и границы настройки

```text
Mac: Make → Multipass → Ubuntu 24.04 VM
     Make → Ansible по SSH → admin → sudo → Docker Engine

Ubuntu / Docker Compose:
  Redis [internal backend, AOF + named volume] ←→ scraper
                                                  ↓ запись
                              /srv/quotes-scraper/results
                                                  ↑ только чтение
  браузер ← HTTP :8080 ← Nginx [отдельная HTTP-сеть]
```

Cloud-init обеспечивает только первоначальный доступ и Python. Роли Ansible:
`bootstrap`, `ssh_hardening`, `docker_engine`, `scraper`. Новый вход admin и sudo
проверяются до закрытия временного доступа. SSH host key закрепляется в `.lab`,
его проверка включена. Admin не добавляется в группу docker; Docker вызывается
через sudo. Парольный, keyboard-interactive, root и временные SSH-входы закрыты.

В Redis stream называется `jobs`, consumer group — `scrapers`. Сообщение содержит
`job_id` и `url`; результат — `<job_id>.json` с массивом text/author/tags. Worker
один, обрабатывает задания последовательно. JSON пишется во временный файл в том
же каталоге, синхронизируется и атомарно переименовывается; XACK идёт последним.
Pending возвращается через XAUTOCLAIM, готовый валидный JSON переиспользуется.
Это at-least-once доставка с идемпотентным результатом; XACK не удаляет историю
stream. AOF everysec не является обещанием нулевой потери при отключении питания.

Redis не публикует порт и доступен только worker во внутренней сети. Nginx
разрешает выдачу готовых JSON, временные файлы возвращают 404. Секреты, ключи,
реальный inventory и журналы не входят в Git или Docker-контекст.

## Команды лабораторной № 3

```bash
make setup       # локальные инструменты, отдельный SSH-ключ и cloud-init
make provision   # создать/проверить VM, admin, SSH и Docker
make deploy      # собрать отсутствующий образ и запустить три сервиса
make run URL=https://quotes.toscrape.com/scroll
make verify      # проверить конкретное последнее задание, до 180 секунд
make fault-test  # два реальных SIGKILL, pending/recovery, дубликаты и Redis AOF
make ci          # статические проверки и шесть контейнерных тестов без VM
make destroy     # удалить только эту VM, .lab и сгенерированный inventory
```

Повторные provision/deploy должны давать changed=0. Destroy удаляет и данные VM;
он не является резервным копированием. `make lifecycle-test` сначала сохраняет
результаты и доказательства в `out/lab3-checks-<UTC>/`, затем выполняет destroy
дважды, создаёт всё заново и повторяет реальное задание. `make check-all`
последовательно запускает ci, fault-test и lifecycle-test. После пересоздания
прежние HTTP-ссылки относятся к удалённым результатам, их копии остаются в out/.

Дополнительные команды: `vm-status`, `ssh-check`, `docker-check`,
`app-check`, `http-check`, `http-isolation-check`, `timeout-check`, `app-logs`,
`scraper-test`, `ansible-check`, `compose-check`, `image-import`.
`fault-mode-on/off`, `redis-recreate`, `backup-results` — служебные шаги тестов.
Fault-test использует уникальные job_id и одноразовые маркеры с TTL, временно
останавливает автоматический restart worker, а в finally возвращает штатный
режим. Сторонние VM, контейнеры и пользовательские SSH-ключи не удаляются.

GitHub Actions вызывает `make ci` с Python 3.14; CI использует example inventory,
не требует работающей VM и не подключается к ней. Зелёный локальный `make ci`
не означает выполненный GitHub Actions run: его результат проверяется в PR.

## Короткие ответы для защиты

- Cloud-init решает первый вход; повторяемой конфигурацией управляет Ansible.
- Playbook задаёт порядок, role объединяет задачи одной ответственности;
  handlers применяют изменения конфигурации только при изменении файлов.
- SSH сначала проверяется новым входом admin; sshd -t проверяет синтаксис,
  sshd -T — эффективную политику. Проверка host key защищает от подмены сервера.
- Группа docker даёт возможности уровня root, поэтому admin её не получает.
- BLPOP удаляет элемент при получении; Streams сохраняет учёт незавершённого
  сообщения в PEL. После падения новый consumer забирает его через XAUTOCLAIM.
- Rename защищает читателя от половины JSON. ACK до записи мог бы потерять
  результат, а падение после записи до ACK требует безопасного повтора job_id.
- Реальная первоначальная ошибка агента: protected-mode yes без пароля блокирует
  worker из другого контейнера. Это обнаружено по коду Redis и исправлено до
  запуска: Redis помещён в internal-сеть без опубликованного порта. Ещё один
  наблюдавшийся случай — Running VM раньше готовности SSH, исправлен ожиданием.

## Структура проекта

```text
Makefile                         # команды лабораторной и проверок
Dockerfile                       # образ worker с Node.js и Chromium
src/                             # очередь, браузер, JSON и healthcheck
infra/cloud-init.yaml            # только первоначальный доступ
infra/manage_vm.py               # VM, SSH и inventory
infra/ansible/playbooks/          # сценарии подготовки, deploy и проверок
infra/ansible/roles/              # bootstrap, SSH, Docker и приложение
deploy/                          # рабочие Compose, Redis и Nginx
test/                            # отдельный Compose и тесты worker
.github/workflows/ci.yml          # make ci на GitHub
```

В Makefile сначала перечислены основные действия, затем диагностика, затем
служебные цели. Служебные `fault-mode-on/off`, `redis-recreate`, `backup-results`
вызываются тестовыми сценариями; удалять их отдельно от этих сценариев нельзя.
`make setup` уже входит в `make provision` через `vm-up`.

## Если сайт не открывается

`page.goto: Timeout` после получения задания означает, что браузер не смог
завершить обращение к странице. Проверь сеть VM и логи: `make app-logs`.
При необходимости передай доступный из VM прокси через `SCRAPER_PROXY_SERVER`
в окружении запуска `make deploy`. Не используй `localhost` для прокси на Mac:
внутри контейнера это адрес самого контейнера. Для прямого доступа оставь
переменную пустой. Прокси передаётся Chromium, соединение с Redis не меняется.
