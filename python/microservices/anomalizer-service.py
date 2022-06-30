# Simple deployment for anomalizer.
# TODO: make it detect changes in the source and relaunch.

import subprocess
processes = []
try:
    # https://stackoverflow.com/questions/11585168/launch-an-independent-process-with-python
    processes.append(subprocess.Popen(['python', 'anomalizer-engine.py'], close_fds=True))
    processes.append(subprocess.Popen(['python', 'anomalizer-images.py'], close_fds=True))
    processes.append(subprocess.Popen(['python', 'anomalizer-api.py'], close_fds=True))

    print('anomalizer is running: ' + str(processes))

    for process in processes:
        process.wait()

except:
    for process in processes:
        print('anomalizer is killing: ' + str(process))
        process.kill()
