#!/bin/bash

export PAT=`cat .pat`
echo $PAT | docker login ghcr.io -u simontuffs --password-stdin

cd ..
cp -R python/microservices/web-build/ docker/web-build/
docker buildx build --push -t ghcr.io/pogadog/anomalizer-multi:latest --platform=linux/arm64,linux/amd64 -f docker/Dockerfile-anomalizer .
docker pull ghcr.io/pogadog/anomalizer-multi:latest

