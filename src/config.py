import os

# Load config file if it exists (simple KEY=VALUE format)
config_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
file_config = {}
if os.path.exists(config_file):
    with open(config_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                file_config[key.strip()] = value.strip()

def get_config(key, default=None):
    """Get config from environment variable first, then config file, then default"""
    # Check environment variable first
    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value
    
    # Check config file
    if key in file_config:
        return file_config[key]
    
    # Return default
    return default

class Config:
    SECRET_KEY = get_config('SECRET_KEY', os.urandom(32).hex())
    TMUX_HISTORY_LINES = int(get_config('TMUX_HISTORY_LINES', '32768'))
    HOST = get_config('FLASK_HOST', '0.0.0.0')
    PORT = int(get_config('FLASK_PORT', '5000'))
    DEBUG = get_config('DEBUG', 'True').lower() == 'true'
    NGINX_PORT = int(get_config('NGINX_PORT', '8080'))
    
    allowed_origins_str = get_config('ALLOWED_ORIGINS', 'http://localhost:8080')
    ALLOWED_ORIGINS = allowed_origins_str.split(',')

class ProductionConfig(Config):
    DEBUG = False

class DevelopmentConfig(Config):
    DEBUG = True

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
