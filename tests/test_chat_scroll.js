const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync('src/templates/chat.html', 'utf8');
const state = source.slice(source.indexOf('    let _userScrolledUp = false;'), source.indexOf('    let _suppressScroll = false;'));
const follow = source.slice(source.indexOf('    function scrollToBottom(force)'), source.indexOf('    function collapseCodeBlocks('));

function fixture() {
    const listeners = {};
    let top = 0;
    const messages = {
        scrollHeight: 1000,
        clientHeight: 200,
        get scrollTop() { return top; },
        set scrollTop(value) { top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
        addEventListener(type, handler) { listeners[type] = handler; },
    };
    const context = vm.createContext({document: {getElementById: () => messages, addEventListener(type, handler) { listeners[type] = handler; }}, CopyControls: {observe() {}}});
    vm.runInContext(state + '\nlet _suppressScroll = false;\n' + follow, context);
    const scroll = force => context.scrollToBottom(force);
    scroll();
    return {messages, listeners, scroll};
}

test('new streamed content follows while at the bottom', () => {
    const {messages, listeners, scroll} = fixture();
    listeners.scroll();
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 900);
});

test('slight upward scroll pauses before the browser dispatches its scroll event', () => {
    const {messages, listeners, scroll} = fixture();
    listeners.wheel({deltaY: -4});
    messages.scrollTop -= 4;
    scroll();
    assert.equal(messages.scrollTop, 796);
    listeners.scroll();
    listeners.scroll();
    for (let i = 0; i < 100; i++) {
        messages.scrollHeight += 10;
        scroll();
    }
    assert.equal(messages.scrollTop, 796);
});

test('wheel intent pauses even before the native scroll moves', () => {
    const {messages, listeners, scroll} = fixture();
    listeners.wheel({deltaY: -1});
    listeners.scroll();
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 800);
});

test('scrolling back to the bottom area resumes following', () => {
    const {messages, listeners, scroll} = fixture();
    listeners.wheel({deltaY: -50});
    messages.scrollTop = 750;
    listeners.scroll();
    listeners.wheel({deltaY: 45});
    messages.scrollTop = 795;
    listeners.scroll();
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 900);
});

test('explicit jump to bottom resumes following', () => {
    const {messages, listeners, scroll} = fixture();
    listeners.wheel({deltaY: -200});
    messages.scrollTop = 600;
    listeners.scroll();
    scroll(true);
    assert.equal(messages.scrollTop, 800);
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 900);
});

test('layout shrink followed by tool growth does not disable following', () => {
    const {messages, listeners, scroll} = fixture();
    messages.scrollHeight = 700;
    messages.scrollTop = messages.scrollTop;
    messages.scrollHeight = 1100;
    listeners.scroll();
    scroll();
    assert.equal(messages.scrollTop, 900);
});

test('touch upward reading gesture pauses and returning down resumes', () => {
    const {messages, listeners, scroll} = fixture();
    listeners.touchstart({touches: [{clientY: 100}]});
    listeners.touchmove({touches: [{clientY: 110}]});
    messages.scrollTop = 790;
    listeners.scroll();
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 790);
    listeners.touchmove({touches: [{clientY: 0}]});
    messages.scrollTop = 900;
    listeners.scroll();
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 1000);
});

test('scroll pill drag pauses before its scroll write and resumes at the bottom', () => {
    const {messages, listeners, scroll} = fixture();
    listeners['scroll-pill-input']({detail: {top: 400}});
    messages.scrollTop = 400;
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 400);
    listeners['scroll-pill-input']({detail: {top: 900}});
    messages.scrollTop = 900;
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 1000);
});

test('keyboard navigation pauses while typing does not', () => {
    const {messages, listeners, scroll} = fixture();
    listeners.keydown({key: 'ArrowUp', target: {closest: () => ({})}});
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 900);
    listeners.keydown({key: 'PageUp', target: {closest: () => null}});
    messages.scrollTop = 700;
    messages.scrollHeight += 100;
    scroll();
    assert.equal(messages.scrollTop, 700);
});

test('only explicit navigation and sending force following', () => {
    assert.equal((source.match(/scrollToBottom\(true\)/g) || []).length, 2);
    const scripts = [...source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
    for (const [, script] of scripts) new vm.Script(script.replace(/\{\{[\s\S]*?\}\}/g, 'null'));
});
