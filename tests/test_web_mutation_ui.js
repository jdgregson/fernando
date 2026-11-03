const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');

test('web mutate animates without disconnect and reloads only when ready', () => {
    const source = fs.readFileSync('src/static/js/core.js', 'utf8');
    const start = source.indexOf("socket.on('web_mutating'");
    const end = source.indexOf("socket.on('connected'", start);
    const dom = new JSDOM('<div id="mutateOverlay"></div><div id="overlaySpinner"></div><div id="mutateHelix"></div><div id="overlayLabel"></div>');
    let handler;
    let reloads = 0;
    let animations = 0;
    const context = vm.createContext({document: dom.window.document, window: {location: {reload: () => reloads++}}, socket: {on: (name, callback) => {handler = callback;}}, startMutateHelix: () => animations++, stopMutateHelix: () => animations--});
    vm.runInContext('let isMutating = false;\n' + source.slice(start, end), context);
    handler({phase: 'starting'});
    assert.equal(animations, 1);
    assert.equal(reloads, 0);
    assert.equal(dom.window.document.getElementById('mutateOverlay').classList.contains('open'), true);
    handler({phase: 'ready'});
    assert.equal(reloads, 1);
    handler({phase: 'failed'});
    assert.equal(reloads, 1);
    assert.equal(vm.runInContext('isMutating', context), false);
    dom.window.close();
});
