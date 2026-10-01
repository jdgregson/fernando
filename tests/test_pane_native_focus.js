const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

const source = fs.readFileSync(path.join(__dirname, '../src/static/js/panes.js'), 'utf8');

function workspace({ios = true, split = true, install = true} = {}) {
    const dom = new JSDOM('<!doctype html><body>' + [1, 2].map(id =>
        `<div id="terminal${id}-container"><div id="terminal${id}"></div><div id="browser${id}"></div></div>`
    ).join('') + '<div id="sidebar"></div></body>', {
        url: 'https://fernando.test/', runScripts: 'outside-only', pretendToBeVisual: true,
    });
    const win = dom.window;
    Object.defineProperty(win.navigator, 'userAgent', {value: ios ? 'iPhone' : 'Desktop'});
    const context = dom.getInternalVMContext();
    vm.runInContext(source, context);
    vm.runInContext(`isSplit=${split}`, context);
    const controller = vm.runInContext('paneController', context);
    if (install) controller.installFocusPolicy();
    win.scrollTo = win.scrollBy = () => assert.fail('Focus policy must not move the viewport');
    return {win, doc: win.document, controller, close: () => win.close()};
}

function input(doc, parent, tag = 'textarea') {
    const el = doc.createElement(tag);
    if (tag === 'div') {
        el.setAttribute('contenteditable', 'true');
        el.tabIndex = 0;
        Object.defineProperty(el, 'isContentEditable', {value: true});
    }
    parent.append(el);
    const calls = [];
    const nativeFocus = el.focus.bind(el);
    el.focus = options => {
        if (options?.preventScroll) calls.push(options.preventScroll);
        nativeFocus(options);
    };
    return {el, calls};
}

function fire(el, type) {
    const event = new el.ownerDocument.defaultView.MouseEvent(type, {bubbles: true, composed: true, cancelable: true});
    el.dispatchEvent(event);
    assert.equal(event.defaultPrevented, false);
}

function tap(editor, target = editor.el) {
    fire(target, 'pointerdown');
    fire(target, 'mousedown');
    editor.el.focus();
    if (editor.el.tagName === 'TEXTAREA') {
        editor.el.value = 'preserve selection';
        editor.el.setSelectionRange(2, 5);
    }
    assert.equal(editor.calls.length, 0, 'No opt-out before native focus and caret placement');
    fire(target, 'click');
    if (editor.el.tagName === 'TEXTAREA') {
        assert.equal(editor.el.selectionStart, 2);
        assert.equal(editor.el.selectionEnd, 5);
    }
}

function frame(parent) {
    const el = parent.ownerDocument.createElement('iframe');
    parent.append(el);
    el.dispatchEvent(new parent.ownerDocument.defaultView.Event('load'));
    return el;
}

test('same host policy covers chat, notebook, Jupyter, desktop web inputs, and arbitrary same-origin views', () => {
    for (const type of ['chat', 'notebook', 'jupyter', 'desktop', 'new-session-type']) {
        for (const tag of ['textarea', 'input', 'div']) {
            const w = workspace();
            w.controller.get(1).surface = 'browser';
            w.controller.get(1).contentKey = type + ':example';
            const iframe = frame(w.doc.getElementById('browser1'));
            const editor = input(iframe.contentDocument, iframe.contentDocument.body, tag);
            let target = editor.el;
            if (tag === 'div') {
                target = iframe.contentDocument.createElement('span');
                editor.el.append(target);
            }
            tap(editor, target);
            assert.deepEqual(editor.calls, [true], type + '/' + tag);
            w.close();
        }
    }
});

test('terminal hidden textarea newly focused by the rendered terminal receives the policy', () => {
    const w = workspace();
    const terminal = w.doc.getElementById('terminal1');
    const editor = input(w.doc, terminal);
    editor.el.style.opacity = '0';
    const grid = w.doc.createElement('div');
    terminal.append(grid);
    tap(editor, grid);
    assert.deepEqual(editor.calls, [true]);
    w.close();
});

test('a mounted input moving between panes is resolved at click time', () => {
    const w = workspace();
    const editor = input(w.doc, w.doc.getElementById('terminal2'));
    tap(editor);
    assert.deepEqual(editor.calls, []);
    w.doc.getElementById('terminal1').append(editor.el);
    tap(editor);
    assert.deepEqual(editor.calls, [true]);
    editor.calls.length = 0;
    w.doc.getElementById('terminal2').append(editor.el);
    tap(editor);
    assert.deepEqual(editor.calls, []);
    w.close();
});

test('nested same-origin frames inherit binding through load events', () => {
    const w = workspace();
    w.controller.get(1).surface = 'browser';
    const outer = frame(w.doc.getElementById('browser1'));
    const nested = frame(outer.contentDocument.body);
    const editor = input(nested.contentDocument, nested.contentDocument.body);
    tap(editor);
    assert.deepEqual(editor.calls, [true]);
    w.close();
});

test('new frames inherit the policy on actual load without a session adapter hookup', {timeout: 3000}, async () => {
    const w = workspace();
    w.controller.get(1).surface = 'browser';
    const iframe = w.doc.createElement('iframe');
    const loaded = new Promise(resolve => iframe.addEventListener('load', resolve, {once: true}));
    w.doc.getElementById('browser1').append(iframe);
    await loaded;
    const editor = input(iframe.contentDocument, iframe.contentDocument.body);
    tap(editor);
    assert.deepEqual(editor.calls, [true]);
    w.close();
});

test('existing loaded frames are discovered and repeated installation does not duplicate listeners', () => {
    const w = workspace({install: false});
    w.controller.get(1).surface = 'browser';
    const outer = frame(w.doc.getElementById('browser1'));
    const nested = frame(outer.contentDocument.body);
    const editor = input(nested.contentDocument, nested.contentDocument.body);
    w.controller.installFocusPolicy();
    w.controller.installFocusPolicy();
    outer.dispatchEvent(new w.win.Event('load'));
    tap(editor);
    assert.deepEqual(editor.calls, [true]);
    w.close();
});

test('iframe navigation binds the new document and rejects the old document', () => {
    const w = workspace();
    w.controller.get(1).surface = 'browser';
    const iframe = frame(w.doc.getElementById('browser1'));
    const old = input(iframe.contentDocument, iframe.contentDocument.body);
    iframe.src = 'about:blank';
    iframe.dispatchEvent(new w.win.Event('load'));
    assert.equal(w.controller.viewForElement(old.el), null);
    const next = input(iframe.contentDocument, iframe.contentDocument.body);
    tap(next);
    assert.deepEqual(next.calls, [true]);
    w.close();
});

test('detached and retained hidden iframe documents do not receive focus changes', () => {
    const w = workspace();
    w.controller.get(1).surface = 'browser';
    const iframe = frame(w.doc.getElementById('browser1'));
    const editor = input(iframe.contentDocument, iframe.contentDocument.body);
    w.controller.get(1).surface = 'terminal';
    tap(editor);
    assert.deepEqual(editor.calls, []);
    iframe.remove();
    assert.equal(w.controller.viewForElement(editor.el), null);
    w.close();
});

test('opaque/cross-origin documents and frames outside registered panes are not bound', () => {
    const w = workspace();
    const outside = frame(w.doc.getElementById('sidebar'));
    const editor = input(outside.contentDocument, outside.contentDocument.body);
    tap(editor);
    assert.deepEqual(editor.calls, []);
    const opaque = w.doc.createElement('iframe');
    w.doc.getElementById('browser1').append(opaque);
    Object.defineProperty(opaque, 'contentDocument', {get: () => null});
    assert.doesNotThrow(() => opaque.dispatchEvent(new w.win.Event('load')));
    w.close();
});

test('editable fields inside open shadow roots preserve native focus and selection', () => {
    const w = workspace();
    const host = w.doc.createElement('div');
    w.doc.getElementById('terminal1').append(host);
    const shadow = host.attachShadow({mode: 'open'});
    const editor = input(w.doc, shadow);
    tap(editor);
    assert.deepEqual(editor.calls, [true]);
    w.close();
});

test('desktop, single-pane, and bottom-pane behavior are unchanged', () => {
    for (const config of [{ios: false}, {split: false}, {pane: 2}]) {
        const w = workspace(config);
        const pane = config.pane || 1;
        w.controller.get(pane).surface = 'browser';
        const iframe = frame(w.doc.getElementById('browser' + pane));
        const editor = input(iframe.contentDocument, iframe.contentDocument.body);
        tap(editor);
        assert.deepEqual(editor.calls, []);
        w.close();
    }
});

test('unrelated clicks, cancelled gestures, and controls without editable focus are left alone', () => {
    const w = workspace();
    const editor = input(w.doc, w.doc.getElementById('terminal1'));
    const other = w.doc.createElement('span');
    w.doc.getElementById('terminal1').append(other);
    editor.el.focus();
    fire(other, 'pointerdown');
    fire(other, 'click');
    assert.deepEqual(editor.calls, []);
    editor.el.blur();
    fire(other, 'pointerdown');
    editor.el.focus();
    fire(other, 'pointercancel');
    fire(other, 'click');
    assert.deepEqual(editor.calls, []);
    for (const kind of ['checkbox', 'radio', 'range', 'button']) {
        const control = input(w.doc, w.doc.getElementById('terminal1'), 'input');
        control.el.type = kind;
        tap(control);
        assert.deepEqual(control.calls, []);
    }
    editor.el.readOnly = true;
    tap(editor);
    assert.deepEqual(editor.calls, []);
    w.close();
});
