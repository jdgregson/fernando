import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'tmux-web-secret')
    TMUX_HISTORY_LINES = int(os.environ.get('TMUX_HISTORY_LINES', 32768))
    HOST = os.environ.get('HOST', '0.0.0.0')
    PORT = int(os.environ.get('PORT', 5000))
    DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'

class ProductionConfig(Config):
    DEBUG = False

class DevelopmentConfig(Config):
    DEBUG = True

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
