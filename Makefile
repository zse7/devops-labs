IMAGE ?= quotes-scraper
export DOCKER_BUILDKIT ?= 1

.PHONY: build run clean ci

build:
	docker build -t $(IMAGE) .

run: build
	mkdir -p out
	docker run --rm -v "$(CURDIR)/out:/app/out" $(IMAGE)

clean:
	-docker rmi $(IMAGE)
	rm -rf out

ci: build run
