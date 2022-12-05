# Simple deployment for anomalizer.
# TODO: make it detect changes in the source and relaunch.

import subprocess, time, os
import traceback, argparse

import shared
processes = []

PATH = os.environ.get('MICROSERVICES', '')
print('PATH=' + PATH)

parser = argparse.ArgumentParser()
parser.add_argument('--mini-prom', action='store_true', help='run an internal mini-prometheus for demo purposes', default=os.environ.get('MINI_PROM', 'False')=='True')
parser.add_argument('--load-test', action='store_true', help='spin up a load test on port 7070 to generate interesting metrics', default=os.environ.get('LOAD_TEST', 'False')=='True')
parser.add_argument('--interpreter', default='python')
args = parser.parse_known_args()[0]
print(args)

PROMETHEUS = os.environ.get('PROMETHEUS', 'http://localhost:9090')

CMD = args.interpreter

try:
    SLEEP = 1 # just enough time for dependent services to come up and avoid errors.

    # https://stackoverflow.com/questions/11585168/launch-an-independent-process-with-python
    ENV = os.environ
    if args.load_test:
        processes.append(subprocess.Popen(CMD.replace('{service}', 'load-test').split() + [PATH + 'load-test.py'], close_fds=False))
    ENV['SHARDS'] = str(os.environ.get('E_SHARDS', 1))
    for i in range(0, int(ENV['SHARDS'])):
        processes.append(subprocess.Popen(CMD.replace('{service}', 'anomalizer-engine-' + str(i)).split() + [PATH + 'anomalizer-engine.py'], close_fds=False, env=ENV.update({'PROMETHEUS': PROMETHEUS, 'E_SHARD': str(i)})))
    time.sleep(SLEEP)
    ENV['SHARDS'] = str(os.environ.get('I_SHARDS', 1))
    for i in range(0, int(ENV['SHARDS'])):
        processes.append(subprocess.Popen(CMD.replace('{service}', 'anomalizer-images-' + str(i)).split() + [PATH + 'anomalizer-images.py'], close_fds=False, env=ENV.update({'I_SHARD': str(i)})))
    ENV['SHARDS'] = str(os.environ.get('C_SHARDS', 1))
    for i in range(0, int(ENV['SHARDS'])):
        processes.append(subprocess.Popen(CMD.replace('{service}', 'anomalizer-correlator-' + str(i)).split() + [PATH + 'anomalizer-correlator.py'], close_fds=False, env=ENV.update({'C_SHARD': str(i)})))
    time.sleep(SLEEP)
    processes.append(subprocess.Popen(CMD.replace('{service}', 'anomalizer-api').split() + [PATH + 'anomalizer-api.py'], close_fds=False))

    if args.mini_prom:
        # bring up mini-prom last so it doesn't scrape endpoints that are not up.
        processes.append(subprocess.Popen(CMD.replace('{service}', 'mini-prom').split() + [PATH + 'mini-prom.py'], close_fds=False))

    print('anomalizer is running: ' + str(processes))

    for process in processes:
        process.wait()

except Exception as x:
    traceback.print_exc()
    for process in processes:
        print('anomalizer is killing: ' + str(process))
        process.kill()
