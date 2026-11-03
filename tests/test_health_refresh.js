const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

test('log tabs fetch only selected sources and stop when closed', async t => {
    const dom = new JSDOM(fs.readFileSync('src/templates/index.html', 'utf8'), {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const win = dom.window;
    const requests = [];
    win.fetch = async url => {
        requests.push(url);
        const params = new URL(url, 'http://localhost').searchParams;
        return {ok: true, json: async () => params.has('list') ? {sources: [{id: 'abc', label: 'fernando-desktop'}]} : {records: [{text: params.get('source') + ' output'}]}};
    };
    const source = fs.readFileSync('src/static/js/core.js', 'utf8');
    const context = dom.getInternalVMContext();
    vm.runInContext(source.slice(source.indexOf('function updateTabsOverflow('), source.indexOf('function initSettingsTabsOverflow(')), context);
    vm.runInContext('let healthModalOpen = true;\n' + source.slice(source.indexOf('const healthLogSources'), source.indexOf('const defenderLayoutObserver')), context);
    await win.activateHealthLog();
    assert.equal(win.document.querySelector('#healthLogTabs .sa-tab.active').dataset.source, 'flask');
    assert.deepEqual(requests, ['/api/health/logs?source=flask&target=']);
    vm.runInContext("healthLogSource = 'nginx-error'", context);
    await win.activateHealthLog();
    assert.equal(requests.at(-1), '/api/health/logs?source=nginx-error&target=');
    assert.equal(win.document.querySelector('#healthLogTabs .sa-tab.active').dataset.source, 'nginx-error');
    const healthStart = source.indexOf('function updateDefenderHealth(');
    vm.runInContext(source.slice(healthStart, source.indexOf('// Load health thresholds', healthStart)), context);
    win.updateHealthModal({backend_errors: {recent_count: 2}});
    const errorTab = win.document.querySelector('[data-source="errors"]');
    assert.equal(errorTab.textContent, 'Errors (2)');
    assert.equal(errorTab.classList.contains('has-recent-errors'), true);
    win.updateHealthModal({backend_errors: {recent_count: 0}});
    assert.equal(errorTab.textContent, 'Errors');
    assert.equal(errorTab.classList.contains('has-recent-errors'), false);
    vm.runInContext("healthLogSource = 'docker'", context);
    await win.activateHealthLog();
    assert.deepEqual(requests.slice(-2), ['/api/health/logs?source=docker&list=1', '/api/health/logs?source=docker&target=abc']);
    assert.equal(win.document.getElementById('healthLogs').textContent, 'docker output');
    const viewport = win.document.getElementById('healthLogs').parentElement;
    Object.defineProperty(viewport, 'scrollHeight', {value: 1000});
    Object.defineProperty(viewport, 'clientHeight', {value: 100});
    viewport.scrollTop = 100;
    const count = requests.length;
    await vm.runInContext('pollHealthLog(healthLogGeneration, healthLogController.signal)', context);
    assert.equal(requests.length, count);
    vm.runInContext('healthModalOpen = false; stopHealthLog()', context);
    await vm.runInContext('pollHealthLog(healthLogGeneration, healthLogController.signal)', context);
    assert.equal(requests.length, count);
});

test('health logs render ANSI colors and resets without interpreting log HTML', t => {
    const dom = new JSDOM('<pre></pre>', {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const source = fs.readFileSync('src/static/js/core.js', 'utf8');
    vm.runInContext(source.slice(source.indexOf('function highlightHealthLogText('), source.indexOf('const defenderLayoutObserver')), dom.getInternalVMContext());
    const element = dom.window.document.querySelector('pre');
    dom.window.renderHealthLogs(element, [{level: 'INFO', timestamp: 'today', logger: 'werkzeug', message: '"\x1b[31m\x1b[1mGET <img src=x>\x1b[0m" 403'}]);
    assert.equal(element.textContent, 'today werkzeug INFO "GET <img src=x>" 403');
    assert.equal(element.querySelector('img'), null);
    const spans = element.querySelector('.health-log-message').children;
    assert.equal(spans[1].style.color, 'rgb(224, 108, 117)');
    assert.equal(spans[1].style.fontWeight, 'bold');
    assert.equal(spans[2].style.color, '');
    assert.equal(spans[2].style.fontWeight, '');
    dom.window.renderHealthLogs(element, [{text: '\x1b[38;2;12;34;56mRGB\x1b[0m plain'}]);
    assert.equal(element.firstChild.style.color, 'rgb(12, 34, 56)');
    assert.equal(element.textContent, 'RGB plain');
    const lines = [
        '127.0.0.1 - - [31/Oct/2026:14:44:29 +0000] "GET /test HTTP/1.1" 403 12',
        '2026/10/31 14:44:29 [error] failed to connect',
        '2026-10-31T14:44:29+00:00 host systemd: Failed to start service',
        '[INFO] Restart complete',
        '2026-10-31T14:44:29.123Z WARNING Docker message',
        '[W 2026-10-31 14:44:29.123 ServerApp] https://localhost/test'
    ];
    for (const text of lines) {
        dom.window.renderHealthLogs(element, [{text}]);
        assert.equal(element.textContent, text);
        assert.ok(element.querySelector('[class^="health-token-"]'), text);
    }
    dom.window.renderHealthLogs(element, [{text: lines[0]}]);
    assert.equal(element.querySelector('.health-token-warning').textContent, '403');
});

test('health refresh retains Defender tables, rows, and selected unchanged values', t => {
    const dom = new JSDOM(fs.readFileSync('src/templates/index.html', 'utf8'), {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const source = fs.readFileSync('src/static/js/core.js', 'utf8');
    vm.runInContext(source.slice(source.indexOf('function updateDefenderHealth('), source.indexOf('function updateHealthModal(')), dom.getInternalVMContext());
    const win = dom.window;
    const data = {report: {healthy: true, definitionsVersion: '123', definitionsUpdatedMinutesAgo: 1}, matches: ['definitionsVersion']};
    win.updateDefenderHealth(data);
    const details = win.document.getElementById('healthDefenderDetails');
    const nodes = [...details.querySelectorAll('*')];
    const version = details.querySelector('[data-field="definitionsVersion"]');
    const text = version.firstChild;
    const range = win.document.createRange();
    range.selectNodeContents(text);
    win.getSelection().addRange(range);
    for (let age = 2; age < 20; age++) {
        win.updateDefenderHealth({...data, report: {...data.report, definitionsUpdatedMinutesAgo: age}});
        assert.deepEqual([...details.querySelectorAll('*')], nodes);
        assert.equal(version.firstChild, text);
        assert.equal(win.getSelection().toString(), '123');
        assert.equal(details.querySelector('[data-field="definitionsUpdatedMinutesAgo"]').textContent, age + ' min');
    }
    win.updateDefenderHealth({...data, matches: []});
    assert.equal(version.className, '');
    win.updateDefenderHealth({error: 'Unavailable'});
    assert.equal(details.hidden, true);
    win.updateDefenderHealth(data);
    assert.equal(details.hidden, false);
    assert.deepEqual([...details.querySelectorAll('*')], nodes);
});
