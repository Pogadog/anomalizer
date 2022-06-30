import os, time, threading, traceback, gc, psutil, requests, uuid, json, re, ast, enum
import pandas as pd
import numpy as np
from base64 import b64encode
import plotly.express as px
import plotly.graph_objs as go
import plotly.io

from health import Health
import shared
from shared import C_EXCEPTIONS_HANDLED

from prometheus_client import Summary

S_TO_IMAGE = shared.S_TO_IMAGE
S_FIGURE = Summary('anomalizer_figures_seconds', 'time to compute figures')

from flask import jsonify, request, make_response
from apiflask import APIFlask, Schema
from prometheus_client import Summary, Histogram, Counter, Gauge, generate_latest

import logging
logging.getLogger("werkzeug").disabled = True

# rest endpoints.
ANOMALIZER_ENGINE = os.environ.get('ANOMALIZER_ENGINE', 'http://localhost:8060')

ANOMALIZER_ENGINE_HEALTHY = False
ANOMALIZER_IMAGES_HEALTHY = False

IMAGES = {}
FIGURES = {}

app = APIFlask(__name__, title='anomalizer-images')

PORT = int(os.environ.get('PORT', 8061))

@app.route('/health')
def health():
    healthy = ANOMALIZER_ENGINE_HEALTHY
    return jsonify({'status': Health.HEALTHY if healthy else Health.UNHEALTHY,
                    'anomalizer-engine': Health.UP if ANOMALIZER_ENGINE_HEALTHY else Health.DOWN,
                    'anomalizer-images': Health.UP
                    })

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

@app.route('/images')
def images():
    return jsonify(IMAGES)

@app.route('/ids')
def ids():
    return jsonify(list(IMAGES.keys()))

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

@app.route('/metrics')
def metrics():
    # add in our metrics.
    lines = ''
    latest = generate_latest()
    lines += latest.decode()
    response = make_response(lines, 200)
    response.mimetype = "text/plain"
    return response

@S_TO_IMAGE.time()
def to_image(fig, id=None):
    return fig.to_image(format='jpg')

def poll_images():
    global ANOMALIZER_ENGINE_HEALTHY
    while True:
        try:
            # 1. ask the anomalizer-engine for a list of metric ids.
            # 2. grab the dataframe for each image-id
            # 3. convert to an image and cache.
            # 4. TODO: bulk queries.
            ids = requests.get(ANOMALIZER_ENGINE + '/ids')
            assert ids.status_code == 200
            ANOMALIZER_ENGINE_HEALTHY = True
            ids = ids.json()

            #print('ids=' + str(ids))
            for id in ids:
                result = requests.get(ANOMALIZER_ENGINE + '/dataframes/' + id)
                assert result.status_code == 200
                result = result.json()
                dataframes = result['dataframes']
                labels = result['labels'][id]
                id_map = result['id_map']
                stats = result['stats'][id]
                features = result['features'][id]
                query  = result['queries'][id]
                cardinality = result['cardinalities'][id]
                metric_types = result['metric_types']
                status = result['status'][id]

                metric = id_map[id]
                dfp = pd.read_json(dataframes[id], orient='index').T

                type = metric_types[id]

                fig = px.line(dfp, title=metric, color_discrete_sequence=px.colors.qualitative.Bold)
                if type != 'histogram' and type != 'summary':
                    fig.update_layout(xaxis={'title': ''}, legend_title="tag") #, legend_x=0, legend_y=-0.1+-0.1*len(labels))

                fig.update_layout(template=None, height=400, width=400, autosize=False, font={'size': 11}, title={'x': 0.05, 'xanchor': 'left'})
                fig.update_xaxes(showgrid=False)
                fig.update_yaxes(showgrid=False)
                fig.update_layout(showlegend=True)

                FIGURES[id] = fig

                img_bytes = to_image(fig)
                encoding = b64encode(img_bytes).decode()
                img_b64 = "data:image/jpg;base64," + encoding

                IMAGES[id] = {'type': type, 'plot': 'timeseries', 'id': id, 'img': img_b64, 'prometheus': query, 'status': status, 'features': features, 'metric': metric, 'cardinality': cardinality, 'tags': labels, 'stats': stats}

        except:
            traceback.print_exc()
            ANOMALIZER_ENGINE_HEALTHY = False
        time.sleep(1)

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


def startup():
    thread = threading.Thread(target=poll_images)
    thread.start()

if __name__ == '__main__':

    startup()

    print('anomalizer-images: PORT=' + str(PORT))
    app.run(port=PORT)