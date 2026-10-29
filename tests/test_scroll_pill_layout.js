const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const mutations = [];
const resizes = [];
const parent = {style: {}, appendChild(node) { this.pill = node; }};
const element = {
    offsetParent: parent,
    parentElement: parent,
    scrollHeight: 400,
    clientHeight: 100,
    scrollTop: 300,
    offsetTop: 20,
    getClientRects() { return this.hidden ? [] : [{}]; },
    addEventListener() {}
};
const context = {
    document: {
        body: {},
        documentElement: {},
        querySelectorAll() { return []; },
        createElement() {
            return {style: {}, classList: {add() {}, remove() {}}, addEventListener() {}};
        }
    },
    window: {},
    getComputedStyle() { return {position: 'relative'}; },
    setTimeout() { return 1; },
    clearTimeout() {},
    MutationObserver: class {
        constructor(callback) { this.callback = callback; }
        observe(target) { mutations.push({target, callback: this.callback}); }
    },
    ResizeObserver: class {
        constructor(callback) { this.callback = callback; }
        observe(target) { resizes.push({target, callback: this.callback}); }
    }
};
vm.runInNewContext(fs.readFileSync('src/static/js/scroll-pill.js', 'utf8'), context);
context.window._attachScrollPill(element);
assert.equal(parent.pill.style.display, '');
assert.equal(parent.pill.style.top, '95px');
element.hidden = true;
element.clientHeight = 0;
element.scrollHeight = 0;
mutations.find(observer => observer.target === element).callback();
assert.equal(parent.pill.style.display, 'none');
element.hidden = false;
element.clientHeight = 100;
element.scrollHeight = 400;
element.scrollTop = 0;
resizes.find(observer => observer.target === element).callback();
assert.equal(parent.pill.style.display, '');
assert.equal(parent.pill.style.top, '20px');
element.scrollHeight = 100;
mutations.find(observer => observer.target === element).callback();
assert.equal(parent.pill.style.display, 'none');
console.log('Scroll pill collapse, restore and non-overflow checks passed');
