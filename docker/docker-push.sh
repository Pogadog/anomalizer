#!/bin/bash

# put a write access token into .pat then run this script.
export PAT=`cat .pat`
echo $PAT | docker login ghcr.io -u simontuffs --password-stdin

# built on mac-m1: anomalizer backend for arm64
docker tag anomalizer-arm64 ghcr.io/pogadog/anomalizer-arm64:latest
docker push ghcr.io/pogadog/anomalizer-arm64:latest

# built on mac-m1: load tester to generate metrics for anomalizer to analyze.
docker tag load-test-arm64 ghcr.io/pogadog/load-test-arm64:latest
docker push ghcr.io/pogadog/load-test-arm64:latest

#docker tag mini-prom ghcr.io/simontuffs/mini-prom:latest
#docker push ghcr.io/simontuffs/mini-prom:latest

