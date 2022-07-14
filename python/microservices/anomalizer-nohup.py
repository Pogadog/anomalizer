import subprocess, time, os
import traceback

print('launching anomalizer-service in no-hup mode')
pid = subprocess.Popen('nohup python3 anomalizer-service.py 2>&1 > nohup.txt', shell=True)
print('pid=' + str(pid))
