import os, time, threading, traceback, gc, psutil, requests, uuid, json, re, ast, enum
import sys

import pandas as pd
import numpy as np
from base64 import b64encode
import functools

from flask import jsonify, request, make_response
from apiflask import APIFlask, Schema
from prometheus_client import Summary, Histogram, Counter, Gauge, generate_latest

import plotly.express as px

import shared
from health import Health

C_SHARD = shared.C_SHARD

shared.hook_logging('correlator-' + str(C_SHARD))

import logging
logging.getLogger("werkzeug").disabled = True

ANOMALIZER_ENGINE = os.environ.get('ANOMALIZER_ENGINE', 'http://localhost:8060')
LIMIT = shared.LIMITS[-2]

app = APIFlask(__name__, title='anomalizer-correlator')

PORT = int(os.environ.get('ANOMALIZER_CORRELATOR_PORT', C_SHARD*10000+8062))
N_CORR = 8

S_CORRELATE = Summary('anomalizer_correlation_time_seconds', 'time to compute correlation', ('mode',))
G_CORRELATE = Gauge('anomalizer_correlation_time_gauge', 'time to compute (gauge)')

S_CORRELATE_ID = S_CORRELATE.labels('id')
S_CORRELATE_ALL = S_CORRELATE.labels('all')

T_CORRELATE = shared.Timer('anomalizer_correlation_time_timer', 'time to compute correlation', ('mode','checkpoint'))
T_CORRELATE_ALL = T_CORRELATE.labels(['all'])
T_CORRELATE_ID = T_CORRELATE.labels(['id'])

S_TO_IMAGE = shared.S_TO_IMAGE.labels('correlator')
S_POLL_METRICS = shared.S_POLL_METRICS.labels('correlator-' + str(C_SHARD))

@S_TO_IMAGE.time() #.labels('correlator')
def to_image(fig, id=None):
    return fig.to_image(format='jpg')

# Shard the correlator as follows.  Divide thee dataframes into 4 requal sets [1,2,3,4].
# Shard as follows: 0 -> 1,2, 1->1,3, 2->1,4, 3->2,3, 4-> 2,4, 5 -> 3,4
# Each shard is 1/4 of the original data, and will run in 1/16 of the original time (O(n^2))
# so overall the efficiency is 6/16 better (approx 4x).
#
@app.route('/correlate/all')
def correlate_all():
    negative = request.args.get('negative', 'false')=='true'
    all = correlate('all', negative)
    return jsonify(all)

@app.route('/correlate/<id>')
def correlate_id(id):
    negative = request.args.get('negative', 'false')=='true'
    return jsonify(correlate(id, negative))

DATAFRAMES = {}
ID_MAP = {}
ANOMALIZER_ENGINE_HEALTHY = False

@app.route('/ids')
def ids():
    return jsonify(list(DATAFRAMES.keys()))

@app.route('/health')
def health():
    healthy = ANOMALIZER_ENGINE_HEALTHY
    return jsonify({'status': Health.HEALTHY if healthy else Health.UNHEALTHY,
                    'anomalizer-engine': Health.UP if ANOMALIZER_ENGINE_HEALTHY else Health.DOWN,
                    'anomalizer-correlator': Health.UP
                    })

def poll_dataframes():
    global ANOMALIZER_ENGINE_HEALTHY
    while True:
        with S_POLL_METRICS.time():
            try:
                result = requests.get(ANOMALIZER_ENGINE + '/dataframes')
                assert result.status_code == 200, 'unable to call engine/dataframes'
                ANOMALIZER_ENGINE_HEALTHY = True
                dataframes = result.json()['dataframes']
                id_map = result.json()['id_map']

                for dataframe in dataframes:
                    _id, df = dataframe
                    dataframe = pd.read_json(df, orient='index').T
                    DATAFRAMES[_id] = dataframe
                    ID_MAP[_id] = id_map[_id]
            except Exception as x:
                #traceback.print_exc()
                print(repr(x), sys.stderr)
                ANOMALIZER_ENGINE_HEALTHY = False
        time.sleep(1)

def clear_cache():
    while True:
        correlate.cache_clear()
        time.sleep(30)


def correlate_all_poller():
    print('correlate_all_poller')
    while True:
        try:
            all = correlate('all', False)
            #print('all: ' + str([(c['metrics']) for c in all.get('correlates', [])]))
        except:
            traceback.print_exc()
        time.sleep(1)


@functools.lru_cache
def correlate(id, negative):
    #if id=='all':
    #    print('correlate/all is disabled for reasons of scale')
    #    return jsonify({'status': 'failed', 'exception': 'correlate/all is disabled for performance reasons'})

    neg = 1 if negative else -1
    data = pd.DataFrame()

    start = time.time()
    #samples = H_PROMETHEUS_CALL._samples()
    #print('H_PROMETHEUS_CALL: ' + str(samples))
    scorr = S_CORRELATE_ALL if id=='all' else S_CORRELATE_ID
    tcorr = T_CORRELATE_ALL if id=='all' else T_CORRELATE_ID
    with tcorr.per(len(DATAFRAMES)) as timer:
        try:

            # find all correlations. pearson correlation pairwise of all dataframes that are currently active.
            #print('correlator: #DATAFRAMES=' + str(len(DATAFRAMES)))
            # collect all data-frames expanded as columns and tagged as individual metrics.
            #print('correlator: data')
            i = -1
            for _id, df in DATAFRAMES.copy().items():
                i += 1
                if not shared.sharded(i, C_SHARD):
                    continue
                #print('shard=' + str((i, C_SHARD)))
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
            timer.checkpoint(['data'])
            #print('correlator: starting correlation')
            if id=='all':
                # correleate everything against everything
                print('correlate/all: shard=' + str(C_SHARD) + ', #metrics=' + str(data.shape))
                # TODO: this does not s     cale, and may cause performance probelms. pick only top-N metrics by interest?
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
            timer.checkpoint(['correlation'])
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
                    #print('fit=' + str(fit))
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
                    zipped = list(zip(meta, values))
                    zipped = list(filter(lambda z: abs(z[1]) > 0.3, zipped))
                    ordered += [{'fit': fit, 'metrics': zipped}]
                else:
                    pass
            timer.checkpoint(['images'])
            # most time is spent generating and serializing the b64 images.
            elapsed = time.time()-start
            ordered = sorted(ordered, key=lambda x: -x['fit'])
            #print('correlator: correlation finished')
            return {'status': 'success', 'elapsed': elapsed, 'correlates': ordered, 'metrics': len(DATAFRAMES), 'results': len(ordered)}

        except Exception as x:
            traceback.print_exc()
            print('correlate failed: ' + repr(x), sys.stderr)
            return {'status': 'failed', 'exception': str(x)}

@app.route('/metrics')
def metrics():
    # add in our metrics.
    lines = ''
    latest = generate_latest()
    lines += latest.decode()
    response = make_response(lines, 200)
    response.mimetype = "text/plain"
    return response

def startup():
    thread = threading.Thread(target=poll_dataframes)
    thread.start()

    thread = threading.Thread(target=clear_cache)
    thread.start()

    thread = threading.Thread(target=correlate_all_poller)
    thread.start()

if __name__ == '__main__':

    try:
        startup()

        print('PORT=' + str(PORT))
        app.run(host='0.0.0.0', port=PORT, use_reloader=False)
    except Exception as x:
        print(repr(x))
        exit(1)