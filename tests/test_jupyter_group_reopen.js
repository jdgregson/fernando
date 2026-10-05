const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

test('reopening a moved or ungrouped notebook does not assign the active chat group', () => {
    const adapters = new Map();
    const memberships = new Map([['TestBook', 'campout']]);
    let groupSelections = 0;
    const context = vm.createContext({
        paneController: {register: (name, adapter) => adapters.set(name, adapter)},
        getNewSessionGroupId: () => { groupSelections++; return 'fernando-dev'; },
        closeNewSessionModal() {},
        _jupyterCounter: 0,
        emitWithCsrf(event, data) {
            if (event === 'open_jupyter' && data.group_id) memberships.set(data.name, data.group_id);
        },
    });
    vm.runInContext(fs.readFileSync('src/static/js/session-adapters.js', 'utf8'), context);
    const adapter = adapters.get('jupyter');
    adapter.afterOpen(adapter.prepare('jupyter:TestBook'));
    assert.equal(memberships.get('TestBook'), 'campout');
    memberships.delete('TestBook');
    adapter.afterOpen(adapter.prepare('jupyter:TestBook'));
    assert.equal(memberships.has('TestBook'), false);
    assert.equal(groupSelections, 0);
    const created = adapter.prepare('jupyter');
    adapter.afterOpen(created);
    assert.equal(memberships.get(created.name), 'fernando-dev');
    assert.equal(groupSelections, 1);
});
