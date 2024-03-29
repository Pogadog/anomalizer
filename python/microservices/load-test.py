# a simple client-server load-tester, where the server uses a single queue with a capacity limited by a semaphore
# and a client which ramps up and down to push the server beyond its capacity.  This genereates a classic M-M-1
# hockeystick in both the client and server response times.
#

from prometheus_client import Summary, Histogram, Counter, Gauge, generate_latest

from flask import Flask, jsonify, request, make_response
import threading, requests, time, random
from threading import current_thread
import logging
import shared

logging.getLogger("werkzeug").disabled = True

S_SERVER = Summary('load_test_server_request_time', 'time to execute server')
S_CLIENT_THREAD = Summary('load_test_client_request_time_per_thread', 'time to call server', ['endpoint', 'thread'])
S_CLIENT_TOTAL = Summary('load_test_client_request_time_total', 'time to call server', ['endpoint'])
G_THREADS = Gauge('load_test_threads', 'number of client threads')

app = Flask(__name__)

MAX_LOAD = 20
MAX_GAUGES = 10
SLEEP = 0.1
THREADS = []

# initial capacity is 5 clients.
SEMAPHORE = threading.Semaphore(1)

@app.route('/')
def root():
    return "load-test"

@app.route('/sleep/<id>')
def sleep(id):
    global SLEEP
    SLEEP = float(id)
    return 'SLEEP=' + str(SLEEP)

@app.route('/max-load/<id>')
def max_load(id):
    global MAX_LOAD, THREADS
    MAX_LOAD = int(id)
    return 'MAX_LOAD=' + str(MAX_LOAD)

def set_load(load):
    G_THREADS.set(load)
    print('LOAD=' + str(load))
    # spin up/down threads.
    global THREADS
    if load > len(THREADS):
        for i in range(0, load-len(THREADS)):
            THREADS += [threading.Thread(target=load_test, args=(load,))]
            THREADS[-1].start()
    else:
        for i in range(0, len(THREADS)-load):
            del THREADS[-1]
    return 'LOAD=' + str(load) + ', THREADS=' + str(len(THREADS))

@app.route('/capacity/<id>')
def capacity(id):
    global SEMAPHORE
    capacity = float(id)
    SEMAPHORE = threading.Semaphore(capacity)
    return 'CAPACITY=' + str(capacity)

@app.route('/server')
@S_SERVER.time()
def server():
    # simulate fixed capacity by slow response. With q sleep time of 0.1 seconds this server will hockey-stick
    # at a request rate of 10/second, i.e. 10 client threads polling 1 per second on average.
    #print('/server')
    SEMAPHORE.acquire()
    time.sleep(SLEEP)
    SEMAPHORE.release()
    return 'OK'

@app.route('/metrics')
def metrics():
    #print('/metrics')
    response = make_response(generate_latest(), 200)
    response.mimetype = "text/plain"
    return response

GAUGES = [Gauge('anomalizer_load_gauge_' + str(i), 'poll-time (seconds)') for i in range(MAX_GAUGES)]

def load_test(index):
    endpoint = 'http://localhost:7070/server'
    while True:
        if not current_thread() in THREADS:
            break
        with S_CLIENT_TOTAL.labels(endpoint).time():
            with S_CLIENT_THREAD.labels(endpoint, 'thread-' + str(index)).time():
                requests.get(endpoint)
        # average request rate is 1/second.
        time.sleep(random.uniform(0, 2))

def up_down_load():
    load = MAX_LOAD
    updown = 1
    while True:
        # generate a lot of synthetic gauges.
        #print('load_test: ' + current_thread().name)
        count = 0
        for gauge in GAUGES:
            gauge.set(count)
            count += 1
        # spend 1 minute at each load level
        set_load(load)
        if load >= MAX_LOAD or load <= 1:
            updown = -updown
        load += updown
        time.sleep(60)



threading.Thread(target=up_down_load).start()
threading.Thread(target=shared.resource_monitoring).start()

set_load(1)

if __name__=='__main__':
    import os
    PORT = int(os.environ.get('LOAD_TEST_PORT', '7070'))
    print('load-test: PORT=' + str(PORT))
    app.run(host='0.0.0.0', port=PORT, threaded=True)