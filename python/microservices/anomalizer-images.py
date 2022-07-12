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

# Are we sharded?
SHARD = int(os.environ.get('SHARD', '0'))
SHARDS = int(os.environ.get('SHARDS', '0'))
print('SHARDS=' + str(SHARDS) + ', SHARD=' + str(SHARD))

shared.hook_logging('images-' + str(SHARD))

from prometheus_client import Summary, Gauge

S_TO_IMAGE = shared.S_TO_IMAGE.labels('images')
G_TO_IMAGE = Gauge('anomalizer_to_image_time_gauge', 'gauge of to-image time')
S_FIGURE = Summary('anomalizer_figures_seconds', 'time to compute figures')

G_NUM_IMAGES = Gauge('anomalizer_num_images', 'number of images in memory')
S_POLL_METRICS = shared.S_POLL_METRICS.labels('images')

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

PORT = int(os.environ.get('ANOMALIZER_IMAGES_PORT', SHARD*10000+8061))

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

@app.route('/images')
def images():
    return jsonify(IMAGES)

@app.route('/ids')
def ids():
    return jsonify(list(IMAGES.keys()))

@app.route('/images/html')
def images_html():
    if not IMAGES:
        return 'nothing to see here yet, still gathering data?'
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

@S_TO_IMAGE.time() #.labels('images')
def to_image(fig, id=None):
    start = time.time()
    try:
        return fig.to_image(format='jpg')
    finally:
        G_TO_IMAGE.set(time.time()-start)

def poll_images():
    global ANOMALIZER_ENGINE_HEALTHY
    while True:
        print('poll_images, SHARD=' + str(SHARD) + ', #IMAGES=' + str(len(IMAGES)))
        start = time.time()
        with S_POLL_METRICS.time():
            try:
                # 1. ask the anomalizer-engine for a list of metric ids.
                # 2. grab the dataframe for each image-id
                # 3. convert to an image and cache.
                # 4. bulk queries.

                dataframes = requests.get(ANOMALIZER_ENGINE + '/dataframes')
                assert dataframes.status_code == 200, 'unable to get engine/dataframes'
                dataframes = dataframes.json()
                # in-place translation of incoming dataframes.
                for dataframe in dataframes['dataframes']:
                    id, df = dataframe
                    _df = pd.read_json(df, orient='index').T
                    dataframe[1] = _df

                ANOMALIZER_ENGINE_HEALTHY = True

                for df in dataframes['dataframes']:
                    id = df[0]
                    # sharding algorithm.
                    shard = shared.shard(id)
                    if shard!=SHARD:
                        #print('ignoring ' + id + ' because SHARD=' + str(SHARD))
                        continue
                    try:
                        dfp = df[1]
                        labels = dataframes['labels'][id]
                        id_map = dataframes['id_map']
                        stats = dataframes['stats'][id]
                        features = dataframes['features'][id]
                        query  = dataframes['queries'][id]
                        cardinality = dataframes['cardinalities'][id]
                        metric_types = dataframes['metric_types']
                        status = dataframes['status'][id]

                        metric = id_map[id]

                        #print('rendering metric: ' + metric)
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


                    except Exception as x:
                        #traceback.print_exc()
                        #print(repr(x))
                        pass

                # scattergrams.
                result = requests.get(ANOMALIZER_ENGINE + '/scattergrams')
                assert result.status_code == 200, 'unable to call engine/scattergrams'
                result = result.json()
                for scat_id, v in result.items():
                    FIGURES[scat_id] = []
                    for i, x in enumerate(v):
                        dxy = pd.read_json(x['xy'], orient='index').T
                        metric = x['metric']
                        dii = dxy.iloc[:,0]
                        dxi = dxy.iloc[:,1]
                        dyi = dxy.iloc[:,2]
                        fig = px.scatter(x=dxi, y=dyi, title=metric + '.' + str(i), labels={'x':'rate(/sec)', 'y':'value'}, color=dii)
                        fig.update_layout(template=None, height=400, width=400, autosize=False, font={'size': 11}, title={'x': 0.05, 'xanchor': 'left'})
                        fig.update_xaxes(showgrid=False)
                        fig.update_yaxes(showgrid=False)
                        fig.update_layout(showlegend=True)

                        # overlay hockey-stick if present.
                        dl1 = pd.read_json(x['l1'], orient='index').T
                        dl2 = pd.read_json(x['l2'], orient='index').T
                        if len(dl1) and len(dl2):
                            # add lines to an existing scattergram (scat)
                            line1 = go.Scatter(x=dl1[0], y=dl1[1], mode='lines', showlegend=False, line={'color':'blue', 'width':2})
                            line2 = go.Scatter(x=dl2[0], y=dl2[1], mode='lines', showlegend=False, line={'color':'orange', 'width':2})

                            fig.add_trace(line1)
                            fig.add_trace(line2)

                        features = {}
                        stats = {}
                        FIGURES[scat_id] += [(fig, features, stats)]

                        #print('scattergram=' + metric + '.' + str(i))

                        img_bytes = to_image(fig)
                        encoding = b64encode(img_bytes).decode()
                        img_b64 = "data:image/jpg;base64," + encoding

                        # the following attributes are derived from the time-series image.
                        type = ''
                        id, _ = scat_id.split('.')
                        query = ''
                        features = x['features']
                        cardinality = x['cardinality']
                        labels = x['labels']
                        stats = x['stats']
                        status = x['status']

                        IMAGES[scat_id + '.' + str(i)] = {'type': type, 'plot': 'scatter', 'id': id, 'img': img_b64, 'prometheus': query, 'status': status, 'features': features, 'metric': metric, 'cardinality': cardinality, 'tags': labels, 'stats': stats}

            except Exception as x:
                #traceback.print_exc()
                print(repr(x))
                ANOMALIZER_ENGINE_HEALTHY = False
            shared.G_POLL_METRICS.labels('images').set(time.time()-start)
            G_NUM_IMAGES.set(len(IMAGES))
        time.sleep(1)

def cleanup():
    while True:
        try:
            # reconcile image ids with engine ids, and remove any images that no longer exist.
            ids = requests.get(ANOMALIZER_ENGINE + '/ids')
            assert ids.status_code==200, 'unable to contact engine at ' + ANOMALIZER_ENGINE + '/ids'
            ids = ids.json()
            for id in list(IMAGES.keys())[:]:
                id = id.split('.')[0]
                if not id in ids:
                    #print('cleaning image=' + id)
                    del IMAGES[id]
        except Exception as x:
            print(repr(x))
        time.sleep(10)

def startup():
    thread = threading.Thread(target=poll_images)
    thread.start()

    thread = threading.Thread(target=cleanup)
    thread.start()

if __name__ == '__main__':

    try:
        startup()

        print('PORT=' + str(PORT))
        app.run(port=PORT, use_reloader=False)
    except Exception as x:
        print('error: ' + str(x))
        exit(1)