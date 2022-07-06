# Simple deployment for anomalizer.
# TODO: make it detect changes in the source and relaunch.

import subprocess, time, os
import shared
processes = []

PATH = os.environ.get('MICROSERVICES', '')
print('PATH=' + PATH)

try:

    # https://stackoverflow.com/questions/11585168/launch-an-independent-process-with-python
    processes.append(subprocess.Popen(['python', PATH + 'mini-prom.py'], close_fds=False))
    time.sleep(2)
    processes.append(subprocess.Popen(['python', PATH + 'load-test.py'], close_fds=False))
    time.sleep(2)
    processes.append(subprocess.Popen(['python', PATH + 'anomalizer-engine.py'], close_fds=False))
    time.sleep(2)
    ENV = os.environ
    ENV['SHARDS'] = str(shared.SHARDS)
    for i in range(0, shared.SHARDS):
        processes.append(subprocess.Popen(['python', PATH + 'anomalizer-images.py'], close_fds=False, env=ENV.update({'SHARD': str(i)})))
    time.sleep(2)
    processes.append(subprocess.Popen(['python', PATH + 'anomalizer-correlator.py'], close_fds=False))
    time.sleep(2)
    processes.append(subprocess.Popen(['python', PATH + 'anomalizer-api.py'], close_fds=False))

    print('anomalizer is running: ' + str(processes))

    for process in processes:
        process.wait()

except:
    for process in processes:
        print('anomalizer is killing: ' + str(process))
        process.kill()
