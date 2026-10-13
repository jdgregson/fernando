const assert = require('node:assert/strict');
const fs = require('node:fs');
const { test } = require('node:test');
const { JSDOM } = require('jsdom');

function fixture(t) {
    const dom = new JSDOM('<button id="anchor">Color</button>', { runScripts: 'outside-only' });
    t.after(() => dom.window.close());
    const source = fs.readFileSync('src/static/js/sessions.js', 'utf8');
    const win = dom.window;
    const messages = [];
    win.emitWithCsrf = (name, data) => messages.push({ name, ...data });
    win.eval("const GROUP_COLORS = ['#263fce']; const _cachedGroups = [{id:'test',color:'#ffffff'}];\n" + source.slice(source.indexOf('function groupColorForeground('), source.indexOf('let contextMenuCleanup')));
    win.showGroupColorPicker('test', win.document.getElementById('anchor'));
    return { win, doc: win.document, messages };
}

test('foreground maintains WCAG AA contrast throughout RGB color space', t => {
    const { win } = fixture(t);
    for (let r = 0; r <= 255; r += 15) for (let g = 0; g <= 255; g += 15) for (let b = 0; b <= 255; b += 15) {
        const hex = '#' + [r, g, b].map(c => c.toString(16).padStart(2, '0')).join('');
        const linear = [r, g, b].map(c => c / 255 <= 0.04045 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4);
        const luminance = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
        const contrast = win.groupColorForeground(hex) === '#000000' ? (luminance + 0.05) / 0.05 : 1.05 / (luminance + 0.05);
        assert.ok(contrast >= 4.5, hex);
    }
});

test('custom panel uses themed input and Select button, saving only confirmed valid edits', t => {
    const { win, doc, messages } = fixture(t);
    doc.querySelector('.custom-color-swatch').click();
    assert.equal(doc.querySelector('input[type=color]'), null);
    const hex = doc.querySelector('input[type=text]');
    assert.equal(doc.querySelectorAll('.group-custom-panel > *').length, 2);
    const button = doc.querySelector('.group-custom-panel button');
    assert.equal(button.textContent, 'Select');
    assert.ok(button.classList.contains('sa-btn'));
    assert.equal(doc.querySelector('input[type=range]'), null);
    assert.ok(hex.classList.contains('settings-input'));
    assert.equal(hex.value, '#ffffff');
    hex.value = '#bad';
    hex.dispatchEvent(new win.Event('input'));
    assert.equal(hex.getAttribute('aria-invalid'), 'true');
    assert.equal(button.disabled, true);
    assert.equal(messages.length, 0);
    hex.value = '#123456';
    hex.dispatchEvent(new win.Event('input'));
    assert.equal(hex.getAttribute('aria-invalid'), 'false');
    assert.equal(button.disabled, false);
    assert.equal(messages.length, 0);
    button.click();
    assert.deepEqual(messages, [{ name: 'group_set_color', group_id: 'test', color: '#123456' }]);
    assert.equal(doc.querySelector('.group-color-picker'), null);
});

test('color area edits remain pending until selected and escape dismisses', t => {
    const { win, doc, messages } = fixture(t);
    doc.querySelector('.custom-color-swatch').click();
    const field = doc.querySelector('.group-color-field');
    let captured = false;
    field.setPointerCapture = () => { captured = true; };
    field.hasPointerCapture = () => captured;
    field.releasePointerCapture = () => { captured = false; };
    field.getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 100 });
    field.dispatchEvent(new win.MouseEvent('pointerdown', { button: 0, clientX: 100, clientY: 0 }));
    assert.equal(messages.length, 0);
    assert.equal(doc.querySelector('input[type=text]').value, '#ff0000');
    field.dispatchEvent(new win.MouseEvent('pointerup', { button: 0, clientX: 100, clientY: 0 }));
    assert.equal(messages.length, 0);
    doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape' }));
    assert.equal(doc.querySelector('.group-color-picker'), null);
});

test('hue ring selects colors outside the initial hue and preserves saturation and brightness', t => {
    const { win, doc, messages } = fixture(t);
    doc.querySelector('.custom-color-swatch').click();
    const hex = doc.querySelector('input[type=text]');
    hex.value = '#ff0000';
    hex.dispatchEvent(new win.Event('input'));
    const ring = doc.querySelector('.group-color-ring');
    ring.setPointerCapture = () => {};
    ring.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 200 });
    ring.dispatchEvent(new win.MouseEvent('pointerdown', { button: 0, clientX: 100, clientY: 200 }));
    assert.equal(hex.value, '#00ffff');
    assert.equal(messages.length, 0);
    hex.value = '#804040';
    hex.dispatchEvent(new win.Event('input'));
    ring.dispatchEvent(new win.MouseEvent('pointerdown', { button: 0, clientX: 100, clientY: 200 }));
    assert.equal(hex.value, '#408080');
    doc.querySelector('.group-custom-panel button').click();
    assert.deepEqual(messages, [{ name: 'group_set_color', group_id: 'test', color: '#408080' }]);
});

test('custom picker is a separate pop-out and stays within desktop and mobile viewports', t => {
    const { win, doc } = fixture(t);
    const picker = doc.querySelector('.group-color-picker');
    Object.defineProperties(picker, {
        offsetWidth: { value: 132 },
        offsetHeight: { value: 102 }
    });
    picker.getBoundingClientRect = () => {
        const left = parseFloat(picker.style.left), top = parseFloat(picker.style.top);
        return { left, top, right: left + 132, bottom: top + 102 };
    };
    doc.querySelector('.custom-color-swatch').click();
    const panel = doc.querySelector('.group-custom-panel');
    assert.equal(panel.parentNode, doc.body);
    assert.equal(picker.contains(panel), false);
    Object.defineProperties(panel, {
        offsetWidth: { get: () => Math.min(258, parseFloat(panel.style.maxWidth)) },
        offsetHeight: { get: () => Math.min(300, parseFloat(panel.style.maxHeight)) }
    });
    for (const [width, height] of [[1280, 720], [390, 844], [320, 568], [844, 390]]) {
        win.innerWidth = width;
        win.innerHeight = height;
        win.dispatchEvent(new win.Event('resize'));
        const x = parseFloat(panel.style.left), y = parseFloat(panel.style.top);
        assert.ok(x >= 8 && x + panel.offsetWidth <= width - 8);
        assert.ok(y >= 8 && y + panel.offsetHeight <= height - 8);
        if (width >= 844) assert.equal(x, picker.getBoundingClientRect().right + 6);
        else assert.equal(y, picker.getBoundingClientRect().bottom + 6);
    }
    const viewport = new win.EventTarget();
    Object.assign(viewport, { width: 320, height: 240, offsetLeft: 0, offsetTop: 120 });
    Object.defineProperty(win, 'visualViewport', { value: viewport });
    win.dispatchEvent(new win.Event('resize'));
    assert.ok(parseFloat(panel.style.top) >= 128);
    assert.ok(parseFloat(panel.style.top) + panel.offsetHeight <= 352);
    panel.dispatchEvent(new win.MouseEvent('pointerdown', { bubbles: true }));
    assert.ok(panel.isConnected);
    doc.body.dispatchEvent(new win.MouseEvent('pointerdown', { bubbles: true }));
    assert.equal(panel.isConnected, false);
    assert.equal(picker.isConnected, false);
});

test('opening custom picker does not move presets when anchor hover geometry changes', t => {
    const { doc } = fixture(t);
    const picker = doc.querySelector('.group-color-picker');
    const initialLeft = picker.style.left;
    const initialTop = picker.style.top;
    doc.getElementById('anchor').getBoundingClientRect = () => ({ left: 40, bottom: 50 });
    doc.querySelector('.custom-color-swatch').click();
    assert.equal(picker.style.left, initialLeft);
    assert.equal(picker.style.top, initialTop);
});

test('desktop mouse-out dismisses after a grace period; pop-out, dragging, and touch stay open', async t => {
    const { win, doc } = fixture(t);
    win.matchMedia = () => ({ matches: true });
    doc.querySelector('.custom-color-swatch').click();
    const picker = doc.querySelector('.group-color-picker');
    const panel = doc.querySelector('.group-custom-panel');
    const move = (target, pointerType = 'mouse', buttons = 0) => {
        const event = new win.MouseEvent('pointermove', { bubbles: true, buttons });
        Object.defineProperty(event, 'pointerType', { value: pointerType });
        target.dispatchEvent(event);
    };
    const wait = () => new Promise(resolve => setTimeout(resolve, 300));
    move(doc.body);
    move(panel);
    await wait();
    assert.ok(picker.isConnected);
    move(doc.body, 'touch');
    await wait();
    assert.ok(picker.isConnected);
    move(doc.body, 'mouse', 1);
    await wait();
    assert.ok(picker.isConnected);
    win.matchMedia = () => ({ matches: false });
    move(doc.body);
    await wait();
    assert.ok(picker.isConnected);
    win.matchMedia = () => ({ matches: true });
    move(doc.body);
    assert.ok(picker.isConnected);
    await wait();
    assert.equal(picker.isConnected, false);
    assert.equal(panel.isConnected, false);
});

for (const destination of ['top pane', 'bottom pane', 'outside document']) {
    test(`mouse crossing into ${destination} dismisses both color menus without further parent events`, async t => {
        const { win, doc } = fixture(t);
        doc.querySelector('.custom-color-swatch').click();
        const picker = doc.querySelector('.group-color-picker');
        const panel = doc.querySelector('.group-custom-panel');
        const frame = destination === 'outside document' ? null : doc.createElement('iframe');
        if (frame) {
            frame.id = destination === 'top pane' ? 'pane1' : 'pane2';
            doc.body.append(frame);
        }
        const event = new win.MouseEvent('pointerout', { bubbles: true, relatedTarget: frame });
        Object.defineProperty(event, 'pointerType', { value: 'mouse' });
        panel.dispatchEvent(event);
        await new Promise(resolve => setTimeout(resolve, 300));
        assert.equal(picker.isConnected, false);
        assert.equal(panel.isConnected, false);
    });
}

test('touch leaving the parent document does not dismiss the color menus', async t => {
    const { win, doc } = fixture(t);
    doc.querySelector('.custom-color-swatch').click();
    const event = new win.MouseEvent('pointerout', { bubbles: true, relatedTarget: null });
    Object.defineProperty(event, 'pointerType', { value: 'touch' });
    doc.querySelector('.group-custom-panel').dispatchEvent(event);
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.ok(doc.querySelector('.group-color-picker'));
    assert.ok(doc.querySelector('.group-custom-panel'));
});
