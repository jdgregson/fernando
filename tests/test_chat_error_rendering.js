const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/templates/chat.html', 'utf8');
for (const match of source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    new vm.Script(match[1]);
}

function extract(name) {
    const start = source.indexOf('    function ' + name + '(');
    const end = source.indexOf('\n    }', start) + 6;
    assert.ok(start >= 0 && end > start);
    return source.slice(start, end);
}

for (const type of ['acp_error']) {
    const messages = [];
    const thought = {style: {}};
    const timers = [];
    const context = vm.createContext({
        _pipelines: {}, currentTurnTs: 1, currentTurnModel: 'model',
        _chunkRenderPending: false, _canvasTimer: null, _activateCanvases() {},
        _modelListPending: false, toolCalls: {},
        currentAssistantMsg: {}, currentContentDiv: null, currentText: '',
        collapseCodeBlocks() {}, highlightDiffs() {},
        currentThoughtBody: thought, currentThoughtArrow: {}, currentThoughtText: 'Thinking',
        isWorking: true, setStatus(state) { context.status = state; },
        resetStallTimer() {}, document: {visibilityState: 'hidden'},
        addSystemMessage(text, kind) { messages.push({text, kind}); },
        setTimeout(callback, delay) { timers.push({callback, delay}); },
    });
    vm.runInContext(extract('closeThought') + '\n' + extract('finishTurn') + '\n' + extract('processEvent'), context);
    context.processEvent({event: {type, error: 'Response failed'}});
    assert.deepEqual(messages, [{text: 'Error: Response failed', kind: 'error'}]);
    assert.equal(thought.style.display, 'none');
    assert.equal(context.status, 'error');
    assert.equal(context.isWorking, false);
    assert.equal(timers[0].delay, 3000);
    timers[0].callback();
    assert.equal(context.status, 'ready');
    assert.deepEqual(messages, [{text: 'Error: Response failed', kind: 'error'}]);
}
console.log('Chat script syntax and thinking-state error rendering passed');
