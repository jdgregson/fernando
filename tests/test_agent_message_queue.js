const assert = require('node:assert/strict');
const fs = require('node:fs');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

test('queued message safely previews the full prompt and requests authenticated deletion', t => {
    const dom = new JSDOM('<div id="messages"></div>', {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const win = dom.window;
    const emitted = [];
    const modals = [];
    win.socket = {emit: (...args) => emitted.push(args)};
    win.csrfToken = 'csrf-token';
    win.sessionId = 'recipient';
    win.scrollToBottom = () => {};
    win.showAgentMessageModal = (...args) => modals.push(args);
    const source = fs.readFileSync('src/templates/chat.html', 'utf8');
    win.eval(source.slice(source.indexOf('    function addQueuedAgentMessage('), source.indexOf('    function addAgentMessage(')));
    const text = '<img src=x onerror=alert(1)>\nFull message';
    win.addQueuedAgentMessage({id: 'queue-id', from: 'sender', text});
    const row = win.document.querySelector('[data-queue-id]');
    assert.equal(row.querySelector('img'), null);
    row.querySelector('.queued-agent-preview').click();
    assert.deepEqual(modals, [['sender', '[AGENT MESSAGE from sender] ' + text]]);
    row.querySelector('.queued-agent-remove').click();
    assert.equal(emitted[0][0], 'acp_delete_queued_message');
    assert.equal(emitted[0][1].csrf_token, 'csrf-token');
    assert.equal(emitted[0][1].session_id, 'recipient');
    assert.equal(emitted[0][1].message_id, 'queue-id');
    assert.ok(row.isConnected);
    win.removeQueuedAgentMessage('queue-id');
    assert.equal(row.isConnected, false);
});

test('all inline chat scripts parse', () => {
    const source = fs.readFileSync('src/templates/chat.html', 'utf8');
    for (const match of source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
        const code = match[1].replace(/\{\{[\s\S]*?\}\}/g, 'null');
        new (require('node:vm').Script)(code);
    }
});

test('Send appears beside Stop only with text and queues without disturbing the current turn', t => {
    const dom = new JSDOM('<div id="status" class="working"></div><div id="slashMenu"></div><textarea id="chat-message"></textarea><button id="sendBtn">Stop</button><button id="queueSendBtn"></button>', {
        runScripts: 'outside-only', url: 'https://fernando.example/'
    });
    t.after(() => dom.window.close());
    const win = dom.window;
    const emitted = [];
    Object.assign(win, {
        isReady: true, isWorking: true, attachedFiles: [], _stagedReward: null,
        _unreadChildMessagesCache: 0, _rewardBalanceCache: 23,
        draftKey: 'draft', csrfToken: 'csrf-token', sessionId: 'recipient',
        socket: {emit: (...args) => emitted.push(args)}, scrollToBottom: () => {},
        addUserMessage: () => assert.fail('Must not finish or append to the active turn'),
        setStatus: () => assert.fail('Must not change the active turn status'),
        resetStallTimer: () => assert.fail('Must not reset the active turn timer')
    });
    const source = fs.readFileSync('src/templates/chat.html', 'utf8');
    win.eval(source.slice(source.indexOf('    function updateQueueSendButton('), source.indexOf('    function setStatus(')));
    win.eval(source.slice(source.indexOf('    function sendMessage('), source.indexOf("    socket.on('acp_history_size'")));
    const input = win.document.getElementById('chat-message');
    const send = win.document.getElementById('queueSendBtn');
    win.updateQueueSendButton();
    assert.equal(send.style.display, 'none');
    input.value = 'Next task';
    win.updateQueueSendButton();
    assert.equal(send.style.display, 'flex');
    assert.equal(win.document.getElementById('sendBtn').textContent, 'Stop');
    win.sendMessage();
    assert.equal(input.value, '');
    assert.equal(send.style.display, 'none');
    assert.equal(emitted.length, 1);
    assert.equal(emitted[0][0], 'acp_prompt');
    assert.equal(emitted[0][1].text, 'Next task');
    assert.equal(emitted[0][1].csrf_token, 'csrf-token');
    input.value = 'Draft';
    win.document.getElementById('status').className = 'ready';
    win.updateQueueSendButton();
    assert.equal(send.style.display, 'none');
});
