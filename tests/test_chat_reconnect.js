const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync('src/templates/chat.html', 'utf8');

function fixture(t) {
    const dom = new JSDOM('<div id="messages"></div><div id="queuedMessages"></div>', {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const context = dom.getInternalVMContext();
    const frames = [];
    const callbacks = {};
    Object.assign(dom.window, {
        requestAnimationFrame: callback => frames.push(callback),
        socket: {on: (name, callback) => { callbacks[name] = callback; }, emit() {}},
        chatImages: {pause() {}, resume() {}},
        searchNavigation: {reset() {}, loaded() {}},
        resetStallTimer() {}, resetTaskBar() {}, scrollToBottom() {}, updateHeaderStats() {},
        _activateCanvases() {}, collapseCodeBlocks() {}, highlightDiffs() {},
    });
    vm.runInContext(`
        let sessionId = 'aaaaaaaa', csrfToken = 'test', _historyReplayPending = false, _historyReplayEvents = [];
        let currentUserMsg = null, currentAssistantMsg = null, currentTurnTs = null, currentTurnIndex = 0;
        let toolCalls = {}, _sessionMetadata = {}, sessionTotalCost = 0, _replayingHistory = false, _suppressScroll = false;
        let expectedSeq = -1, historyOffset = 0, sessionEventCount = 0, seqBuffer = {};
        let _chunkRenderPending = false, _canvasTimer = null, currentContentDiv = null, currentText = '', isWorking = false;
        let finished = 0, status = '';
        function closeThought() {}
        function finishTurn() { finished++; toolCalls = {}; currentAssistantMsg = null; isWorking = false; }
        function setStatus(value) { status = value; }
        function processEvent(data) {
            if (data.event.type === 'test_running') {
                currentAssistantMsg = document.createElement('div');
                document.getElementById('messages').append(currentAssistantMsg);
                toolCalls.command = {status: 'in_progress'};
                isWorking = true;
            } else if (data.event.type === 'test_completed') {
                toolCalls.command.status = 'completed';
            } else {
                renderEvent(data);
            }
        }
    `, context);
    const renderStart = source.indexOf('    function renderEvent(');
    const renderEnd = source.indexOf('\n    }', renderStart) + 6;
    vm.runInContext(source.slice(renderStart, renderEnd), context);
    const start = source.indexOf("    socket.on('acp_history_batch'");
    const end = source.indexOf("    socket.on('acp_event', handleAcpEvent);") + "    socket.on('acp_event', handleAcpEvent);".length;
    vm.runInContext(source.slice(start, end), context);
    return {callbacks, context, frames, evaluate: expression => vm.runInContext(expression, context)};
}

test('working status arriving before the replay frame cannot finish the active tool', t => {
    const f = fixture(t);
    f.callbacks.acp_history_batch({session_id: 'aaaaaaaa', events: [{type: 'test_running'}], sync_seq: 10});
    f.callbacks.acp_event({session_id: 'aaaaaaaa', event: {type: 'session_working'}});
    assert.equal(f.evaluate('_historyReplayEvents.length'), 1);
    f.frames.shift()();
    assert.equal(f.evaluate('finished'), 0);
    assert.equal(f.evaluate('isWorking'), true);
    assert.equal(f.evaluate('status'), 'working');
    assert.equal(f.evaluate('toolCalls.command.status'), 'in_progress');
    f.callbacks.acp_event({session_id: 'aaaaaaaa', seq: 10, event: {type: 'test_completed'}});
    assert.equal(f.evaluate('toolCalls.command.status'), 'completed');
});

test('live tool completion arriving during replay is applied after reconstruction', t => {
    const f = fixture(t);
    f.callbacks.acp_history_batch({session_id: 'aaaaaaaa', events: [{type: 'test_running'}], sync_seq: 20});
    f.callbacks.acp_event({session_id: 'aaaaaaaa', seq: 20, event: {type: 'test_completed'}});
    f.frames.shift()();
    assert.equal(f.evaluate('toolCalls.command.status'), 'completed');
    assert.equal(f.evaluate('expectedSeq'), 21);
    assert.equal(f.evaluate('finished'), 0);
});

test('actual ready event completes the replayed turn only after replay', t => {
    const f = fixture(t);
    f.callbacks.acp_history_batch({session_id: 'aaaaaaaa', events: [{type: 'test_running'}], sync_seq: 30});
    f.callbacks.acp_event({session_id: 'aaaaaaaa', event: {type: 'session_ready'}});
    assert.equal(f.evaluate('finished'), 0);
    f.frames.shift()();
    assert.equal(f.evaluate('finished'), 1);
    assert.equal(f.evaluate('isWorking'), false);
    assert.equal(f.evaluate('status'), 'ready');
});
