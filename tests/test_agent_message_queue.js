const assert = require('node:assert/strict');
const fs = require('node:fs');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

test('queued message safely previews the full prompt and requests authenticated deletion', t => {
    const dom = new JSDOM('<div id="messages"></div><form id="chat-composer"><div id="queuedMessages"></div></form>', {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const win = dom.window;
    const emitted = [];
    const modals = [];
    win.socket = {emit: (...args) => emitted.push(args)};
    win.csrfToken = 'csrf-token';
    win.sessionId = 'recipient';
    win.scrollToBottom = () => assert.fail('Queue must not scroll the conversation');
    win.showAgentMessageModal = (...args) => modals.push(args);
    const source = fs.readFileSync('src/templates/chat.html', 'utf8');
    win.eval(source.slice(source.indexOf('    function addQueuedAgentMessage('), source.indexOf('    function addAgentMessage(')));
    const text = '<img src=x onerror=alert(1)>\nFull message';
    win.addQueuedAgentMessage({id: 'queue-id', from: 'sender', text});
    const row = win.document.querySelector('[data-queue-id]');
    assert.equal(row.parentElement.id, 'queuedMessages');
    assert.equal(win.document.getElementById('messages').children.length, 0);
    assert.equal(row.querySelector('.queued-agent-preview').type, 'button');
    assert.equal(row.querySelector('.queued-agent-remove').type, 'button');
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

for (const draft of ['', 'Existing draft']) {
    test('pencil restores acknowledged queue text and preserves draft: ' + JSON.stringify(draft), t => {
        const dom = new JSDOM('<div id="queuedMessages"></div><textarea id="chat-message"></textarea>', {runScripts: 'outside-only'});
        t.after(() => dom.window.close());
        const win = dom.window;
        let acknowledge;
        let saved = false;
        let inputEvents = 0;
        Object.assign(win, {
            csrfToken: 'csrf', sessionId: 'recipient',
            saveDraft: () => { saved = true; },
            socket: {emit: (name, data, callback) => {
                assert.equal(name, 'acp_edit_queued_message');
                assert.equal(data.csrf_token, 'csrf');
                assert.equal(data.session_id, 'recipient');
                assert.equal(data.message_id, 'queued');
                acknowledge = callback;
            }}
        });
        const source = fs.readFileSync('src/templates/chat.html', 'utf8');
        win.eval(source.slice(source.indexOf('    function addQueuedAgentMessage('), source.indexOf('    function addAgentMessage(')));
        const input = win.document.getElementById('chat-message');
        input.value = draft;
        input.addEventListener('input', () => inputEvents++);
        win.addQueuedAgentMessage({type: 'user_message_queued', id: 'queued', text: 'first'});
        const edit = win.document.querySelector('.queued-agent-edit');
        assert.equal(edit.nextElementSibling.className, 'queued-agent-remove');
        edit.click();
        assert.equal(input.value, draft);
        assert.equal(edit.disabled, true);
        acknowledge({text: 'first\n\nsecond'});
        assert.equal(input.value, 'first\n\nsecond' + (draft ? '\n\n' + draft : ''));
        assert.equal(win.document.querySelector('[data-queue-id]'), null);
        assert.equal(win.document.activeElement, input);
        assert.equal(inputEvents, 1);
        assert.equal(saved, true);
    });
}

test('failed queue edit does not restore text or change draft', t => {
    const dom = new JSDOM('<div id="queuedMessages"></div><textarea id="chat-message">Draft</textarea>', {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const win = dom.window;
    const errors = [];
    Object.assign(win, {
        csrfToken: 'csrf', sessionId: 'recipient',
        addSystemMessage: text => errors.push(text),
        socket: {emit: (name, data, callback) => callback({error: 'Message is no longer queued'})}
    });
    const source = fs.readFileSync('src/templates/chat.html', 'utf8');
    win.eval(source.slice(source.indexOf('    function addQueuedAgentMessage('), source.indexOf('    function addAgentMessage(')));
    win.addQueuedAgentMessage({type: 'user_message_queued', id: 'queued', text: 'first'});
    win.document.querySelector('.queued-agent-edit').click();
    assert.equal(win.document.getElementById('chat-message').value, 'Draft');
    assert.deepEqual(errors, ['Message is no longer queued']);
});

test('user queue updates one row in place and previews the complete merged message', t => {
    const dom = new JSDOM('<div id="messages"></div><form id="chat-composer"><div id="queuedMessages"></div></form>', {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const win = dom.window;
    const modals = [];
    Object.assign(win, {
        scrollToBottom: () => {},
        showContinuationModal: (...args) => modals.push(args)
    });
    const source = fs.readFileSync('src/templates/chat.html', 'utf8');
    win.eval(source.slice(source.indexOf('    function addQueuedAgentMessage('), source.indexOf('    function addAgentMessage(')));
    const evt = {type: 'user_message_queued', id: 'user-queue', text: 'first'};
    win.addQueuedAgentMessage(evt);
    const row = win.document.querySelector('[data-queue-id]');
    win.addQueuedAgentMessage({id: 'agent-queue', from: 'child', text: 'agent message'});
    const text = 'first\n\n<img src=x onerror=alert(1)>\n\nthird';
    win.addQueuedAgentMessage({...evt, text});
    assert.equal(win.document.querySelectorAll('[data-queue-id]').length, 2);
    assert.equal(win.document.getElementById('queuedMessages').firstElementChild, row);
    assert.equal(win.document.getElementById('messages').children.length, 0);
    assert.equal(row.querySelector('img'), null);
    assert.equal(row.querySelector('.queued-agent-preview').textContent, 'Queued · You: ' + text);
    row.querySelector('.queued-agent-preview').click();
    assert.deepEqual(modals, [[text, 'Queued Message']]);
    win.removeQueuedAgentMessage(evt.id);
    assert.equal(win.document.querySelectorAll('[data-queue-id]').length, 1);
});

test('Send appears beside Stop only with text and queues without disturbing the current turn', t => {
    const dom = new JSDOM('<div id="status" class="working"></div><div id="slashMenu"></div><textarea id="chat-message"></textarea><button id="sendBtn">Stop</button><button id="queueSendBtn"></button>', {
        runScripts: 'outside-only', url: 'https://fernando.example/'
    });
    t.after(() => dom.window.close());
    const win = dom.window;
    const emitted = [];
    Object.assign(win, {
        isReady: true, isWorking: true, attachedFiles: [],
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
