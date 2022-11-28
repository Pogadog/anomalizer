# Setup
To avoid this error when runing ./grafana-up.sh

```
ERROR: for docker_grafana_1  Cannot create container for service grafana: error looking up logging plugin loki: plugin "loki" not found
```
make sure to run this command:
```
docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions
```