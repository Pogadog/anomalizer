# app.py
from flask import Flask
import time, random, threading, requests, os
from apiflask import APIFlask, Schema
from prometheus_flask_exporter import PrometheusMetrics

FILE = os.path.basename(__file__)

app = APIFlask(__name__, title=FILE)
metrics = PrometheusMetrics(app, path='/metrics')

@app.route('/')
def index():
    return 'ok'

@app.route('/method-1')
@metrics.summary('s_method_1', 'time for /method-1', labels={'app': FILE})
def method1():
    time.sleep(random.random())
    return 'method-1'

@app.route('/method-2')
@metrics.summary('s_method_2', 'time for /method-2', labels={'app': FILE})
def method2():
    time.sleep(random.random())
    return 'method1-2'

def poll():
    time.sleep(5)
    while True:
        if random.random() > 0.5:
            requests.get('http://localhost:5000/method-1')
        if random.random() > 0.5:
            requests.get('http://localhost:5000/method-2')
        time.sleep(1)

threading.Thread(target=poll).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)