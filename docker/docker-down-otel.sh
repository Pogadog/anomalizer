#!/bin/bash

docker-compose -p anomalizer -f anomalizer-compose-otel.yaml $1 down
