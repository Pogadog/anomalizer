#!/bin/bash

cd ..
cp -R python/microservices/web-build/ docker/web-build/
docker build -t anomalizer-arm64 -f docker/Dockerfile-anomalizer .
docker tag anomalizer-arm64 ghcr.io/pogadog/anomalizer-arm64:latest

docker build -t anomalizer-load-test-arm64 -f docker/Dockerfile-load-test .
docker tag anomalizer-load-test-arm64 ghcr.io/pogadog/anomalizer-load-test-arm64:latest
