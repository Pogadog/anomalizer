import subprocess, os

PATH = os.environ.get('MICROSERVICES', '../microservices/')

CMD = 'opentelemetry-instrument --service_name=anomalizer-engine  python'.split()

process = subprocess.Popen(CMD + [PATH + 'anomalizer-engine.py'], close_fds=False)

process.wait()