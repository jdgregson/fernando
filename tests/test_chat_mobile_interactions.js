const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

const chat = fs.readFileSync('src/templates/chat.html', 'utf8');
const core = fs.readFileSync('src/static/js/core.js', 'utf8');
const copy = fs.readFileSync('src/static/js/copy-controls.js', 'utf8');
function extract(source, name, indent = '    ') {
    const start = source.indexOf(indent + 'function ' + name + '(');
    assert.ok(start >= 0);
    return source.slice(start, source.indexOf('\n' + indent + '}', start) + indent.length + 2);
}
function fixture(t, html) {
    const dom = new JSDOM(html, {runScripts: 'outside-only', pretendToBeVisual: true});
    t.after(() => dom.window.close());
    return dom;
}

test('unchanged health errors preserve text nodes and selected range; copy reads exact errors', async t => {
    const dom = fixture(t, fs.readFileSync('src/templates/index.html', 'utf8'));
    const win = dom.window;
    win.getHealthClass = () => '';
    let copied;
    Object.defineProperty(win.navigator, 'clipboard', {value: {writeText: async value => { copied = value; }}});
    vm.runInContext(copy + extract(core, 'updateHealthModal', ''), dom.getInternalVMContext());
    const data = {errors: ['Error: first\\nsecond'], logs: {flask: ['line']}};
    win.updateHealthModal(data);
    const errors = win.document.getElementById('healthErrors');
    const node = errors.firstChild;
    const range = win.document.createRange();
    range.selectNodeContents(node);
    win.getSelection().addRange(range);
    win.updateHealthModal(data);
    assert.equal(errors.firstChild, node);
    assert.equal(win.getSelection().toString(), 'Error: first\nsecond');
    await errors.previousElementSibling.querySelector('button').onclick();
    assert.equal(copied, 'Error: first\nsecond');
    win.updateHealthModal({...data, errors: ['new error']});
    assert.equal(errors.textContent, 'new error');
    assert.equal(errors.previousElementSibling.querySelectorAll('button').length, 1);
});

test('code copy controls exist without hovering and preserve collapsed full text', async t => {
    const dom = fixture(t, '<div id="messages"></div>');
    const win = dom.window;
    let copied;
    Object.defineProperty(win.navigator, 'clipboard', {value: {writeText: async value => { copied = value; }}});
    vm.runInContext(copy, dom.getInternalVMContext());
    const root = win.document.getElementById('messages');
    win.CopyControls.observe(root);
    root.innerHTML = '<pre><code>short</code></pre><div class="markdown-table-copy" data-table-markdown="a%7Cb"><table></table></div>';
    root.querySelector('code')._fullText = 'full\ntext\n';
    await new Promise(resolve => win.setTimeout(resolve, 0));
    assert.equal(root.querySelectorAll('button').length, 2);
    await root.querySelector('button').onclick();
    assert.equal(copied, 'full\ntext');
    await root.querySelector('.markdown-table-copy button').onclick();
    assert.equal(copied, 'a|b');
});

test('long press keeps native selection and opens actions away from the touch point', t => {
    const dom = fixture(t, '<div id="messages"><div class="msg"><div class="msg-label">You</div><p>select this text</p></div></div>');
    const win = dom.window;
    const timers = [];
    win.setTimeout = fn => { timers.push(fn); return timers.length; };
    win.clearTimeout = () => {};
    win.ChatPins = {pinIcon: ''};
    win.messagePins = {pins: new Set()};
    vm.runInContext(['showMsgContextMenu', 'dismissMsgContextMenu', 'addMsgContextMenu'].map(name => extract(chat, name)).join('\n'), dom.getInternalVMContext());
    const messages = win.document.getElementById('messages');
    messages.getBoundingClientRect = () => ({top: 30, bottom: 700});
    const label = messages.querySelector('.msg-label');
    win.addMsgContextMenu(label, 1, 'user');
    assert.ok(label.querySelector('button[aria-label="Message actions"]'));
    const paragraph = messages.querySelector('p');
    const range = win.document.createRange();
    range.selectNodeContents(paragraph);
    win.getSelection().addRange(range);
    const touch = new win.Event('touchstart', {bubbles: true, cancelable: true});
    touch.touches = [{clientX: 100, clientY: 100}];
    paragraph.dispatchEvent(touch);
    timers.shift()();
    assert.equal(touch.defaultPrevented, false);
    assert.equal(win.getSelection().toString(), 'select this text');
    const menu = win.document.querySelector('.msg-touch-actions');
    assert.ok(menu);
    assert.ok(parseFloat(menu.style.top) > 500);
    const context = new win.Event('contextmenu', {bubbles: true, cancelable: true});
    paragraph.dispatchEvent(context);
    assert.equal(context.defaultPrevented, false);
});

test('historical ready events cannot replace the current waking status', t => {
    const dom = fixture(t, '<div id="status" class="status connecting"></div><span id="statusText">Waking...</span><button id="sendBtn">Send</button>');
    const win = dom.window;
    win._replayingHistory = true;
    win.isReady = false;
    vm.runInContext(extract(chat, 'setStatus'), dom.getInternalVMContext());
    win.setStatus('ready', 'Ready');
    assert.equal(win.document.getElementById('statusText').textContent, 'Waking...');
    assert.equal(win.isReady, false);
});
