#!/bin/bash

docker run -d -p 13000:3000 -v `pwd`/pogadog-grafana-plugins/:/var/lib/grafana/plugins -v `pwd`/grafana-storage-7:/var/lib/grafana --name=grafana-anomalizer grafana/grafana:7.0.0
