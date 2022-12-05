# app.py
from flask import Flask, make_response
import time, random, threading, requests, os
from prometheus_client import Summary, Histogram, Counter, Gauge, generate_latest

C_METHOD_1 = Counter('method_1', 'calls to /method-1', ['app'])
C_METHOD_2 = Counter('method_2', 'calls to /method-2', ['app'])
S_METHOD_1 = Summary('s_method_1', 'time for /method-1', ['app'])
S_METHOD_2 = Summary('s_method_2', 'time for /method-2', ['app'])
G_METHOD_1 = Gauge('g_method_1', 'gauge-time for /method-1', ['app'])
G_METHOD_2 = Gauge('g_method_2', 'gauge-time for /method-2', ['app'])

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
    C_METHOD_1.labels(FILE).inc()
    start = time.time()
    with S_METHOD_1.labels(FILE).time():
        time.sleep(random.random())
    G_METHOD_1.labels(FILE).set(time.time()-start)
    return 'method-1'

@app.route('/method-2')
def method2():
    C_METHOD_2.labels(FILE).inc()
    start = time.time()
    with S_METHOD_2.labels(FILE).time():
        time.sleep(random.random())
    G_METHOD_2.labels(FILE).set(time.time()-start)
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