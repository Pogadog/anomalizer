#!/bin/bash

cd ..
cp -R python/microservices/web-build/ docker/web-build/
docker build -t anomalizer-arm64 -f docker/Dockerfile-anomalizer .
docker tag anomalizer-arm64 ghcr.io/pogadog/anomalizer-arm64:latest

