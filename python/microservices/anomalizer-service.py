# Simple deployment for anomalizer.
# TODO: make it detect changes in the source and relaunch.

import subprocess, time, os
import traceback, argparse

import shared
processes = []

PATH = os.environ.get('MICROSERVICES', '')
print('PATH=' + PATH)

parser = argparse.ArgumentParser()
parser.add_argument('--mini-prom', action='store_true', help='run an internal mini-prometheus for demo purposes')
parser.add_argument('--load-test', action='store_true', help='spin up a load test on port 7070 to generate interesting metrics')
args = parser.parse_args()
print(args)

try:

    # https://stackoverflow.com/questions/11585168/launch-an-independent-process-with-python
    if args.load_test:
        processes.append(subprocess.Popen(['python', PATH + 'load-test.py'], close_fds=False))
        time.sleep(2)
    processes.append(subprocess.Popen(['python', PATH + 'anomalizer-engine.py'], close_fds=False))
    time.sleep(2)
    ENV = os.environ
    ENV['SHARDS'] = str(os.environ.get('I_SHARDS', 1))
    for i in range(0, int(ENV['SHARDS'])):
        processes.append(subprocess.Popen(['python', PATH + 'anomalizer-images.py'], close_fds=False, env=ENV.update({'I_SHARD': str(i)})))
    time.sleep(2)
    ENV['SHARDS'] = str(os.environ.get('C_SHARDS', 1))
    for i in range(0, int(ENV['SHARDS'])):
        processes.append(subprocess.Popen(['python', PATH + 'anomalizer-correlator.py'], close_fds=False, env=ENV.update({'C_SHARD': str(i)})))
    time.sleep(2)
    processes.append(subprocess.Popen(['python', PATH + 'anomalizer-api.py'], close_fds=False))
    time.sleep(2)

    if args.mini_prom:
        # bring up mini-prom last so it doesn't scrape endpoints that are not up.
        processes.append(subprocess.Popen(['python', PATH + 'mini-prom.py'], close_fds=False))
        time.sleep(2)

    print('anomalizer is running: ' + str(processes))

    for process in processes:
        process.wait()

except Exception as x:
    traceback.print_exc()
    for process in processes:
        print('anomalizer is killing: ' + str(process))
        process.kill()
