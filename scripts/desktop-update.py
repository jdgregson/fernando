import argparse
from pathlib import Path
import sys
import time


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import desktop_update


parser = argparse.ArgumentParser()
parser.add_argument('action', choices=('start', 'cancel', 'stop', 'prepare', 'run'))
parser.add_argument('generation', nargs='?')
parser.add_argument('--nginx-port', type=int, default=8080)
parser.add_argument('--flask-port', type=int, default=5000)
parser.add_argument('--no-cache', action='store_true')
parser.add_argument('--wait', action='store_true')
args = parser.parse_args()
if args.action == 'start':
    state = desktop_update.launch(replace=True, no_cache=args.no_cache)
    print('Desktop update started in the background. Open Desktop to view progress.')
    if args.wait:
        generation = state['id']
        deadline = time.monotonic() + 4500
        phase = None
        while time.monotonic() < deadline:
            state = desktop_update.status(include_log=False)
            if state.get('id') != generation:
                raise RuntimeError('Desktop update was superseded')
            if state['phase'] != phase:
                print(state['message'], flush=True)
                phase = state['phase']
            if state['ready']:
                break
            if state['phase'] in ('failed', 'stopped'):
                raise RuntimeError(state['message'])
            time.sleep(0.5)
        else:
            raise TimeoutError('Desktop update did not finish within 75 minutes')
elif args.action == 'prepare':
    desktop_update.prepare(args.nginx_port, args.flask_port)
elif args.action == 'run':
    if not args.generation:
        parser.error('run requires a generation')
    desktop_update.run(args.generation)
elif args.action == 'cancel':
    desktop_update.cancel()
else:
    desktop_update.stop()
