#!/bin/bash

cd ..
docker build -t anomalizer-arm64 -f docker/Dockerfile-anomalizer .

docker build -t load-test-arm64 -f docker/Dockerfile-load-test .