const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/static/js/terminal.js', 'utf8');
function extract(name) {
    const start = source.indexOf('function ' + name + '(');
    const end = source.indexOf('\n}', start) + 2;
    assert.ok(start >= 0 && end > start);
    return source.slice(start, end);
}

function harness() {
    const focus = [];
    const scrolling = [];
    const emitted = [];
    const timers = [];
    const initializers = [];
    let document;
    function element() {
        const listeners = new Map();
        const textarea = {
            addEventListener(type, callback) { listeners.set(type, callback); },
            focus() { document.activeElement = textarea; listeners.get('focus')(); },
        };
        return {
            children: [],
            style: {setProperty(name, value) { this[name] = value; }},
            clientHeight: 600,
            scrollTop: 0,
            setAttribute() {},
            addEventListener(type, callback) { listeners.set('element:' + type, callback); },
            getBoundingClientRect() { return {top: 0, bottom: 600, height: 600}; },
            appendChild(child) { this.children.push(child); child.parentElement = this; },
            querySelector(selector) { return selector === 'textarea' ? textarea : null; },
            textarea,
        };
    }
    const containers = {terminal1: element(), terminal2: element()};
    document = {
            createElement: element,
            getElementById(id) {
                if (containers[id]) return containers[id];
                assert.ok(['terminal1-container', 'terminal2-container'].includes(id));
                return {getBoundingClientRect: () => ({top: id === 'terminal1-container' ? 42 : 842})};
            },
            body: {scrollHeight: 1800},
        };
    const sandbox = vm.createContext({
        termInstances: {},
        document,
        AbortController,
        ResizeObserver: class { observe() {} disconnect() {} },
        fitTerminal() {},
        getComputedStyle: () => ({getPropertyValue: () => '17px'}),
        clearTimeout() {},
        WTerm: class {
            constructor(el, options) { this.element = el; this.options = options; }
            init() { return {then(callback) { initializers.push(callback); return {catch() {}}; }}; }
            scrollToBottom() {}
        },
        measureTermSize: () => ({rows: 24, cols: 80}),
        navigator: {userAgent: 'test', platform: 'test', maxTouchPoints: 0},
        setActiveTerminal(pane, direct) { focus.push([pane, direct]); },
        requestAnimationFrame(callback) { callback(); },
        setTimeout(callback, delay) { timers.push({callback, delay}); },
        emitWithCsrf(type, data) { emitted.push([type, JSON.parse(JSON.stringify(data))]); },
        window: {
            getSelection: () => ({isCollapsed: true}),
            innerHeight: 800,
            scrollBy(value) { scrolling.push(['by', JSON.parse(JSON.stringify(value))]); },
            scrollTo(value) { scrolling.push(['to', JSON.parse(JSON.stringify(value))]); },
        },
        isSplit: true,
        console,
    });
    vm.runInContext(extract('getOrCreateTerm') + '\n' + extract('setupFocusScroll'), sandbox);
    return {sandbox, focus, scrolling, emitted, timers, initializers};
}

for (const initialPane of [1, 2]) {
    const destination = initialPane === 1 ? 2 : 1;
    for (const moveBeforeInit of [false, true]) {
        const h = harness();
        const entry = h.sandbox.getOrCreateTerm('Shell', initialPane);
        if (!moveBeforeInit) h.initializers[0]();
        assert.equal(h.sandbox.getOrCreateTerm('Shell', destination), entry);
        if (moveBeforeInit) h.initializers[0]();
        entry.element.textarea.focus();
        assert.deepEqual(h.focus, [[destination, false]]);
        assert.equal(h.timers.length, 1);
        assert.equal(h.timers[0].delay, 300);
        h.timers[0].callback();
        assert.deepEqual(h.scrolling, [['by', {top: destination === 1 ? 40 : 840, behavior: 'smooth'}]]);
        entry.wterm.onData('test');
        entry.wterm.options.onResize(90, 30);
        assert.deepEqual(h.emitted, [
            ['input', {terminal: destination, data: 'test'}],
            ['resize', {terminal: destination, rows: 30, cols: 90}],
        ]);
        h.sandbox.getOrCreateTerm('Shell', initialPane);
        entry.element.textarea.focus();
        assert.deepEqual(h.focus.at(-1), [initialPane, false]);
        h.sandbox.isSplit = false;
        h.timers.at(-1).callback();
        assert.ok(h.scrolling.every(([type]) => type !== 'to'));
    }
}

const duringDelay = harness();
const entry = duringDelay.sandbox.getOrCreateTerm('Shell', 2);
duringDelay.initializers[0]();
entry.element.textarea.focus();
duringDelay.sandbox.getOrCreateTerm('Shell', 1);
duringDelay.timers[0].callback();
assert.deepEqual(duringDelay.scrolling, [['by', {top: 40, behavior: 'smooth'}]]);

console.log('Terminal focus, scrolling, input, and resize follow pane moves before/after initialization; scroll timing and offsets preserved');

const single = harness();
single.sandbox.isSplit = false;
single.sandbox.window.visualViewport = {height: 400, offsetTop: 80, scale: 1, addEventListener() {}};
const singleEntry = single.sandbox.getOrCreateTerm('Shell', 1);
single.initializers[0]();
const textarea = singleEntry.element.textarea;
let cursorTop = 400;
let gridBottom = 600;
singleEntry.element.querySelector = selector => {
    if (selector === 'textarea') return textarea;
    if (selector === '.term-cursor') return {getBoundingClientRect: () => ({top: cursorTop})};
    if (selector === '.term-grid') return {getBoundingClientRect: () => ({bottom: gridBottom})};
    return singleEntry.element.children.find(child => child.className === 'terminal-focus-spacer') || null;
};
textarea.focus();
single.timers.at(-1).callback();
assert.equal(singleEntry.element.scrollTop, 128.5);
assert.equal(singleEntry.element.querySelector('.terminal-focus-spacer'), null);
assert.equal(singleEntry.element.style['--terminal-keyboard-inset'], '120px');
assert.deepEqual(single.scrolling, []);
cursorTop = 450;
singleEntry.positionInputLine();
assert.equal(singleEntry.element.scrollTop, 128.5);
cursorTop = 480;
gridBottom = 497;
singleEntry.positionInputLine(true);
assert.equal(singleEntry.element.scrollTop, 145.5);
single.sandbox.window.getSelection = () => ({isCollapsed: false});
single.timers.at(-1).callback();
assert.equal(singleEntry.element.scrollTop, 145.5);
console.log('Focus centers an early prompt; typing only reveals hidden rows; bottom prompts remain at the bottom; keyboard reduces viewport without adding scroll content');
