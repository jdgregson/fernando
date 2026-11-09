import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path


ACTIVE = {'scheduled', 'queued', 'waking'}
logger = logging.getLogger(__name__)


class ChatResumeScheduler:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.rows = None
        self.manager = None
        self.thread = None
        self.stop_event = threading.Event()

    def load(self):
        if self.rows is None:
            self.rows = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(self.rows, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def publish(self, row):
        session = self.manager.get_session(row['session_id'])
        if not session:
            return
        event = {'type': 'scheduled_resume', 'schedule': dict(row), 'ts': row['created_at']}
        with session._lock:
            session.history.append(event)
            session.scheduled_resume = dict(row) if row['status'] in ACTIVE else None
            session._save_history()
            if session.on_event:
                session.on_event(session.id, event)
        self.manager._broadcast_sessions_list()

    def create(self, session_id, delay_seconds, reason, continuation):
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= 31536000:
            raise ValueError('delay_seconds must be an integer between 1 and 31536000')
        for name, value, limit in [('reason', reason, 1000), ('continuation', continuation, 16000)]:
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f'{name} must contain 1 to {limit} characters')
        with self.lock:
            self.load()
            session = self.manager.get_session(session_id)
            if not session or not session.acp_session_id:
                raise ValueError('An existing chat conversation is required')
            if any(row['session_id'] == session_id and row['status'] in ACTIVE for row in self.rows.values()):
                raise ValueError('This chat already has a pending resume; cancel it before scheduling another')
            now = time.time()
            row = dict(id=uuid.uuid4().hex, session_id=session_id, created_at=now,
                       resume_at=now + delay_seconds, reason=reason, continuation=continuation,
                       status='scheduled', error=None, wake_started=None)
            self.rows[row['id']] = row
            self.save()
            self.publish(row)
            return dict(row)

    def change(self, session_id, schedule_id, action):
        if not isinstance(schedule_id, str):
            raise ValueError('Missing schedule ID')
        if not isinstance(action, str) or action not in {'cancel', 'resume_now'}:
            raise ValueError('Unknown scheduled resume action')
        with self.lock:
            self.load()
            row = self.rows.get(schedule_id)
            if not row or row['session_id'] != session_id:
                raise ValueError('Scheduled resume not found in this chat')
            if row['status'] not in ACTIVE:
                raise ValueError('This scheduled resume is no longer pending')
            if action == 'cancel':
                row['status'] = 'canceled'
            else:
                row.update(resume_at=time.time(), status='queued', wake_started=None)
            self.save()
            self.publish(row)
            return dict(row)

    def cancel_session(self, session_id):
        with self.lock:
            self.load()
            for row in self.rows.values():
                if row['session_id'] == session_id and row['status'] in ACTIVE:
                    row['status'] = 'canceled'
                    self.save()
                    if self.manager:
                        self.publish(row)

    def start(self, manager):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.manager = manager
            self.load()
            for row in self.rows.values():
                if row['status'] in ACTIVE:
                    row['wake_started'] = None
                    self.publish(row)
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, name='chat-resume-scheduler', daemon=True)
            self.thread.start()

    def run(self):
        while not self.stop_event.wait(1):
            self.tick()

    def tick(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self.load()
            for row in self.rows.values():
                if row['status'] not in ACTIVE or row['resume_at'] > now:
                    continue
                previous = dict(row)
                try:
                    result = self.manager.deliver_chat_resume(row)
                    if result == 'waking':
                        if row['wake_started'] is None:
                            row['wake_started'] = now
                        elif now - row['wake_started'] >= 120:
                            raise RuntimeError('Chat did not become ready within 120 seconds')
                    else:
                        row['wake_started'] = None
                    row['status'] = result
                except (OSError, RuntimeError, ValueError) as error:
                    logger.exception('Unable to resume chat %s', row['session_id'])
                    row.update(status='failed', error=str(error))
                if row != previous:
                    self.save()
                    self.publish(row)


chat_resume_scheduler = ChatResumeScheduler(Path(__file__).resolve().parents[2] / 'data' / 'chat_resumes.json')
