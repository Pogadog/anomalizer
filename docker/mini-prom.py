# in-memory mini-prometheus TSDB which scrapes endpoints and serves sufficient rest queries to satisfy the anomalizer.
# this will form the core design for a dfinity/IC based metrics system. Prototyping in python for velocity.

# mini-prom serves the following prometheus-engine endpoints that are consumed by anomalizer:
#   /metrics -- current metrics gathered from other prometheus (proxy)
#   /api/v1/metadata -- metadata about metrics
#   /api/v1/query_range?query=<metric>>&start=<start>>&end=<end>&step=<step> -- time-series for metrics.

from flask import Flask, jsonify, request, make_response
import yaml
from prometheus_client import Summary, Gauge, generate_latest
from prometheus_client.parser import text_string_to_metric_families
import requests, time, ast, re, os

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.WARN)

server = Flask(__name__)

@server.route('/health')
def health():
    return jsonify({'status': 'healthy'})

PORT = int(os.environ.get('MINIPROM_PORT', '9091'))

CONFIG = None

# METRICS_BY_NAME = [(start, end, metrics={name: [time, {sorted(tags): value}]
RESOLUTION = 60
METRICS_BY_NAME = [{'start': time.time(), 'end': time.time() + RESOLUTION, 'metrics': {}}]
METRICS_FAMILY = {}
CURRENT = 0

@server.route('/config')
def config():
    return jsonify(CONFIG)

@server.route('/metrics')
def metrics():
    latest = generate_latest()
    response = make_response(latest, 200)
    response.mimetype = "text/plain"
    return response

@server.route('/api/v1/metadata')
def metadata():
    '''
    blob = {
        'status': "success",
        'data': {
            'anomalizer_correlation_time_seconds': [
                {
                    'type': "summary",
                    'help': "time to compute correlation",
                    'unit': ""
                }
            ]
        }
    }
    '''
    blob = {'status': 'success', 'data': {}}
    data = blob['data']
    for metric, family in METRICS_FAMILY.items():
        if not metric in data:
            data[metric] = []
        data[metric] += [{'type': family['type'], 'help': family['help'], 'unit': family['unit']}]
    return jsonify(blob)

@server.route('/api/v1/query_range')
def query_range():

    blob = {
        'status': 'success',
        'data': {
            'resultType': 'matrix',
            'result': []
        }
    }
    query = request.query_string.decode()
    split = re.split(r'[()]', query)
    metric = split[1].split('[')[0]
    current = METRICS_BY_NAME[CURRENT]
    m = current['metrics'].get(metric)
    result = blob['data']['result']
    if m:
        for tag in m:
            values = [[_m[0], str(_m[1])] for _m in m[tag]]
            tags = dict([x.split('=') for x in ast.literal_eval(tag)])
            _metric = {'__name__': metric}
            _metric.update(tags)
            result += [{'metric': _metric, 'values': values}]
    return jsonify(blob)

if __name__=='__main__':
    # load prometheus.yaml and start scraping it
    with open('mini-prom.yaml') as file:
        CONFIG = yaml.safe_load(file)

    print(CONFIG)

    # todo: break this down into multiple threads and poll at the appropriate rates.
    def poller():
        while True:
            time.sleep(1) # server come up.
            for config in CONFIG['scrape_configs']:
                job = config['job_name']
                targets = config['static_configs']
                for mtarget in targets:
                    for target in mtarget['targets']:
                        try:
                            print('scraping: http://' + target + '/metrics')
                            text = requests.get('http://' + target + '/metrics').text
                            _time = time.time()
                            for family in text_string_to_metric_families(text):
                                METRICS_FAMILY[family.name] = {'help': family.documentation, 'type': family.type, 'unit': family.unit}
                                for sample in family.samples:
                                    name = sample.name
                                    value = sample.value
                                    labels = sample.labels
                                    labels.update({'job': job, 'instance': target})

                                    if not name in METRICS_BY_NAME[CURRENT]['metrics']:
                                        METRICS_BY_NAME[CURRENT]['metrics'][name] = {}
                                    _labels = str([l + '=' + labels[l] for l in sorted(labels)])
                                    if not _labels in METRICS_BY_NAME[CURRENT]['metrics'][name]:
                                        METRICS_BY_NAME[CURRENT]['metrics'][name][_labels] = []
                                    METRICS_BY_NAME[CURRENT]['metrics'][name][_labels] += [[_time, value]]
                                    _list = ast.literal_eval(_labels)
                                    _tags = dict(item.split('=') for item in _list)

                            #for name in METRICS_BY_NAME[CURRENT]['metrics']:
                            #    print(name + ': ' + str(METRICS_BY_NAME[CURRENT]['metrics'][name]))
                            time.sleep(10)
                        except Exception as x:
                            print('scrape exception: ' + str(x))


    import threading
    poller = threading.Thread(target=poller)
    poller.start()
    time.sleep(5)
    server.run(host='0.0.0.0', port=PORT)

