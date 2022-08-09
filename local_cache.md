# Building locally with buildx and caching

Create a custom buildx builder, with unlimited logging, host networking, and less sandboxing:
```
docker buildx create --use --name custom \
    --driver-opt env.BUILDKIT_STEP_LOG_MAX_SIZE=-1,env.BUILDKIT_STEP_LOG_MAX_SPEED=-1,network=host \
    --buildkitd-flags '--allow-insecure-entitlement network.host --allow-insecure-entitlement security.insecure' \
	--use
```

Launch a local registry on port 50000:
```
docker container run --name local_registry -d -p 50000:5000 registry:2
```

Build the container images using the local registry as a cache:
```
make tagged REPO=local CACHE_REPO=localhost:50000 LOAD=1 PUSH_CACHE=1
```
