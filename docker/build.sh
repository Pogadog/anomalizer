#!/bin/bash

cd ..
cp -R python/microservices/web-build/ docker/web-build/
docker build -t anomalizer-arm64 -f docker/Dockerfile-anomalizer .

docker build -t anomalizer-load-test-arm64 -f docker/Dockerfile-load-test .
