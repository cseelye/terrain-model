SHELL := /bin/bash
NAME := terrain-model
REPO := ghcr.io/cseelye

.DEFAULT_GOAL: container
.PHONY: container
container:
	docker image build --tag=$(NAME) --target=prod .

.PHONY: dev-container
dev-container:
	docker image build --tag=$(NAME).dev --target=dev .

# Build each target in the dockerfile and tag it as NAME.target
.PHONY: tagged
tagged:
	export NAME=$(NAME); export REPO=$(REPO); time container_build/make-tagged
.PHONY: push
push:
	export NAME=$(NAME); export REPO=$(REPO); container_build/make-push

.PHONY: run
run: container
	docker container run --rm -it --volume $(pwd):/work --workdir /work $(NAME)

.PHONY: dev
dev: dev-container
	docker container run --rm -it --volume $(pwd):/work --workdir /work $(NAME).dev

.PHONY: pylint
pylint: dev-container
	docker container run --rm -it --volume $(pwd):/work --workdir /work $(NAME).dev pylint *.py
