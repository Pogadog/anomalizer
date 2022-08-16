#!/bin/bash

export PAT=`cat .pat`
echo $PAT | docker login ghcr.io -u simontuffs --password-stdin

cd ..
docker buildx build --push -t ghcr.io/pogadog/anomalizer-multi:latest --platform=linux/arm64,linux/amd64 -f docker/Dockerfile-anomalizer .


