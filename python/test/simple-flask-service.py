# app.py
from flask import Flask
import time, random, threading, requests

app = Flask(__name__)

@app.route('/')
def index():
    return 'ok'

@app.route('/method-1')
def method1():
    time.sleep(random.random())
    return 'method-1'

@app.route('/method-2')
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