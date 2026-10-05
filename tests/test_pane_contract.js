const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

function workspace(search = '') {
    const dom = new JSDOM(fs.readFileSync('src/templates/index.html', 'utf8'), {
        url: 'https://fernando.test/' + search, runScripts: 'outside-only', pretendToBeVisual: true,
    });
    const win = dom.window;
    const context = dom.getInternalVMContext();
    const events = new Map();
    const emitted = [];
    win.socket = {on: (name, callback) => events.set(name, callback), connected: true};
    win.emitWithCsrf = (name, data) => emitted.push({name, data});
    win.doFit = win.scrollBy = win.applyProviderSettings = () => {};
    win.showConfirm = () => Promise.resolve(true);
    win.FERNANDO_API_KEY = 'fixture';
    win._paneSession = {1: null, 2: null};
    win.term1 = win.term2 = {focus() {}};
    win.showTermInPane = () => ({ready: true, firstAttach: false, wterm: {focus() {}}});
    for (const name of ['panes', 'session-adapters', 'file-session', 'sessions', 'chat', 'mobile']) {
        vm.runInContext(fs.readFileSync('src/static/js/' + name + '.js', 'utf8'), context);
    }
    const controller = vm.runInContext('paneController', context);
    controller.installBridge();
    return {dom, win, context, controller, events, emitted, close: () => win.close()};
}

test('one new adapter inherits sidebar, lifecycle, URL restoration, context, mobile controls, and validated activation', () => {
    const w = workspace();
    const {win, controller} = w;
    let closed = 0;
    let released = 0;
    controller.register('fixture', {
        matches: key => key.startsWith('fixture:'),
        context: key => ({type: 'fixture', description: key}),
        sessions: data => (data.fixture_sessions || []).map(name => ({key: 'fixture:' + name, name})),
        sidebar: () => ({}),
        mount({browser, key}) {
            browser.replaceChildren();
            const frame = win.document.createElement('iframe');
            frame.src = '/fixture/' + encodeURIComponent(key.slice(8));
            browser.appendChild(frame);
        },
        unmount: () => released++,
        close: () => closed++,
    });
    vm.runInContext('window._urlParamsProcessed = true', w.context);
    win.updateSessionList([], [], {fixture_sessions: ['<literal>']});
    const item = win.document.querySelector('[data-session="fixture:<literal>"]');
    assert.equal(item.querySelector('.session-name').textContent, '<literal>');
    assert.equal(item.querySelector('.session-name').children.length, 0);
    item.click();
    assert.equal(controller.sessionKey(1), 'fixture:<literal>');
    assert.equal(new URL(win.location).searchParams.get('session'), 'fixture:<literal>');
    assert.equal(controller.context(1).description, 'fixture:<literal>');
    assert.equal(win.document.getElementById('mobileControls').classList.contains('chat-active'), true);
    const oldFrame = controller.elements(1).browser.querySelector('iframe');
    const oldSource = oldFrame.contentWindow;
    vm.runInContext('isSplit = true; activeTerminal = 2', w.context);
    const message = (source, origin, version = 1) => win.dispatchEvent(new win.MessageEvent('message', {
        source, origin, data: {type: 'fernando-pane', version, event: 'activate', payload: {}},
    }));
    message(win, win.location.origin);
    message(oldSource, 'https://wrong.test');
    message(oldSource, win.location.origin, 2);
    assert.equal(vm.runInContext('activeTerminal', w.context), 2);
    message(oldSource, win.location.origin);
    assert.equal(vm.runInContext('activeTerminal', w.context), 1);
    item.querySelector('.close-btn').click();
    assert.equal(closed, 1);
    assert.equal(released, 1);
    assert.equal(controller.sessionKey(1), null);
    vm.runInContext('activeTerminal = 2', w.context);
    message(oldSource, win.location.origin);
    assert.equal(vm.runInContext('activeTerminal', w.context), 2);
    win.history.replaceState(null, '', '/?session=fixture:restored');
    vm.runInContext('activeTerminal = 1; window._urlParamsProcessed = false', w.context);
    controller.restore([]);
    assert.equal(controller.sessionKey(1), 'fixture:restored');
    w.close();
});

test('existing adapters preserve reuse, terminal movement, sidebar actions, and notebook readiness', () => {
    const w = workspace();
    const {win, controller} = w;
    vm.runInContext('window._urlParamsProcessed = true', w.context);
    win.updateSessionList(['Shell'], [{id: 'a', name: 'Chat A', loaded: false}], {
        running_notebooks: ['fernando'], running_jupyter: ['Jupyter'],
    });
    assert.deepEqual([...win.document.querySelectorAll('#sessionList .session-item')].map(item => item.dataset.session),
        ['desktop', 'jupyter:Jupyter', 'notebook:fernando', 'Shell', 'chat:a']);
    win.openChatPane('a');
    const frame = controller.elements(1).browser.querySelector('iframe');
    win.openChatPane('a');
    assert.equal(controller.elements(1).browser.querySelector('iframe'), frame);
    vm.runInContext('activeTerminal = 2; isSplit = true', w.context);
    win.openChatPane('a');
    assert.notEqual(controller.elements(2).browser.querySelector('iframe'), frame);
    win.attachSession('Shell');
    vm.runInContext('activeTerminal = 1', w.context);
    win.attachSession('Shell');
    assert.equal(controller.get(2).terminalSession, null);
    assert.equal(controller.get(1).terminalSession, 'Shell');
    assert.ok(w.emitted.some(event => event.name === 'detach_viewer' && event.data.terminal === 2));
    win.openNotebook('fernando');
    w.events.get('notebook_started')({name: 'fernando'});
    assert.ok(controller.elements(1).browser.querySelector('iframe').src.includes('/notes/fernando/'));
    win.openChatPane('a');
    win.closeChatSession('a');
    assert.equal(controller.get(1).surface, 'terminal');
    assert.ok(w.emitted.some(event => event.name === 'acp_close' && event.data.session_id === 'a'));
    w.close();
});

test('file browser views retain independent locations in both panes and across restoration', () => {
    const w = workspace();
    const {win, controller} = w;
    vm.runInContext('window._urlParamsProcessed = true', w.context);
    controller.open('files:fixture?path=%2Fhome%2Ffernando');
    vm.runInContext('activeTerminal = 2; isSplit = true', w.context);
    controller.open('files:fixture?path=%2Ftmp');
    const frame = controller.elements(2).browser.querySelector('iframe');
    win.dispatchEvent(new win.MessageEvent('message', {source: frame.contentWindow, origin: win.location.origin, data: {type:'fernando-pane', version:1, event:'navigate', payload:{name:'var',path:'/var'}}}));
    assert.equal(controller.get(1).location, '/home/fernando');
    assert.equal(controller.get(2).location, '/var');
    assert.equal(controller.sessionKey(1), controller.sessionKey(2));
    assert.equal(controller.context(2).description, 'File browser at /var');
    const restored = workspace(win.location.search);
    restored.controller.restore([]);
    assert.equal(restored.controller.get(1).location, '/home/fernando');
    assert.equal(restored.controller.get(2).location, '/var');
    restored.controller.get(2).location = '/etc';
    assert.equal(controller.get(2).location, '/var');
    restored.close();
    w.close();
});

test('file sidebar labels use active view paths, home shorthand, trailing 50 characters and full path tooltips', () => {
    const w = workspace();
    const metadata = {id: 'fixture', name: 'Files-1', home: '/home/fernando'};
    assert.equal(w.win.fileSessionPresentation(metadata).name, '~');
    w.controller.open('files:fixture?path=%2Fhome%2Ffernando%2Fprojects');
    assert.equal(w.win.fileSessionPresentation(metadata).name, '~/projects');
    const path = '/etc/' + 'very-long-path/'.repeat(5) + 'config';
    w.controller.get(1).location = path;
    const session = w.win.fileSessionPresentation(metadata);
    assert.equal(session.name, '…' + path.slice(-50));
    const item = w.controller.sidebarItem({...session, adapter: w.controller.adapter(session.key)});
    assert.equal(item.title, path);
    assert.equal(item.querySelector('.session-name').textContent, session.name);
    w.controller.get(1).location = '/home/fernando-other';
    assert.equal(w.win.fileSessionPresentation(metadata).name, '/home/fernando-other');
    w.close();
});

test('bridge owns destination, protocol version, activation wiring, and parent validation', () => {
    const dom = new JSDOM('<script src="https://fernando.test/static/js/pane-bridge.js" data-activate-on="pointerdown" data-activate-capture="true"></script><canvas></canvas>', {
        url: 'https://app.test/', runScripts: 'outside-only',
    });
    const win = dom.window;
    const messages = [];
    const parent = {postMessage: (message, origin) => messages.push({message, origin})};
    Object.defineProperty(win, 'parent', {value: parent});
    Object.defineProperty(win.document, 'currentScript', {value: win.document.querySelector('script')});
    win.eval(fs.readFileSync('src/static/js/pane-bridge.js', 'utf8'));
    const canvas = win.document.querySelector('canvas');
    canvas.addEventListener('pointerdown', event => { event.stopPropagation(); event.preventDefault(); });
    canvas.dispatchEvent(new win.MouseEvent('pointerdown', {bubbles: true, cancelable: true}));
    win.FernandoPane.navigate({name: 'Page', path: '/page'});
    assert.deepEqual(messages.map(entry => entry.message.event), ['ready', 'activate', 'navigate']);
    assert.ok(messages.every(entry => entry.origin === 'https://fernando.test' && entry.message.version === 1));
    let received = 0;
    win.FernandoPane.on('state', () => received++);
    for (const [source, origin] of [[win, 'https://fernando.test'], [parent, 'https://wrong.test'], [parent, 'https://fernando.test']]) {
        win.dispatchEvent(new win.MessageEvent('message', {source, origin, data: {type: 'fernando-pane', version: 1, event: 'state', payload: {active: true}}}));
    }
    assert.equal(received, 1);
    win.close();
});
