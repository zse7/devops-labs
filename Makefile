IMAGE ?= quotes-scraper
export DOCKER_BUILDKIT ?= 1

.PHONY: build run clean ci provision destroy deploy

build:
	docker build -t $(IMAGE) .

deploy: build provision
	bucket=$$(terraform -chdir=infra output -raw bucket_name); \
	queue=$$(terraform -chdir=infra output -raw queue_url | sed 's#://localhost:#://floci:#'); \
	echo "Starting scraper with bucket=$$bucket queue=$$queue"; \
	BUCKET_NAME="$$bucket" QUEUE_URL="$$queue" \
		docker compose --profile app up -d --no-deps --force-recreate scraper

run:
	queue=$$(terraform -chdir=infra output -raw queue_url); \
	bucket=$$(terraform -chdir=infra output -raw bucket_name); \
	docker inspect -f '{{.State.Running}}' quotes-worker | grep -qx true || { echo 'Сначала make deploy'; exit 1; }; \
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

ci:
	terraform -chdir=infra fmt -check -recursive
	docker compose up -d --wait floci
	terraform -chdir=infra init -input=false
	terraform -chdir=infra validate
	terraform -chdir=infra plan -input=false

provision:
	docker compose up -d --wait floci
	terraform -chdir=infra init -input=false
	terraform -chdir=infra apply -input=false -auto-approve

destroy:
	docker compose up -d --wait floci
	terraform -chdir=infra init -input=false
	terraform -chdir=infra destroy -input=false -auto-approve
	docker compose down
