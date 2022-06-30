# Finds anomalies in prometheus.  Note: this is a proof-of-concept: it is engineered as such and is not designed
# as a scalable system.  It is suitable for analyzing a small prometheus cluster (<1000 metrics, <10,000 MTS)

# The code is built as a single-file monolith to keep things simple: production quality code would break out the
# analytics engine and the REST web-server, and abstract the time-series data-base (prometheus) access so that it could
# be run against alternative TSDB's.

# The key features are gathering metrics, discarding those that are not "interesting" based on standard-deviation,
# creating images and attaching statistical measures to them, and then classifying them as follows:
#  * increasing/decreasing: which metrics are showing trends
#  * hot/warm/cold (based on relative-standard deviation) which metric are most variable
#  * identify noisy metrics based on the FFT of the signal, comparing high and low frequencies.
#  * measuring hockey-stick behavior for scattergram metrics (e.g. count/latency charts) to look for capacity
#    constraints
#  * correlation from one-to-many, and many-to-many to detect relationships between metrics

# The analytics for the metrics are used to sort them to make the most interesting images show up at the top
# of the list.

# The code is multi-threaded, and uses optimistic execution assuming that datastructures will not have thread
# collisions.  Collisions do occur (e.g. elements are removed by one thread while another is using them), and these
# throw exceptions that are handled and ignored, and repaired by a cleanup(id, metric) method.

import gc
import sys
import threading
import traceback
from prometheus_client import Summary, Histogram, Counter, Gauge, generate_latest

from flask import Flask, jsonify, request, make_response

from apiflask import APIFlask, Schema
from apiflask.fields import String, Boolean

'''
from dash import Dash, html, dcc
from dash.dependencies import Input, Output, State
'''
import plotly.express as px
import plotly.graph_objs as go
import plotly.io
import pandas as pd
import requests, json, re, random, ast, copy, enum, socket
import math, time, os
import numpy as np
import uuid
import cProfile, pstats, io
from base64 import b64encode

import psutil

import warnings
warnings.simplefilter(action='ignore', category=pd.errors.PerformanceWarning)

import logging
logging.getLogger("werkzeug").disabled = True

print('Anomalizer(TM) by Pogadog. Copyright (C) 2022. All Rights Reserved.')

TYPES = ['', 'counter', 'gauge', 'histogram', 'summary']
TYPE = TYPES[0]
LIMITS = [0.1, 0.01, 0.001, 0.0001, 0.00001, 0.0000001, 0.00000001, 0.000000001, 0]
LIMIT = LIMITS[-2]
PORT = int(os.environ.get('PORT', 8056))
KEEP_DATAFRAMES = os.environ.get('KEEP_DATAFRAMES', 'true')=='true'   
FILTER = ''
INVERT = False
OLD_TYPE = None

INCREASE_THRESH = 0.5
DECREASE_THRESH = -0.25

LOCALHOST = os.environ.get('PROMETHEUS', 'localhost:9090')
PROMETHEUS = 'http://' + LOCALHOST

print('prometheus is on: ' + PROMETHEUS)

META = PROMETHEUS + '/api/v1/metadata'
LABELS = PROMETHEUS + '/api/v1/labels'

DURATION = 60*60*3
STEP = 60
INVERT = False

IMAGES = {}
DATAFRAMES = {}
FIGURES = {}
ID_MAP = {}
METRIC_MAP = {}
METRICS = {}
METRIC_TYPES = {}
N_CORR = 8

POLL_TIME = 0

# hockey-stick detector. A hockey-stick is declared by splitting the scattergram (x/y) into two
# portions: 0:(N-1)/N and (N-1)/N:N points, performing a linear regression, and comparing the
# gradients relative to the range variable (x). This function computes those gradients.

def hockey_stick(scat, dxi, dyi, N=3):
    dxy = pd.concat([dxi, dyi], axis=1)
    dxy.columns=['x', 'y']
    dxy = dxy.sort_values(by='x')

    x = dxy['x']
    y = dxy['y']

    xr = x.max()-x.min()
    yr = y.max()-y.min()

    NUM = N-1
    DEN = N

    # fit linear models to the first and second halfs of the x/y data, sorted by x, and then look for the change
    # in gradient to indicate a hockeystick.
    x1 = x.iloc[0:len(x)*NUM//DEN]
    y1 = y.iloc[0:len(y)*NUM//DEN]
    fit1 = np.polyfit(x1, y1, 1)
    p1 = np.poly1d(fit1)

    x2 = x[len(x)*NUM//DEN:]
    y2 = y[len(y)*NUM//DEN:]
    fit2 = np.polyfit(x2, y2, 1)
    p2 = np.poly1d(fit2)

    # add lines to an existing scattergram (scat)
    xp1 = np.linspace(min(x1), max(x1), 2)
    line1 = go.Scatter(x=xp1, y=p1(xp1), mode='lines', showlegend=False, line_color='blue')

    xp2 = np.linspace(min(x2), max(x2), 2)
    line2 = go.Scatter(x=xp2, y=p2(xp2), mode='lines', showlegend=False)

    scat.add_trace(line1)
    scat.add_trace(line2)

    # return the ratio of the second and first gradients. > 2 is a hockey-stick feature.
    return p1[1]*xr/yr, p2[1]*xr/yr


class Status(enum.Enum):
    NORMAL = 'normal'
    WARNING = 'warning'
    CRITICAL = 'critical'

#app = Flask(__name__, static_folder=None)
app = APIFlask(__name__)

# roll-your-own flask monitoring to strip id's from metrics.
FLASK_REQUEST_LATENCY = Histogram('anomalizer_flask_request_latency_seconds', 'Flask Request Latency',
                                  ['method', 'root'])
FLASK_REQUEST_COUNT = Counter('anomalizer_flask_request_count', 'Flask Request Count',
                              ['method', 'root', 'http_status'])

def before_request():
    request.start_time = time.time()

def after_request(response):
    request_latency = time.time() - request.start_time
    FLASK_REQUEST_LATENCY.labels(request.method, '/' + request.path.split('/')[1]).observe(request_latency)
    FLASK_REQUEST_COUNT.labels(request.method, '/' + request.path.split('/')[1], response.status_code).inc()
    return response

app.before_request(before_request)
app.after_request(after_request)

'''
dash = Dash(
    __name__,
    server=app,
    url_base_pathname='/'
)
'''

'''
@app.route('/')
def my_dash_app():
    return dash.index()
'''

@app.route("/")
def main():
    return "ok"

def filter_json(json, match, value):
    if not value:
        return
    for k,v in json.copy().items():
        if v[0][match] != value:
            del json[k]

def get_prometheus(metric, _rate, type, step):
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
        result = requests.get(PROM)

    _json = result.json()['data']['result']
    for j in _json:
        if type=='histogram':
            label = j['metric']['le']
            labels.append(float(label))
        elif type=='summary':
            label = j['metric']['quantile']
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

def line_chart(metric, id, type=None, _rate=False, thresh=0.0001, filter=''):
    try:
        #print('.', end='', flush=True)
        if not metric:
            return None, None, None, None

        # add in tags to the match. TODO: align this with the match strings on the UI
        labels, values, query = get_prometheus(metric, _rate, TYPE, 60*60//3)
        cardinality = 'high' if len(labels) > 100 else 'medium' if len(labels) > 10 else 'low'
        match = metric + json.dumps({'tags': labels}) + ',' + type + ',' + json.dumps({'cardinality': cardinality})
        if (re.match('.*(' + filter + ').*', match)==None) != INVERT:
            return None, None, None, None
        if thresh:
            # big step first to filter.
            if not values:
                # try _sum and _count with rate, since python does not do quantiles properly.
                labels, values, query = get_prometheus(metric + '_sum', True, TYPE, STEP)
                if not values:
                    return None, None, None, None

            maxlen = max(map(len, values))
            values = [[0]*(maxlen - len(row))+row for row in values]
            dfp = pd.DataFrame(columns= np.arange(len(labels)).T, data=np.array(values).T)
            std = dfp.std(ddof=0).sum()
            if std < thresh:
                #print('metric; ' + metric + ' is not interesting, std=' + str(std) + '...')
                return None, None, None, None

        labels, values, query = get_prometheus(metric, _rate, TYPE, STEP)
        if not values:
            # try _sum and _count with rate, since python does not do quantiles properly.
            labels, values, query = get_prometheus(metric + '_sum', True, TYPE, STEP)
            labels_count, values_count, _ = get_prometheus(metric + '_count', True, TYPE, STEP)
            if not values:
                return None, None, None, None

            # TODO: also produce a load/latency scattergram from _sum and _count.
            if type=='histogram' or type=='summary':
                scat_id = id + '.scatter'
                FIGURES[scat_id] = []
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
                        # remove zero rows.
                        dxy = dxy.loc[(dxy.iloc[:,1:3]!=0).any(1)]

                        dii = dxy.iloc[:,0]
                        dxi = dxy.iloc[:,1]
                        dyi = dxy.iloc[:,2]
                        fig = px.scatter(x=dxi, y=dyi, title=metric + '.' + str(i), labels={'x':'rate(/sec)', 'y':'value'}, color=dii)
                        fig.update_layout(template=None, height=400, width=400, autosize=False, font={'size': 11}, title={'x': 0.05, 'xanchor': 'left'})
                        fig.update_xaxes(showgrid=False)
                        fig.update_yaxes(showgrid=False)
                        fig.update_layout(showlegend=True)
                        features = {}
                        # for a hockey-stick: require x to at least double over the domain.
                        if (dxi.max() > 2*dxi.min()):
                            try:
                                p1, p2 = hockey_stick(fig, dxi, dyi)
                                ratio = p2-p1
                                if math.isnan(ratio):
                                    ratio = 0
                                # normalize hockeysticks to range 0-1.
                                features['hockeyratio'] = ratio
                                if ratio > 2:
                                    ratio = min(4, ratio)/4
                                    features.update({'hockeystick': {'increasing': ratio}})
                                elif ratio < -2:
                                    ratio = max(-4, ratio)/4
                                    features.update({'hockeystick':  {'decreasing': ratio}})

                            except Exception as x:
                                print('problem computing hockey-stick: ' + metric + '.' + str(i) + ': ' + str(x))

                        mean = dyi.mean()
                        _max = dyi.max()
                        _min = dyi.min()
                        std = dyi.std(ddof=0)
                        rstd = std/mean if mean>std else std
                        spike = _max/mean if mean>0 else _max

                        stats = {'rstd': rstd, 'max': _max, 'rmax': -_max, 'mean': mean, 'std': std, 'spike': spike}
                        FIGURES[scat_id] += [(fig, features, stats)]

                    else:
                        FIGURES[scat_id] += [(None, {}, {})]
                        #print('ignoring boring scattergram for ' + metric + ': ' + scat_id + '.' + str(i))


        # right-align mismatch row lengths to make latest time points right.
        maxlen = max(map(len, values))
        values = [[0]*(maxlen - len(row))+row for row in values]

        dfp = pd.DataFrame(columns= np.arange(len(labels)).T, data=np.array(values).T)
        dfp = dfp.fillna(0).copy()

        for i, label in enumerate(labels):
            labels[i] = ast.literal_eval(label)

        # limit display tags cardinality to 10, and sort them descending on mean.
        if len(dfp.columns) > 10:
            order = dfp.mean().sort_values(ascending=False).index
            dfp = dfp.reindex(order, axis=1).iloc[:,0:10]
            for i, l in enumerate(labels):
                l['tag'] = i
            labels = np.array(labels)[order].tolist()[0:10]

        if KEEP_DATAFRAMES:
            DATAFRAMES[id] = dfp

        fig = px.line(dfp, title=metric, color_discrete_sequence=px.colors.qualitative.Bold)
        if type != 'histogram' and type != 'summary':
            fig.update_layout(xaxis={'title': ''}, legend_title="tag") #, legend_x=0, legend_y=-0.1+-0.1*len(labels))

        fig.update_layout(template=None, height=400, width=400, autosize=False, font={'size': 11}, title={'x': 0.05, 'xanchor': 'left'})
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(showgrid=False)
        fig.update_layout(showlegend=True)

        FIGURES[id] = fig

        return fig, labels, query, dfp
    except Exception as x:
        #traceback.print_exc()
        print('error generating line_chart: ' + metric + '.' + id + ': ' + str(x))
        C_EXCEPTIONS_HANDLED.labels(x.__class__.__name__).inc()
        time.sleep(1)
        return None, None, None, None

METRICS_PROCESSED = 0
METRICS_AVAILABLE = 0
METRICS_DROPPED = 0
METRICS_TOTAL_TS = 0

G_POLL_TIME = Gauge('anomalizer_poll_time', 'poll-time (seconds)')
G_METRICS = Gauge('anomalizer_num_metrics', 'number of metrics')
G_METRICS_AVAILABLE = Gauge('anomalizer_num_metrics_available', 'number of metrics available on server')
G_METRICS_PROCESSED = Gauge('anomalizer_num_metrics_processed', 'number of metrics processed')
G_METRICS_DROPPED = Gauge('anomalizer_num_metrics_dropped', 'number of metrics dropped')
G_METRICS_TOTAL_TS = Gauge('anomalizer_num_metrics_total_timeseries', 'number of time-series available on server')
G_MEMORY_RSS = Gauge('anomalizer_memory_rss', 'resident memory consumption of program', unit='GB')
G_MEMORY_VMS = Gauge('anomalizer_memory_vms', 'virtual memory consumption of program', unit='GB')
G_THREADS = Gauge('anomalizer_active_threads', 'number of active threads')

C_EXCEPTIONS_HANDLED = Counter('anomalizer_num_exceptions_handled', 'number of exeptions handled', ['exception'])

H_PROMETHEUS_CALL = Histogram('anomalizer_prometheus_request_latency', 'request latency for prometheus metrics')

S_TO_IMAGE = Summary('anomalizer_to_image_time', 'time to convert images')
S_FIGURE = Summary('anomalizer_figures_seconds', 'time to compute figures')


@S_TO_IMAGE.time()
def to_image(fig, id=None):
    return fig.to_image(format='jpg')

def resource_monitoring():
    while True:
        gc.collect()
        info = psutil.Process().memory_info()
        GB = 1024*1024*1024
        G_MEMORY_RSS.set(info.rss/GB)
        G_MEMORY_VMS.set(info.vms/GB)
        G_THREADS.set(threading.active_count())
        G_METRICS_AVAILABLE.set(METRICS_AVAILABLE)
        G_METRICS_PROCESSED.set(METRICS_PROCESSED)
        G_METRICS_DROPPED.set(METRICS_DROPPED)
        G_METRICS_TOTAL_TS.set(METRICS_TOTAL_TS)
        G_METRICS.set(len(METRICS))
        G_POLL_TIME.set(POLL_TIME)
        time.sleep(30)


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


S_POLL_METRICS = Summary('anomalizer_poll_metrics_time_seconds', 'time to poll metrics')

def cleanup(id, metric):
    try:
        ID_MAP.pop(id, None)
        IMAGES.pop(id, None)
        METRIC_MAP.pop(metric, None)
        DATAFRAMES.pop(id, None)
        FIGURES.pop(id, None)
        scatter = FIGURES.get(id + '.scatter', [])
        if scatter:
            for i, _ in enumerate(scatter):
                s_id = id + '.scatter.' + str(i)
                IMAGES.pop(s_id, None)
            FIGURES.pop(id + '.scatter', None)
    except Exception as x:
        traceback.print_exc()

@S_POLL_METRICS.time()
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
            if TYPE and type != TYPE:
                fig, dfp, query = None, None, ''
            else:
                fig, labels, query, dfp = line_chart(metric, id, type, _rate=type=='counter' or type=='summary', thresh=LIMIT, filter=FILTER)
            if fig:
                #print('rendering metric: ' + metric)
                METRICS_TOTAL_TS += len(labels)
                img_bytes = to_image(fig)
                encoding = b64encode(img_bytes).decode()
                img_b64 = "data:image/jpg;base64," + encoding
                # forward/backward map between metrics and their ids.
                ID_MAP[id] = metric
                METRIC_MAP[metric] = id
                if dfp is None or dfp.shape[0] < 5: # need 5 points to compute things.
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

                features = {}
                if std > LIMIT:
                    mean_shift = (data2.mean()-data1.mean()).max()/mean
                    if mean_shift > INCREASE_THRESH:
                        features.update({'increasing': {'increase': mean_shift}})
                    if mean_shift < DECREASE_THRESH:
                        features.update({'decreasing': {'decrease': mean_shift}})

                if not dfp is None:
                    std = dfp.std(ddof=0).abs().sum()
                    mean = dfp.mean().sum()
                    _max = dfp.max().max()
                    _min = dfp.min().min()
                    rstd = std/mean if mean>std else std
                    spike = _max/mean if mean>0 else _max

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

                    IMAGES[id] = {'type': type, 'plot': 'timeseries', 'id': id, 'img': img_b64, 'prometheus': query, 'status': status.value, 'features': features, 'metric': metric, 'cardinality': cardinality, 'tags': labels, 'stats': {'rstd': rstd, 'max': _max, 'rmax': -_max, 'mean': mean, 'std': std, 'snr': snr, 'spike': spike}}

                    # handle scattergrams.
                    for i, _fig in enumerate(FIGURES.get(id + '.scatter', [])):
                        fig, features, stats = _fig
                        if fig:
                            img_bytes = to_image(fig)
                            encoding = b64encode(img_bytes).decode()
                            img_b64 = "data:image/jpg;base64," + encoding
                            scat_id = id + '.scatter.' + str(i)
                            IMAGES[scat_id] = {'type': type, 'plot': 'scatter', 'id': id, 'img': img_b64, 'prometheus': query, 'status': status.value, 'features': features, 'metric': metric, 'cardinality': cardinality, 'tags': labels, 'stats': stats}

                    #img = html.Img(src=img_b64, id=id, style={'height': '300px'})
            else:
                METRICS_DROPPED += 1
                cleanup(id, metric)

            # prune
            for id in IMAGES.copy():
                if not IMAGES[id]['metric'] in METRIC_MAP:
                    print('removing: ' + IMAGES[id]['metric'] + '.' + id)
                    cleanup(id, metric)

            # sanity check: each image should have a figure.
            for id in IMAGES.copy():
                if '.scatter' in id:
                    _id, _, _i = id.split('.')
                    if not FIGURES.get(_id + '.scatter')[int(_i)]:
                        print('sanity check failed: no scattergram for image metric=' + metric + ', id=' + id)
                elif not FIGURES.get(id):
                    print('sanity check failed: no figure for image metric=' + metric + ', id=' + id)

        except Exception as x:
            traceback.print_exc()
            cleanup(id, metric)
            C_EXCEPTIONS_HANDLED.labels(x.__class__.__name__).inc()


    time.sleep(1)

@app.route('/_dash-update-component', methods=['GET', 'POST'])
def _dash_update_component():
    # TODO: remove -- old dash UI compat.
    result = {'status': 'success'}
    return jsonify(result)


'''
@dash.callback(
    Output('charts', 'children'),
    Input('dropdown-types', 'value'),
    Input('filter', 'value'),
    Input('limit', 'value')
)
def menu(type, filter, limit):
    _params(type, filter, limit)
'''

def _params(type, filter, limit):
    try:
        if filter == None:
            filter = ''
        global FILTER, TYPE, OLD_TYPE, LIMIT, METRICS
        FILTER = filter.strip(' ')
        OLD_TYPE = TYPE
        TYPE = type
        if TYPE and TYPE != OLD_TYPE:
            ID_MAP.clear()
            IMAGES.clear()
            METRIC_MAP.clear()
            DATAFRAMES.clear()
        LIMIT = limit
        #print('FILTER=' + FILTER + ', LIMIT=' + str(LIMIT))
    except Exception as x:
        print('unable to contact prometheus at: ' + META)
        traceback.print_exc()
        C_EXCEPTIONS_HANDLED.labels(x.__class__.__name__).inc()

def refresh_metrics():
    try:
        global METRICS
        print('fetching ' + META)
        result = requests.get(META)
        _json = result.json()
        METRICS = _json['data']
        print('#METRICS=' + str(len(METRICS)))
        # synthetic metrics for histogram and summary
        synth = {}
        for k, metric in METRICS.items():
            type = metric[0]['type']
            if type=='summary' or type=='histogram':
                synth.update({k + '_count': [{'type': 'counter'}]})
                METRIC_TYPES[k + '_count'] = 'counter'
        METRICS.update(synth)
        #print('METRICS=' + str(METRICS))
        filter_json(METRICS, 'type', TYPE)
    except Exception as x:
        print('error refreshing metrics: ' + str(x))
        time.sleep(5)


def serve_layout():
    return html.Div(children=[
        html.H1(children=' Anomalizer by Pogadog: '),
        html.Span(socket.gethostname()),
        html.Div(style={'width': '25%'}, children=[
            dcc.Dropdown(TYPES, id='dropdown-types'),
            dcc.Dropdown(LIMITS, id='limit', value=LIMIT),
            dcc.Input(id='filter', debounce=True),
        ]),
        html.A(href='/images/html', children=['images']),
        html.Div(id='charts')
    ])

pr = cProfile.Profile()
pr.enable()

@app.route('/profile')
def profile():
    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats('cumtime')
    ps.print_stats()
    pr.enable()
    response = make_response(s.getvalue(), 200)
    response.mimetype = "text/plain"
    return response

@app.route('/image/<id>')
def get_image(id):
    return jsonify(IMAGES[id])

@app.route('/images/html')
def images_html():
    '''
    page = ''
    page += '<div">'
    for id, image in IMAGES.copy().items():
        page += '<img title="' + str(image['metric']) + '" width="200" height="200" src="' + image['img'] + '"/>'
    page += '</div>'
    '''
    images = []
    for _, image in IMAGES.copy().items():
        images += [image]

    # sorting cost-function for the images (below)
    def cost(x):
        cost = 0
        if x['status']=='warning': cost += 1
        if x['status']=='critical': cost += 2
        cost += x['stats']['rstd']
        cost += abs(x['features'].get('increasing', {}).get('increase', 0))
        cost += abs(x['features'].get('decreasing', {}).get('decrease', 0))
        cost += abs(x['features'].get('hockeystick', {}).get('increasing', 0))
        cost += abs(x['features'].get('hockeystick', {}).get('decreasing', 0))
        return cost

    images = sorted(images, key=cost, reverse=True)

    page = ''
    page += '<div">'
    for image in images:
        page += '<img title="' + image['status'] + ', ' + str(image['stats']) + ', ' + str(image['features']) + '" width="170" height="170" src="' + image['img'] + '"/>'
    page += '</div>'

    return page

@app.route('/images')
def get_images():
    '''
    hide_images = request.args.get('hide-images', 'false')=='true'
    result = copy.deepcopy(IMAGES)
    if hide_images:
        for k, v in result.items():
            del v['img']
    for k, v in result.items():
        v['status'] = random.choice(STATUS)
    '''
    return jsonify(IMAGES)

@app.route("/ids")
def get_ids():
    return jsonify(list(ID_MAP.keys()))

@app.route("/labels")
def get_names():
    return jsonify(list(METRICS.keys()))

@app.route("/figure/<id>")
@S_FIGURE.time()
def figure(id):
    try:
        if '.scatter' in id:
            metric, scatter, tag = id.split('.')
            fig = FIGURES.get(metric + '.' + scatter)[int(tag)][0]
        else:
            fig = FIGURES.get(id)
        if fig:
            return jsonify(json.loads(plotly.io.to_json(fig)))
        else:
            return jsonify({'status': 'failed', 'reason': 'missing'})
    except Exception as x:
        return jsonify({'status': 'failed', 'exception': str(x)})

# prometheus proxy to filter only interesting metrics.
@app.route('/metrics')
def metrics():
    PROM = PROMETHEUS + '/metrics'
    lines = ''
    try:
        result = requests.get(PROM)
        text = result.text.split('\n')
        for line in text:
            if line.startswith('# HELP') or line.startswith('# TYPE'):
                split = line.split(' ')
                metric = split[2]
            else:
                metric = line.replace('{', ' ').split(' ')[0]
            if not METRIC_MAP or metric in METRIC_MAP:
                lines += line + '\n'
    except Exception as x:
        print('unable to get /metrics from ' + PROM)

    # add in our metrics.
    lines = ''
    latest = generate_latest()
    lines += latest.decode()
    response = make_response(lines, 200)
    response.mimetype = "text/plain"
    return response

class FilterSchema(Schema):
    query = String(metadata={'query': 'The name of the pet.'})
    invert = Boolean(metadata={'invert': 'Invert the filter'})
    limit = String(metadata={'limit': 'Set limit to ignore metrics'})

@app.route('/filter', methods=['GET', 'POST'])
# TODO: enable once anomalizer UI is updated
#@app.input(FilterSchema)
def filter_metrics():
    body = request.json
    if body:
        global FILTER, INVERT, LIMIT
        FILTER = body.get('query', '')
        INVERT = body.get('invert', False)
        LIMIT = float(body.get('limit', LIMIT))
    result = {'status': 'success', 'query': FILTER, 'invert': INVERT, 'limit': LIMIT}
    print(result)
    return jsonify(result)

@app.route('/server-metrics')
def server_metrics():
    sm = {'poll-time': POLL_TIME, 'metric-count': len(METRICS), 'metrics-processed': METRICS_PROCESSED, 'metrics-available': METRICS_AVAILABLE, 'metrics-dropped': METRICS_DROPPED, 'metrics-total-ts': METRICS_TOTAL_TS}
    #print(sm)
    return jsonify(sm)

@app.route('/correlate/html')
def correlate_html():
    result = requests.get(request.url_root + '/correlate/all', params=request.args)
    page = '<html><body>'
    correlates = result.json()['correlates']
    for correlate in correlates:
        page += '<div">'
        for metric in correlate['metrics']:
            page += '<img title="' + str(metric[0]) + ', fit=' + str(metric[1]) + '" width="200" height="200" src="' + metric[2] + '"/>'
        page += '</div><hr/>'
    page += '</body></html>'
    return page

@app.route('/features-slow/html')
def features_html():
    result = requests.get(request.url_root + '/features-slow')
    page = '<html><body>'
    mean_shifts = result.json()['mean-up']
    page += '<div"><h2>increasing</h2>'
    for mean_shift in mean_shifts:
        values = pd.DataFrame(mean_shift['values'])
        fig = px.line(values.T, color_discrete_sequence=px.colors.qualitative.Bold)
        img_bytes = to_image(fig)
        encoding = b64encode(img_bytes).decode()
        img_b64 = "data:image/jpg;base64," + encoding
        page += '<img title="' + mean_shift['metric'] + ': ' + str(int(mean_shift['mean-up']*100)) + '%" width="200" height="200" src="' + img_b64 + '"/>'
    page += '</div><hr/>'
    mean_shifts = result.json()['mean-down']
    page += '<div"><h2>decreasing</h2>'
    for mean_shift in mean_shifts:
        values = pd.DataFrame(mean_shift['values'])
        fig = px.line(values.T, color_discrete_sequence=px.colors.qualitative.Bold)
        img_bytes = to_image(fig)
        encoding = b64encode(img_bytes).decode()
        img_b64 = "data:image/jpg;base64," + encoding
        page += '<img title="' + mean_shift['metric'] + ': ' + str(int(mean_shift['mean-down']*100)) + '%" width="200" height="200" src="' + img_b64 + '"/>'
    page += '</body></html>'
    return page

@app.route('/features')
def features_fast():
    increasing = []
    decreasing = []
    for _id, image in IMAGES.copy().items():
        features = image.get('features')
        if features.get('increasing'):
            increasing += [image]
        if features.get('decreasing'):
            decreasing += [image]
    increasing = sorted(increasing, key=lambda x: -x['features']['increasing']['increase'])
    decreasing = sorted(decreasing, key=lambda x: x['features']['decreasing']['decrease'])
    return jsonify({'features': {'increasing': increasing, 'decreasing': decreasing}})

@app.route('/features/html')
def features_fast_html():

    result = requests.get(request.url_root + '/features')
    features = result.json()['features']
    increasing = ''
    decreasing = ''
    page = '<html><body>'
    for feature in features['increasing']:
        img = '<img title="' + str(feature) + '" width="200" height="200" src="' + feature['img'] + '"/>'
        increasing += img
    for feature in features['decreasing']:
        img = '<img title="' + str(feature) + '" width="200" height="200" src="' + feature['img'] + '"/>'
        decreasing += img
    page += '<div"><h2>increasing</h2>'
    page += increasing
    page += '</div><hr/>'
    page += '<div"><h2>decreasing</h2>'
    page += decreasing
    page += '</div><hr/>'
    page += '</body></html> '
    return page

@app.route('/features-slow')
def features():
    mean_up = []
    mean_down = []
    for _id, df in DATAFRAMES.copy().items():
        try:
            metric = ID_MAP.get(_id)
            dfc = df.copy()
            N = len(dfc)
            data1 = dfc.loc[0:9*N//10]
            data2 = dfc.loc[9*N//10:]

            values = dfc.values
            std = dfc.std(ddof=0).mean()
            if std < LIMIT:
                continue
            mean_shift = (data2.mean()-data1.mean()).max()/std
            if mean_shift > INCREASE_THRESH:
                mean_up += [{'metric': metric, 'id': _id, 'mean-up': mean_shift, 'values': values.T.tolist()}]
            if mean_shift < DECREASE_THRESH:
                mean_down += [{'metric': metric, 'id': _id, 'mean-down': mean_shift, 'values': values.T.tolist()}]
        except Exception as x:
            traceback.print_exc()
            C_EXCEPTIONS_HANDLED.labels(x.__class__.__name__).inc()

    mean_up = sorted(mean_up, key=lambda m: -m['mean-up'])[0:10]
    mean_down = sorted(mean_down, key=lambda m: m['mean-down'])[0:10]
    #print('mean-shifts=' + str(mean_shifts) + ', variance-shifts=' + str(variance_shifts))
    return jsonify({'status': 'success', 'mean-up': mean_up, 'mean-down': mean_down})

@app.route('/correlate/html/<id>')
def correlate_html_id(id):
    result = requests.get(request.url_root + '/correlate/' + id)
    page = '<html><body>'
    correlates = result.json()['correlates']
    for correlate in correlates:
        page += '<div">'
        for metric in correlate['metrics']:
            page += '<img title="' + str(metric[0]) + ', fit=' + str(metric[1]) + '" width="200" height="200" src="' + metric[2] + '"/>'
        page += '</div><hr/>'
    page += '</body></html>'
    return page


S_CORRELATE = Summary('anomalizer_correlation_time_seconds', 'time to compute correlation', ('mode',))
G_CORRELATE = Gauge('anomalizer_correlation_time_gauge', 'time to compute (gauge)')

S_CORRELATE_ID = S_CORRELATE.labels('id')
S_CORRELATE_ALL = S_CORRELATE.labels('all')

@app.route('/correlate/all')
@S_CORRELATE_ALL.time()
def correlate_all():
    return correlate('all')

@app.route('/correlate/<id>')
@G_CORRELATE.time()
def correlate_id(id):
    return correlate(id)

def correlate(id):
    #if id=='all':
    #    print('correlate/all is disabled for reasons of scale')
    #    return jsonify({'status': 'failed', 'exception': 'correlate/all is disabled for performance reasons'})

    negative = request.args.get('negative', 'false')=='true'
    neg = 1 if negative else -1
    data = pd.DataFrame()

    start = time.time()
    #samples = H_PROMETHEUS_CALL._samples()
    #print('H_PROMETHEUS_CALL: ' + str(samples))

    try:
        # find all correlations. pearson correlation pairwise of all dataframes that are currently active.
        print('correlate #DATAFRAMES=' + str(len(DATAFRAMES)))
        # collect all data-frames expanded as columns and tagged as individual metrics.
        for _id, df in DATAFRAMES.copy().items():
            dfc = df.copy()
            # don't bother to correlate things that aren't moving around.
            std = dfc.std(ddof=0).abs().sum()
            if std < LIMIT:
                continue
            mean = dfc.mean().sum()
            #rstd = std/mean*100 if mean>std else std
            #if rstd < 5:
            #    continue
            metric = ID_MAP.get(_id)
            if not metric:
                continue
            named_cols = [metric + '.' + _id + '.' + str(l) for l in list(dfc.columns)]
            dfc.columns = named_cols
            #print('size=' + str(dfc.shape[0]) + ', ' + str(named_cols) + ', names=' + str(df[1]))
            if data.size==0:
                data = dfc
            else:
                data = pd.concat([data, dfc], axis=1)
        data = data.fillna(0)
        if id=='all':
            # correleate everything against everything
            #print('correlate/all')
            # TODO: this does not scale, and may cause performance probelms. pick only top-N metrics by interest?
            corr = data.corr().fillna(0)
            #print('correlate/all is disabled for reasons of scale')
            #corr = pd.DataFrame()
            #pass
        else:
            print('correlate/id=' + id)
            # lookup the metric id, and correlate just that against everything.
            single = DATAFRAMES.get(id)
            if single is None:
                raise Exception('no id ' + id + ' found in DATAFRAMES')
            # trim all data to the first column length.
            n = len(single[0])
            data = data[1:n]
            metric = ID_MAP.get(id)
            if not metric:
                raise Exception('no id ' + id + ' found in ID_MAP')
            # one-to-many correlation
            corr = pd.DataFrame()
            # expand the input signal into individual time-series.
            for name, col in single.iteritems():
                print('C-' + metric + '-' + str(name))
                if col.std(ddof=0) < LIMIT:
                    print(metric + '-' + str(name) + ' is too boring to correlate')
                    continue
                corr1 = data.corrwith(col).fillna(0)
                if corr.size==0:
                    corr = pd.DataFrame(corr1)
                else:
                    corr = pd.concat([corr, corr1], axis=1)
            corr = corr.T
        # for each input metric, find the strongest N correlates (will include the metric itself at 1)
        ordered = []
        dedup = set()
        for _, v in enumerate(corr.values):
            # support abs(v) argsort to find +ve and -ve correlates.
            isort = np.argsort(neg*v)
            if negative:
                isort = np.insert(isort, 0, isort[-1])
            isort = isort[0:N_CORR]
            metrics = list(corr.columns[isort])
            # do not put same combinations of correlations in twice.
            if not dedup.intersection(set(metrics)):
                dedup.update(metrics)
                values = list(v[isort])
                fit = np.abs(values).sum()/N_CORR
                #print('metrics=' + str(metrics), ', correlates=' + str(values))
                timeseries = data[metrics].T.values
                images = []
                for i, ts in enumerate(timeseries):
                    if i > N_CORR:
                        # limit to N_CORR.
                        break
                    # Note: these figurs are indexed by a differnt kind of id: <metric-name>.<id>.<tag#>
                    fig = px.line(ts, title=metrics[i].split('.')[0], color_discrete_sequence=px.colors.qualitative.Bold)
                    fig.update_layout(xaxis={'title': ''}, legend_title="tag") #, legend_x=0, legend_y=-0.1+-0.1*len(labels))
                    fig.update_layout(template=None, height=400, width=400, autosize=False, title={'x': 0.05, 'xanchor': 'left'})
                    fig.update_xaxes(showgrid=False)
                    fig.update_yaxes(showgrid=False)
                    fig.update_layout(showlegend=False)

                    img_bytes = to_image(fig)
                    encoding = b64encode(img_bytes).decode()
                    img_b64 = "data:image/jpg;base64," + encoding
                    images += [img_b64]
                meta = [{'metric': metric.split('.')[0], 'id': metric.split('.')[1], 'tag': metric.split('.')[2]} for metric in metrics]
                zipped = list(zip(meta, values, images))
                zipped = list(filter(lambda z: abs(z[1]) > 0.3, zipped))
                ordered += [{'fit': fit, 'metrics': zipped}]
            else:
                pass
        # most time is spent generating and serializing the b64 images.
        elapsed = time.time()-start
        ordered = sorted(ordered, key=lambda x: -x['fit'])
        return jsonify({'status': 'success', 'elapsed': elapsed, 'correlates': ordered, 'metrics': len(DATAFRAMES), 'results': len(ordered)})

    except Exception as x:
        #traceback.print_exc()
        print('correlate failed: ' + str(x))
        return jsonify({'status': 'failed', 'exception': str(x)})

@app.route('/api/v1/metadata')
def metadata():
    return jsonify(_metadata())

def _metadata():
    try:
        meta = requests.get(META).json()['data']
        for m in list(meta.keys())[:]:
            METRIC_TYPES[m] = meta[m][0]['type'] # todo: handle multi-variable
            if not m in METRIC_MAP:
                del meta[m]
        #print('METRIC_TYPES=' + str(METRIC_TYPES))
        return {'status': 'success', 'data': meta}
    except Exception as x:
        print('unable to contact prometheus at: ' + META)
        return {'status': 'failure'}


FORM_ENCODED = {'Content-Type': 'application/x-www-form-urlencoded'}

@app.route('/api/v1/query_exemplars', methods=['POST'])
def exemplars():
    body = request.form
    result = requests.post(PROMETHEUS + '' + request.path, data=body, headers=FORM_ENCODED)
    return result.json()

@app.route('/api/v1/query_range', methods=['POST'])
def query_range():
    body = request.form
    result = requests.post(PROMETHEUS + '' + request.path, data=body, headers=FORM_ENCODED)
    return result.json()


@app.route('/api/v1/labels', methods=['GET', 'POST'])
def labels():
    body = request.form
    meta = requests.post(LABELS, data=body, headers=FORM_ENCODED).json()
    return jsonify(meta)

@app.route('/api/v1/label/__name__/values')
def values():
    path = PROMETHEUS + request.path
    meta = requests.get(path).json()['data']
    _meta = []
    for m in meta[:]:
        if m in METRIC_MAP:
            _meta += [m]
    return jsonify({'status': 'success', 'data': _meta})

def startup():
    import threading, gc
    gc.enable()
    thread = threading.Thread(target=poller)
    thread.start()

    thread = threading.Thread(target=resource_monitoring)
    thread.start()

@app.after_request
def apply_caching(response):
    response.headers.update({
        'Access-Control-Allow-Credentials': 'true',
        'Access-Control-Allow-Methods':  '*',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': '*'
    })

    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response

import tracemalloc

tracemalloc.start()
s = None
@app.route("/snapshot")
def snap():
    global s
    if not s:
        s = tracemalloc.take_snapshot()
        return "taken snapshot\n"
    else:
        lines = []
        top_stats = tracemalloc.take_snapshot().compare_to(s, 'lineno')
        for stat in top_stats[:40]:
            lines.append(str(stat))
        s = tracemalloc.take_snapshot()
        display_top(s)
        return '<pre>' + "\n".join(lines) + '</pre>'

_params('', '', LIMIT)
_metadata()

#os.makedirs('dataframes', exist_ok=True)

if os.environ.get('MINIPROM'):
    # bring up a mini-prom for testing
    print(os.environ)
    import subprocess
    subprocess.Popen(['python', 'mini-prom.py'], shell=True)

#dash.layout = serve_layout

if __name__ == '__main__':
    print('anomalizer: PORT=' + str(PORT))
    startup()

    # pure flask engine
    app.run(port=PORT)

    # waitress
    #from waitress import serve
    #serve(app, host='0.0.0.0', port=PORT)
    
    # dash
    #dash.run_server(debug=False, host='0.0.0.0', port=PORT)
