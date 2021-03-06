# polls prometheus and maintains a live-cache of metrics in a form that can be queried with high bandwidth and low
# latency.
import os, time, sys, threading, traceback, gc, psutil, requests, uuid, json, re, ast, enum, math
import pandas as pd
import numpy as np
from copy import deepcopy

from collections import defaultdict
from flask import jsonify, request, make_response
from apiflask import APIFlask, Schema
from apiflask.fields import Integer, String, Float
from prometheus_client import Histogram, Gauge, generate_latest

from health import Health
import shared
from shared import C_EXCEPTIONS_HANDLED

import warnings
warnings.simplefilter('ignore', np.RankWarning)

shared.hook_logging('engine')

H_PROMETHEUS_CALL = Histogram('anomalizer_prometheus_request_latency', 'request latency for prometheus metrics')
S_POLL_METRICS = shared.S_POLL_METRICS.labels('engine')

import logging
logging.getLogger("werkzeug").disabled = True

app = APIFlask(__name__, title='anomalizer-engine')

PORT = int(os.environ.get('ANOMALIZER_ENGINE_PORT', 8060))

DURATION = 60*60*3
STEP = 60
INVERT = False
INCREASE_THRESH = 0.5
DECREASE_THRESH = -0.25

LIMIT = float(os.environ.get('LIMIT', shared.LIMITS[-2]))
FILTER = ''
INVERT = False

PROMETHEUS = os.environ.get('PROMETHEUS', 'localhost:9090')
PROMETHEUS = 'http://' + PROMETHEUS

META = PROMETHEUS + '/api/v1/metadata'
LABELS = PROMETHEUS + '/api/v1/labels'

ID_MAP = {}
METRIC_MAP = {}
DATAFRAMES = {}
SCATTERGRAMS = {}
STATS = {}
STATUS = {}
LABELS = {}
QUERIES = {}
FEATURES = defaultdict(dict)
CARDINALITY = {}
# METRICS = {}
POLL_TIME = 0
METRIC_TYPES = {}
METRICS = {}

PROMETHEUS_HEALTHY = False
INTERNAL_FAILURE = False

PROMETHEUS_TIMEOUT=1

START_TIME = time.time()

class Status(str, enum.Enum):
    NORMAL = 'normal'
    WARNING = 'warning'
    CRITICAL = 'critical'

@app.route('/')
def root():
    return jsonify({'status': 'ok'})

@app.route('/health')
def health():
    healthy = PROMETHEUS_HEALTHY
    return jsonify({'status': Health.HEALTHY if healthy else Health.UNHEALTHY,
        'prometheus': Health.UP if PROMETHEUS_HEALTHY else Health.DOWN,
        'anomalizer-engine': Health.UP if not INTERNAL_FAILURE else Health.DOWN
    })

@app.route('/features')
def features():
    return jsonify(FEATURES)


@app.route('/dataframes')
def dataframes():
    # check headers for documentation call (/docs), if so just return 1 items to avoid overload.
    headers = request.headers
    limit = 1 if 'docs' in headers.environ.get('HTTP_REFERER', '') else -1
    ids = list(DATAFRAMES.keys())[0:limit]
    id_map = dict( ((key, ID_MAP[key]) for key in ids if key in ID_MAP))
    metric_map = dict([(ID_MAP[id], id) for id in ids if id in ID_MAP])
    labels = dict( ((key, LABELS[key]) for key in ids if key in LABELS))
    stats = dict( [key, STATS[key]] for key in ids if key in STATS)
    queries = dict( [key, QUERIES[key]] for key in ids if key in QUERIES)
    features = dict( [key, FEATURES[key]] for key in ids if key in FEATURES)
    cardinalities = dict( [key, CARDINALITY[key]] for key in ids if key in CARDINALITY)
    metric_types = dict( [key, METRIC_TYPES[id_map[key]]] for key in ids if key in ID_MAP)
    status = dict( [key, STATUS[key]] for key in ids if key in STATUS)

    dfs = [[id, df.to_json()] for id, df in DATAFRAMES.copy().items()]
    dfs = dfs[0:limit]
    return jsonify({'dataframes': dfs, 'id_map': id_map, 'metric_map': metric_map, 'labels': labels, 'stats': stats, 'queries': queries, 'features': features, 'cardinalities': cardinalities, 'metric_types': metric_types, 'status': status})

@app.route('/scattergrams')
def scattergrams():
    scattergrams = deepcopy(SCATTERGRAMS)
    for k,v in scattergrams.items():
        for x in v:
            x['xy'] = x['xy'].to_json()
            x['l1'] = x['l1'].to_json()
            x['l2'] = x['l2'].to_json()

    return jsonify(scattergrams)

@app.route('/dataframes/<ids>')
def dataframes_ids(ids):
    ids = ids.split(',')
    result = []

    id_map = dict( ((key, ID_MAP[key]) for key in ids if key in ID_MAP))
    metric_map = dict([(ID_MAP[id], id) for id in ids if id in ID_MAP])
    labels = dict( ((key, LABELS[key]) for key in ids if key in LABELS))
    stats = dict( [key, STATS[key]] for key in ids if key in STATS)
    queries = dict( [key, QUERIES[key]] for key in ids if key in QUERIES)
    features = dict( [key, FEATURES[key]] for key in ids if key in FEATURES)
    cardinalities = dict( [key, CARDINALITY[key]] for key in ids if key in CARDINALITY)
    metric_types = dict( [key, METRIC_TYPES[id_map[key]]] for key in ids if key in ID_MAP)
    status = dict( [key, STATUS[key]] for key in ids if key in STATUS)

    dfs = dict( ((key, DATAFRAMES[key].to_json()) for key in ids if key in DATAFRAMES))
    return jsonify({'dataframes': dfs, 'id_map': id_map, 'metric_map': metric_map, 'labels': labels, 'stats': stats, 'queries': queries, 'features': features, 'cardinalities': cardinalities, 'metric_types': metric_types, 'status': status})

@app.route('/ids')
def ids():
    return jsonify(list(ID_MAP.keys()))

@app.route('/metric_map')
def metric_map():
    return jsonify(METRIC_MAP)

@app.route('/id_map')
def id_map():
    return jsonify(ID_MAP)

@app.route('/metrics')
def metrics():
    # add in our metrics.
    lines = ''
    latest = generate_latest()
    lines += latest.decode()
    response = make_response(lines, 200)
    response.mimetype = "text/plain"
    return response

@app.route('/server-metrics')
def server_metrics():
    sm = {'uptime': int(time.time()-START_TIME), 'poll-time': POLL_TIME, 'metric-count': len(METRICS), 'metrics-processed': METRICS_PROCESSED, 'metrics-available': METRICS_AVAILABLE, 'metrics-dropped': METRICS_DROPPED, 'metrics-total-ts': METRICS_TOTAL_TS}
    #print(sm)
    return jsonify(sm)

@app.route('/filter', methods=['GET', 'POST'])
def filter_metrics():
    body = request.json
    if body:
        global FILTER, INVERT, LIMIT
        FILTER = body.get('query', '')
        INVERT = body.get('invert', False)
        # TODO: reflect back to UI, until then, fix at [-1].
        LIMIT = float(body.get('limit', LIMIT))
    result = {'status': 'success', 'query': FILTER, 'invert': INVERT, 'limit': LIMIT}
    print(result)
    return jsonify(result)

def cleanup(id, metric):
    try:
        #print('cleanup: id=' + id + ', metric=' + metric)
        ID_MAP.pop(id, None)
        METRIC_MAP.pop(metric, None)
        DATAFRAMES.pop(id, None)
    except Exception as x:
        traceback.print_exc()

def poller():
    print('poller starting...')
    while True:
        refresh_metrics()
        start = time.time()
        try:
            poll_metrics()
        except Exception as x:
            traceback.print_exc()
            C_EXCEPTIONS_HANDLED.labels(x.__class__.__name__).inc()

        finally:
            global POLL_TIME
            POLL_TIME = time.time() - start
        time.sleep(1)

def _metadata():
    try:
        meta = requests.get(META, timeout=PROMETHEUS_TIMEOUT).json()['data']
        for m in list(meta.keys())[:]:
            METRIC_TYPES[m] = meta[m][0]['type'] # todo: handle multi-variable
            #if not m in METRIC_MAP:
            #    del meta[m]
        #print('METRIC_TYPES=' + str(METRIC_TYPES))
        return {'status': 'success', 'data': meta}
    except Exception as x:
        print('unable to contact prometheus at: ' + META, file=sys.stderr)
        return {'status': 'failure'}

def refresh_metrics():
    try:
        global METRICS
        _metadata()
        print('fetching ' + META)
        result = requests.get(META)
        _json = result.json()
        METRICS = _json['data']
        print('#METRICS=' + str(len(METRICS)) + ', #DATAFRAMES=' + str(len(DATAFRAMES)))
        # synthetic metrics for histogram and summary
        synth = {}
        for k, metric in METRICS.items():
            type = metric[0]['type']
            if type=='summary' or type=='histogram':
                synth.update({k + '_count': [{'type': 'counter'}]})
                METRIC_TYPES[k + '_count'] = 'counter'
        METRICS.update(synth)
        #print('METRICS=' + str(METRICS))
    except Exception as x:
        print('error refreshing metrics: ' + str(x), file=sys.stderr)
        time.sleep(5)


def get_prometheus(metric, _rate, type, step):
    global PROMETHEUS_HEALTHY
    #print('get_prometheus: ' + metric)
    try:
        labels = []
        values = []
        now = time.time()
        start = now-DURATION
        rate, agg = '', ''
        if _rate or type == 'counter':
            rate = 'rate'
            agg = '[5m]'

        PROM = PROMETHEUS + '/api/v1/query_range?query=' + rate + '(' + metric + agg + ')&start=' + str(start) + '&end=' + str(now) + '&step=' + str(step)

        with H_PROMETHEUS_CALL.time():
            result = requests.get(PROM, timeout=PROMETHEUS_TIMEOUT) # Should never timeout, if it does, bail.

        PROMETHEUS_HEALTHY = True

        _json = result.json()['data']['result']
        for i, j in enumerate(_json):
            if type=='histogram':
                label = j['metric'].get('le')
                if not label:
                    label = str(j['metric'])
                labels.append(label)
            elif type=='summary':
                label = j['metric'].get('quantile')
                if not label:
                    label = str(j['metric'])
                labels.append(label)
            else:
                if '__name__' in j['metric']:
                    del j['metric']['__name__']
                label = str(j['metric'])
                labels.append(label)
            lvalues = list(map(lambda x: float(x[1]), j['values']))
            values.append(lvalues)
        query = PROM.replace('/api/v1/query_range?query=', '/graph?g0.expr=')
        return labels, values, query
    except:
        PROMETHEUS_HEALTHY = False
        raise

def hockey_stick(metric, dxi, dyi, N=5):

    dxy = pd.concat([dxi, dyi], axis=1)
    dxy.columns=['x', 'y']
    dxy = dxy.sort_values(by='x')

    x = dxy['x']
    y = dxy['y']

    xmin, xmax = min(x), max(x)

    xr = x.max()-x.min()
    yr = y.max()-y.min()

    xx = x.le(xmin+xr*(N-1)//N)

    # fit linear models to the first and second halfs of the x/y data, sorted by x, and then look for the change
    # in gradient to indicate a hockeystick.
    x1 = x[xx]
    y1 = y[xx]
    fit1 = np.polyfit(x1, y1, 1)
    p1 = np.poly1d(fit1)

    xx = x.ge(xmin+xr*(N-1)/N)
    x2 = x[xx]
    y2 = y[xx]
    fit2 = np.polyfit(x2, y2, 1)
    p2 = np.poly1d(fit2)

    #print('metric=' + metric + ', p1=' + str(p1).replace('\n', ' ') + ', p2=' + str(p2).replace('\n', ' '))

    xp1 = np.linspace(min(x1), max(x1), 2)
    yp1 = p1(xp1)
    l1 = pd.DataFrame([xp1, yp1]).T

    xp2 = np.linspace(min(x2), max(x2), 2)
    yp2 = p2(xp2)
    l2 = pd.DataFrame([xp2, yp2]).T

    # return the normalized second and first grafndients
    return p1[1]*xr/yr, p2[1]*xr/yr, l1, l2

def line_chart(metric, id, type=None, _rate=False, thresh=0.0001, filter=''):
    global INTERNAL_FAILURE
    try:
        INTERNAL_FAILURE = False
        #print('.', end='', flush=True)
        if not metric:
            return None, None, None, None

        # add in tags to the match. TODO: align this with the match strings on the UI
        labels, values, query = get_prometheus(metric, _rate, type, 60*60//3)
        cardinality = 'high' if len(labels) > 100 else 'medium' if len(labels) > 10 else 'low'
        match = metric + json.dumps({'tags': labels}) + ',' + type + ',' + json.dumps({'cardinality': cardinality})
        if (re.match('.*(' + filter + ').*', match)==None) != INVERT:
            return None, None, None, None
        if thresh:
            # big step first to filter.
            if not values:
                # try _sum and _count with rate, since python does not do quantiles properly.
                labels, values, query = get_prometheus(metric + '_sum', True, type, STEP)
                if not values:
                    return None, None, None, None

            maxlen = max(map(len, values))
            values = [[0]*(maxlen - len(row))+row for row in values]
            dfp = pd.DataFrame(columns= np.arange(len(labels)).T, data=np.array(values).T)
            std = dfp.std(ddof=0).sum()
            if std < thresh:
                #print('metric; ' + metric + ' is not interesting, std=' + str(std) + '...')
                return None, None, None, None

        labels, values, query = get_prometheus(metric, _rate, type, STEP)
        if not values:
            # try _sum and _count with rate, since python does not do quantiles properly.
            labels, values, query = get_prometheus(metric + '_sum', True, type, STEP)
            labels_count, values_count, _ = get_prometheus(metric + '_count', True, type, STEP)

            average = pd.DataFrame(values)/pd.DataFrame(values_count)
            average = average.fillna(0) # [5m]
            values = average.values.tolist()

            #print('average rate: ' + type + '/' + metric + ': \n' + str(average))

            if not values:
                return None, None, None, None

            # TODO: also produce a load/latency scattergram from _sum and _count.
            if type=='histogram' or type=='summary':
                scat_id = id + '.scatter'
                SCATTERGRAMS[scat_id] = []
                dx = pd.DataFrame(values_count).fillna(0) # per-second
                dy = pd.DataFrame(values).fillna(0)
                maxdy = dy.max().max()
                count = 0
                for i in range(dx.shape[0]):
                    if dy.loc[i].max() > maxdy*0.2 and dy.loc[i].std(ddof=0) > thresh:
                        # top 80% of scattergrams by value, limit 20. TODO: sort.
                        count += 1
                        if count > 20:
                            break
                        #print('generating scattergram for ' + metric + '.' + str(i))
                        dxi = dx.loc[i]
                        dyi = dy.loc[i]
                        index = pd.DataFrame(dx.T.index)
                        dxy = pd.concat([index, dxi, dyi], axis=1)
                        dxy.columns = ['i', 'x', 'y']
                        # remove zero rows.
                        dxy = dxy.loc[(dxy.iloc[:,1:3]!=0).any(1)]

                        dii = dxy.iloc[:,0]
                        dxi = dxy.iloc[:,1]
                        dyi = dxy.iloc[:,2]

                        features = FEATURES[scat_id]
                        features.clear()
                        # for a hockey-stick: require x to at least double over the domain.
                        l1, l2 = pd.DataFrame(), pd.DataFrame()
                        if (dxi.max() > 1.5*dxi.min() and len(dxi) > 20):
                            try:
                                p1, p2, l1, l2 = hockey_stick(metric + '.' + str(i), dxi, dyi)
                                ratio = 0
                                ratio = p2 - p1
                                if math.isnan(ratio):
                                    ratio = 0
                                # normalize hockeysticks to range 0-1.
                                features['hockeyratio'] = ratio
                                if ratio > 2:
                                    ratio = min(4, ratio)/4
                                    features.update({'hockeystick': {'increasing': ratio, 'p1': p1, 'p2':p2}})
                                elif ratio < -2:
                                    ratio = max(-4, ratio)/4
                                    features.update({'hockeystick':  {'decreasing': ratio, 'p1': p1, 'p2':p2}})

                            except Exception as x:
                                # traceback.print_exc()
                                print('problem computing hockey-stick: ' + metric + '.' + str(i) + ': ' + repr(x))


                        _mean = dyi.mean()
                        _max = dyi.max()
                        _min = dyi.min()
                        std = dyi.std(ddof=0)
                        rstd = std/_mean if _mean>std else std
                        spike = _max/_mean if _min>0 else 0
                        if spike > 10:
                            features.update({'spike': spike})

                        stats = {'rstd': rstd, 'max': _max, 'rmax': -_max, 'mean': _mean, 'std': std, 'spike': spike}
                        stats = shared.no_nan(stats)

                        status = STATUS.get(id, Status.NORMAL.value)
                        SCATTERGRAMS[scat_id] += [{'xy': dxy, 'stats': stats, 'labels': labels, 'cardinality': cardinality, 'metric': metric, 'l1': l1, 'l2': l2, 'features': features, 'status': status}]

                    else:
                        pass
                        #print('ignoring boring scattergram for ' + metric + ': ' + scat_id + '.' + str(i))


        # right-align mismatch row lengths to make latest time points right.
        maxlen = max(map(len, values))
        values = [[0]*(maxlen - len(row))+row for row in values]

        dfp = pd.DataFrame(columns= np.arange(len(labels)).T, data=np.array(values).T)
        dfp = dfp.fillna(0).copy()

        for i, label in enumerate(labels):
            labels[i] = ast.literal_eval(label)

        '''
        # limit display tags cardinality to 10, and sort them descending on mean.
        if len(dfp.columns) > 10:
            order = dfp.mean().sort_values(ascending=False).index
            dfp = dfp.reindex(order, axis=1).iloc[:,0:10]
            for i, l in enumerate(labels):
                l['tag'] = i
            labels = np.array(labels)[order].tolist()[0:10]
        '''

        DATAFRAMES[id] = dfp
        LABELS[id] = labels
        QUERIES[id] = query
        CARDINALITY[id] = cardinality

        return labels, values, query, dfp
    except Exception as x:
        # traceback.print_exc()
        print('error collecting DATAFRAME: ' + metric + '.' + id + ': ' + str(x), file=sys.stderr)
        C_EXCEPTIONS_HANDLED.labels(x.__class__.__name__).inc()
        INTERNAL_FAILURE = True
        time.sleep(1)
        return None, None, None, None

METRICS_PROCESSED = 0
METRICS_AVAILABLE = 0
METRICS_DROPPED = 0
METRICS_TOTAL_TS = 0

@S_POLL_METRICS.time() #.labels('engine')
def poll_metrics():
    global METRICS_DROPPED, METRICS_PROCESSED, METRICS_AVAILABLE, METRICS_TOTAL_TS
    METRICS_AVAILABLE = METRICS_PROCESSED-METRICS_DROPPED
    METRICS_PROCESSED = 0
    METRICS_DROPPED = 0
    METRICS_TOTAL_TS = 0
    #print('poll_metrics: ' + str(METRICS))
    for metric in METRICS.copy():
        METRICS_PROCESSED += 1
        id = None
        try:
            type = METRIC_TYPES.get(metric, '')
            id = METRIC_MAP.get(metric, str(uuid.uuid4()))
            _rate=type=='counter' or type=='summary'
            labels, values, query, dfp = line_chart(metric, id, type, _rate=type=='counter' or type=='summary', thresh=LIMIT, filter=FILTER)
            if labels and values:
                maxlen = max(map(len, values))
                values = [[0]*(maxlen - len(row))+row for row in values]
                dfp = pd.DataFrame(columns= np.arange(len(labels)).T, data=np.array(values).T)
                dfp = dfp.replace([np.inf, -np.inf, np.nan], 0)
                if labels:
                    METRICS_TOTAL_TS += len(labels)
                    # forward/backward map between metrics and their ids.
                    ID_MAP[id] = metric
                    METRIC_MAP[metric] = id
                    if dfp is None or dfp.shape[0] < 3: # need min 3 points to compute things.
                        continue
                    # features
                    N = len(dfp)
                    data1 = dfp.loc[0:4*N//5]
                    data2 = dfp.loc[4*N//5:]
                    std = dfp.std(ddof=0).sum()
                    mean = dfp.mean().sum()

                    # compute signal/noise ratio using power-s pectrum (split frequency range into low/high ranges,
                    # power-ratio low/high is defined as the SNR).
                    # frequency spectrum (noisy/quiet)
                    dff = dfp.loc[N//5:4*N//5]
                    M = len(dff)
                    fdf = pd.DataFrame(np.fft.fft(dff-dff.mean(), axis=0))
                    psd = abs(pd.DataFrame(fdf))**2
                    low = psd[0:M//8].sum()
                    high = psd[M//8: M//2].sum()
                    snr = 0
                    if high.min():
                        snr = (low/high).fillna(0).sum()

                    features = FEATURES[id]
                    features.clear()
                    if std > LIMIT:
                        mean_shift = (data2.mean()-data1.mean()).max()/mean if mean else 0
                        if mean_shift > INCREASE_THRESH:
                            features.update({'increasing': {'increase': mean_shift}})
                        elif mean_shift < DECREASE_THRESH:
                            features.update({'decreasing': {'decrease': mean_shift}})

                    if not dfp is None:
                        std = dfp.std(ddof=0).abs().sum()
                        _mean = dfp.mean().sum()
                        _max = dfp.max().max()
                        _min = dfp.min().min()
                        rstd = std/_mean if _mean>std else 0
                        spike = _max/_mean if _mean>0 else _max
                        if spike > 10:
                            features.update({'spike': spike})

                        status = Status.NORMAL
                        ''' TODO: assign status based on real anomalies.
                        if rstd > 0.4:
                            status = Status.WARNING
                        elif rstd > 0.7:
                            status = Status.CRITICAL
                        '''
                        cardinality = 'high' if len(labels) >= 10 else 'low'

                        if snr > 0 and rstd < 0.4 and snr < 2: # normal and noisy.
                            features.update({'noisy': {'snr': snr}})

                        DATAFRAMES[id] = dfp

                        stats = {'rstd': rstd, 'max': _max, 'rmax': -_max, 'mean': mean, 'std': std, 'spike': spike, 'snr': snr}
                        stats = shared.no_nan(stats)

                        STATS[id] = stats
                        STATUS[id] = status.value

            else:
                #print('dropping ' + metric)
                METRICS_DROPPED += 1
                cleanup(id, metric)

        except Exception as x:
            traceback.print_exc()

            cleanup(id, metric)
            C_EXCEPTIONS_HANDLED.labels(x.__class__.__name__).inc()

    time.sleep(1)

def startup():
    import threading, gc
    gc.enable()
    thread = threading.Thread(target=poller)
    thread.start()

    thread = threading.Thread(target=resource_monitoring)
    thread.start()

G_METRICS_AVAILABLE = Gauge('anomalizer_num_metrics_available', 'number of metrics available on server')
G_METRICS_PROCESSED = Gauge('anomalizer_num_metrics_processed', 'number of metrics processed')
G_METRICS_DROPPED = Gauge('anomalizer_num_metrics_dropped', 'number of metrics dropped')
G_METRICS_TOTAL_TS = Gauge('anomalizer_num_metrics_total_timeseries', 'number of time-series available on server')
G_METRICS = Gauge('anomalizer_num_metrics', 'number of metrics')
G_POLL_TIME = Gauge('anomalizer_poll_time', 'poll-time (seconds)')

def resource_monitoring():
    while True:
        gc.collect()
        G_METRICS_AVAILABLE.set(METRICS_AVAILABLE)
        G_METRICS_PROCESSED.set(METRICS_PROCESSED)
        G_METRICS_DROPPED.set(METRICS_DROPPED)
        G_METRICS_TOTAL_TS.set(METRICS_TOTAL_TS)
        G_METRICS.set(len(METRICS))
        G_POLL_TIME.set(POLL_TIME)
        time.sleep(30)

if __name__ == '__main__':
    try:
        startup()

        print('PORT=' + str(PORT))
        app.run(host='0.0.0.0', port=PORT, use_reloader=False)
    except Exception as x:
        print('error: ' + str(x))
        exit(1)