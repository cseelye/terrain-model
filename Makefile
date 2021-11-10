SHELL := /bin/bash
NAME = terrain-model

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
	container_build/make-tagged

.PHONY: run
run: container
	docker container run --rm -it --volume $(pwd):/work --workdir /work $(NAME)

.PHONY: dev
dev: dev-container
	docker container run --rm -it --volume $(pwd):/work --workdir /work $(NAME).dev

.PHONY: pylint
pylint: dev-container
	docker container run --rm -it --volume $(pwd):/work --workdir /work $(NAME).dev pylint *.py
