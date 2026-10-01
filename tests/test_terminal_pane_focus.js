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
    function element() {
        const listeners = new Map();
        const textarea = {
            addEventListener(type, callback) { listeners.set(type, callback); },
            focus() { listeners.get('focus')(); },
        };
        return {
            children: [],
            appendChild(child) { this.children.push(child); },
            querySelector(selector) { return selector === 'textarea' ? textarea : null; },
            textarea,
        };
    }
    const containers = {terminal1: element(), terminal2: element()};
    const sandbox = vm.createContext({
        termInstances: {},
        document: {
            createElement: element,
            getElementById(id) {
                if (containers[id]) return containers[id];
                assert.ok(['terminal1-container', 'terminal2-container'].includes(id));
                return {getBoundingClientRect: () => ({top: id === 'terminal1-container' ? 42 : 842})};
            },
            body: {scrollHeight: 1800},
        },
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
        assert.deepEqual(h.scrolling.at(-1), ['to', {top: 1800, behavior: 'smooth'}]);
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
