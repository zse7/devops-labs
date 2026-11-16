IMAGE ?= quotes-scraper
.DEFAULT_GOAL := build
export DOCKER_BUILDKIT ?= 1
.NOTPARALLEL:
PYTHON ?= python3
VENV ?= .venv
MULTIPASS ?= multipass
VM_NAME ?= devops-lab3-quotes
URL ?= https://quotes.toscrape.com/scroll
VERIFY_TIMEOUT ?= 180
export URL VERIFY_TIMEOUT
CODE_CHECK_COMPOSE = docker compose -p devops-lab3-codecheck -f test/compose.yaml
# У Python с python.org может отсутствовать CA bundle; используем системный.
ANSIBLE_CA_BUNDLE ?= $(if $(SSL_CERT_FILE),$(SSL_CERT_FILE),$(firstword $(wildcard /etc/ssl/cert.pem /etc/ssl/certs/ca-certificates.crt)))
ANSIBLE_ENV = $(if $(ANSIBLE_CA_BUNDLE),SSL_CERT_FILE="$(ANSIBLE_CA_BUNDLE)",) ANSIBLE_CONFIG="$(CURDIR)/infra/ansible/ansible.cfg" ANSIBLE_HOME="$(CURDIR)/.lab/ansible" XDG_CACHE_HOME="$(CURDIR)/.venv/cache" PATH="$(abspath $(VENV))/bin:$(PATH)"

.PHONY: build run clean ci provision destroy deploy setup vm-up vm-status vm-check
.PHONY: ansible-deps ansible-check bootstrap
.PHONY: harden ssh-check
.PHONY: docker-check docker-install-status lab2-provision
.PHONY: scraper-deps scraper-format scraper-test
.PHONY: app-check http-check app-build-status compose-check lab2-deploy
.PHONY: image-build image-export image-import
.PHONY: verify app-logs http-isolation-check lab2-run
.PHONY: fault-test fault-mode-on fault-mode-off redis-recreate
.PHONY: setup-tools lab2-ci lab2-destroy
.PHONY: lifecycle-test timeout-check backup-results check-all

# инструменты лабораторной устанавливаются только в окружение проекта
setup-tools:
	$(PYTHON) -m venv "$(VENV)"
	"$(VENV)/bin/python" -m pip --disable-pip-version-check --no-cache-dir install -q -r requirements-tools.txt

setup: setup-tools
	"$(VENV)/bin/python" infra/prepare_lab.py

# npm работает в контейнере, Node.js на Mac не требуется.
scraper-deps:
	$(CODE_CHECK_COMPOSE) run --rm --no-deps tools npm install --package-lock-only --ignore-scripts --no-audit --no-fund --save-exact redis@5

scraper-format:
	$(CODE_CHECK_COMPOSE) run --rm --no-deps tools npm exec --yes --package=prettier@3.6.2 -- prettier --write src/scrape.js src/browser.js src/results.js src/healthcheck.js test/worker.test.js test/fixture-worker.js

scraper-test:
	@set -eu; \
	trap '$(CODE_CHECK_COMPOSE) down --volumes' EXIT; \
	$(CODE_CHECK_COMPOSE) up -d --wait redis; \
	$(CODE_CHECK_COMPOSE) run --rm tools sh -c 'npm ci --ignore-scripts --no-audit --no-fund && node --check src/scrape.js && node --check src/browser.js && node --check src/results.js && node --test test/worker.test.js'

# промежуточный этап: VM и доступ, без установки приложения
vm-up: setup
	"$(VENV)/bin/python" infra/prepare_lab.py
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" up

vm-status:
	$(MULTIPASS) version
	$(MULTIPASS) list --format json
	$(MULTIPASS) get "local.$(VM_NAME).cpus"
	$(MULTIPASS) get "local.$(VM_NAME).memory"
	$(MULTIPASS) get "local.$(VM_NAME).disk"

vm-check:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible" lab -m ansible.builtin.ping

ansible-deps: setup-tools
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-galaxy" collection install -r infra/ansible/requirements.yml -p infra/ansible/collections

ansible-check:
	"$(VENV)/bin/yamllint" infra/cloud-init.yaml infra/ansible/playbooks infra/ansible/roles infra/ansible/requirements.yml infra/ansible/inventories/hosts.example.yml deploy/compose.yaml test/compose.yaml .github/workflows/ci.yml
	@set -e; for playbook in infra/ansible/playbooks/*.yml; do \
	$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" -i infra/ansible/inventories/hosts.example.yml --syntax-check "$$playbook"; done
	@$(ANSIBLE_ENV) ANSIBLE_INVENTORY="$(CURDIR)/infra/ansible/inventories/hosts.example.yml" "$(VENV)/bin/ansible-lint" --offline infra/ansible/playbooks

# Сначала создаём admin и проверяем новый вход, затем обновляем inventory.
bootstrap: vm-up ansible-deps
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/bootstrap.yml
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" up

harden: vm-up ansible-deps
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/harden.yml
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" verify-ssh

ssh-check:
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" verify-ssh

# Настройка Ubuntu: доступ, защита SSH и Docker. Приложение разворачивается позже.
provision: vm-up ansible-deps
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/site.yml
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" up

docker-check:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/verify_docker.yml

docker-install-status:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible" lab --become -m ansible.builtin.command -a "du -sh /var/cache/apt/archives/partial"

build:
	docker build -t $(IMAGE) .

# Необязательный перенос локального кеша; deploy умеет собирать образ и на VM.
image-build:
	@set -eu; \
	digest=$$("$(VENV)/bin/python" infra/source_digest.py); \
	docker build --tag "quotes-scraper:$$digest" .

image-export: image-build
	@set -eu; \
	digest=$$("$(VENV)/bin/python" infra/source_digest.py); \
	docker image save --output .lab/scraper-image.tar "quotes-scraper:$$digest"

image-import: image-export
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/import_image.yml

deploy: ansible-deps
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/deploy.yml

app-check:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/verify_app.yml

run:
	@"$(VENV)/bin/python" infra/jobs.py run

verify:
	@"$(VENV)/bin/python" infra/jobs.py verify

fault-test:
	@"$(VENV)/bin/python" infra/fault_test.py

fault-mode-on:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/fault_mode.yml -e fault_mode_enabled=true

fault-mode-off:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/fault_mode.yml -e fault_mode_enabled=false

redis-recreate:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/recreate_redis.yml

backup-results:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/backup_results.yml

timeout-check:
	@"$(VENV)/bin/python" infra/checks.py timeout

lifecycle-test:
	@"$(VENV)/bin/python" infra/checks.py lifecycle

check-all: ci fault-test lifecycle-test

app-logs:
	@"$(VENV)/bin/python" infra/jobs.py logs

http-isolation-check:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/verify_http_isolation.yml

http-check:
	@set -eu; \
	address=$$("$(VENV)/bin/python" -c 'import yaml; print(yaml.safe_load(open("infra/ansible/inventories/lab/hosts.yml"))["all"]["children"]["lab"]["hosts"]["quotes"]["ansible_host"])'); \
	curl --noproxy "$$address" --connect-timeout 5 --max-time 10 -fsS "http://$$address:8080/healthz"

app-build-status:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible" lab --become -m ansible.builtin.command -a "du -sh /var/lib/docker /var/lib/containerd"
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible" lab --become -m ansible.builtin.command -a "ps -C apt-get,dpkg,docker,docker-buildx -o pid,etime,args"
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible" lab --become -m ansible.builtin.shell -a 'for pid in $$(pgrep -x apt-get); do du -sh /proc/$$pid/root/var/cache/apt/archives; ls -lh /proc/$$pid/root/var/cache/apt/archives/partial; done'

compose-check:
	SCRAPER_IMAGE=quotes-scraper:check docker compose -f deploy/compose.yaml config --quiet

# сбор образа, создание инфры и запуск скраппера
# bucket - читает выходное значение bucket_name (quotes-results)
# queue - получает URL очереди и меняет localhost на floci
lab2-deploy: build lab2-provision
	set -eu; \
	bucket=$$(terraform -chdir=infra output -raw bucket_name); \
	queue=$$(terraform -chdir=infra output -raw queue_url); \
	queue=$$(printf '%s' "$$queue" | sed 's#://localhost:#://floci:#'); \
	echo "Starting scraper with bucket=$$bucket queue=$$queue"; \
	BUCKET_NAME="$$bucket" QUEUE_URL="$$queue" \
		docker compose --profile app up -d --no-deps --force-recreate scraper

# отправка сообщения в sqs; скачивание реза из s3
lab2-run:
	set -eu; \
	queue=$$(terraform -chdir=infra output -raw queue_url); \
	bucket=$$(terraform -chdir=infra output -raw bucket_name); \
	docker inspect -f '{{.State.Running}}' quotes-worker 2>/dev/null | grep -qx true || { echo 'Скрапер лабы 2 не запущен. Выполни make lab2-deploy, затем make lab2-run'; exit 1; }; \
	before=$$(docker logs quotes-worker 2>&1 | grep -c 'deleted the message' || true); \
	echo "Sending one job to $$queue"; \
	curl -fsS -X POST "$$queue" \
		-H 'Content-Type: application/x-www-form-urlencoded' \
		--data-urlencode 'Action=SendMessage' \
		--data-urlencode 'Version=2012-11-05' \
		--data-urlencode 'MessageBody=scrape'; \
	echo; \
	i=0; \
	while [ $$i -lt 40 ]; do \
		after=$$(docker logs quotes-worker 2>&1 | grep -c 'deleted the message' || true); \
		if [ "$$after" -gt "$$before" ]; then \
			break; \
		fi; \
		i=$$((i + 1)); \
		sleep 2; \
	done; \
	after=$$(docker logs quotes-worker 2>&1 | grep -c 'deleted the message' || true); \
	[ "$$after" -gt "$$before" ] || { echo 'Скрапер не обработал задание'; docker logs --tail 40 quotes-worker; exit 1; }; \
	curl -fsS "http://localhost:4566/$$bucket/result.json" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert isinstance(data, list) and len(data) > 0; print("quotes:", len(data))'

clean:
	-docker rmi $(IMAGE)
	rm -rf out

ci: ansible-deps
	$(MAKE) ansible-check
	$(MAKE) compose-check
	"$(VENV)/bin/black" --check infra/*.py
	"$(VENV)/bin/python" -m py_compile infra/*.py
	$(MAKE) scraper-test

destroy:
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" destroy

# Проверки и удаление окружения предыдущей лабораторной.
lab2-ci:
	terraform -chdir=infra fmt -check -recursive
	docker compose up -d --wait floci
	terraform -chdir=infra init -input=false
	terraform -chdir=infra validate
	terraform -chdir=infra plan -input=false

# запуск floci, создание S3 и SQS terraform-ом
lab2-provision:
	docker compose up -d --wait floci
	terraform -chdir=infra init -input=false
	terraform -chdir=infra apply -input=false -auto-approve

# удаление ресурсов (содержимое бакета будет удаляться) и остановка контейнеров
lab2-destroy:
	docker compose up -d --wait floci
	terraform -chdir=infra init -input=false
	terraform -chdir=infra destroy -input=false -auto-approve
	docker compose --profile app down
