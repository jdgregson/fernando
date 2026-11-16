const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

function workspace() {
    const dom = new JSDOM(fs.readFileSync('src/templates/index.html', 'utf8'), {url: 'https://fernando.test/', runScripts: 'outside-only', pretendToBeVisual: true});
    const win = dom.window;
    const context = dom.getInternalVMContext();
    const emitted = [];
    const handlers = new Map();
    win.socket = {on(name, handler) { if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(handler); }, connected: true};
    const requests = [];
    const jobs = new Map();
    win.fetch = async (path, options = {}) => {
        const data = options.body ? JSON.parse(options.body) : undefined;
        requests.push({path, data});
        if (path === '/api/jobs') return {ok: true, json: async () => ({jobs: [...jobs.values()]})};
        const id = path === '/api/jobs/drafts' ? 'a'.repeat(32) : path.split('/')[3];
        const job = jobs.get(id) || {id, name: 'New Job', state: 'draft', paused: 1, view_open: 1, session_revision: 0, group_id: data?.group_id || null};
        if (data?.action === 'close') job.view_open = 0;
        if (data?.action === 'open') job.view_open = 1;
        if (data?.action === 'rename') job.name = data.name;
        job.session_revision++;
        jobs.set(id, job);
        return {ok: true, json: async () => ({...job})};
    };
    win.showAlert = message => { throw new Error(message); };
    win.emitWithCsrf = (name, data) => emitted.push({name, data});
    win.doFit = win.scrollBy = win.applyProviderSettings = () => {};
    win.FERNANDO_API_KEY = 'fixture';
    win._paneSession = {1: null, 2: null};
    win.term1 = win.term2 = {focus() {}};
    for (const name of ['modal', 'context', 'panes', 'session-adapters', 'job-session', 'sessions', 'chat', 'mobile']) vm.runInContext(fs.readFileSync('src/static/js/' + name + '.js', 'utf8'), context);
    const controller = vm.runInContext('paneController', context);
    win._urlParamsProcessed = true;
    return {win, dom, context, controller, emitted, requests, jobs, handlers};
}

test('job session reuses pane registration, sidebar, group selection and URL serialization', async t => {
    const w = workspace();
    t.after(() => w.win.close());
    vm.runInContext("newSessionTargetGroupId = 'selected-group'", w.context);
    await w.win.createJobSession();
    await w.win.createNewJob();
    const frame = w.controller.elements(1).browser.querySelector('iframe');
    assert.equal(new URL(frame.src).pathname, '/jobs/' + 'a'.repeat(32));
    assert.equal(w.requests.find(item => item.path === '/api/jobs/drafts').data.group_id, 'selected-group');
    const id = 'a'.repeat(32);
    w.win.dispatchEvent(new w.win.MessageEvent('message', {source: frame.contentWindow, origin: w.win.location.origin, data: {type: 'job-created', id}}));
    assert.equal(w.controller.get(1).contentKey, 'job:' + id);
    assert.equal(new URL(w.win.location.href).searchParams.get('session'), 'job:' + id);
    assert.ok(w.emitted.some(item => item.name === 'get_sessions'));
    w.win.updateSessionList([], [], {job_sessions: [{id, name: '<literal job>', state: 'running', view_open: 1, session_revision: 99}]});
    const item = w.win.document.querySelector('[data-session="job:' + id + '"]');
    assert.equal(item.querySelector('.session-name').textContent, '<literal job>');
    assert.equal(item.querySelector('literal'), null);
});

test('Job re-selection and duplicate views use shared panes without ACP lifecycle actions', async t => {
    const w = workspace();
    t.after(() => w.win.close());
    const key = 'job:' + 'b'.repeat(32);
    await w.controller.open(key);
    const first = w.controller.elements(1).browser.querySelector('iframe');
    first.contentDocument.open();
    first.contentDocument.write('<!doctype html><body>Preserve this view</body>');
    first.contentDocument.close();
    await w.controller.open(key);
    assert.equal(w.controller.elements(1).browser.querySelector('iframe'), first);
    assert.equal(first.contentDocument.body.textContent, 'Preserve this view');
    vm.runInContext('isSplit = true; activeTerminal = 2', w.context);
    await w.controller.open(key);
    const second = w.controller.elements(2).browser.querySelector('iframe');
    assert.notEqual(second, first);
    let saved = 0;
    first.contentWindow.saveJobDraft = second.contentWindow.saveJobDraft = async () => { saved++; };
    await w.controller.sessionTypes.get('job').close(key);
    assert.equal(saved, 2);
    assert.equal(w.controller.get(1).surface, 'terminal');
    assert.equal(w.controller.get(2).surface, 'terminal');
    assert.ok(w.requests.some(item => item.data?.action === 'close'));
    assert.ok(!w.requests.some(item => ['archive', 'sleep', 'wake', 'fork'].includes(item.data?.action)));
    const descriptor = w.controller.sessionTypes.get('job').sidebar(w.jobs.get('b'.repeat(32)));
    assert.equal(descriptor.sleep, undefined);
    assert.equal(descriptor.clone, undefined);
    assert.equal(w.controller.sessionTypes.get('job').closeLabel, 'Close');
});

test('leaving a Job waits for its draft save and retains the view on failure', async t => {
    const w = workspace();
    t.after(() => w.win.close());
    const key = 'job:' + 'c'.repeat(32);
    await w.controller.open(key);
    const frame = w.controller.elements(1).browser.querySelector('iframe');
    let finish;
    frame.contentWindow.saveJobDraft = () => new Promise(resolve => { finish = resolve; });
    const switching = w.controller.open('job:new');
    assert.equal(w.controller.get(1).contentKey, key);
    finish();
    await switching;
    assert.equal(w.controller.get(1).contentKey, 'job:new');
    await w.controller.open(key);
    const current = w.controller.elements(1).browser.querySelector('iframe');
    current.contentWindow.saveJobDraft = async () => { throw new Error('Save failed'); };
    const errors = [];
    w.win.showAlert = message => errors.push(message);
    assert.equal(await w.controller.open('job:new'), false);
    assert.equal(w.controller.get(1).contentKey, key);
    assert.deepEqual(errors, ['Save failed']);
});

test('Job metadata updates preserve document state and update shared context', async t => {
    const w = workspace();
    t.after(() => w.win.close());
    const id = 'd'.repeat(32);
    await w.controller.open('job:' + id);
    const frame = w.controller.elements(1).browser.querySelector('iframe');
    await frame.jobOpening;
    for (const handler of w.handlers.get('job_changed')) handler({jobs: [{id, name: 'Renamed live', state: 'running', paused: 0, view_open: 1, session_revision: 100}]});
    assert.equal(w.controller.elements(1).browser.querySelector('iframe'), frame);
    assert.equal(w.controller.context(1).description, 'Renamed live (running)');
    for (const handler of w.handlers.get('job_changed')) handler({jobs: [{id, name: 'Stale name', state: 'draft', paused: 1, view_open: 0, session_revision: 1}]});
    assert.equal(w.controller.get(1).contentKey, 'job:' + id);
    for (const handler of w.handlers.get('job_changed')) handler({jobs: [{id, name: 'Renamed live', state: 'running', paused: 0, view_open: 0, session_revision: 101}]});
    assert.equal(w.controller.get(1).surface, 'terminal');
});

test('split Job restoration honors the saved active pane', async t => {
    const w = workspace();
    t.after(() => w.win.close());
    const first = 'job:' + 'a'.repeat(32);
    const second = 'job:' + 'b'.repeat(32);
    w.win.history.replaceState(null, '', '/?session=' + first + '&session2=' + second + '&split=1&active=1');
    w.win._urlParamsProcessed = false;
    w.controller.restore([]);
    assert.equal(w.controller.get(1).contentKey, first);
    assert.equal(w.controller.get(2).contentKey, second);
    assert.equal(vm.runInContext('activeTerminal', w.context), 1);
    await Promise.all([1, 2].map(pane => w.controller.elements(pane).browser.querySelector('iframe').jobOpening));
});

test('sidebar updates retain an in-progress rename and Escape applies the latest inventory', t => {
    const w = workspace();
    t.after(() => w.win.close());
    const id = 'e'.repeat(32);
    w.win.updateSessionList([], [], {job_sessions: [{id, name: 'Original', state: 'draft', view_open: 1, session_revision: 1}]});
    const item = w.win.document.querySelector('[data-session="job:' + id + '"]');
    item.dispatchEvent(new w.win.MouseEvent('contextmenu', {bubbles: true, clientX: 20, clientY: 20}));
    [...w.win.document.querySelectorAll('.context-menu-item')].find(element => element.textContent === 'Rename').click();
    const input = w.win.document.querySelector('.session-rename-input');
    input.value = 'Uncommitted local edit';
    w.win.updateSessionList([], [], {job_sessions: [{id, name: 'Remote update', state: 'draft', view_open: 1, session_revision: 2}]});
    assert.equal(w.win.document.querySelector('.session-rename-input'), input);
    assert.equal(input.value, 'Uncommitted local edit');
    input.dispatchEvent(new w.win.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    assert.equal(w.win.document.querySelector('[data-session="job:' + id + '"] .session-name').textContent, 'Remote update');
    assert.ok(!w.requests.some(request => request.data?.action === 'rename'));
});

test('job agents are contiguous beneath their collapsible owner', t => {
    const w = workspace();
    t.after(() => w.win.close());
    const id = 'f'.repeat(32);
    const chats = [{id: '11111111', name: 'worker', job_id: id, job_role: 'worker', loaded: false}, {id: '22222222', name: 'ordinary', loaded: false}, {id: '33333333', name: 'reviewer', job_id: id, job_role: 'reviewer', loaded: false}];
    w.win.updateSessionList([], chats, {job_sessions: [{id, name: 'Job', state: 'running', view_open: 1, session_revision: 1}]});
    const keys = () => [...w.win.document.querySelectorAll('#sessionList [data-session]')].map(element => element.dataset.session);
    const before = keys();
    assert.deepEqual(before.slice(before.indexOf('job:' + id), before.indexOf('job:' + id) + 3), ['job:' + id, 'chat:11111111', 'chat:33333333']);
    assert.equal(w.win.document.querySelectorAll('.job-agent-row').length, 2);
    w.win.document.querySelector('[data-session="job:' + id + '"] .parent-chevron').click();
    assert.ok(!keys().includes('chat:11111111'));
    assert.ok(keys().includes('chat:22222222'));
    w.win.document.querySelector('[data-session="job:' + id + '"] .parent-chevron').click();
    assert.ok(keys().includes('chat:33333333'));
});

test('archived subagents default hidden but checkbox and matching searches reveal them', t => {
    const w = workspace();
    t.after(() => w.win.close());
    w.win.switchToArchived();
    for (const handler of w.handlers.get('acp_archived_list')) handler({sessions: [
        {id: '11111111', name: 'Independent chat', is_subagent: false},
        {id: '22222222', name: 'Job worker', job_id: 'a'.repeat(32), is_subagent: true},
        {id: '33333333', name: 'General helper', is_subagent: true},
        {id: '44444444', name: 'Independent fork', session_kind: 'fork', is_subagent: false},
    ]});
    const visible = id => w.win.document.querySelector('[data-session="chat:' + id + '"]').style.display !== 'none';
    assert.equal(visible('11111111'), true);
    assert.equal(visible('22222222'), false);
    assert.equal(visible('33333333'), false);
    assert.equal(visible('44444444'), true);
    w.win.document.getElementById('includeArchivedSubagents').checked = true;
    w.win.filterArchivedEntries('');
    assert.equal(visible('22222222'), true);
    w.win.document.getElementById('includeArchivedSubagents').checked = false;
    w.win.document.getElementById('archiveSearch').value = 'hidden evidence';
    w.win.filterArchived();
    const requestId = vm.runInContext('conversationSearchRequest', w.context);
    for (const handler of w.handlers.get('acp_conversation_search_results')) handler({scope: 'archived', query: 'hidden evidence', request_id: requestId, sessions: [{id: '22222222', snippet: 'hidden evidence', match: 'Tool content'}]});
    assert.equal(visible('22222222'), true);
    assert.equal(visible('33333333'), false);
    w.win.clearConversationSearch();
    assert.equal(visible('22222222'), false);
    w.win.switchToActive();
    assert.equal(w.win.document.getElementById('archiveSubagentFilter').hidden, true);
});

test('job bridge rejects wrong windows and origins and opens agents in its owning pane', async t => {
    const w = workspace();
    t.after(() => w.win.close());
    await w.controller.open('job:new');
    const frame = w.controller.elements(1).browser.querySelector('iframe');
    const message = (source, origin, data) => w.win.dispatchEvent(new w.win.MessageEvent('message', {source, origin, data}));
    message(w.win, w.win.location.origin, {type: 'job-created', id: 'a'.repeat(32)});
    message(frame.contentWindow, 'https://wrong.test', {type: 'job-created', id: 'a'.repeat(32)});
    assert.equal(w.controller.get(1).contentKey, 'job:new');
    const opened = [];
    w.controller.open = key => opened.push(key);
    vm.runInContext('activeTerminal = 2', w.context);
    message(frame.contentWindow, w.win.location.origin, {type: 'job-open-chat', id: '1234abcd'});
    assert.deepEqual(opened, ['chat:1234abcd']);
    assert.equal(vm.runInContext('activeTerminal', w.context), 1);
});

test('clicking inside the actual Job document activates its pane through the shared bridge', async t => {
    const w = workspace();
    t.after(() => w.win.close());
    w.controller.installBridge();
    vm.runInContext('isSplit = true', w.context);
    await w.controller.open('job:new');
    const frame = w.controller.elements(1).browser.querySelector('iframe');
    const child = frame.contentWindow;
    const doc = child.document;
    doc.open();
    doc.write(fs.readFileSync('src/templates/jobs.html', 'utf8'));
    doc.close();
    const bridge = doc.querySelector('script[src="/static/js/pane-bridge.js"]');
    assert.ok(bridge, 'Every embedded Job page must load the shared pane bridge');
    Object.defineProperty(doc, 'currentScript', {configurable: true, get: () => bridge});
    w.win.postMessage = (data, origin) => w.win.dispatchEvent(new w.win.MessageEvent('message', {source: child, origin, data}));
    child.postMessage = (data, origin) => child.dispatchEvent(new child.MessageEvent('message', {source: w.win, origin, data}));
    child.eval(fs.readFileSync('src/static/js/pane-bridge.js', 'utf8'));
    assert.ok(doc.querySelector('link[href$="group-selection.css"]'));
    for (const selector of ['#jobName', '#jobScope', '#jobChooseDocument', 'main']) {
        vm.runInContext('activeTerminal = 2', w.context);
        doc.querySelector(selector).dispatchEvent(new child.MouseEvent('click', {bubbles: true}));
        assert.equal(vm.runInContext('activeTerminal', w.context), 1, selector);
    }
    w.controller.post(1, 'state', {active: true, group: {color: '#b8860b'}});
    assert.equal(doc.documentElement.style.getPropertyValue('--group-selection-background'), '#b8860b');
    assert.ok(doc.documentElement.hasAttribute('data-group-selection'));
});
