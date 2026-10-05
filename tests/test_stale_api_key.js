const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../src/static/js/core.js'), 'utf8');
const socketSource = source.slice(source.indexOf('let csrfToken ='), source.indexOf('function openSettings()'));
const healthSource = source.slice(source.indexOf('function fetchHealth()'), source.indexOf('function updateHealthIndicator('));

function workspace() {
    const events = {};
    const listeners = {};
    const requests = [];
    const timers = [];
    const intervals = [];
    const elements = new Map();
    const state = {reloads: 0, updates: 0, animationStarts: 0, animationStops: 0};
    const context = vm.createContext({
        window: {FERNANDO_API_KEY: 'stale-key', location: {reload: () => state.reloads++}},
        document: {
            hidden: false,
            addEventListener: (event, callback) => { listeners[event] = callback; },
            getElementById: id => {
                if (!elements.has(id)) elements.set(id, {style: {}, classList: {add() {}, remove() {}}, querySelector: () => null});
                return elements.get(id);
            }
        },
        socket: {connected: false, on: (event, callback) => { events[event] = callback; }, io: {on() {}}},
        fetch: (url, options) => new Promise(resolve => requests.push({url, options, resolve})),
        setTimeout: callback => timers.push(callback),
        setInterval: callback => intervals.push(callback),
        clearInterval() {},
        startMutateHelix: () => state.animationStarts++,
        stopMutateHelix: () => state.animationStops++,
        healthModalOpen: false,
        updateHealthIndicator: () => state.updates++,
        console
    });
    vm.runInContext(socketSource + healthSource, context);
    return {context, events, listeners, requests, timers, intervals, state};
}

async function respond(request, status) {
    request.resolve({status, ok: status === 200, json: async () => ({status: 'healthy'})});
    await new Promise(resolve => setImmediate(resolve));
}

test('health 401 reloads a stale tab without rendering unauthorized data', async () => {
    const w = workspace();
    vm.runInContext('fetchHealth()', w.context);
    await respond(w.requests[0], 401);
    assert.equal(w.state.reloads, 1);
    assert.equal(w.state.updates, 0);
});

test('successful health polling still updates the indicator', async () => {
    const w = workspace();
    vm.runInContext('fetchHealth()', w.context);
    await respond(w.requests[0], 200);
    assert.equal(w.state.reloads, 0);
    assert.equal(w.state.updates, 1);
});

test('returning to a disconnected tab checks the configured API key and reloads on 401', async () => {
    const w = workspace();
    w.listeners.visibilitychange();
    assert.equal(w.requests[0].options.headers['X-API-Key'], 'stale-key');
    await respond(w.requests[0], 401);
    assert.equal(w.state.reloads, 1);
});

test('mutation beginning with requests in flight suppresses their reloads and preserves animation polling', async () => {
    const w = workspace();
    w.listeners.visibilitychange();
    vm.runInContext('fetchHealth()', w.context);
    w.events.mutating();
    w.events.disconnect();
    w.timers[0]();
    assert.equal(w.state.animationStarts, 1);
    await respond(w.requests[0], 401);
    await respond(w.requests[1], 401);
    assert.equal(w.state.reloads, 0);
    assert.equal(w.state.animationStops, 0);
    w.listeners.visibilitychange();
    assert.equal(w.requests.length, 2);
    vm.runInContext('fetchHealth()', w.context);
    await respond(w.requests[2], 401);
    assert.equal(w.state.reloads, 0);
    w.intervals[0]();
    assert.equal(w.requests[3].url, '/');
    assert.equal(w.requests[3].options.method, 'HEAD');
    await respond(w.requests[3], 200);
    assert.equal(w.state.animationStops, 1);
    assert.equal(w.state.reloads, 1);
});
