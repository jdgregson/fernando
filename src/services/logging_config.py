import logging
import os
import re


class SingleLineFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record).replace('\n', '\\n').replace('\r', '\\r')
        return re.sub(r'api_key=[a-fA-F0-9]+', 'api_key=REDACTED', message)


class WerkzeugDisconnectFilter(logging.Filter):
    def filter(self, record):
        return not (record.name == 'werkzeug' and record.levelno == logging.ERROR and "write() before start_response" in record.getMessage())


def configure_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    path = os.environ.get('FERNANDO_LOG_FILE')
    if path:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        logging.getLogger().addHandler(logging.StreamHandler(os.fdopen(descriptor, 'a', buffering=1)))
    for handler in logging.getLogger().handlers:
        handler.setFormatter(SingleLineFormatter('%(asctime)s %(name)s %(levelname)s %(message)s'))
        handler.addFilter(WerkzeugDisconnectFilter())
