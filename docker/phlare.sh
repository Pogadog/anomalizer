docker run -d --rm --name phlare --network=phlare-demo -p 4100:4100 --volume "$(pwd)"/phlare-demo.yaml:/etc/phlare/demo.yaml grafana/phlare:latest --config.file=/etc/phlare/demo.yaml
docker run -d --rm --name=grafana -p 3000:3000 -e "GF_FEATURE_TOGGLES_ENABLE=flameGraph" --network=phlare-demo grafana/grafana:main

