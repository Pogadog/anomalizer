# legacy API to serve the anomalizer-ui. Delegates endpoints to the microservices:
# * anomalizer-images
# * anomalizer-engine

import os

from flask import Flask, jsonify, request, make_response

from apiflask import APIFlask, Schema
from apiflask.fields import String, Boolean
from health import Health

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
        resp = requests.request(
            method=request.method,
            url=request.url.replace(request.host_url, args[0]),
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


app = APIFlask(__name__, title='anomalizer-api')

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
def images_html():
    return _proxy(ANOMALIZER_IMAGES)

@app.route('/images/html')
def images():
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

@app.route('/filter', methods=['GET', 'POST'])
def filter():
    return _proxy(ANOMALIZER_ENGINE)

@app.route('/figure/<id>')
def figure_id(id):
    return _proxy(ANOMALIZER_IMAGES)

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


if __name__ == '__main__':
    try:
        print('anomalizer-api: PORT=' + str(PORT))
        app.run(port=PORT, use_reloader=False)
    except Exception as x:
        print('anomalizer-api error: ' + str(x))
        exit(1)