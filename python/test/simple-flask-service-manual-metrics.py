# app.py
from flask import Flask, make_response
import time, random, threading, requests, os
from prometheus_client import Summary, Histogram, Counter, Gauge, generate_latest

C_METHOD = Counter('method', 'calls to /methods', ['app', 'method'])
S_METHOD = Summary('s_method', 'time for /methods', ['app', 'method'])
G_METHOD = Gauge('g_method', 'gauge-time for /methods', ['app', 'method'])

FILE = os.path.basename(__file__)

app = Flask(__name__)

@app.route('/')
def index():
    return 'ok'

@app.route('/metrics')
def metrics():
    response = make_response(generate_latest())
    response.mimetype='text/plain'
    return response

@app.route('/method-1')
def method1():
    C_METHOD.labels(FILE, '/method-1').inc()
    start = time.time()
    with S_METHOD.labels(FILE, '/method-1').time():
        time.sleep(random.random())
    G_METHOD.labels(FILE, '/method-1').set(time.time()-start)
    return 'method-1'

@app.route('/method-2')
def method2():
    C_METHOD.labels(FILE, '/method-2').inc()
    start = time.time()
    with S_METHOD.labels(FILE, '/method-2').time():
        time.sleep(random.random())
    G_METHOD.labels(FILE, '/method-2').set(time.time()-start)
    return 'method1-2'

def poll():
    time.sleep(5)
    print('starting poll()')
    while True:
        if random.random() > 0.5:
            requests.get('http://localhost:5000/method-1')
        if random.random() > 0.5:
            requests.get('http://localhost:5000/method-2')
        time.sleep(1)

if __name__ == '__main__':
    threading.Thread(target=poll).start()
    app.run(host='0.0.0.0', port=5000)