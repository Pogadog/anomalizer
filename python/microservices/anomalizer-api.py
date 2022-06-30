# legacy API to serve the anomalizer-ui. Delegates endpoints to the microservices:
# * anomalizer-images
# * anomalizer-engine

import os

from flask import Flask, jsonify, request, make_response

from apiflask import APIFlask, Schema
from apiflask.fields import String, Boolean

from flask import request, Response
import requests

import logging
logging.getLogger("werkzeug").disabled = True

# thanks to: https://stackoverflow.com/questions/6656363/proxying-to-another-web-service-with-flask
def _proxy(*args, **kwargs):
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

app = APIFlask(__name__, title='anomalizer-api')

PORT = int(os.environ.get('PORT', 8057))

ANOMALIZER_ENGINE = 'http://localhost:8060/'
ANOMALIZER_IMAGES = 'http://localhost:8061/'
ANOMALIZER_CORRELATOR = 'http://localhost:8062/'
ANOMALIZER_MONOLITH = 'http://localhost:8056/'

@app.route('/_dash-update-component', methods=['GET', 'POST'])
def _dash_update_component():
    result = {'status': 'success'}
    return jsonify(result)

@app.route('/ids')
def ids():
    return _proxy(ANOMALIZER_ENGINE)

@app.route('/images')
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
    return _proxy(ANOMALIZER_ENGINE)

@app.route('/filter', methods=['GET', 'POST'])
def filter():
    return _proxy(ANOMALIZER_ENGINE)

@app.route('/figure/<id>')
def figure_id(id):
    return _proxy(ANOMALIZER_IMAGES)

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


if __name__ == '__main__':

    print('anomalizer-images: PORT=' + str(PORT))
    app.run(port=PORT)