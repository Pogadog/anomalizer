# in-memory mini-prometheus TSDB which scrapes endpoints and serves sufficient rest queries to satisfy the anomalizer.
# this will form the core design for a dfinity/IC based metrics system. Prototyping in python for velocity.

# mini-prom serves the following prometheus-engine endpoints that are consumed by anomalizer:
#   /metrics -- current metrics gathered from other prometheus (proxy)
#   /api/v1/metadata -- metadata about metrics
#   /api/v1/query_range?query=<metric>>&start=<start>>&end=<end>&step=<step> -- time-series for metrics.
import json, sys, os
import traceback

import shared

MINI_PROM_PICKLE = '/tmp/mini-prom.pickle'

shared.hook_logging('mini-prom')

from flask import Flask, jsonify, request, make_response
from apiflask import APIFlask
from urllib.parse import unquote
import pandas as pd

import yaml
from prometheus_client import Summary, Gauge, generate_latest
from prometheus_client.parser import text_string_to_metric_families
import requests, time, ast, re, os

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

#server = Flask(__name__)
server = APIFlask(__name__, title='mini-prom')

@server.route('/health')
@server.doc(summary='healthcheck', description='health-check endpoint')
def health():
    return jsonify({'status': 'healthy'})

PORT = int(os.environ.get('MINIPROM_PORT', '9090'))

CONFIG = None

# METRICS_BY_NAME = [(start, end, metrics={name: [time, {sorted(tags): value}]
RESOLUTION = 60
METRICS_BY_NAME = [{'start': time.time(), 'end': time.time() + RESOLUTION, 'metrics': {}}]
METRICS_FAMILY = {}
CURRENT = 0

@server.route('/config')
@server.doc(summary='configuraton information', description='dumps the prometheus.yaml file')
def config():
    return jsonify(CONFIG)

@server.route('/metrics')
@server.doc(summary='prometheus metrics', description='returns prometheus metrics from scraped endpoints')
def metrics():
    latest = generate_latest()
    response = make_response(latest, 200)
    response.mimetype = "text/plain"
    return response

@server.route('/metrics-by-name')
def metrics_by_name():
    return jsonify(METRICS_BY_NAME)


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

@server.route('/api/v1/metadata')
def metadata():
    blob = {'status': 'success', 'data': {}}
    data = blob['data']
    for metric, family in METRICS_FAMILY.items():
        if not metric in data:
            data[metric] = []
        data[metric] += [{'type': family['type'], 'help': family['help'], 'unit': family['unit']}]
    return jsonify(blob)

@server.route('/api/v1/query_range')
def query_range():

    try:
        blob = {
            'status': 'success',
            'data': {
                'resultType': 'matrix',
                'result': []
            }
        }
        query = unquote(request.query_string.decode())
        split = re.split(r'[()]', query)
        if split[0].endswith('rate'):
            metric, rate = split[1].split('[')
            rate = rate.split(']')[0]
            rate, unit = int(rate[0:-1]), rate[-1]
            if unit=='m':
                rate *= 60
        else:
            metric = split[1]
            rate = None

        current = METRICS_BY_NAME[CURRENT]
        family = METRICS_FAMILY.get(metric, {})
        if family.get('type') == 'counter':
            metric += '_total'
        m = current['metrics'].get(metric)
        result = blob['data']['result']
        if m:
            for tag in m:
                values = [[_m[0], str(_m[1])] for _m in m[tag]]
                if values and rate and not (metric.endswith('_count')):
                    dvalues = pd.DataFrame(values, dtype=float)
                    diff = dvalues.diff().clip(lower=0)
                    diff = diff.rolling(rate//60).mean() # TODO: read the time interval from the metric (or scrape).
                    diff = diff.fillna(0)
                    dvalues.iloc[:,1:] = diff.iloc[:,1:]
                    values = dvalues.values.tolist()

                tags = dict([x.split('=') for x in ast.literal_eval(tag)])
                _metric = {'__name__': metric}
                _metric.update(tags)
                result += [{'metric': _metric, 'values': values}]
        return jsonify(blob)
    except Exception as x:
        shared.trace(x, msg='unable to process prometheus query: ' + query)
        return make_response({'status': 'failed', 'message': 'unable to process prometheus query: ' + query}, 500)

PATH = os.environ.get('MICROSERVICES', '')
print('MICROSERVICES=' + PATH)

from google.cloud import storage
def write_to_cloud (upload):
    print('write_to_cloud: ' + upload)
    client = storage.Client()
    bucket = client.get_bucket( 'anomalizer-demo.appspot.com' )
    blob = bucket.blob('/mini-prom/' + os.path.basename(upload))
    blob.upload_from_filename(upload)

def read_from_cloud (name):
    print('read_from_cloud: ' + name)
    client = storage.Client()
    bucket = client.get_bucket( 'anomalizer-demo.appspot.com' )
    blob = bucket.blob('/mini-prom/' + os.path.basename(name))
    if blob.exists():
        file = open(name, 'wb')
        with file:
            blob.download_to_file(file)

def miniprom():
    global CONFIG
    # load prometheus.yaml and start scraping it
    with open(PATH + 'mini-prom.yaml') as file:
        CONFIG = yaml.safe_load(file)

    print(yaml.dump(CONFIG))

    def scraper(job, targets, scrape_interval):
        while True:
            for mtarget in targets:
                for target in mtarget['targets']:
                    try:
                        print('scraping: job=' + job + ', endpoint=http://' + target + '/metrics')
                        text = requests.get('http://' + target + '/metrics').text
                        _time = time.time()
                        for family in text_string_to_metric_families(text):
                            METRICS_FAMILY[family.name] = {'help': family.documentation, 'type': family.type, 'unit': family.unit}
                            for sample in family.samples:
                                name = sample.name
                                #print('  scraping ' + name)
                                value = sample.value
                                labels = sample.labels
                                labels.update({'job': job, 'instance': target})

                                if not name in METRICS_BY_NAME[CURRENT]['metrics']:
                                    METRICS_BY_NAME[CURRENT]['metrics'][name] = {}
                                _labels = str([l + '=' + labels[l] for l in sorted(labels)])
                                if not _labels in METRICS_BY_NAME[CURRENT]['metrics'][name]:
                                    METRICS_BY_NAME[CURRENT]['metrics'][name][_labels] = []

                                METRICS_BY_NAME[CURRENT]['metrics'][name][_labels] += [[_time, value]]
                                # limit this to 180 samples (simulate 3hrs@1s)
                                if len(METRICS_BY_NAME[CURRENT]['metrics'][name][_labels]) >= 180:
                                    #print('pruning data ' + name + '. ' + str(labels))
                                    METRICS_BY_NAME[CURRENT]['metrics'][name][_labels].pop(0)
                                _list = ast.literal_eval(_labels)
                                _tags = dict(item.split('=') for item in _list)

                        #for name in METRICS_BY_NAME[CURRENT]['metrics']:
                        #    print(name + ': ' + str(METRICS_BY_NAME[CURRENT]['metrics'][name]))
                    except Exception as x:
                        # shared.trace(x, msg='scrape exception')
                        # traceback.print_exc()
                        pass
            time.sleep(scrape_interval)

    import threading, pickle
    
    def checkpoint(loop=True):
        if os.environ.get('SAVE_STATE', 'False')=='True':
            while True:
                try:
                    # passivate to the cloud bucket.
                    file = open(MINI_PROM_PICKLE, 'wb')
                    print('passivating to: ' + MINI_PROM_PICKLE)
                    with file:
                        pickle.dump([METRICS_BY_NAME, METRICS_FAMILY], file=file)
                    print('pickled to disk: ' + MINI_PROM_PICKLE)
                    write_to_cloud(MINI_PROM_PICKLE)
                    if not loop:
                        break
                except:
                    # ignore errors writing to cloud bucket.
                    pass
                time.sleep(60)

    # todo: break this down into multiple threads and poll at the appropriate rates.
    def start_scrapers():
        time.sleep(1) # server come up.
        for config in CONFIG['scrape_configs']:
            job = config['job_name']
            targets = config['static_configs']
            scrape_interval = config['scrape_interval']
            num, units = int(scrape_interval[0:-1]), scrape_interval[-1]
            # support reasonable scrape intervals, not the unreasonable d, h, ms versions.
            scrape_interval = num*(60 if units=='m' else 1)
            threading.Thread(target=scraper, args=(job, targets, scrape_interval)).start()
        
        # start thread to checkpoint the state one a per-minugte basis
        threading.Thread(target=checkpoint).start()
        

    if os.environ.get('RESTORE_STATE', 'False')=='True':
        try:
            read_from_cloud(MINI_PROM_PICKLE)
            pass
        except Exception as x:
            #traceback.print_exc()
            print('unable to read from mini-prom.pickle, state will be reset: ' + repr(x))

        try:
            file = open(MINI_PROM_PICKLE, 'rb')
            global METRICS_BY_NAME, METRICS_FAMILY
            with file:
                METRICS_BY_NAME, METRICS_FAMILY = pickle.load(file)
        except Exception as x:
            print('unable to load local mini-prom.pickle: ' + repr(x), sys.stderr)

    def shutdown(signum, frame):
        checkpoint(False)
        exit(0)

    import signal
    signal.signal(signal.SIGINT, shutdown)

    start_scrapers()

    print('port=' + str(PORT))
    try:
        server.run(host='0.0.0.0', port=PORT)
    except:
        traceback.print_exc()

if __name__=='__main__':
    miniprom()
