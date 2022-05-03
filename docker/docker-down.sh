#!/bin/bash

docker-compose -p anomalizer -f anomalizer-compose.yaml $1 down
