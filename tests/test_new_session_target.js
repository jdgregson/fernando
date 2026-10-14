const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

test('new session target shows and preserves the destination selected by the user', () => {
    const dom = new JSDOM(fs.readFileSync('src/templates/index.html', 'utf8'), {
        url: 'https://fernando.test/', runScripts: 'outside-only',
    });
    const win = dom.window;
    const context = dom.getInternalVMContext();
    const source = fs.readFileSync('src/static/js/sessions.js', 'utf8');
    const start = source.indexOf('let newSessionTargetGroupId = null;');
    const end = source.indexOf('function closeSession(', start);
    let activeGroup = 'one';
    const emitted = [];
    win.getActivePaneGroupId = () => activeGroup;
    win.emitWithCsrf = (event, payload) => emitted.push({event, payload});
    win._cachedGroups = [{id: 'one', name: 'First group', color: '#2eab83'}, {id: 'two', name: '<Second group>', color: '#ff0000'}];
    vm.runInContext(source.slice(start, end), context);
    const target = win.document.getElementById('newSessionTarget');
    const change = () => win.updateNewSessionTarget();
    win.openNewSessionModal();
    assert.equal(target.value, 'one');
    assert.equal(target.title, 'First group');
    assert.equal(target.style.getPropertyValue('--target-group-color'), '#2eab83');
    activeGroup = 'two';
    assert.equal(win.getNewSessionGroupId(), 'one');
    target.value = 'two';
    change();
    assert.equal(target.style.getPropertyValue('--target-group-color'), '#ff0000');
    win.createSessionType('shell');
    assert.equal(emitted.at(-1).payload.group_id, 'two');
    assert.equal(win.document.querySelector('option[value="two"]').textContent, '<Second group>');
    assert.equal(win.document.querySelector('option[value="two"]').children.length, 0);
    win.openNewSessionModal('__ungrouped__');
    assert.equal(target.value, '__ungrouped__');
    assert.equal(target.selectedOptions[0].textContent, 'Other');
    assert.equal(target.style.getPropertyValue('--target-group-color'), '#7ea8e3');
    win.createSessionType('kiro-unchained');
    assert.equal(emitted.at(-1).payload.group_id, null);
    win.openNewSessionModal('one');
    assert.equal(target.value, 'one');
    win.closeNewSessionModal();
    activeGroup = null;
    win.openNewSessionModal();
    assert.equal(target.value, '__ungrouped__');
    win.close();
});

test('notebook creation honors an explicit Ungrouped destination', () => {
    const adapters = new Map();
    const context = vm.createContext({
        paneController: {register: (name, adapter) => adapters.set(name, adapter)},
        window: {_notebookTargetGroupId: '__ungrouped__'},
        _cachedSessionGroups: {},
        getActivePaneGroupId: () => 'active-group',
    });
    vm.runInContext(fs.readFileSync('src/static/js/session-adapters.js', 'utf8'), context);
    assert.equal(adapters.get('notebook').prepare('notebook:new').groupId, null);
    context.window._notebookTargetGroupId = 'selected-group';
    assert.equal(adapters.get('notebook').prepare('notebook:new').groupId, 'selected-group');
});
