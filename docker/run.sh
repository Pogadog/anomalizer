#!/bin/bash

# run anomalizer, serving port 8056 to localhost, accessing prometheus on localhost:9092
docker run -p 8056:8056 -it -e PROMETHEUS=host.docker.internal:9092 anomalizer