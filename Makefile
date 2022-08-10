SHELL := /usr/bin/env -S bash -euET -o pipefail -O inherit_errexit
export NAME := terrain-model
export REPO := ghcr.io/cseelye
export CACHE_REPO := ghcr.io/cseelye

.DEFAULT_GOAL: container

# Build the container image
.PHONY: container
container:
	docker image build --tag=$(NAME) --target=runtime .

# Build the dev container image
.PHONY: dev-container
dev-container:
	docker image build --tag=$(NAME).dev --target=dev .


# Following targets are advanced builds using buildx and remote layer caching

# Create and tag cache layers for each dockerfile target
.PHONY: build-cache
build-cache:
	time container_build/make-cache 2>&1 | sed -u 's/^/build-cache | /' | tee build-cache.log

# Empty the layer cache
.PHONY: prune-cache
prune-cache:
	docker buildx prune -f

# Build each target in the dockerfile and tag it as NAME.target
.PHONY: tagged
tagged: export LOAD := 1
tagged: build-cache
	time container_build/make-tagged 2>&1 | sed -u 's/^/tagged | /' | tee build.log

# Build just the usable artifacts - runtime and dev containers
.PHONY: images
images: export LOAD := 1
images: export TARGETS := runtime dev
images: build-cache
	time container_build/make-tagged 2>&1 | sed -u 's/^/images | /' | tee build.log

# Push the already built runtime and build images
.PHONY: push
push: build-cache
	container_build/make-push

.PHONY: env
env:
	export
