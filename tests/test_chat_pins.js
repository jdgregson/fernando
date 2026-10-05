const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

function fixture(t) {
    const dom = new JSDOM('<div id="messages"></div><nav id="navigation"></nav>', {runScripts: 'outside-only', pretendToBeVisual: true});
    t.after(() => dom.window.close());
    const win = dom.window;
    win.ResizeObserver = class {observe() {} unobserve() {}};
    vm.runInContext(fs.readFileSync('src/static/js/chat-pins.js', 'utf8'), dom.getInternalVMContext());
    const ChatPins = vm.runInContext('ChatPins', dom.getInternalVMContext());
    const messages = win.document.getElementById('messages');
    Object.defineProperties(messages, {clientHeight: {value: 300}, scrollHeight: {value: 2000}});
    messages.getBoundingClientRect = () => ({top: 0, bottom: 300});
    const stored = new Set();
    let paused = 0;
    const pins = new ChatPins({
        messages, navigation: win.document.getElementById('navigation'),
        pauseFollowing: () => paused++,
        bottom: () => { messages.scrollTop = 1700; },
        save: (key, value, done) => {
            if (value) stored.add(key); else stored.delete(key);
            done([...stored]);
        },
    });
    function add(role, turn, top, height = 100) {
        const message = win.document.createElement('div');
        message.className = 'msg ' + role;
        message.dataset.turn = turn;
        message.innerHTML = '<div class="msg-label">' + (role === 'user' ? 'You' : 'Fernando') + '<span class="msg-ts">timestamp</span></div>';
        message.getBoundingClientRect = () => ({top: top - messages.scrollTop, bottom: top + height - messages.scrollTop});
        pins.register(message, role, turn);
        messages.append(message);
        return message;
    }
    return {win, messages, pins, add, stored, paused: () => paused};
}

test('pinning either role adds only its pin indicator and unpin restores the original label', t => {
    const f = fixture(t);
    for (const role of ['user', 'assistant']) {
        const message = f.add(role, 1, 0);
        const before = message.innerHTML;
        assert.equal(message.querySelector('.message-unpin'), null);
        f.pins.toggle(message);
        assert.equal(message.classList.contains('pinned'), true);
        const button = message.querySelector('.message-unpin');
        assert.equal(button.nextElementSibling.className, 'msg-ts');
        button.click();
        assert.equal(message.classList.contains('pinned'), false);
        assert.equal(message.innerHTML, before);
    }
});

test('pins survive rerender and distinguish user and multiple assistant messages in a turn', t => {
    const f = fixture(t);
    const user = f.add('user', 3, 0);
    const first = f.add('assistant', 3, 100);
    const second = f.add('assistant', 3, 200);
    assert.equal(user.dataset.pinKey, 'user:3:0');
    assert.equal(first.dataset.pinKey, 'assistant:3:0');
    assert.equal(second.dataset.pinKey, 'assistant:3:1');
    f.pins.toggle(second);
    f.messages.replaceChildren();
    f.add('user', 3, 0);
    const replayFirst = f.add('assistant', 3, 100);
    const replaySecond = f.add('assistant', 3, 200);
    assert.equal(replayFirst.classList.contains('pinned'), false);
    assert.equal(replaySecond.classList.contains('pinned'), true);
    f.pins.receive([]);
    assert.equal(replaySecond.classList.contains('pinned'), false);
});

test('navigation steps through nearest off-screen pins in both directions and jumps to bottom', t => {
    const f = fixture(t);
    const messages = [f.add('user', 1, 100), f.add('assistant', 1, 600), f.add('user', 2, 1200)];
    for (const message of messages) f.pins.toggle(message);
    f.messages.scrollTop = 1700;
    f.pins.update();
    assert.equal(f.pins.above, messages[2]);
    assert.equal(f.pins.up.hidden, false);
    assert.equal(f.pins.down.hidden, true);
    assert.equal(f.pins.end.hidden, true);
    f.pins.up.click();
    assert.equal(f.messages.scrollTop, 1188);
    assert.equal(f.pins.above, messages[1]);
    f.pins.up.click();
    assert.equal(f.messages.scrollTop, 588);
    assert.equal(f.pins.above, messages[0]);
    assert.equal(f.pins.below, messages[2]);
    assert.equal(f.pins.down.hidden, false);
    assert.equal(f.pins.end.hidden, false);
    f.pins.up.click();
    assert.equal(f.pins.up.hidden, true);
    assert.equal(f.pins.below, messages[1]);
    f.pins.down.click();
    assert.equal(f.messages.scrollTop, 588);
    assert.equal(f.paused(), 4);
    f.pins.end.click();
    f.pins.update();
    assert.equal(f.messages.scrollTop, 1700);
    assert.equal(f.pins.end.hidden, true);
});

test('partially visible pinned messages do not get off-screen navigation buttons', t => {
    const f = fixture(t);
    const message = f.add('assistant', 1, 0, 1200);
    f.pins.toggle(message);
    f.messages.scrollTop = 400;
    f.pins.update();
    assert.equal(f.pins.up.hidden, true);
    assert.equal(f.pins.down.hidden, true);
    assert.equal(f.pins.end.hidden, false);
});
