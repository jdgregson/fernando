const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function extract(path, name, indent = '') {
    const source = fs.readFileSync(path, 'utf8');
    const start = source.indexOf(indent + 'function ' + name + '(');
    const end = source.indexOf('\n' + indent + '}', start) + indent.length + 2;
    assert.ok(start >= 0 && end > start);
    return source.slice(start, end);
}

async function main() {
    const emitted = [];
    const alerts = [];
    const requests = [];
    const picked = [];
    const tiles = new Map();
    const definitions = [
        {id: 'kiro', name: 'Kiro', model: 'kiro/model', enabled: true},
        {id: 'opencode', name: 'OpenCode', model: 'opencode/model', enabled: false},
        {id: 'claude-code', name: 'Claude Code', model: 'default', enabled: true},
        {id: 'fixture', name: 'Fixture Agent', model: 'fixture/model', enabled: true},
    ];
    for (const provider of definitions) {
        const description = {};
        tiles.set('[data-provider="' + provider.id + '"]', {
            style: {}, description, querySelector() { return description; },
        });
    }
    const sandbox = vm.createContext({
        window: {FERNANDO_API_KEY: 'api-key', _sessionBackend: 'fixture',
            _sessionModel: 'fixture/model', _sessionProvider: {capabilities: {model_switch: true}}},
        document: {querySelector(selector) { return tiles.get(selector); }},
        CSS: {escape(value) { return value; }}, apiKey: 'api-key',
        getNewSessionGroupId() { return 'chosen-group'; }, closeNewSessionModal() {},
        emitWithCsrf(...args) { emitted.push(args); }, showAlert(message) { alerts.push(message); },
        showModelModal(...args) { picked.push(args); },
        fetch(url, options) {
            requests.push({url, options});
            return Promise.resolve({ok: true, json: () => Promise.resolve({models: [{id: 'fixture/model', name: 'Fixture Model'}]})});
        },
    });
    vm.runInContext([
        extract('src/static/js/core.js', 'applyProviderSettings'),
        extract('src/static/js/core.js', 'saveProviderValue'),
        extract('src/static/js/chat.js', 'createChatSession'),
        extract('src/templates/chat.html', 'loadProviderModelPicker', '    '),
    ].join('\n'), sandbox);
    sandbox.applyProviderSettings({providers: definitions});
    assert.equal(tiles.get('[data-provider="fixture"]').description.textContent, 'Graphical chat UI (fixture/model)');
    assert.equal(tiles.get('[data-provider="opencode"]').style.display, 'none');
    assert.equal(tiles.get('[data-provider="claude-code"]').description.textContent, 'Graphical chat UI (default)');
    assert.equal(tiles.get('[data-provider="claude-code"]').style.display, '');
    sandbox.createChatSession('fixture');
    assert.equal(emitted[0][0], 'acp_create');
    assert.equal(emitted[0][1].backend, 'fixture');
    assert.equal(emitted[0][1].group_id, 'chosen-group');
    sandbox.saveProviderValue('fixture_model', 'fixture/other');
    sandbox.loadProviderModelPicker();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests[0].options.headers['X-API-Key'], 'api-key');
    assert.deepEqual(JSON.parse(requests[0].options.body), {key: 'fixture_model', value: 'fixture/other'});
    assert.equal(requests[1].url, '/api/providers/fixture/models');
    assert.equal(requests[1].options.headers['X-API-Key'], 'api-key');
    assert.equal(picked[0][0][0], 'fixture/model');
    sandbox.window._sessionProvider.capabilities.model_switch = false;
    sandbox.loadProviderModelPicker();
    assert.equal(requests.length, 2);
    assert.equal(alerts.length, 1);
    console.log('Provider-driven cards, settings, session creation, model picker, and capability guards passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
