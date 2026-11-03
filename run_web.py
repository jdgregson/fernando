import os

os.environ['FERNANDO_ROLE'] = 'web'

from src import create_app
from src.config import get_config
from src.services.logging_config import configure_logging


configure_logging()
app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(get_config('WEB_PORT', '5001')), threaded=True, debug=False, use_reloader=False)
