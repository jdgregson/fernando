import hashlib
import json
import time

from src.services import groups
from src.services.jobs import controller, identifier, store, text


class JobSessions:
    def __init__(self, ledger, workflow):
        self.store = ledger
        self.controller = workflow

    def validate_group(self, group_id):
        if group_id and not any(group['id'] == group_id for group in groups.list_groups()):
            raise ValueError('Target group no longer exists or is archived')

    def create(self, **values):
        with self.controller.lock:
            self.validate_group(values.get('group_id'))
            job = self.store.create(**values)
            groups.move_session_to_group('job:' + job['id'], job['group_id'])
            return job

    def create_draft(self, worker, reviewer, group_id=None):
        with self.controller.lock:
            self.validate_group(group_id)
            jid = identifier()
            draft = {'name': '', 'scope': '', 'source_id': None, 'source_name': '', 'worker': worker, 'reviewer': reviewer}
            with self.store.connect() as db:
                now = time.time()
                db.execute("INSERT INTO jobs(id,name,scope,source_name,source_sha256,worker,reviewer,group_id,state,paused,draft,created,updated) VALUES(?,'New Job','','Draft',?,?,?,?,'draft',1,?,?,?)",
                           (jid, hashlib.sha256(b'').hexdigest(), json.dumps(worker), json.dumps(reviewer), group_id, json.dumps(draft), now, now))
                self.store.event(db, jid, 'draft_created', {})
            groups.move_session_to_group('job:' + jid, group_id)
            return self.store.get(jid)

    def save_draft(self, jid, values, revision, submit=False):
        with self.controller.lock:
            with self.store.connect() as db:
                job = self.store.snapshot(db, jid)
                if job['state'] != 'draft':
                    raise ValueError('This Job is not an editable draft')
                if type(revision) is not int or revision != job['draft_revision']:
                    raise ValueError('This draft changed in another view. Your edits are still in this page; reload to use the saved version.')
                if not isinstance(values.get('name'), str) or len(values['name']) > 120 or not isinstance(values.get('scope'), str) or len(values['scope']) > 200000:
                    raise ValueError('Draft name or scope exceeds its limit')
                source_id = values.get('source_id')
                source = None
                if source_id:
                    source = db.execute('SELECT * FROM job_sources WHERE id=? AND (job_id IS NULL OR job_id=?)', (source_id, jid)).fetchone()
                    if not source:
                        raise ValueError('Uploaded source is not available to this draft')
                    db.execute('DELETE FROM job_sources WHERE job_id=? AND id!=?', (jid, source_id))
                    db.execute('UPDATE job_sources SET job_id=? WHERE id=?', (jid, source_id))
                values = {key: values.get(key) for key in ('name', 'scope', 'source_id', 'source_name', 'worker', 'reviewer')}
                db.execute('UPDATE jobs SET name=?,draft=?,draft_revision=draft_revision+1,worker=?,reviewer=? WHERE id=?',
                           (values['name'].strip() or 'New Job', json.dumps(values), json.dumps(values['worker']), json.dumps(values['reviewer']), jid))
                if submit:
                    scope = text(values['scope'], 200000)
                    db.execute("UPDATE jobs SET name=?,scope=?,source_name=?,source_sha256=?,state='planning',paused=0 WHERE id=?",
                               (text(values['name'], 120), scope, source['filename'] if source else 'Pasted description', source['sha256'] if source else hashlib.sha256(scope.encode()).hexdigest(), jid))
                self.store.event(db, jid, 'draft_submitted' if submit else 'draft_saved', {})
            return self.store.get(jid)

    def move(self, jid, group_id):
        with self.controller.lock:
            self.validate_group(group_id)
            self.store.get(jid)
            groups.move_session_to_group('job:' + jid, group_id)
            with self.store.connect() as db:
                db.execute('UPDATE jobs SET group_id=? WHERE id=?', (group_id, jid))
                self.store.event(db, jid, 'group_changed', {'group_id': group_id})

    def synchronize_groups(self):
        with self.controller.lock:
            membership = groups.get_session_groups()
            archived = groups.list_groups(archived=True)
            known = {group['id'] for group in groups.list_groups() + archived}
            previous = self.store.suspended_groups
            self.controller.suspended_groups = {group['id'] for group in archived}
            self.store.suspended_groups = self.controller.suspended_groups
            for job in self.store.list():
                group_id = membership.get('job:' + job['id'])
                if group_id not in known:
                    group_id = None
                if job['group_id'] != group_id:
                    with self.store.connect() as db:
                        db.execute('UPDATE jobs SET group_id=? WHERE id=?', (group_id, job['id']))
                        self.store.event(db, job['id'], 'group_changed', {'group_id': group_id})
                elif (group_id in previous) != (group_id in self.store.suspended_groups):
                    with self.store.connect() as db:
                        self.store.event(db, job['id'], 'group_dispatch_suspended', {'suspended': group_id in self.store.suspended_groups})

    def perform(self, jid, action, data):
        with self.controller.lock:
            job = self.store.get(jid)
            if action not in ('rename', 'open', 'close'):
                return self.store.operator(jid, action, data)
            with self.store.connect() as db:
                if action == 'rename':
                    name = text(data.get('name'), 120)
                    db.execute('UPDATE jobs SET name=? WHERE id=?', (name, jid))
                    if job['state'] == 'draft':
                        job['draft']['name'] = name
                        db.execute('UPDATE jobs SET draft=?,draft_revision=draft_revision+1 WHERE id=?', (json.dumps(job['draft']), jid))
                    self.store.event(db, jid, 'renamed', {'name': name})
                elif bool(job['view_open']) != (action == 'open'):
                    db.execute('UPDATE jobs SET view_open=? WHERE id=?', (int(action == 'open'), jid))
                    self.store.event(db, jid, 'view_' + action, {})
            return self.store.get(jid)


session_service = JobSessions(store, controller)
