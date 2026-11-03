import os
import subprocess
import sys
import threading
import time

from flask import Flask, request
from flask_socketio import SocketIO
from src.services.service_boundary import install_boundary


app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading')
events = []
child = subprocess.Popen([sys.executable, '-u', '-c', 'import time\nfor i in range(10000):\n print(i, flush=True)\n time.sleep(0.03)'], stdout=subprocess.PIPE, text=True)


def collect():
    for line in child.stdout:
        value = int(line)
        events.append(value)
        socketio.emit('fixture_event', {'value': value})


threading.Thread(target=collect, daemon=True).start()


@app.get('/api/agents/working', endpoint='web.api_working_agents')
def status():
    return {'pid': os.getpid(), 'child_pid': child.pid, 'events': events, 'working_count': 1}


@app.post('/api/step_progress', endpoint='web.api_step_progress')
def long_request():
    time.sleep(1.5)
    return {'completed': True, 'value': request.json}


install_boundary(app, 'runtime')
socketio.run(app, host='127.0.0.1', port=int(os.environ['FLASK_PORT']), allow_unsafe_werkzeug=True)
