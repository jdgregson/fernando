const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

function fixture(t) {
    const dom = new JSDOM('<div id="messages"></div>', {runScripts: 'outside-only'});
    t.after(() => dom.window.close());
    const win = dom.window;
    let frames = [];
    win.requestAnimationFrame = callback => frames.push(callback);
    win.cancelAnimationFrame = id => { frames[id - 1] = null; };
    let observer;
    win.IntersectionObserver = class {
        constructor(callback, options) {
            this.callback = callback;
            this.options = options;
            this.observed = new Set();
            observer = this;
        }
        observe(image) { this.observed.add(image); }
        unobserve(image) { this.observed.delete(image); }
        disconnect() { this.observed.clear(); }
    };
    vm.runInContext(fs.readFileSync('src/static/js/chat-images.js', 'utf8'), dom.getInternalVMContext());
    const root = win.document.getElementById('messages');
    let loads = 0;
    const loader = new win.ChatImages(root, () => loads++);
    function add(id, visible = true) {
        const image = win.document.createElement('img');
        image.getClientRects = () => visible ? [{}] : [];
        loader.set(image, '/image/' + id + '.png');
        root.append(image);
        return image;
    }
    function flush() {
        const callbacks = frames;
        frames = [];
        callbacks.forEach(callback => callback?.());
    }
    return {win, root, loader, observer, add, flush, loads: () => loads};
}

test('history requests no images before replay finishes, then only the newest three', t => {
    const f = fixture(t);
    const images = Array.from({length: 30}, (_, i) => f.add(i));
    f.flush();
    assert.equal(images.filter(image => image.hasAttribute('src')).length, 0);
    f.loader.resume();
    assert.equal(images.filter(image => image.hasAttribute('src')).length, 0);
    f.flush();
    assert.deepEqual(images.filter(image => image.hasAttribute('src')), images.slice(-3));
    f.observer.callback([{target: images[4], isIntersecting: true}, {target: images[5], isIntersecting: false}]);
    assert.equal(images[4].getAttribute('src'), '/image/4.png');
    assert.equal(images[5].hasAttribute('src'), false);
    images[4].dispatchEvent(new f.win.Event('load'));
    assert.equal(f.loads(), 1);
    assert.equal(images[4].classList.contains('chat-image-pending'), false);
});

test('hidden tool output and recording frames remain deferred; removed images are released', t => {
    const f = fixture(t);
    const images = Array.from({length: 6}, (_, i) => f.add(i));
    const hidden = f.add('hidden', false);
    const removed = f.add('removed');
    removed.remove();
    f.loader.resume();
    f.flush();
    assert.equal(hidden.hasAttribute('src'), false);
    assert.equal(f.loader.pending.has(removed), false);
    assert.deepEqual(images.filter(image => image.hasAttribute('src')), images.slice(-3));
    f.observer.callback([{target: hidden, isIntersecting: true}]);
    assert.equal(hidden.hasAttribute('src'), true);
});

test('reconnect pauses observation and live arrivals do not fetch older images', t => {
    const f = fixture(t);
    const images = Array.from({length: 8}, (_, i) => f.add(i));
    f.loader.resume();
    f.flush();
    f.loader.pause();
    f.observer.callback([{target: images[0], isIntersecting: true}]);
    assert.equal(images[0].hasAttribute('src'), false);
    const live = f.add('live');
    f.flush();
    assert.equal(live.hasAttribute('src'), false);
    f.loader.resume();
    f.flush();
    assert.equal(live.hasAttribute('src'), true);
    assert.equal(images[0].hasAttribute('src'), false);
});

test('native tool image uses the cached image rather than an inaccessible original path', t => {
    const f = fixture(t);
    const source = fs.readFileSync('src/templates/chat.html', 'utf8');
    const start = source.indexOf('    function renderToolImages(');
    const end = source.indexOf('\n    function _renderOutPretty', start);
    f.win.FernandoRecording = {render: () => false};
    f.win.chatImages = f.loader;
    f.win.fileUrl = path => '/api/files/' + path;
    f.win.extractFilePaths = () => ['/blocked/screenshot.png'];
    vm.runInContext('const IMAGE_EXTS = /\\.png$/i;\n' + source.slice(start, end), f.win);
    const div = f.win.document.createElement('div');
    f.root.append(div);
    const tc = {div, status: 'completed', tcState: {rawOutput: {items: [{Image: {type: 'image', mimeType: 'image/png', path: 'cached.png'}}]}}};
    f.win.renderToolImages(tc);
    const image = div.querySelector('img');
    assert.equal(f.loader.sources.get(image), '/api/files/cached.png');
    assert.equal(image.hasAttribute('src'), false);
});
