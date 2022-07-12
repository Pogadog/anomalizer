import math

import psutil, os
from prometheus_client import Summary, Histogram, Counter, Gauge, generate_latest
import uuid
from urllib.parse import urlparse

LIMITS = [0.1, 0.01, 0.001, 0.0001, 0.00001, 0.0000001, 0.00000001, 0.000000001, 0]

C_EXCEPTIONS_HANDLED = Counter('anomalizer_num_exceptions_handled', 'number of exeptions handled', ['exception'])
S_TO_IMAGE = Summary('anomalizer_to_image_time', 'time to convert images', ['service'])

S_POLL_METRICS = Summary('anomalizer_poll_metrics', 'time to poll metrics', ['service'])
G_POLL_METRICS = Gauge('anomalizer_poll_metrics_gauge', 'time to poll metrics', ['service'])

SHARDS = 2

SENTRY_KEY = os.environ.get('SENTRY_KEY')

if SENTRY_KEY:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    sentry_sdk.init(
        dsn=SENTRY_KEY,
        #integrations=[
        #    FlaskIntegration(),
        #],

        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for performance monitoring.
        # We recommend adjusting this value in production.
        traces_sample_rate=1.0
    )

def shard(id):
    uid = int(uuid.UUID(id))
    return uid%SHARDS

def shard_endpoint(end, shard):
    url = urlparse(end)
    port = url.port
    port += shard*10000
    return url.scheme + '://' + url.hostname + ':' + str(port) + '/'

G_MEMORY_RSS = Gauge('anomalizer_memory_rss', 'resident memory consumption of program', unit='GB')
G_MEMORY_VMS = Gauge('anomalizer_memory_vms', 'virtual memory consumption of program', unit='GB')
G_THREADS = Gauge('anomalizer_active_threads', 'number of active threads')
G_CPU = Gauge('anomalizer_cpu', 'percent cpu utilizaton')

import gc, threading, time, psutil

def resource_monitoring():
    while True:
        gc.collect()
        info = psutil.Process().memory_info()
        GB = 1024*1024*1024
        G_MEMORY_RSS.set(info.rss/GB)
        G_MEMORY_VMS.set(info.vms/GB)
        G_THREADS.set(threading.active_count())
        G_CPU.set(psutil.cpu_percent())
        time.sleep(30)

threading.Thread(target=resource_monitoring).start()

C_SHARDS = int(os.environ.get('C_SHARDS', '3'))
C_SHARD = int(os.environ.get('C_SHARD', '0'))
N_SHARDS = max(1, C_SHARDS*(C_SHARDS-1)//2)

ARRAY = []
for i in range(C_SHARDS):
    for j in range(i+1, C_SHARDS):
        ARRAY += [[i,j]]

def sharded(i, shard):
    if not ARRAY:
        return True
    return i%C_SHARDS in ARRAY[shard]

def no_nan(dict):
    dict = {k: 0 if math.isnan(v) or math.isinf(v) else v for k, v in dict.items()}
    return dict

def no_nan_vec(vec):
    vec = [0 if (math.isnan(v) or math.isinf(v)) else v for v in vec]
    return vec

# Prometheus needs a simpler timer than Histogram & Summary.  Let's make one.
class Timer:
    def __init__(self, name, help, labels=()):
        self.gauge = Gauge(name, help, labels) # the gauge used to report timers
        self.gauge_per = Gauge(name + '_per', help, labels)
        self._per = 1

    def labels(self, labels):
        self._labels = labels
        return self

    def checkpoint(self, labels):
        self.gauge.labels(*(self._labels+labels)).set(time.time()-self.check)
        self.check = time.time()
        return self

    # supports measuring time/per-per, e.g. per per of work.
    def per(self, per):
        self._per = max(1, per)
        return self

    def __enter__(self):
        self.start = time.time()
        self.check = self.start
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.time() - self.start
        #print(str(self.gauge) + ': time=' + str(elapsed))
        diff = len(self.gauge._labelnames)-len(self._labels)
        labels = self._labels + [None]*diff
        self.gauge.labels(*labels).set(elapsed)
        self.gauge_per.labels(*labels).set(elapsed/self._per)

# hook stdout/stderr into logging with a bridge class.
# https://stackoverflow.com/questions/19425736/how-to-redirect-stdout-and-stderr-to-logger-in-python
import sys, logging
class LoggerWriter:
    def __init__(self, level):
        # self.level is really like using log.debug(message)
        # at least in my case
        self.level = level

    def write(self, message):
        # if statement reduces the amount of newlines that are
        # printed to the logger
        if type(message)==bytes:
            message = message.decode('utf-8')
        if len(message) and message != '\n':
            self.level(message)

    def flush(self):
        # create a flush method so things can be flushed when
        # the system wants to. Not sure if simply 'printing'
        # sys.stderr is the correct way to do it, but it seemed
        # to work properly for me.
        self.level('')

# Hook process stdout & stderr to a logger, based on service name.
def hook_logging(name):
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('anomalizer-' + name)
    sys.stdout = LoggerWriter(log.info)
    print('sys.stdout')
    sys.stderr = LoggerWriter(log.error)
    print('sys.stderr', file=sys.stderr)
