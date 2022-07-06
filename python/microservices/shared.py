from prometheus_client import Summary, Histogram, Counter, Gauge, generate_latest
import uuid
from urllib.parse import urlparse

LIMITS = [0.1, 0.01, 0.001, 0.0001, 0.00001, 0.0000001, 0.00000001, 0.000000001, 0]

C_EXCEPTIONS_HANDLED = Counter('anomalizer_num_exceptions_handled', 'number of exeptions handled', ['exception'])
S_TO_IMAGE = Summary('anomalizer_to_image_time', 'time to convert images', ['service'])

S_POLL_METRICS = Summary('anomalizer_poll_metrics', 'time to poll metrics', ['service'])
G_POLL_METRICS = Gauge('anomalizer_poll_metrics_gauge', 'time to poll metrics', ['service'])

SHARDS = 4

import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
sentry_sdk.init(
    dsn="https://1c1774e5abf343b7a38b44ee99bfb3ff@o1309692.ingest.sentry.io/6556078",

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

