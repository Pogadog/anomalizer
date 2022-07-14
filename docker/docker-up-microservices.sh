#!/bin/bash

export PAT=`cat .pat`
echo $PAT | docker login ghcr.io -u simontuffs --password-stdin

docker-compose -p anomalizer -f anomalizer-compose-microservices.yaml up -d $1
