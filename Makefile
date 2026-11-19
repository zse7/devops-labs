# Лабораторная № 3: Ubuntu VM, Ansible, Redis Streams и Nginx.
.DEFAULT_GOAL := help
.NOTPARALLEL:
export DOCKER_BUILDKIT ?= 1
PYTHON ?= python3
VENV ?= .venv
MULTIPASS ?= multipass
VM_NAME ?= devops-lab3-quotes
URL ?= https://quotes.toscrape.com/scroll
VERIFY_TIMEOUT ?= 180
export URL VERIFY_TIMEOUT
CODE_CHECK_COMPOSE = docker compose -p devops-lab3-codecheck -f test/compose.yaml
# Системные CA для Python с python.org; проверка TLS остаётся включённой.
ANSIBLE_CA_BUNDLE ?= $(if $(SSL_CERT_FILE),$(SSL_CERT_FILE),$(firstword $(wildcard /etc/ssl/cert.pem /etc/ssl/certs/ca-certificates.crt)))
ANSIBLE_ENV = $(if $(ANSIBLE_CA_BUNDLE),SSL_CERT_FILE="$(ANSIBLE_CA_BUNDLE)",) ANSIBLE_CONFIG="$(CURDIR)/infra/ansible/ansible.cfg" ANSIBLE_HOME="$(CURDIR)/.lab/ansible" XDG_CACHE_HOME="$(CURDIR)/.venv/cache" PATH="$(abspath $(VENV))/bin:$(PATH)"

.PHONY: help setup provision deploy run verify fault-test destroy ci
.PHONY: vm-status app-logs app-build-status ssh-check docker-check app-check http-check http-isolation-check timeout-check lifecycle-test check-all image-import
.PHONY: setup-tools vm-up ansible-deps ansible-check compose-check scraper-test fault-mode-on fault-mode-off redis-recreate backup-results

# Основной порядок: setup → provision → deploy → run → verify.

help:
	@printf '%s\n' \
	  'Основные команды лабораторной № 3:' \
	  '  make provision   — создать/настроить Ubuntu, SSH и Docker' \
	  '  make deploy      — развернуть Redis, worker и Nginx' \
	  '  make run URL=... — отправить одно задание' \
	  '  make verify      — проверить результат и показать ссылку' \
	  '  make fault-test  — проверить падения и восстановление' \
	  '  make ci          — статические проверки и контейнерные тесты' \
	  '  make app-logs / vm-status / app-build-status — диагностика' \
	  '  make image-import — необязательный перенос локального образа' \
	  '  make lifecycle-test — backup, удаление и создание VM заново' \
	  '  make destroy    — УДАЛИТЬ учебную VM вместе с её данными'

setup: setup-tools
	"$(VENV)/bin/python" infra/prepare_lab.py

provision: vm-up ansible-deps
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/site.yml
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" up

deploy: ansible-deps
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/deploy.yml

run:
	@"$(VENV)/bin/python" infra/jobs.py run

verify:
	@"$(VENV)/bin/python" infra/jobs.py verify

fault-test:
	@"$(VENV)/bin/python" infra/fault_test.py

destroy:
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" destroy

ci: ansible-deps
	$(MAKE) ansible-check
	$(MAKE) compose-check
	"$(VENV)/bin/black" --check infra/*.py
	"$(VENV)/bin/python" -m py_compile infra/*.py
	$(MAKE) scraper-test

# Просмотр состояния и дополнительные проверки.

vm-status:
	$(MULTIPASS) version
	$(MULTIPASS) list --format json
	$(MULTIPASS) get "local.$(VM_NAME).cpus"
	$(MULTIPASS) get "local.$(VM_NAME).memory"
	$(MULTIPASS) get "local.$(VM_NAME).disk"

app-logs:
	@"$(VENV)/bin/python" infra/jobs.py logs

app-build-status:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible" lab --become -m ansible.builtin.command -a "du -sh /var/lib/docker /var/lib/containerd"
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible" lab --become -m ansible.builtin.command -a "ps -C apt-get,dpkg,docker,docker-buildx -o pid,etime,args"
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible" lab --become -m ansible.builtin.shell -a 'for pid in $$(pgrep -x apt-get); do du -sh /proc/$$pid/root/var/cache/apt/archives; ls -lh /proc/$$pid/root/var/cache/apt/archives/partial; done'

ssh-check:
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" verify-ssh

docker-check:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/verify_docker.yml

app-check:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/verify_app.yml

http-check:
	@set -eu; \
	address=$$("$(VENV)/bin/python" -c 'import yaml; print(yaml.safe_load(open("infra/ansible/inventories/lab/hosts.yml"))["all"]["children"]["lab"]["hosts"]["quotes"]["ansible_host"])'); \
	curl --noproxy "$$address" --connect-timeout 5 --max-time 10 -fsS "http://$$address:8080/healthz"

http-isolation-check:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/verify_http_isolation.yml

timeout-check:
	@"$(VENV)/bin/python" infra/checks.py timeout

lifecycle-test:
	@"$(VENV)/bin/python" infra/checks.py lifecycle

check-all: ci fault-test lifecycle-test

image-import:
	@set -eu; \
	digest=$$("$(VENV)/bin/python" infra/source_digest.py); \
	docker build --tag "quotes-scraper:$$digest" .; \
	docker image save --output .lab/scraper-image.tar "quotes-scraper:$$digest"
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/import_image.yml

# Служебные цели: вызываются основными командами и сценариями проверок.

setup-tools:
	$(PYTHON) -m venv "$(VENV)"
	"$(VENV)/bin/python" -m pip --disable-pip-version-check --no-cache-dir install -q -r requirements-tools.txt

vm-up: setup
	"$(VENV)/bin/python" infra/manage_vm.py --multipass "$(MULTIPASS)" --name "$(VM_NAME)" up

ansible-deps: setup-tools
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-galaxy" collection install -r infra/ansible/requirements.yml -p infra/ansible/collections

ansible-check:
	"$(VENV)/bin/yamllint" infra/cloud-init.yaml infra/ansible/playbooks infra/ansible/roles infra/ansible/requirements.yml infra/ansible/inventories/hosts.example.yml deploy/compose.yaml test/compose.yaml .github/workflows/ci.yml
	@set -e; for playbook in infra/ansible/playbooks/*.yml; do \
	$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" -i infra/ansible/inventories/hosts.example.yml --syntax-check "$$playbook"; done
	@$(ANSIBLE_ENV) ANSIBLE_INVENTORY="$(CURDIR)/infra/ansible/inventories/hosts.example.yml" "$(VENV)/bin/ansible-lint" --offline infra/ansible/playbooks

compose-check:
	SCRAPER_IMAGE=quotes-scraper:check docker compose -f deploy/compose.yaml config --quiet

scraper-test:
	@set -eu; \
	trap '$(CODE_CHECK_COMPOSE) down --volumes' EXIT; \
	$(CODE_CHECK_COMPOSE) up -d --wait redis; \
	$(CODE_CHECK_COMPOSE) run --rm tools sh -c 'npm ci --ignore-scripts --no-audit --no-fund && node --check src/scrape.js && node --check src/browser.js && node --check src/results.js && node --test test/worker.test.js'

fault-mode-on:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/fault_mode.yml -e fault_mode_enabled=true

fault-mode-off:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/fault_mode.yml -e fault_mode_enabled=false

redis-recreate:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/recreate_redis.yml

backup-results:
	@$(ANSIBLE_ENV) "$(VENV)/bin/ansible-playbook" infra/ansible/playbooks/backup_results.yml
