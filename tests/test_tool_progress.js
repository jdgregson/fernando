const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

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
const { document } = new JSDOM('<div id="message"></div>').window;
let now = 100000;
const context = vm.createContext({
    document, Date: { now: () => now }, toolCalls: {},
    currentAssistantMsg: document.getElementById('message'),
    _chunkRenderPending: false, ensureAssistantMsg() {}, scrollToBottom() {},
    toolPreview: title => title, renderToolImages() {},
    createCopyBtn: () => document.createElement('button'),
    requestAnimationFrame: callback => callback(),
});
vm.runInContext(['startToolProgress', 'addToolCall', 'updateToolCall'].map(extract).join('\n'), context);

// Replay the OpenCode sequence: empty pending card, input on in_progress.
context.addToolCall('open', 'system_run_command', 'pending', {}, 99);
const open = context.toolCalls.open;
assert.equal(open.div.querySelector('.tc-bar'), null);
context.updateToolCall('open', 'in_progress', [], null, {command: 'sleep 20', timeout: 30}, null, 100);
const bar = open._bar;
assert.ok(bar.classList.contains('tc-bar-running'));
assert.equal(bar.style.getPropertyValue('--tc-timeout'), '30s');
assert.equal(open._barStart, 100000);
now += 10000;
context.updateToolCall('open', 'in_progress', [], null, {timeout: 30}, null, 110);
assert.equal(open._bar, bar);
assert.equal(open._barStart, 100000);
assert.equal(open.div.querySelectorAll('.tc-bar').length, 1);
now += 10000;
context.updateToolCall('open', 'completed', [], null, null, null, 120);
assert.equal(open._bar, null);
assert.equal(bar.classList.contains('tc-bar-running'), false);
assert.equal(open.div.querySelector('.tool-duration').textContent, '20.0s');
context.updateToolCall('open', undefined, [], null, {timeout: 30});
assert.equal(open._bar, null, 'late input must not restart a completed bar');

// Kiro still starts immediately; replay uses the recorded timestamp.
context.addToolCall('kiro', 'Running: @system/run_command', undefined, {timeout: 30}, 115);
assert.equal(context.toolCalls.kiro._bar.style.animationDelay, '-5s');
context.updateToolCall('kiro', 'failed', [], null, null, null, 120);
assert.equal(context.toolCalls.kiro._bar, null);
context.addToolCall('done', 'system_run_command', 'completed', {timeout: 30}, 100);
assert.equal(context.toolCalls.done.div.querySelector('.tc-bar'), null);
for (const timeout of [0, -1, 'invalid', Infinity]) {
    context.addToolCall('invalid', 'system_run_command', 'in_progress', {timeout});
    assert.equal(context.toolCalls.invalid.div.querySelector('.tc-bar'), null);
}
console.log('Chat script syntax and OpenCode/Kiro tool progress lifecycle passed');
