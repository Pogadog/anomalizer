# Simple deployment for anomalizer. Uses fork rather than subprocess to allow the python interpreter code to be
# shared between the forked subprocesses.
# TODO: make it detect changes in the source and relaunch.

import subprocess, time, os
import traceback

processes = []

PATH = os.environ.get('MICROSERVICES', '')
print('PATH=' + PATH)

try:
    n1 = os.fork()
    n2 = os.fork()
    # at this point there are 4 subprocesses running. (n1, n2):
    # (0, 0), child 1
    # (0, !0), child 2
    # (!0, 0), child 3
    # (!0, !0) this process
    print('(n1, n2)=' + str((n1, n2)))
    if n1==0 and n2==0:
        n0 = os.fork()
        if n0 == 0:
            engine = __import__('mini-prom')
            engine.miniprom()
        else:
            engine = __import__('load-test')
    elif n1==0 and n2!=0:
        time.sleep(2)
        engine = __import__('anomalizer-engine')
        engine.startup()
        engine.app.run(port=8060)
    elif n1!=0 and n2==0:
        time.sleep(4)
        engine = __import__('anomalizer-images')
        engine.startup()
        engine.app.run(port=8061)
    else:
        n3 = os.fork()
        if n3==0:
            time.sleep(4)
            engine = __import__('anomalizer-correlator')
            engine.startup()
            engine.app.run(port=8062)
        else:
            time.sleep(6)
            engine = __import__('anomalizer-api')
            engine.app.run(port=8056)
        # also fork a miniprom.
    
    '''

    # https://stackoverflow.com/questions/11585168/launch-an-independent-process-with-python
    processes.append(subprocess.Popen(['python', PATH + 'anomalizer-engine.py'], close_fds=False))
    time.sleep(2)
    processes.append(subprocess.Popen(['python', PATH + 'anomalizer-images.py'], close_fds=False))
    time.sleep(2)
    processes.append(subprocess.Popen(['python', PATH + 'anomalizer-correlator.py'], close_fds=False))
    time.sleep(2)
    processes.append(subprocess.Popen(['python', PATH + 'anomalizer-api.py'], close_fds=False))
    print('anomalizer is running: ' + str(processes))

    for process in processes:
        process.wait()
    '''
    while True:
        time.sleep(1)

except:
    traceback.print_exc()
