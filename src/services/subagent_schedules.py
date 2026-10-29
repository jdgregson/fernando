import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from croniter import croniter


logger = logging.getLogger(__name__)
DEFAULT_MESSAGE = "Recheck your assigned task using this conversation's context. If monitoring is complete, cancel your recurring schedule with manage_subagent_schedule and report the result to your parent. Otherwise finish this check and wait for the next scheduled wake-up."
DEFAULT_TIMEZONE = "America/Los_Angeles"
SCHEDULES_FILE = Path(__file__).resolve().parents[2] / 'data' / 'subagent_schedules.json'


def validate_schedule(expression, timezone, message):
    if not isinstance(expression, str) or len(expression) > 256 or len(expression.split()) != 5:
        raise ValueError("cron_schedule must contain exactly five cron fields")
    if not croniter.is_valid(expression):
        raise ValueError("Invalid cron_schedule")
    if not isinstance(timezone, str) or timezone not in available_timezones():
        raise ValueError("Unknown schedule timezone")
    if not isinstance(message, str) or not message.strip() or len(message) > 16000:
        raise ValueError("wake_message must contain 1 to 16000 characters")
    next_occurrence(expression, timezone, time.time())


def next_occurrence(expression, timezone, now):
    start = datetime.fromtimestamp(now, ZoneInfo(timezone))
    return croniter(expression, start, max_years_between_matches=8).get_next(datetime).timestamp()


class ResumeScheduler:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._loaded = False
        self._rows = {}
        self._pending = {}
        self._thread = None
        self._stop = threading.Event()

    def _load(self):
        if not self._loaded:
            if self.path.exists():
                self._rows = json.loads(self.path.read_text())
            self._loaded = True

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(self._rows, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def create(self, session_id, parent_id, expression, timezone, message, initial_prompt, now=None):
        validate_schedule(expression, timezone, message)
        now = time.time() if now is None else now
        with self._lock:
            self._load()
            if session_id in self._rows:
                raise ValueError("Session already has a recurring schedule")
            self._rows[session_id] = {
                'session_id': session_id,
                'parent_session_id': parent_id,
                'cron_schedule': expression,
                'timezone': timezone,
                'wake_message': message,
                'enabled': True,
                'initial_prompt': initial_prompt,
                'next_run': now,
                'created_at': now,
                'last_run': None,
                'last_result': 'starting',
                'error': None,
            }
            self._save()
            return self.get(session_id)

    def get(self, session_id):
        with self._lock:
            self._load()
            row = self._rows.get(session_id)
            return {key: value for key, value in row.items() if key != 'initial_prompt'} if row else None

    def update(self, session_id, **changes):
        if not changes or set(changes) - {'cron_schedule', 'timezone', 'wake_message'}:
            raise ValueError("Supply cron_schedule, timezone, or wake_message")
        with self._lock:
            self._load()
            row = self._rows.get(session_id)
            if not row or not row['enabled']:
                raise ValueError("No active recurring schedule for this session")
            updated = {**row, **changes}
            validate_schedule(updated['cron_schedule'], updated['timezone'], updated['wake_message'])
            updated['next_run'] = time.time() if updated['initial_prompt'] else next_occurrence(updated['cron_schedule'], updated['timezone'], time.time())
            self._rows[session_id] = updated
            self._pending.pop(session_id, None)
            self._save()
            return self.get(session_id)

    def cancel(self, session_id, reason='cancelled'):
        with self._lock:
            self._load()
            row = self._rows.get(session_id)
            if row:
                row.update(enabled=False, next_run=None, last_result=reason)
                row['initial_prompt'] = None
                self._pending.pop(session_id, None)
                self._save()
            return self.get(session_id)

    def start(self, manager):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._load()
            now = time.time()
            for row in self._rows.values():
                if row['enabled'] and not row['initial_prompt']:
                    row['next_run'] = next_occurrence(row['cron_schedule'], row['timezone'], now)
            self._save()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, args=(manager,), name='subagent-resume-scheduler', daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self, manager):
        while not self._stop.wait(1):
            self.tick(manager)

    def tick(self, manager, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._load()
            for session_id, row in self._rows.items():
                if not row['enabled']:
                    continue
                try:
                    self._tick_session(manager, session_id, row, now)
                except (OSError, RuntimeError, ValueError) as error:
                    logger.exception('Scheduled check failed for %s', session_id)
                    self._fail(row, str(error))

    def _tick_session(self, manager, session_id, row, now):
        if not manager.get_session(session_id):
            self.cancel(session_id, 'session_removed')
            return
        pending = self._pending.get(session_id)
        if pending is None:
            if row['next_run'] > now:
                if not row['initial_prompt'] and row['next_run'] > now + 15:
                    manager.sleep_scheduled_session(session_id, now)
                return
            self._pending[session_id] = now + 120
            row['next_run'] = next_occurrence(row['cron_schedule'], row['timezone'], now)
            self._save()
        elif now >= pending:
            self._fail(row, 'Session did not become ready within 120 seconds')
            return
        initial = row['initial_prompt']
        result = manager.deliver_scheduled_prompt(session_id, initial or row['wake_message'], initial=bool(initial))
        if result == 'waking':
            return
        self._pending.pop(session_id, None)
        if result == 'missing_context':
            self._fail(row, 'Cannot resume session without its original conversation')
            return
        row.update(last_run=now, last_result=result, error=None)
        row['initial_prompt'] = None
        row['next_run'] = next_occurrence(row['cron_schedule'], row['timezone'], now)
        self._save()

    def _fail(self, row, error):
        self._pending.pop(row['session_id'], None)
        row.update(enabled=False, next_run=None, last_result='error', error=error)
        self._save()
        logger.error('Recurring subagent %s: %s', row['session_id'], error)


def read_schedule_snapshot(session_id):
    return ResumeScheduler(SCHEDULES_FILE).get(session_id)


resume_scheduler = ResumeScheduler(SCHEDULES_FILE)
