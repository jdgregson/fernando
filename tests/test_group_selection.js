const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

test('selection follows validated group changes and clears when ungrouped', () => {
    const dom = new JSDOM('<script src="https://fernando.test/static/js/pane-bridge.js"></script>', {
        url: 'https://fernando.test/chat/fixture', runScripts: 'outside-only',
    });
    const win = dom.window;
    Object.defineProperty(win.document, 'currentScript', {value: win.document.querySelector('script')});
    vm.runInContext(fs.readFileSync('src/static/js/pane-bridge.js', 'utf8'), dom.getInternalVMContext());
    const root = win.document.documentElement;
    const send = (color, source = win, origin = win.location.origin) => win.dispatchEvent(new win.MessageEvent('message', {
        source, origin, data: {type: 'fernando-pane', version: 1, event: 'state', payload: {group: color ? {color} : null}},
    }));
    send('#ffffff', {}, win.location.origin);
    assert.equal(root.hasAttribute('data-group-selection'), false);
    send('#ffffff', win, 'https://untrusted.test');
    assert.equal(root.hasAttribute('data-group-selection'), false);
    for (const [color, foreground] of [['#ffffff', '#000000'], ['#000000', '#ffffff'], ['#2eab83', '#000000']]) {
        send(color);
        assert.equal(root.hasAttribute('data-group-selection'), true);
        assert.equal(root.style.getPropertyValue('--group-selection-background'), color);
        assert.equal(root.style.getPropertyValue('--group-selection-foreground'), foreground);
    }
    send(null);
    assert.equal(root.hasAttribute('data-group-selection'), false);
    assert.equal(root.style.getPropertyValue('--group-selection-background'), '');
    send('invalid');
    assert.equal(root.hasAttribute('data-group-selection'), false);
    win.close();
});

test('right clicks bubble from the message body to its message menu', () => {
    const dom = new JSDOM('<div class="msg"><div class="msg-label">Fernando</div><div class="msg-content"><pre>body</pre></div></div>', {runScripts: 'outside-only'});
    const source = fs.readFileSync('src/templates/chat.html', 'utf8');
    const start = source.indexOf('    function addMsgContextMenu(');
    const end = source.indexOf('\n    }', start) + 6;
    vm.runInContext(source.slice(start, end), dom.getInternalVMContext());
    const win = dom.window;
    const message = win.document.querySelector('.msg');
    const label = message.querySelector('.msg-label');
    let calls = 0;
    win.showMsgContextMenu = (x, y, turn, role, target) => {
        assert.equal(turn, 4);
        assert.equal(role, 'assistant');
        assert.equal(target, label);
        calls++;
    };
    win.addMsgContextMenu(message, label, 4, 'assistant');
    for (const target of [message, label, message.querySelector('pre')]) {
        const event = new win.MouseEvent('contextmenu', {bubbles: true, cancelable: true});
        target.dispatchEvent(event);
        assert.equal(event.defaultPrevented, true);
    }
    assert.equal(calls, 3);
    win.close();
});
