# legacy API to serve the anomalizer-ui. Delegates endpoints to the microservices:
# * anomalizer-images
# * anomalizer-engine

import os, json

from flask import Flask, jsonify, request, make_response, send_from_directory

from apiflask import APIFlask, Schema
from apiflask.fields import String, Float
from health import Health
import shared

from flask import request, Response
import requests

import logging
logging.getLogger("werkzeug").disabled = True

from prometheus_client import generate_latest

ANOMALIZER_ENGINE_HEALTHY = Health.UNKNOWN
ANOMALIZER_IMAGES_HEALTHY = Health.UNKNOWN
ANOMALIZER_CORRELATOR_HEALTHY = Health.UNKNOWN

# thanks to: https://stackoverflow.com/questions/6656363/proxying-to-another-web-service-with-flask
def _proxy(*args, **kwargs):
    global ANOMALIZER_ENGINE_HEALTHY, ANOMALIZER_IMAGES_HEALTHY, ANOMALIZER_CORRELATOR_HEALTHY
    if ANOMALIZER_ENGINE in args[0]:
        ANOMALIZER_ENGINE_HEALTHY = Health.UP
    if ANOMALIZER_IMAGES in args[0]:
        ANOMALIZER_IMAGES_HEALTHY = Health.UP
    if ANOMALIZER_CORRELATOR in args[0]:
        ANOMALIZER_CORRELATOR_HEALTHY = Health.UP
    print('proxy: ' + request.url + '->' + args[0])
    try:
        url = request.url.replace(request.host_url, args[0])
        if 'proxy' in url:
            url = url.replace('/proxy/engine', '')
            url = url.replace('/proxy/images', '')
            url = url.replace('/proxy/correlator', '')
        resp = requests.request(
            method=request.method,
            url=url,
            headers={key: value for (key, value) in request.headers if key != 'Host'},
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False)

        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(name, value) for (name, value) in resp.raw.headers.items()
                   if name.lower() not in excluded_headers]

        response = Response(resp.content, resp.status_code, headers)
        return response
    except:
        if ANOMALIZER_ENGINE in args[0]:
            ANOMALIZER_ENGINE_HEALTHY = Health.DOWN
        if ANOMALIZER_IMAGES in args[0]:
            ANOMALIZER_IMAGES_HEALTHY = Health.DOWN
        if ANOMALIZER_CORRELATOR in args[0]:
            ANOMALIZER_CORRELATOR_HEALTHY = Health.DOWN


app = APIFlask(__name__, title='anomalizer-api', static_folder='web-build')

PORT = int(os.environ.get('ANOMALIZER_API_PORT', 8056))

ANOMALIZER_ENGINE = 'http://localhost:8060/'
ANOMALIZER_IMAGES = 'http://localhost:8061/'
ANOMALIZER_CORRELATOR = 'http://localhost:8062/'
ANOMALIZER_MONOLITH = 'http://localhost:8056/'

@app.route('/health')
def health():
    healthy = ANOMALIZER_ENGINE_HEALTHY==Health.UP and ANOMALIZER_IMAGES_HEALTHY==Health.UP and ANOMALIZER_CORRELATOR_HEALTHY==Health.UP
    return jsonify({'status': Health.HEALTHY if healthy else Health.UNHEALTHY,
                    'anomalizer-engine': ANOMALIZER_ENGINE_HEALTHY,
                    'anomalizer-images': ANOMALIZER_IMAGES_HEALTHY,
                    'anomalizer-correlator': ANOMALIZER_CORRELATOR_HEALTHY,
                    'anomalizer-api': Health.UP
                    })


@app.route('/_dash-update-component', methods=['GET', 'POST'])
def _dash_update_component():
    result = {'status': 'success'}
    return jsonify(result)

@app.route('/ids')
def ids():
    return _proxy(ANOMALIZER_ENGINE)

@app.route('/images')
def images():

    # TODO: Check each shard, accumulate the results.
    images = {}
    headers = {}
    for i in range(0, shared.SHARDS):
        # TODO: some kind of discovery here, rather than hard-wired ports
        endpoint = shared.shard_endpoint(ANOMALIZER_IMAGES, i)
        image = _proxy(endpoint)
        if image:
            headers = image.headers
            print('images: shard=' + str(i) + ', #IMAGES=' + str(len(image.json)))
            images.update(image.json)
    # use the headers from the image response to make a valid response here.
    response = Response(bytes(json.dumps(images), 'utf-8'), 200, headers)
    return response

@app.route('/images/html')
def images_html():
    return _proxy(ANOMALIZER_IMAGES)

@app.route('/server-metrics')
def server_metrics():
    return _proxy(ANOMALIZER_ENGINE)

@app.route('/correlate/<id>')
def correlate_id(id):
    return _proxy(ANOMALIZER_CORRELATOR)

@app.route('/features')
def features():
    return _proxy(ANOMALIZER_IMAGES)

class FilterInSchema(Schema):
    query = String(required=True)
    invert = String(required=True)
    limit = Float(required=True)

@app.post('/filter')
@app.input(FilterInSchema)
def filter_metrics_post(body):
    return _proxy(ANOMALIZER_ENGINE)

@app.get('/filter')
def filter_metrics_get():
    return _proxy(ANOMALIZER_ENGINE)

@app.route('/figure/<id>')
def figure_id(id):
    id = id.split('.')[0] # handle scattergram ids.
    shard = shared.shard(id)
    endpoint = shared.shard_endpoint(ANOMALIZER_IMAGES, shard)
    return _proxy(endpoint)

@app.route('/metrics')
def metrics():
    # gather the downstreams via the proxy.
    r1 = _proxy(ANOMALIZER_ENGINE)
    r2 = _proxy(ANOMALIZER_IMAGES)
    r3 = _proxy(ANOMALIZER_CORRELATOR)

    # add in our metrics.
    lines = ''
    lines += '# HELP anomalizer_engine     ************* anomalizer-engine metrics\n'
    lines += r1.data.decode() if r1 else '# HELP anomalizer-engine no metrics\n'
    lines += '# HELP anomalizer_images     ************* anomalizer-images metrics \n'
    lines += r2.data.decode() if r2 else '# HELP anomalizer-images no metrics\n'
    lines += '# HELP anomalizer_correlator ************* anomalizer-corelator metrics\n'
    lines += r3.data.decode() if r3 else '# HELP anomalizer-correlator no metrics\n'
    latest = generate_latest()
    lines += latest.decode()

    response = make_response(lines, 200)
    response.mimetype = "text/plain"
    return response

@app.after_request
def apply_caching(response):
    response.headers.update({
        'Access-Control-Allow-Credentials': 'true',
        'Access-Control-Allow-Methods':  '*',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': '*'
    })

    #response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response

# Serve React App
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

@app.route('/proxy/<path:path>')
def proxy(path):
    print('proxy: ' + path)
    if 'engine' in path:
        return _proxy(ANOMALIZER_ENGINE)
    if 'images' in path:
        return _proxy(ANOMALIZER_IMAGES)
    if 'correlator' in path:
        return _proxy(ANOMALIZER_CORRELATOR)

if __name__ == '__main__':
    try:
        print('anomalizer-api: PORT=' + str(PORT))
        app.run(host='0.0.0.0', port=PORT, use_reloader=False)
    except Exception as x:
        print('anomalizer-api error: ' + str(x))
        exit(1)