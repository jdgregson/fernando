from src import create_app, socketio
from src.config import config
import os

app = create_app()

if __name__ == '__main__':
    env = os.environ.get('FLASK_ENV', 'development')
    cfg = config[env]
    socketio.run(app, host=cfg.HOST, port=cfg.PORT, debug=cfg.DEBUG, use_reloader=False, allow_unsafe_werkzeug=True)
