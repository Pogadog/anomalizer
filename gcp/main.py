# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# [START gae_python38_app]
# [START gae_python3_app]
from flask import Flask, request, Response, send_from_directory, make_response
import subprocess, requests
import traceback, os
import time

# If `entrypoint` is not defined in app.yaml, App Engine will look for an app
# called `app` in `main.py`.
app = Flask(__name__, static_folder='web-build')

ANOMALIZER_API = 'http://localhost:8056/'
ANOMALIZER_ENGINE = 'http://localhost:8060/'
ANOMALIZER_IMAGES = 'http://localhost:8061/'
ANOMALIZER_CORRELATOR = 'http://localhost:8062/'

'''
@app.route('/')
def hello():
    """Return a friendly HTTP greeting."""
    return 'Hello anomalizer-service on app-engine!'
'''

# thanks to: https://stackoverflow.com/questions/6656363/proxying-to-another-web-service-with-flask
def _proxy(*args, **kwargs):

    count = 3 # retries before concluding a failure.
    while True:
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
        except Exception as x:
            if count == 0:
                traceback.print_exc()
                return make_response({'status': 'failed', 'exception': repr(x)}, 500)
            else:
                count -= 1
                time.sleep(1)

CONTEXT = ''

@app.route('/', defaults={'u_path': ''}, methods=['GET', 'POST'])
@app.route('/<path:u_path>', methods=['GET', 'POST'])
def catch_all(u_path):
    if u_path != "" and os.path.exists(app.static_folder + '/' + u_path):
        return send_from_directory(app.static_folder, u_path)
    elif u_path == '/':
        return send_from_directory(app.static_folder, 'index.html')
    else:
        pass
    global CONTEXT
    print('u_path=' + repr(u_path) + ',  url=' + request.url)
    # support direct proxies to the backend components.
    # TODO: this could be a place to implement a load-balancer.
    # TODO: handle the /docs endpoint, which makes a subsequent request to openapi.json, and needs a prefix.
    if u_path.startswith('proxy/engine') or CONTEXT=='engine':
        request.url = request.url.replace('proxy/engine/', '')
        return _proxy(ANOMALIZER_ENGINE)
    if u_path.startswith('proxy/images'):
        request.url = request.url.replace('proxy/images/', '')
        return _proxy(ANOMALIZER_IMAGES)
    if u_path.startswith('proxy/correlator'):
        request.url = request.url.replace('proxy/correlator/', '')
        return _proxy(ANOMALIZER_CORRELATOR)
    return _proxy(ANOMALIZER_API)

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

def startup():
    import os
    print('cwd=' + os.getcwd())
    ENV = os.environ
    ENV['MICROSERVICES'] = 'microservices/'
    subprocess.Popen(['python', 'microservices/anomalizer-service.py'], close_fds=False, env=ENV)

startup()

print(os.environ)

# allow time for the services to come up.
time.sleep(6)
if __name__ == '__main__':

    # This is used when running locally only. When deploying to Google App
    # Engine, a webserver process such as Gunicorn will serve the app. You
    # can configure startup instructions by adding `entrypoint` to app.yaml.
    app.run(host='127.0.0.1', port=8080, debug=True, use_reloader=False)
# [END gae_python3_app]
# [END gae_python38_app]
