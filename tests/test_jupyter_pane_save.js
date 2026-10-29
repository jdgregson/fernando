const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/static/js/panes.js', 'utf8');
const method = source.slice(source.indexOf('    async open('), source.indexOf('    dismiss('));

async function check(mode) {
    let resolve;
    let reject;
    let mounted = false;
    let content = '1 + 1';
    const state = {surface: 'browser'};
    const notebook = {
        dirty: true,
        toJSON: () => ({content}),
        set_dirty(value) { this.dirty = value; },
        save_notebook: () => new Promise((yes, no) => { resolve = () => { notebook.dirty = false; yes(); }; reject = no; }),
    };
    const iframe = {src: 'https://fernando.test/jupyter/notebooks/Test.ipynb', contentWindow: {Jupyter: {notebook}}};
    const alerts = [];
    const controller = vm.runInNewContext('({' + method + '})', {URL, activeTerminal: 1, showAlert: message => alerts.push(message), window: {location: {href: 'https://fernando.test/'}}});
    controller.adapter = () => ({surface: 'browser', mount: () => { mounted = true; }});
    controller.get = () => state;
    controller.elements = () => ({browser: {querySelector: () => iframe}});
    controller.showBrowser = () => ({});
    controller.finishOpen = () => {};
    const result = controller.open('desktop');
    assert.equal(mounted, false);
    if (mode === 'failure') reject(new Error('save failed'));
    else {
        if (mode === 'edited') content = '2 + 2';
        resolve();
    }
    assert.equal(await result, mode === 'success');
    assert.equal(mounted, mode === 'success');
    assert.equal(alerts.length, mode === 'success' ? 0 : 1);
    if (mode === 'edited') assert.equal(notebook.dirty, true);
}

(async () => {
    for (const mode of ['success', 'failure', 'edited']) await check(mode);
    console.log('Save completion, failure retention and in-flight edit retention passed');
})();
