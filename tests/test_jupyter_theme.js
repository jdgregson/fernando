const assert = require('node:assert/strict');
const fs = require('node:fs');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

test('Jupyter tracks group colors through the authenticated pane bridge', t => {
    const dom = new JSDOM('<html><head></head><body></body></html>', {url: 'https://fernando.test/jupyter/notebooks/Test.ipynb', runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const win = dom.window;
    const parent = {postMessage() {}};
    Object.defineProperty(win, 'parent', {value: parent});
    const script = win.document.createElement('script');
    script.src = 'https://fernando.test/static/js/pane-bridge.js';
    Object.defineProperty(win.document, 'currentScript', {value: script});
    win.eval(fs.readFileSync('src/static/js/pane-bridge.js', 'utf8'));
    win.eval(fs.readFileSync('src/static/js/jupyter-theme.js', 'utf8'));
    const color = () => win.document.documentElement.style.getPropertyValue('--fernando-group-color');
    const send = (event, group, source = parent, origin = 'https://fernando.test') => win.dispatchEvent(new win.MessageEvent('message', {
        source, origin, data: {type: 'fernando-pane', version: 1, event, payload: {group}},
    }));
    send('context', {color: '#b8860b'});
    assert.equal(color(), '#b8860b');
    send('state', {color: '#3465a3'});
    assert.equal(color(), '#3465a3');
    send('state', {color: '#ffffff'}, win);
    assert.equal(color(), '#3465a3');
    send('state', {color: '#ffffff'}, parent, 'https://other.test');
    assert.equal(color(), '#3465a3');
    send('state', null);
    assert.equal(color(), '');
    send('state', {color: '#b8860b'});
    send('state', {color: 'red; background: white'});
    assert.equal(color(), '');
});
