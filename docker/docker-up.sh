#!/bin/bash

export PAT=`cat .pat`
echo $PAT | docker login ghcr.io -u simontuffs --password-stdin

docker pull ghcr.io/pogadog/anomalizer-multi:latest
docker-compose -p anomalizer -f anomalizer-compose.yaml up -d $1
