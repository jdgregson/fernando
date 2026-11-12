import threading
import uuid


class MCPStartup:
    def __init__(self, publish):
        self.publish = publish
        self.epoch = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.generation = 0
        self.revision = 0
        self.servers = {}

    def snapshot(self):
        with self.lock:
            return {
                'type': 'mcp_status',
                'epoch': self.epoch,
                'revision': self.revision,
                'failures': [dict(name=name, **state) for name, state in self.servers.items()
                             if state['status'] in ('failed', 'unconfirmed')],
            }

    def begin(self, names):
        with self.lock:
            self.generation += 1
            self.servers = {name: {'status': 'pending', 'error': ''} for name in names}
            self._publish()
            return self.generation

    def _publish(self):
        self.revision += 1
        self.publish(self.snapshot())

    def update(self, generation, name, status, error=''):
        with self.lock:
            if generation != self.generation or name not in self.servers:
                return
            state = {'status': status, 'error': str(error)[:2000]}
            if self.servers[name] != state:
                self.servers[name] = state
                self._publish()

    def unconfirmed(self, generation, error):
        with self.lock:
            if generation != self.generation:
                return
            for name, state in list(self.servers.items()):
                if state['status'] == 'pending':
                    self.update(generation, name, 'unconfirmed', error)

    def opencode(self, generation, statuses):
        with self.lock:
            if generation != self.generation:
                return
            for name in self.servers:
                state = statuses.get(name, {})
                status = state.get('status')
                if status == 'connected':
                    self.update(generation, name, 'connected')
                elif status:
                    self.update(generation, name, 'failed', state.get('error') or f'Backend reported: {status}')
                else:
                    self.update(generation, name, 'unconfirmed', 'Configured server is missing from backend MCP status.')
