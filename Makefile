SHELL := /bin/bash
NAME := terrain-model
REPO := ghcr.io/cseelye
CACHE_REPO := cseelye	# docker hub supports cache layers, while GCR does not

.DEFAULT_GOAL: container

# Build the container image
.PHONY: container
container:
	docker image build --tag=$(NAME) --target=prod .

# Build the dev container image
.PHONY: dev-container
dev-container:
	docker image build --tag=$(NAME).dev --target=dev .


# Following targets are advanced builds using buildx and remote layer caching

# Create and tag cache layers for each dockerfile target
.PHONY: build-cache
build-cache:
	export NAME=$(NAME); export REPO=$(REPO); export CACHE_REPO=$(CACHE_REPO); time container_build/make-cache 2>&1 | tee build-cache.log

# Empty the layer cache
.PHONY: prune-cache
prune-cache:
	docker buildx prune -f

# Build each target in the dockerfile and tag it as NAME.target
.PHONY: tagged
tagged:
	export NAME=$(NAME); export REPO=$(REPO); export CACHE_REPO=$(CACHE_REPO); time container_build/make-tagged 2>&1 | tee build.log

# Build the usable artifacts - runtime and dev containers
.PHONY: final
final:
	export NAME=$(NAME); export REPO=$(REPO); export CACHE_REPO=$(CACHE_REPO); time container_build/make-final 2>&1 | tee build.log

# Push the already built runtime and build images
.PHONY: push
push:
	export NAME=$(NAME); export REPO=$(REPO); container_build/make-push
