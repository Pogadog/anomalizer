# polls prometheus and maintains a live-cache of metrics in a form that can be queried with high bandwidth and low
# latency.
import os, time, sys, threading, gc, psutil, requests, uuid, json, re, ast, enum, math
import pandas as pd
import numpy as np
from copy import deepcopy
from collections import Counter

from collections import defaultdict
from flask import jsonify, request, make_response
from apiflask import APIFlask, Schema
from prometheus_flask_exporter import PrometheusMetrics
from apiflask.fields import Integer, String, Float
from prometheus_client import Histogram, Gauge, generate_latest

from health import Health
import shared
from shared import C_EXCEPTIONS_HANDLED

import warnings
warnings.simplefilter('ignore', np.RankWarning)
warnings.simplefilter('ignore', np.linalg.LinAlgError)
warnings.filterwarnings('ignore', category=RuntimeWarning, module='numpy')
warnings.simplefilter(action="ignore", category=pd.errors.PerformanceWarning)

shared.hook_logging('engine')

H_PROMETHEUS_CALL = Histogram('anomalizer_prometheus_request_latency', 'request latency for prometheus metrics')
S_POLL_METRICS = shared.S_POLL_METRICS.labels('engine')

import logging
logging.getLogger("werkzeug").disabled = True

app = APIFlask(__name__, title='anomalizer-engine')
metrics = PrometheusMetrics(app, path='/flask/metrics')

PORT = int(os.environ.get('ANOMALIZER_ENGINE_PORT', '8060'))

DURATION = 60*60*3
STEP = 60
INVERT = False
INCREASE_THRESH = 0.5
DECREASE_THRESH = -0.25

LIMIT = float(os.environ.get('LIMIT', shared.LIMITS[-2]))
FILTER = '_created|_count'
INVERT = True
FILTER2 = ''
INVERT2 = False
SERVER_TAGS = ''

PROMETHEUS = os.environ.get('PROMETHEUS', 'http://localhost:9090')
META = os.environ.get('PROMETHEUS_META', PROMETHEUS + '/api/v1/metadata')

ID_MAP = {}
METRIC_MAP = {}
DATAFRAMES = {}
SCATTERGRAMS = {}
STATS = {}
STATUS = {}
STATUS_COUNTERS = Counter()
STATUS_HOLD = 2
LABELS = {}
QUERIES = {}
FEATURES = defaultdict(dict)
CARDINALITY = {}
# METRICS = {}
POLL_TIME = 0
METRIC_TYPES = {}
METRICS = {}

EXTRA_METRICS = {}
METRIC_TYPE_MAP = {}

PATH = os.environ.get('MICROSERVICES', '')

import yaml
try:
    with open(PATH+'anomalizer-engine.yaml') as file:
        CONFIG = yaml.safe_load(file)

    EXTRA_METRICS = CONFIG.get('extra-metrics', {})
    METRIC_TYPE_MAP = CONFIG.get('metric-type-map', {})
except:
    pass
if not EXTRA_METRICS:
    EXTRA_METRICS = {}
if not METRIC_TYPE_MAP:
    METRIC_TYPE_MAP = {}

# keep track of staleness for LABELS, DATxAFRAMES, QUERIES and SCATTERGRAMS. if a metric id has not been updated for
# "DURATION" seconds, then drop it.
STALEOUT = {}

PROMETHEUS_HEALTHY = False
INTERNAL_FAILURE = False

PROMETHEUS_TIMEOUT=5

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
    limit = 1 if 'docs' in headers.environ.get('HTTP_REFERER', '') else len(DATAFRAMES)
    ids = list(DATAFRAMES.keys())[0:limit]
    id_map = dict( ((key, ID_MAP[key]) for key in ids if key in ID_MAP))
    metric_map = dict([(ID_MAP[id], id) for id in ids if id in ID_MAP])
    labels = dict( ((key, LABELS[key]) for key in ids if key in LABELS))
    stats = dict( [key, STATS[key]] for key in ids if key in STATS)
    queries = dict( [key, QUERIES[key]] for key in ids if key in QUERIES)
    features = dict( [key, FEATURES[key]] for key in ids if key in FEATURES)
    cardinalities = dict( [key, CARDINALITY[key]] for key in ids if key in CARDINALITY)
    metric_types = dict( [key, METRIC_TYPES[id_map[key]]] for key in ids if key in ID_MAP)
    status = dict( [key, STATUS.get(key, Status.NORMAL)] for key in ids)

    dfs = dict((id, df.to_json()) for id, df in list(DATAFRAMES.copy().items())[0:limit])

    return jsonify({'dataframes': dfs, 'id_map': id_map, 'metric_map': metric_map, 'labels': labels, 'stats': stats, 'queries': queries, 'features': features, 'cardinalities': cardinalities, 'metric_types': metric_types, 'status': status})

@app.route('/scattergrams')
def scattergrams():
    scattergrams = deepcopy(SCATTERGRAMS)
    for k,v in scattergrams.copy().items():
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

@app.route('/metric_types')
def metric_types():
    return jsonify(METRIC_TYPES)

@app.route('/metric_map')
def metric_map():
    return jsonify(METRIC_MAP)

@app.route('/id_map')
def id_map():
    return jsonify(ID_MAP)

def to_string(dict):
    result = []
    for k,v in dict.items():
        result += [k + '=' + '"' + v + '"']
    return '{' + ','.join(result) + '}'

@app.route('/prometheus')
def prometheus():
    lines = ''
    # dump the metrics available in our attached prometheus METADATA
    for metric in METRICS.copy():
        id = METRIC_MAP.get(metric)
        if id:
            meta = METRICS[metric][0]
            lines += '#TYPE ' + metric + ' ' + meta.get('type', '') + '\n'
            lines += '#HELP ' + metric + ' ' + meta.get('help', '') + '\n'
            df = DATAFRAMES[id]
            labels = LABELS[id]
            for i, col in enumerate(df.columns):
                lines += metric + to_string(labels[i]) + ' ' + str(df.iloc[-1, col]) + '\n'
        # print the values in the DATAFRAME
    response = make_response(lines, 200)
    response.mimetype = "text/plain"
    return response

@app.route('/metrics')
def metrics():
    lines = ''

    # add in our metrics.
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

# return a list of known tags, and how many times each tag-key appears in a metric.
@app.route('/tags')
def tags():
    tag_summary = defaultdict(int)
    tag_details = defaultdict(Counter)
    metric_summary = defaultdict(Counter)
    metric_details = defaultdict(Counter)

    for id,labels in LABELS.copy().items():
        metric = ID_MAP[id]
        for tags in labels:
            for tag,value in tags.items():
                tag_summary[tag] += 1
                tag_details[tag][value] += 1
                metric_summary[metric][tag] += 1
                metric_details[tag][metric] += 1
    return jsonify({'tag-summary': tag_summary, 'tag-details': tag_details, 'metric-summary': metric_summary, 'metric-details': metric_details})

sem = threading.Semaphore(0)
WAITING = 0

@app.route('/filter', methods=['GET', 'POST'])
def filter_metrics():
    body = request.json
    if body:
        global FILTER, INVERT, LIMIT, FILTER2, INVERT2, WAITING, SERVER_TAGS
        FILTER = body.get('query', FILTER)
        INVERT = body.get('invert', INVERT)
        FILTER2 = body.get('query2', FILTER2)
        INVERT2 = body.get('invert2', INVERT2)
        SERVER_TAGS = body.get('server_tags', SERVER_TAGS).strip()
        # TODO: reflect back to UI, until then, fix at [-1].
        LIMIT = float(body.get('limit', LIMIT))
        # release the pending waiters
        while WAITING:
            sem.release()
            WAITING -= 1 # no neeed to lock, the GIL does that for us.

    result = {'status': 'success', 'query': FILTER, 'invert': INVERT, 'limit': LIMIT, 'query2': FILTER2, 'invert2': INVERT2, 'server_tags': SERVER_TAGS}
    return jsonify(result)

# long-poll for updates to the server-side filter parameters
@app.route('/poll_filter')
def poll_filter():
    # block on a semaphore, return when released by the filter_metrics() update.
    global WAITING
    WAITING += 1
    sem.acquire()
    result = {'status': 'success', 'query': FILTER, 'invert': INVERT, 'limit': LIMIT, 'query2': FILTER2, 'invert2': INVERT2, 'server_tags': SERVER_TAGS}
    return jsonify(result)


def cleanup(id):
    try:
        metric = ID_MAP.get(id)
        #print('cleanup: id=' + id + ', metric=' + metric)
        ID_MAP.pop(id, None)
        METRIC_MAP.pop(metric, None)
        DATAFRAMES.pop(id, None)
        LABELS.pop(id, None)
        SCATTERGRAMS.pop(id + '.scatter', None)
        FEATURES.pop(id, None)
        QUERIES.pop(id, None)
        CARDINALITY.pop(id, None)
    except Exception as x:
        shared.trace(x)

def poller():
    print('poller starting...')
    while True:
        refresh_metrics()
        start = time.time()
        try:
            poll_metrics()
        except Exception as x:
            shared.trace(x)
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
    except Exception as x:
        shared.trace(x)
        print('unable to contact prometheus at: ' + META, file=sys.stderr)

def refresh_metrics():
    try:
        global METRICS, DATAFRAMES
        _metadata()
        print('fetching ' + META)
        result = requests.get(META)
        _json = result.json()
        METRICS = _json['data']
        if EXTRA_METRICS:
            METRICS.update(EXTRA_METRICS)
        for e in EXTRA_METRICS:
            METRIC_TYPES[e] = EXTRA_METRICS[e][0].get('type')
        print('#METRICS=' + str(len(METRICS)) + ', #DATAFRAMES=' + str(len(DATAFRAMES)))
        # synthetic metrics for histogram and summary
        synth = {}
        for k, metric in METRICS.copy().items():
            _type = metric[0]['type']
            # apply the metric type map loaded from anomalizer.yaml
            for key in METRIC_TYPE_MAP:
                if re.match(key, k):
                    _from, _to = METRIC_TYPE_MAP[key].split('=')
                    if re.match(_from, _type):
                        METRIC_TYPES[k] = _to
                        metric[0]['type'] = _to
                        break
            if _type=='summary' or _type=='histogram':
                synth.update({k + '_count': [{'type': 'counter'}]})
                METRIC_TYPES[k + '_count'] = 'counter'
        METRICS.update(synth)
        #print('METRICS=' + str(METRICS))
    except Exception as x:
        shared.trace(x, msg='error refreshing metrics')
        time.sleep(5)


def get_prometheus(metric, _rate, _type, step):
    global PROMETHEUS_HEALTHY
    print('get_prometheus: ' + metric)
    try:
        labels = []
        values = []
        now = time.time()
        start = now-DURATION
        rate, agg = '', ''
        if _rate or _type == 'counter':
            rate = 'rate'
            agg = '[5m]'
        server_tags = ''
        if SERVER_TAGS:
            server_tags = '{' + SERVER_TAGS + '}'

        import urllib.parse
        query = urllib.parse.quote(rate + '(' + metric + server_tags + agg + ')')

        PROM = PROMETHEUS + '/api/v1/query_range?query=' + query + '&start=' + str(start) + '&end=' + str(now) + '&step=' + str(step)

        with H_PROMETHEUS_CALL.time():
            result = requests.get(PROM, timeout=PROMETHEUS_TIMEOUT) # Should never timeout, if it does, bail.

        if result.status_code != 200:
            return None, None, None

        PROMETHEUS_HEALTHY = True

        _json = result.json()['data']['result']
        _lim = DURATION//STEP
        for i, j in enumerate(_json):
            if '__name__' in j['metric']:
                del j['metric']['__name__']
            label = str(j['metric'])
            labels.append(label)
            tvalues = list(map(lambda x: float(x[0]), j['values']))
            lvalues = list(map(lambda x: float(x[1]), j['values']))
            if os.environ.get('MINI_PROM')=='True':
                values.append(lvalues)
            else:
                # fill in any gaps.
                tvalues = [min(_lim, math.ceil((t-start)/step)) for t in tvalues]
                padded = np.zeros(DURATION//STEP+1)
                padded[tvalues] = lvalues
                values.append(padded.tolist())
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

def get_metrics(metric, id, _type=None, _rate=False):
    global INTERNAL_FAILURE
    try:
        INTERNAL_FAILURE = False
        #print('.', end='', flush=True)
        if not metric:
            return None, None, None, None

        # split filter into to parts: {tags}regex
        if FILTER2.startswith('{'):
            _, filter_tags, filter2 = re.split('{|}', FILTER2)
            filter_tags = '{' + filter_tags + '}'
        else:
            filter_tags = ''
            filter2 = FILTER2

        # add in tags to the match. TODO: align this with the match strings on the UI
        # big step first to filter.
        labels, values, query = get_prometheus(metric+filter_tags, _rate, _type, STEP*20)
        cardinality = 'high' if labels and len(labels) > 100 else 'medium' if labels and len(labels) > 10 else 'low'
        match = metric + json.dumps({'tags': labels}) + ',' + _type + ',' + json.dumps({'cardinality': cardinality})
        if FILTER:
            if (re.match('.*(' + FILTER + ').*', match)==None) != INVERT:
                return None, None, None, None
        if filter2:
            if (re.match('.*(' + FILTER2 + ').*', match)==None) != INVERT2:
                return None, None, None, None
        #print('rendering metric=' + match)
        if LIMIT:
            if not values:
                # try _sum and _count with rate, since python does not do quantiles properly.
                labels, values, query = get_prometheus(metric + '_sum' + filter_tags, True, _type, STEP)
                if not values:
                    return None, None, None, None

            maxlen = max(map(len, values))
            values = [[0]*(maxlen - len(row))+row for row in values]
            dfp = pd.DataFrame(columns= np.arange(len(labels)).T, data=np.array(values).T)
            std = dfp.std(ddof=0).sum()
            if std < LIMIT:
                #print('metric; ' + metric + ' is not interesting, std=' + str(std) + '...')
                return None, None, None, None

        labels, values, query = get_prometheus(metric + filter_tags, _rate, _type, STEP)
        if not values:
            # try _sum and _count with rate, since python does not do quantiles properly.
            labels, values, query = get_prometheus(metric + '_sum' + filter_tags, True, _type, STEP)
            labels_count, values_count, _ = get_prometheus(metric + '_count' + filter_tags, True, _type, STEP)

            average = pd.DataFrame(values)
            #average = pd.DataFrame(values)/pd.DataFrame(values_count)
            average = average.fillna(0) # [5m]
            values = average.values.tolist()

            #print('average rate: ' + _type + '/' + metric + ': \n' + str(average))

            if not values:
                return None, None, None, None

            # TODO: also produce a load/latency scattergram from _sum and _count.
            if _type=='histogram' or _type=='summary':
                scat_id = id + '.scatter'
                SCATTERGRAMS[scat_id] = []
                dx = pd.DataFrame(values_count).fillna(0) # per-second
                dy = pd.DataFrame(values).fillna(0)
                maxdy = dy.max().max()
                count = 0
                if dx.shape[0]==dy.shape[0]:
                    for i in range(dx.shape[0]):
                        if dy.loc[i].max() > maxdy*0.2 and dy.loc[i].std(ddof=0) > LIMIT:
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
                            if 'hockeyratio' in features:
                                del features['hockeyratio']
                            if 'hockeystik' in features:
                                del features['hockeystick']
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
                                    shared.trace(x, trace=False)
                                    print('problem computing hockey-stick: ' + metric + '.' + str(i), file=sys.stderr)

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
        STALEOUT[id] = time.time()+DURATION

        return labels, values, query, dfp
    except Exception as x:
        shared.trace(x)
        print('error collecting DATAFRAME: ' + metric + '.' + id, file=sys.stderr)
        C_EXCEPTIONS_HANDLED.labels(x.__class__.__name__).inc()
        INTERNAL_FAILURE = True
        time.sleep(1)
        return None, None, None, None

METRICS_PROCESSED = 0
METRICS_AVAILABLE = 0
METRICS_DROPPED = 0
METRICS_TOTAL_TS = 0
METRIC_TAGS=defaultdict(Counter)
METRIC_SUMMARY=Counter()

N_HIST = 40
class DistributionFitter:
    def __init__(self):
        self.prototypes = pd.DataFrame()
        self.bins = np.arange(0,N_HIST)
        # un-scaled normal: range -6..6 for stdev==1, centered at 0
        x = (self.bins-N_HIST//2)/6
        y = np.exp(-0.5*x**2) # normal-variance
        self.prototypes['gaussian-0'] = pd.DataFrame(y)
        y = np.exp(-x**2) # medium-variance
        self.prototypes['gaussian-1'] = pd.DataFrame(y)
        y = np.exp(-2*x**2) # low-variance
        self.prototypes['gaussian-2'] = pd.DataFrame(y)
        x = self.bins/3
        y = np.exp(-x)
        self.prototypes['right-tailed'] = pd.DataFrame(y)
        self.prototypes['left-tailed'] = pd.DataFrame(np.flipud(y))

        x = (self.bins-N_HIST//2)/6
        y1 = np.exp(-(x-3.33)**4)
        y2 = np.exp(-(x+3.66)**4)
        self.prototypes['bi-modal']  = pd.DataFrame(y1+y2)
        #import matplotlib.pyplot as plt
        #for k,v in self.prototypes.items():
        #    plt.plot(v)
        #plt.show()

    def best_fit(self, metric, df):
        # compute histogram of dataframe, column by column, then do a fit against the prototypes using
        # pearson correlation.
        best = {}
        for k, v in df.iteritems():
            try:
                npdf = v.fillna(0).to_numpy()
                hist = np.histogram(npdf, N_HIST)
                hdf = pd.DataFrame(hist[0])
                corr = self.prototypes.corrwith(hdf.iloc[:,0])
                #print('corr=' + str(corr))
                if corr.max() > 0.7:
                    best[k] = corr.idxmax()
                    if 'gaussian' in corr.idxmax():
                        best[k] = 'gaussian'
                #print('best=' + str(best))
            except Exception as x:
                # don't know, don't care.
                print('error computing histogram: metric=' + metric + ': ' + repr(x), sys.stderr)
        return best


dist = DistributionFitter()

CLASSIFY_DATA = pd.DataFrame()
INDEX = 0
INDEX_CLUSTER_SIZE = 10

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
            _type = METRIC_TYPES.get(metric, '')
            if shared.args.verbose:
                print('poll-metrics: ' + metric + ': ' + _type)
            id = METRIC_MAP.get(metric, str(uuid.uuid4()))
            _rate=_type=='counter' or _type=='summary'
            query = dict(METRICS[metric][0]).get('query', metric)
            labels, values, query, dfp = get_metrics(query, id, _type, _rate=_type=='counter' or _type=='summary')
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
                    if 'increasing' in features:
                        del features['increasing']
                    if 'decreasing' in features:
                        del features['decreasing']
                    mean_shift = (data2.mean()-data1.mean()).max()/mean if mean else 0
                    if mean_shift > INCREASE_THRESH:
                        features.update({'increasing': {'increase': mean_shift}})
                    elif mean_shift < DECREASE_THRESH:
                        features.update({'decreasing': {'decrease': mean_shift}})

                    # best_fit uses a 21 bin histogram for the data, and fits a set of prototypes (normal,
                    # right-skew, left-skew, bi-modal), picking the best fit.
                    # Fit is judged using Pearson correlation between the prototype and the histogram.
                    best = dist.best_fit(metric, dfp)
                    if 'distribution' in features:
                        del features['distribution']
                    if best:
                        #print(metric + ': ' + str(best))
                        features.update({'distribution': best})

                    if not dfp is None:
                        std = dfp.std(ddof=0).abs().sum()
                        _mean = dfp.mean().sum()
                        _max = dfp.max().max()
                        _min = dfp.min().min()
                        rstd = std/_mean if _mean>std else 0
                        spike = _max/_mean if _mean>0 else _max
                        # compute spike of first difference to detect large positive steps (dspike)
                        dmax = dfp.diff().fillna(0).max().max()
                        if spike > 10:
                            features.update({'spike': spike})

                        cardinality = 'high' if len(labels) >= 10 else 'low'

                        if snr > 0 and rstd < 0.4 and snr < 2: # normal and noisy.
                            features.update({'noisy': {'snr': snr}})

                        stats = {'rstd': rstd, 'max': _max, 'rmax': -_max, 'mean': mean, 'mean_shift': mean_shift, 'std': std, 'spike': spike, 'snr': snr}
                        stats = shared.no_nan(stats)

                        STATS[id] = stats

            else:
                #print('dropping ' + metric)
                METRICS_DROPPED += 1
                cleanup(id)

        except Exception as x:
            shared.trace(x)

            cleanup(id)
            C_EXCEPTIONS_HANDLED.labels(x.__class__.__name__).inc()

    # compute after polling all metrics to allow for overall scaling.
    global INDEX
    for id in DATAFRAMES:
        stats = STATS.get(id, {})
        features = FEATURES.get(id, {})
        idi = id + '.' + str(INDEX)
        CLASSIFY_DATA.loc['rstd', idi] = stats.get('rstd', 0)
        CLASSIFY_DATA.loc['increasing', idi] = features.get('increasing', {}).get('increase', 0)
        CLASSIFY_DATA.loc['decreasing', idi] = abs(features.get('decreasing', {}).get('decrease', 0))
        CLASSIFY_DATA.loc['spike', idi] = features.get('spike', 0)

    # normalize
    scale = CLASSIFY_DATA.max(axis=1)
    normalized = CLASSIFY_DATA.div(scale, axis=0).fillna(0)
    #normalized = CLASSIFY_DATA

    for id in DATAFRAMES:
        FEATURES[id]['normalized_features'] = 0
        STATUS[id] = Status.NORMAL
        for index in range(INDEX, INDEX + INDEX_CLUSTER_SIZE):
            idi = id + '.' + str(index)
            FEATURES[id]['normalized_features'] += np.linalg.norm(normalized.get(idi, 0))/INDEX_CLUSTER_SIZE

    if INDEX >= INDEX_CLUSTER_SIZE and len(CLASSIFY_DATA):
        from sklearn.cluster import DBSCAN
        pred = pd.DataFrame(DBSCAN(eps=0.1, min_samples=len(CLASSIFY_DATA)+1).fit_predict(normalized.T)).T
        pred.columns = normalized.columns

        # compute how many clusters each metric is in.
        id_to_cluster = defaultdict(set)
        cluster_to_id = defaultdict(list)
        for index in range(INDEX - INDEX_CLUSTER_SIZE, INDEX):
            for id in DATAFRAMES:
                idi = id + '.' + str(index)
                if idi in pred:
                    id_to_cluster[id].add(int(pred[idi][0]))
                    cluster_to_id[int(pred[idi][0])] += [id]
                else:
                    print('unable to find id=' + idi + ' in classify-pred')

        print('#clusters=' + str(len(cluster_to_id)))
        for id in id_to_cluster:
            FEATURES[id].pop('clusters', None)
            FEATURES[id]['cluster'] = list(id_to_cluster[id])[0]
            if (len(id_to_cluster[id]) > 1 and not -1 in id_to_cluster[id]):
                STATUS_COUNTERS[id] = STATUS_HOLD # hold for warning status.
                print('id ' + id + ' is in multiple clusters: ' + str(id_to_cluster[id]))
                FEATURES[id]['clusters'] = list(id_to_cluster[id])
                if STATUS_COUNTERS[id] == 0:
                    STATUS[id] = Status.NORMAL
                else:
                    if len(id_to_cluster[id]) == 2:
                        STATUS[id] = Status.WARNING
                    else:
                        STATUS[id] = Status.CRITICAL
            if STATUS_COUNTERS[id] > 0:
                STATUS_COUNTERS[id] -= 1

        for id in DATAFRAMES:
            idi = id + '.' + str(INDEX-INDEX_CLUSTER_SIZE)
            if idi in CLASSIFY_DATA:
                CLASSIFY_DATA.drop([idi], axis=1, inplace=True)


    INDEX += 1

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

log_metrics = logging.getLogger('anomalizer-metrics')
log_metrics.propagate = False # do not emit on stdout, just send to loki and the count handler.
log_metrics.addHandler(shared.CountHandler())

if shared.LOKI:
    log_metrics.addHandler(shared.loki_handler)

# push metrics as structured logs
def metrics_as_logs():
    for k, df in DATAFRAMES.copy().items():
        try:
            #last = df.mask(df==0).ffill().iloc[-1] # the last value is often zero. fill forward.
            last = df.iloc[-2]
            metric = ID_MAP.get(k)
            if metric:
                labels = LABELS[k]
                metric_type = METRIC_TYPES[metric]
                for i, v in enumerate(last.values.tolist()):
                    if metric_type != 'gauge' and v == 0:
                        continue # treat zero as a a missing value for logging.
                    blob = {'metric': metric, 'value': v}
                    blob.update(labels[i])
                    # issue these metrics as INFO level to get them into the main
                    # INFO pipeline.
                    log_metrics.log(logging.INFO, msg=json.dumps(blob))
        except Exception as x:
            shared.trace(x)

def resource_monitoring():
    while True:
        gc.collect()
        G_METRICS_AVAILABLE.set(METRICS_AVAILABLE)
        G_METRICS_PROCESSED.set(METRICS_PROCESSED)
        G_METRICS_DROPPED.set(METRICS_DROPPED)
        G_METRICS_TOTAL_TS.set(METRICS_TOTAL_TS)
        G_METRICS.set(len(METRICS))
        G_POLL_TIME.set(POLL_TIME)
        # also emit the metrics we know about as logs.
        metrics_as_logs()
        # cleanup stale metrics
        for id in DATAFRAMES.copy().keys():
            remain = STALEOUT[id] - time.time()
            if remain < 0:
                print('STALEOUT: ' + id)
                cleanup(id)
                STALEOUT.pop(id, None)
        time.sleep(10)


if __name__ == '__main__':
    try:
        startup()

        print('PORT=' + str(PORT))
        app.run(host='0.0.0.0', port=PORT, use_reloader=False, threaded=True)
    except Exception as x:
        print('error: ' + str(x))
        exit(1)