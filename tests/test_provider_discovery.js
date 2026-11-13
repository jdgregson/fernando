const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/static/js/core.js', 'utf8');
function extract(name) {
    const start = source.indexOf('function ' + name + '(');
    const end = source.indexOf('\n}', start) + 2;
    assert.ok(start >= 0 && end > start);
    return source.slice(start, end);
}

async function main() {
    const calls = [];
    let reloads = 0;
    const selects = ['kiro', 'opencode', 'claude-code'].map(id => ({
        dataset: {providerModel: id, setting: id + '_model'},
        replaceChildren(...children) { this.children = children; },
    }));
    const settings = {kiro: '', opencode: 'providers_opencode', 'claude-code': 'providers_claude_code'};
    const sandbox = vm.createContext({
        window: {FERNANDO_API_KEY: 'fixture'},
        CSS: {escape(value) { return value; }},
        document: {
            getElementById(id) { return selects.find(select => 'provider-model-' + select.dataset.providerModel === id); },
            querySelectorAll() { return selects; },
            querySelector(selector) {
                const id = selector.match(/="([^"]+)"/)[1];
                return {dataset: {setting: settings[id]}};
            },
        },
        Option: function(name, value) { this.text = name; this.value = value; },
        fetch: async (url, options) => {
            calls.push({url, options});
            return {ok: true, json: async () => ({models: [{id: 'model', name: 'Model'}]})};
        },
        emitWithCsrf() {}, showAlert() {}, loadSettings() { reloads++; },
    });
    vm.runInContext(extract('loadProviderModels') + '\n' + extract('saveProviderValue') + '\n' + extract('setProviderEnabled'), sandbox);
    sandbox.loadProviderModels({});
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(calls.map(call => call.url), ['/api/providers/kiro/models']);
    assert.equal(selects[1].disabled, true);
    assert.equal(selects[1].children[0].text, 'Disabled');
    assert.equal(selects[2].disabled, true);
    calls.length = 0;
    sandbox.loadProviderModels({providers_claude_code: true});
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(calls.map(call => call.url), ['/api/providers/kiro/models', '/api/providers/claude-code/models']);
    assert.equal(selects[1].disabled, true);
    assert.equal(selects[2].disabled, false);
    calls.length = 0;
    sandbox.loadProviderModels({providers_opencode: true});
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(calls.map(call => call.url), ['/api/providers/kiro/models', '/api/providers/opencode/models']);
    assert.equal(selects[1].disabled, false);
    sandbox.setProviderEnabled({checked: false, dataset: {providerEnabled: 'opencode', setting: 'providers_opencode'}});
    assert.equal(selects[1].disabled, true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(reloads, 1);
    assert.equal(calls.at(-1).options.headers['X-API-Key'], 'fixture');
    console.log('Disabled providers are not discovered; enable/disable refreshes model controls');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
