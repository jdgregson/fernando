const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function main() {
    const elements = new Map();
    for (const name of ['retry', 'error', 'log', 'heading', 'message', 'phase']) {
        elements.set(name, {hidden: false, textContent: '', scrollTop: 0, scrollHeight: 100, clientHeight: 100});
    }
    const requests = [];
    let reloads = 0;
    let responseState = {phase: 'queued', message: 'Queued', ready: false};
    const sandbox = vm.createContext({
        document: {getElementById: id => elements.get(id)},
        window: {location: {reload() { reloads++; }}, addEventListener() {}},
        setTimeout() { return 1; }, clearTimeout() {}, Date,
        fetch: async (url, options) => {
            requests.push({url, options});
            return {ok: true, json: async () => responseState};
        },
    });
    const html = fs.readFileSync('src/templates/desktop_waiting.html', 'utf8');
    const script = html.match(/<script>([\s\S]*?)<\/script>/)[1]
        .replace('{{ api_key | tojson }}', JSON.stringify('fixture-key'))
        .replace('{{ desktop_status | tojson }}', JSON.stringify({phase: 'building', message: 'Building', ready: false, log: '<img src=x onerror=alert(1)>'}));
    vm.runInContext(script, sandbox);
    assert.equal(elements.get('heading').textContent, 'Desktop updating');
    assert.equal(elements.get('log').textContent, '<img src=x onerror=alert(1)>');
    assert.equal(elements.get('log').innerHTML, undefined);
    assert.equal(elements.get('retry').hidden, true);
    sandbox.render({phase: 'failed', message: 'Build failed', log: 'failure output', ready: false});
    assert.equal(elements.get('retry').hidden, false);
    assert.equal(elements.get('heading').textContent, 'Desktop update failed');
    await elements.get('retry').onclick();
    assert.equal(requests[0].url, '/api/desktop/update');
    assert.equal(requests[0].options.method, 'POST');
    assert.equal(requests[0].options.headers['X-API-Key'], 'fixture-key');
    assert.equal(elements.get('retry').hidden, true);
    responseState = {phase: 'ready', ready: true};
    await sandbox.poll();
    assert.equal(requests[1].url, '/api/desktop/status');
    assert.equal(requests[1].options.headers['X-API-Key'], 'fixture-key');
    assert.equal(reloads, 1);

    const source = fs.readFileSync('src/static/js/sessions.js', 'utf8');
    const start = source.indexOf('function monitorDesktopUpdate()');
    const end = source.indexOf('\n}', start) + 2;
    const frames = [
        {src: 'https://fernando.example/kasm/', dataset: {}, contentDocument: {getElementById() { return null; }}},
        {src: 'https://fernando.example/notes/default/'},
        {src: 'https://other.example/kasm/'},
    ];
    const refreshes = [0, 0, 0];
    frames.forEach((frame, index) => {
        let src = frame.src;
        Object.defineProperty(frame, 'src', {get() { return src; }, set(value) { refreshes[index]++; src = value; }});
    });
    let callback;
    const monitor = vm.createContext({
        desktopUpdateTimer: null,
        window: {location: {origin: 'https://fernando.example'}, FERNANDO_API_KEY: 'fixture-key'},
        document: {querySelectorAll() { return frames; }},
        setTimeout(fn) { callback = fn; return 1; },
        fetch: async () => ({ok: true, json: async () => ({ready: false})}),
        console,
    });
    vm.runInContext(source.slice(start, end), monitor);
    monitor.monitorDesktopUpdate();
    await callback();
    assert.deepEqual(refreshes, [1, 0, 0]);
    await callback();
    assert.deepEqual(refreshes, [1, 0, 0]);
    console.log('Desktop progress, safe logs, authenticated retry, ready transition, and desktop-only refresh passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
