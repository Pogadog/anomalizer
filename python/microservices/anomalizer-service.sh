#!/bin/bash -v

export LIMIT=0.1
export TRACEBACK=True
export SAVE_STATE=True
export RESTORE_STATE=True
export MINI_PROM=True
export LOAD_TEST=True

python anomalizer-service.py &> anomalizer-service.log &
echo 'executing tail-F anomalizer-service.log: ^C then pkill python to cleanup'
tail -F anomalizer-service.log