from flask import Flask
from flask_socketio import SocketIO
from src.config import config
import os

socketio = SocketIO()

def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')
    
    app = Flask(__name__, 
                template_folder='templates',
                static_folder='static')
    app.config.from_object(config[config_name])
    
    # Support for proxy path prefix
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    
    socketio.init_app(app, cors_allowed_origins="*", path=os.environ.get('SOCKET_PATH', 'socket.io'))
    
    from src.routes import web, websocket
    app.register_blueprint(web.bp)
    websocket.register_handlers(socketio)
    
    @app.after_request
    def add_no_cache_headers(response):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    
    return app
