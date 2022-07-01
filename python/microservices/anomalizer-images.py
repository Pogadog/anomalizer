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

PORT = int(os.environ.get('ANOMALIZER_IMAGES_PORT', 8061))

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

@S_TO_IMAGE.time()
def to_image(fig, id=None):
    return fig.to_image(format='jpg')

def poll_images():
    global ANOMALIZER_ENGINE_HEALTHY
    while True:
        print('images: poll_images')
        with shared.S_POLL_METRICS.labels('images').time():
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
                try:
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
                    #traceback.print_exc()
                    ANOMALIZER_ENGINE_HEALTHY = False
        time.sleep(1)

def startup():
    thread = threading.Thread(target=poll_images)
    thread.start()

if __name__ == '__main__':

    try:
        startup()

        print('anomalizer-images: PORT=' + str(PORT))
        app.run(port=PORT, use_reloader=False)
    except Exception as x:
        print('anomalizer-images error: ' + str(x))
        exit(1)