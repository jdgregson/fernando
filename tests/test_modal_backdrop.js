const assert = require('node:assert/strict');
const fs = require('node:fs');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

function fixture(t) {
    const dom = new JSDOM('<div id="overlay"><div id="content"><button>Close</button></div></div>', {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const win = dom.window;
    const doc = win.document;
    const overlay = doc.getElementById('overlay');
    const content = doc.getElementById('content');
    let dismissals = 0;
    let hit = overlay;
    doc.elementFromPoint = () => hit;
    win.eval(fs.readFileSync('src/static/js/modal.js', 'utf8'));
    win.bindModalBackdrop(overlay, () => dismissals++);
    function fire(type, target, options = {}) {
        const event = new win.MouseEvent(type, {bubbles: true, button: 0, ...options});
        Object.defineProperties(event, {
            pointerId: {value: options.pointerId ?? 1},
            isPrimary: {value: options.isPrimary ?? true},
        });
        target.dispatchEvent(event);
    }
    return {win, overlay, content, fire, dismissals: () => dismissals, hit: target => { hit = target; }};
}

test('a press and release on the backdrop dismisses once', t => {
    const w = fixture(t);
    w.fire('pointerdown', w.overlay);
    w.fire('pointerup', w.overlay);
    assert.equal(w.dismissals(), 0);
    w.fire('click', w.overlay);
    w.fire('click', w.overlay);
    assert.equal(w.dismissals(), 1);
});

test('dragging from content to backdrop does not dismiss, even when click targets their common ancestor', t => {
    const w = fixture(t);
    w.content.addEventListener('pointerdown', event => event.stopPropagation());
    w.fire('pointerdown', w.content);
    w.fire('pointerup', w.overlay);
    w.fire('click', w.overlay);
    assert.equal(w.dismissals(), 0);
    w.fire('pointerdown', w.overlay);
    w.fire('pointerup', w.overlay);
    w.fire('click', w.overlay);
    assert.equal(w.dismissals(), 1);
});

test('dragging from backdrop into content does not dismiss', t => {
    const w = fixture(t);
    w.fire('pointerdown', w.overlay);
    w.hit(w.content);
    w.fire('pointerup', w.content);
    w.fire('click', w.overlay);
    assert.equal(w.dismissals(), 0);
});

test('implicit touch capture cannot turn a release over content into a backdrop click', t => {
    const w = fixture(t);
    w.fire('pointerdown', w.overlay);
    w.hit(w.content);
    w.fire('pointerup', w.overlay);
    w.fire('click', w.overlay);
    assert.equal(w.dismissals(), 0);
});

test('cancelled, secondary-button, and mismatched-pointer gestures do not dismiss', t => {
    const w = fixture(t);
    w.fire('pointerdown', w.overlay);
    w.fire('pointercancel', w.overlay);
    w.fire('pointerup', w.overlay);
    w.fire('click', w.overlay);
    w.fire('pointerdown', w.overlay, {button: 2});
    w.fire('pointerup', w.overlay, {button: 2});
    w.fire('click', w.overlay);
    w.fire('pointerdown', w.overlay, {isPrimary: false});
    w.fire('pointerup', w.overlay);
    w.fire('click', w.overlay);
    w.fire('pointerdown', w.overlay, {pointerId: 1});
    w.fire('pointerup', w.overlay, {pointerId: 2});
    w.fire('click', w.overlay);
    assert.equal(w.dismissals(), 0);
});

test('content controls still receive clicks without dismissing the backdrop', t => {
    const w = fixture(t);
    const button = w.content.querySelector('button');
    let clicks = 0;
    button.addEventListener('click', () => clicks++);
    w.content.addEventListener('click', event => event.stopPropagation());
    w.fire('pointerdown', button);
    w.hit(button);
    w.fire('pointerup', button);
    w.fire('click', button);
    assert.equal(clicks, 1);
    assert.equal(w.dismissals(), 0);
});

test('modal instances track separate gestures', t => {
    const w = fixture(t);
    const second = w.overlay.ownerDocument.createElement('div');
    w.overlay.after(second);
    let dismissals = 0;
    w.win.bindModalBackdrop(second, () => dismissals++);
    w.fire('pointerdown', w.overlay);
    w.hit(second);
    w.fire('pointerup', second);
    w.fire('click', second);
    assert.equal(dismissals, 0);
    assert.equal(w.dismissals(), 0);
});

function sidebarFixture(t) {
    const w = fixture(t);
    const doc = w.overlay.ownerDocument;
    const sidebar = w.content;
    sidebar.id = 'sidebar';
    sidebar.classList.add('open');
    const toggle = doc.createElement('button');
    toggle.className = 'sidebar-toggle';
    toggle.innerHTML = '<svg><path></path></svg>';
    doc.body.append(toggle);
    const outside = doc.createElement('div');
    w.overlay.append(outside);
    w.win.innerWidth = 400;
    const source = fs.readFileSync('src/static/js/sessions.js', 'utf8');
    const start = source.indexOf('bindBackdropDismissal(document,');
    assert.notEqual(start, -1);
    w.win.eval(source.slice(start, source.indexOf('// --- Initial load ---', start)));
    return {...w, sidebar, toggle, outside};
}

test('flyout sidebar ignores inside-to-outside drags and closes on an outside click', t => {
    const w = sidebarFixture(t);
    w.fire('pointerdown', w.sidebar);
    w.hit(w.outside);
    w.fire('pointerup', w.outside);
    w.fire('click', w.overlay);
    assert.equal(w.sidebar.classList.contains('open'), true);
    w.fire('pointerdown', w.outside);
    w.fire('pointerup', w.outside);
    w.fire('click', w.outside);
    assert.equal(w.sidebar.classList.contains('open'), false);
});

test('flyout sidebar ignores reverse drags, toggle icon gestures, and desktop outside clicks', t => {
    const w = sidebarFixture(t);
    w.fire('pointerdown', w.outside);
    w.hit(w.sidebar);
    w.fire('pointerup', w.sidebar);
    w.fire('click', w.overlay);
    assert.equal(w.sidebar.classList.contains('open'), true);
    w.fire('pointerdown', w.toggle.querySelector('path'));
    w.hit(w.outside);
    w.fire('pointerup', w.outside);
    w.fire('click', w.overlay);
    assert.equal(w.sidebar.classList.contains('open'), true);
    w.win.innerWidth = 1000;
    w.fire('pointerdown', w.outside);
    w.fire('pointerup', w.outside);
    w.fire('click', w.outside);
    assert.equal(w.sidebar.classList.contains('open'), true);
});
