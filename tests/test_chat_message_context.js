const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/templates/chat.html', 'utf8');
function extract(name) {
    const start = source.indexOf('    function ' + name + '(');
    const end = source.indexOf('\n    }', start) + 6;
    assert.ok(start >= 0 && end > start);
    return source.slice(start, end);
}
function element() {
    return {
        dataset: {}, style: {}, children: [],
        appendChild(child) { this.children.push(child); },
        remove() {},
        getBoundingClientRect() { return {right: 0, bottom: 0}; },
    };
}
const body = element();
const messages = element();
const sandbox = vm.createContext({
    currentTurnIndex: 0,
    document: {createElement: element, body, getElementById() { return messages; }, addEventListener() {}},
    window: {innerWidth: 1000, innerHeight: 1000},
    addMsgLabelContextMenu() {}, _addTimestampSpan() {}, scrollToBottom() {},
    parseRewardPrefix() {}, parseAttachedFiles() {},
    _renderUserBody(el, text) { el.renderedBody = text; },
    dismissMsgContextMenu() {}, setTimeout() {},
});
vm.runInContext(['addUserMessage', 'showMsgContextMenu', 'formatMessageContext', 'showContinuationModal'].map(extract).join('\n'), sandbox);
const prefix = '[Pane context: group_name: "<img src=x onerror=alert(1)>", group_members: notebook:project, reward balance: 13]';
for (const collapsed of [false, true]) {
    const message = sandbox.addUserMessage(prefix + '\nRead the notebook.', collapsed);
    assert.equal(message.dataset.promptContext, prefix);
    if (collapsed) assert.equal(message.children[2].textContent, 'Read the notebook.');
    else assert.equal(message.renderedBody, 'Read the notebook.');
    sandbox.showMsgContextMenu(10, 10, 1, 'user', {closest() { return message; }});
    body.children.at(-1).children[0].onclick();
    const modal = body.children.at(-1).children[0];
    assert.equal(modal.children[0].children[0].textContent, 'Message Context');
    assert.equal(modal.children[1].textContent, 'Group: <img src=x onerror=alert(1)>\n\nGroup members\n  • Notebook: project\n\nReward balance: 13');
    assert.equal(modal.children[1].innerHTML, undefined);
}
const plain = sandbox.addUserMessage('No prefix', false);
assert.equal(plain.dataset.promptContext, '');
assert.equal(plain.renderedBody, 'No prefix');
sandbox.showMsgContextMenu(10, 10, 1, 'user', {closest() { return plain; }});
body.children.at(-1).children[0].onclick();
assert.equal(body.children.at(-1).children[0].children[1].textContent, 'No context was attached to this message.');
console.log('Per-message context hiding, menu, and safe modal rendering passed');
assert.equal(sandbox.formatMessageContext('[Pane context: pane1: this chat, pane2: this chat, group_name: "fernando-dev", group_id: a5055f9f, group_color: #b8860b, group_members: notebook:fernando, chat:6b18013a (sleeping), chat:5c6b90f4, reward balance: 13]'), 'Pane 1: this chat\n\nPane 2: this chat\n\nGroup: fernando-dev\n\nGroup ID: a5055f9f\n\nGroup color: #b8860b\n\nGroup members\n  • Notebook: fernando\n  • Chat: 6b18013a (sleeping)\n  • Chat: 5c6b90f4\n\nReward balance: 13');
