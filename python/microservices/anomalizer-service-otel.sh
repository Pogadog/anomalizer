#!/bin/bash -v

export LIMIT=0.1
export TRACEBACK=True
export SAVE_STATE=True
export RESTORE_STATE=True
export MINI_PROM=True
export LOAD_TEST=True
export OTEL_RESOURCE_ATTRIBUTES='application=anomalizer'

python anomalizer-service.py --interpreter="opentelemetry-instrument --service_name={service} python" &> anomalizer-service.log &
echo 'executing tail-F anomalizer-service.log: ^C then pkill python to cleanup'
tail -F anomalizer-service.log