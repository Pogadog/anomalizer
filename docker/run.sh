#!/bin/bash

# run anomalizer, serving port 8056 to localhost, accessing mini-prom in the same container.
docker run -p 8056:8056 -it ghcr.io/pogadog/anomalizer-arm64 --mini-prom --load-test
