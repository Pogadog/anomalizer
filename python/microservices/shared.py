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