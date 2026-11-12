const assert = require('node:assert/strict');
const {createMcpAlert} = require('../src/static/js/mcp-alert.js');

class Element {
    constructor() { this.children = []; this.hidden = true; this.textContent = ''; this.renders = 0; }
    appendChild(child) { this.children.push(child); }
    setAttribute(name, value) { this[name] = value; }
    replaceChildren() { this.children = []; this.renders++; }
    text() { return this.textContent + this.children.map(child => child.text()).join(''); }
}
global.document = {
    createElement: () => new Element(),
    createTextNode: text => Object.assign(new Element(), {textContent: text}),
};
const element = new Element();
const render = createMcpAlert(element);
const failed = {name: 'desktop', status: 'failed', error: '<img src=x onerror=alert(1)>'};
render({epoch: 'first', revision: 0, failures: []});
assert.equal(element.hidden, true);
render({epoch: 'first', revision: 2, failures: [failed]});
assert.equal(element.hidden, false);
assert.ok(element.text().includes('MCP SERVER LOAD FAILURE'));
assert.ok(element.text().includes(failed.error));
element.children[0].children[1].onclick();
assert.equal(element.hidden, true);
const renders = element.renders;
render({epoch: 'first', revision: 3, failures: [failed]});
assert.equal(element.renders, renders);
render({epoch: 'first', revision: 1, failures: []});
assert.equal(element.hidden, true);
render({epoch: 'first', revision: 4, failures: []});
assert.equal(element.hidden, true);
render({epoch: 'new-runtime', revision: 0, failures: [failed]});
assert.equal(element.hidden, false);
const reopened = new Element();
createMcpAlert(reopened)({epoch: 'new-runtime', revision: 0, failures: [failed]});
assert.equal(reopened.hidden, false);
console.log('MCP alert: failure, quiet success, deduplication, stale replay, recovery, runtime restart, reopen, and literal error text passed');
