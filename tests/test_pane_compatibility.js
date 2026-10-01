const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const baseline = 'be229fa121501eb6f1c6fbb9a10b5972435be219';
const files = ['sessions.js', 'chat.js', 'mobile.js'];
const previous = Object.fromEntries(files.map(name => [name,
    execFileSync('git', ['show', `${baseline}:src/static/js/${name}`], {encoding: 'utf8'})]));
const current = Object.fromEntries(files.map(name => [name,
    fs.readFileSync('src/static/js/' + name, 'utf8')]));
const controller = fs.readFileSync('src/static/js/panes.js', 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));

function environment(refactored, search = '', width = 393) {
    const trace = [];
    const elements = new Map();
    const listeners = new Map();
    const socketListeners = new Map();
    const timers = new Map();
    const storage = new Map();
    let nextTimer = 0;
    let nextElement = 0;
    let now = 10000;

    function element(tag = 'div', id = '') {
        const classes = new Set();
        const events = new Map();
        let html = '';
        const el = {
            tagName: tag.toUpperCase(), id, serial: id || `node${++nextElement}`,
            children: [], dataset: {}, attributes: {}, parentNode: null,
            value: '', textContent: '', draggable: false,
            style: {
                removeProperty(name) { delete this[name]; },
                setProperty(name, value) { this[name] = value; },
            },
            get className() { return [...classes].join(' '); },
            set className(value) { classes.clear(); value.split(/\s+/).filter(Boolean).forEach(c => classes.add(c)); },
            classList: {
                add(...names) { names.forEach(n => classes.add(n)); },
                remove(...names) { names.forEach(n => classes.delete(n)); },
                contains(name) { return classes.has(name); },
                toggle(name, force) {
                    const next = force === undefined ? !classes.has(name) : !!force;
                    if (next) classes.add(name); else classes.delete(name);
                    return next;
                },
            },
            get innerHTML() { return html; },
            set innerHTML(value) { html = value; el.children.forEach(c => { c.parentNode = null; }); el.children = []; },
            appendChild(child) {
                if (child.tagName === 'FRAGMENT') {
                    [...child.children].forEach(c => el.appendChild(c));
                    return child;
                }
                if (child.parentNode) child.remove();
                child.parentNode = el;
                el.children.push(child);
                return child;
            },
            append(...children) { children.forEach(c => el.appendChild(c)); },
            replaceChildren(...children) { el.innerHTML = ''; el.append(...children); },
            remove() {
                if (el.parentNode) el.parentNode.children = el.parentNode.children.filter(c => c !== el);
                el.parentNode = null;
            },
            replaceWith(replacement) {
                const parent = el.parentNode;
                const index = parent.children.indexOf(el);
                parent.children[index] = replacement;
                replacement.parentNode = parent;
                el.parentNode = null;
            },
            setAttribute(name, value) { el.attributes[name] = value; },
            addEventListener(name, callback) {
                if (!events.has(name)) events.set(name, []);
                events.get(name).push(callback);
            },
            fire(name, properties = {}) {
                const event = {
                    detail: 1, clientX: 30, clientY: 40,
                    touches: [{clientX: 30, clientY: 40}],
                    stopPropagation() { trace.push(['stopPropagation']); },
                    preventDefault() { trace.push(['preventDefault']); },
                    ...properties,
                };
                for (const callback of events.get(name) || []) callback.call(el, event);
                if (typeof el['on' + name] === 'function') el['on' + name](event);
                return event;
            },
            querySelector(selector) { return el.querySelectorAll(selector)[0] || null; },
            querySelectorAll(selector) { return descendants(el).filter(child => matches(child, selector)); },
            getBoundingClientRect() { return {top: id === 'terminal2-container' ? 840 : 42, left: 0, right: width, bottom: 800, width, height: 800}; },
            focus() { trace.push(['focus', el.serial]); },
            blur() { trace.push(['blur', el.serial]); },
            select() { trace.push(['select', el.serial]); },
            contains(child) { return descendants(el).includes(child); },
            options: [],
        };
        if (tag === 'iframe') el.contentWindow = {postMessage(message, origin) { trace.push(['post', el.src, copy(message), origin]); }};
        if (id) elements.set(id, el);
        return el;
    }

    function descendants(el) {
        return el.children.flatMap(child => [child, ...descendants(child)]);
    }

    function matches(el, selector) {
        const negative = [...selector.matchAll(/:not\(\.([\w-]+)\)/g)].map(m => m[1]);
        if (negative.some(c => el.classList.contains(c))) return false;
        selector = selector.replace(/:not\([^)]+\)/g, '');
        const id = selector.match(/^#([\w-]+)/);
        if (id && el.id !== id[1]) return false;
        const tag = selector.match(/^[a-z]+/);
        if (tag && el.tagName !== tag[0].toUpperCase()) return false;
        if ([...selector.matchAll(/\.([\w-]+)/g)].some(m => !el.classList.contains(m[1]))) return false;
        const session = selector.match(/\[data-session="([^"]*)"\]/);
        if (session && el.dataset.session !== session[1]) return false;
        const src = selector.match(/\[src\*="([^"]*)"\]/);
        return !src || !!el.src?.includes(src[1]);
    }

    const body = element('body', 'body');
    for (const id of ['terminal1-container', 'terminal2-container', 'sidebar', 'sessionList', 'kbdBtn', 'resizeBtn', 'mobileControls', 'dictationInput', 'newSessionModal']) body.appendChild(element('div', id));
    for (const id of [1, 2]) {
        const container = elements.get(`terminal${id}-container`);
        container.className = 'terminal-container' + (id === 1 ? ' active' : ' hidden');
        container.appendChild(element('div', `terminal${id}`));
        const browser = element('div', `browser${id}`);
        browser.className = 'browser-pane hidden';
        container.appendChild(browser);
    }
    const document = {
        body, hidden: false,
        getElementById(id) { return elements.get(id) || element('div', id); },
        createElement: tag => element(tag),
        createDocumentFragment: () => element('fragment'),
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
        querySelectorAll(selector) {
            const parent = selector.match(/^(#[\w-]+)\s+(?:>\s*)?(.+)$/);
            if (parent) return elements.get(parent[1].slice(1))?.querySelectorAll(parent[2]) || [];
            return [...elements.values(), ...descendants(body)].filter((el, i, all) => all.indexOf(el) === i && matches(el, selector));
        },
        addEventListener() {},
    };
    const window = {
        innerWidth: width, innerHeight: 852, FERNANDO_API_KEY: 'test-key', _urlParamsProcessed: true,
        location: {search, pathname: '/', origin: 'https://fernando.test'},
        addEventListener(name, callback) {
            if (!listeners.has(name)) listeners.set(name, []);
            listeners.get(name).push(callback);
        },
        scrollBy(value) { trace.push(['scrollBy', copy(value)]); },
        scrollTo(value) { trace.push(['scrollTo', copy(value)]); },
    };
    const sessionStorage = {
        getItem: key => storage.get(key) || null,
        setItem(key, value) { storage.set(key, value); },
        removeItem(key) { storage.delete(key); },
    };
    const instances = new Map();
    const sandbox = vm.createContext({
        window, document, navigator: {userAgent: 'iPhone', platform: 'iPhone', maxTouchPoints: 5},
        URLSearchParams, AbortController, console, Date: {now: () => now},
        sessionStorage, localStorage: sessionStorage,
        history: {replaceState(state, title, url) { trace.push(['url', url]); window.location.search = url.includes('?') ? url.slice(url.indexOf('?')) : ''; }},
        socket: {
            connected: true,
            on(name, callback) { if (!socketListeners.has(name)) socketListeners.set(name, []); socketListeners.get(name).push(callback); },
            connect() { trace.push(['connect']); },
        },
        emitWithCsrf(name, data) { trace.push(['emit', name, data === undefined ? null : copy(data)]); },
        setTimeout(callback, delay) {
            const id = ++nextTimer;
            timers.set(id, {callback, delay});
            trace.push(['timer', delay]);
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
        setInterval() {},
        doFit() { trace.push(['fit']); },
        showTermInPane(session, pane) {
            trace.push(['showTerm', session, pane]);
            if (!instances.has(session)) instances.set(session, {ready: true, firstAttach: true, wterm: {focus() { trace.push(['termFocus', session]); }}});
            return instances.get(session);
        },
        destroyTerm(session) { trace.push(['destroyTerm', session]); },
        term1: {element: {querySelector() { return {blur() { trace.push(['blurTerm', 1]); }}; }}, focus() { trace.push(['focusTerm', 1]); }},
        term2: {element: {querySelector() { return {blur() { trace.push(['blurTerm', 2]); }}; }}, focus() { trace.push(['focusTerm', 2]); }},
        _paneSession: {1: null, 2: null},
        applyProviderSettings() { trace.push(['providerSettings']); },
        closeActiveSubmenu() { trace.push(['closeSubmenu']); },
        showAlert(text) { trace.push(['alert', text]); },
        showConfirm() { return {then(callback) { callback(true); }}; },
        fetch() { throw new Error('Unexpected network request'); },
    });
    const run = code => vm.runInContext(code, sandbox);
    if (refactored) run(controller);
    for (const name of files) run((refactored ? current : previous)[name]);
    trace.length = 0;

    return {
        run, trace, elements, sandbox,
        fireSocket(name, data) { for (const callback of socketListeners.get(name) || []) callback(copy(data)); },
        fireMessage(pane, data) {
            const iframe = elements.get('browser' + pane).querySelector('iframe');
            for (const callback of listeners.get('message') || []) callback({data: copy(data), source: iframe.contentWindow, origin: window.location.origin});
        },
        flush(delay) {
            for (const [id, timer] of [...timers]) if (delay === undefined || timer.delay === delay) {
                timers.delete(id);
                timer.callback();
            }
        },
        advance(milliseconds) { now += milliseconds; },
        snapshot() {
            const state = run(refactored
                ? `({split:isSplit,active:activeTerminal,panes:[1,2].map(id=>{const p=paneController.get(id);return [p.surface,p.terminalSession,p.contentKey,p.jupyterPath]})})`
                : `({split:isSplit,active:activeTerminal,panes:[1,2].map(id=>[paneTypes[id],id===1?currentSession1:currentSession2,paneNotebook[id],_jupyterPaths[id]])})`);
            return copy({state, search: window.location.search, storage: [...storage], viewers: sandbox._paneSession,
                panes: [1, 2].map(id => ({
                    container: elements.get(`terminal${id}-container`).className,
                    terminal: elements.get(`terminal${id}`).className,
                    browser: elements.get(`browser${id}`).className,
                    html: elements.get(`browser${id}`).innerHTML,
                    frames: elements.get(`browser${id}`).children.filter(el => el.tagName === 'IFRAME').map(el => ({src: el.src, style: el.style, allow: el.allow, attributes: el.attributes, serial: el.serial})),
                })),
                mobile: elements.get('mobileControls').className,
                keyboard: elements.get('kbdBtn').className,
                resize: elements.get('resizeBtn').className,
                sidebar: descendants(elements.get('sessionList')).map(el => ({tag: el.tagName, classes: el.className, text: el.textContent, html: el.innerHTML, dataset: el.dataset, style: el.style})),
                menus: document.querySelectorAll('.group-context-menu').map(el => ({style: el.style, items: el.children.map(child => child.textContent)})),
                trace,
            });
        },
    };
}

let checks = 0;
function compare(label, actions, search = '', width = 393) {
    const old = environment(false, search, width);
    const next = environment(true, search, width);
    for (const action of actions) {
        for (const env of [old, next]) {
            if (typeof action === 'string') env.run(action);
            else action(env);
        }
        assert.deepEqual(next.snapshot(), old.snapshot(), label + ': ' + String(action));
        checks++;
    }
}

for (const width of [393, 1440]) {
    compare('mixed sessions ' + width, [
        `attachSession('Shell-1')`, `toggleSplit()`, e => e.flush(),
        `openChatPane('12345678')`, `openChatPane('12345678')`,
        `setActiveTerminal(1,true)`, `openChatPane('12345678')`,
        e => e.fireMessage(1, {type: 'get-pane-context'}),
        `openJupyter('Lab')`, `openNotebook('fernando')`,
        e => e.fireSocket('notebook_started', {name: 'fernando'}),
        `toggleDesktop()`, `toggleDesktop()`, `attachSession('Shell-1')`,
        `setActiveTerminal(2,true)`, `attachSession('Shell-1')`,
        `toggleSplit()`, e => e.flush(), `toggleSplit()`, e => e.flush(),
        `closeChatSession('12345678')`,
    ], '?collapsed=abc&custom=keep', width);

    compare('focus guards ' + width, [
        `toggleSplit()`, `setActiveTerminal(1,true)`, `setActiveTerminal(2,false)`,
        e => e.flush(300), e => e.advance(2001), `setActiveTerminal(2,false)`,
        e => e.elements.get('terminal1-container').fire('touchstart'),
        e => e.flush(300),
    ], '', width);
}

for (const first of ['chat:12345678', 'notebook:fernando', 'jupyter:Lab', 'jupyter', 'desktop', 'Shell-1', '']) {
    for (const second of ['chat:12345678', 'notebook:fernando', 'jupyter:Lab', 'desktop', 'Shell-2', '']) {
        const search = '?' + new URLSearchParams({session: first, session2: second, split: '1', active: '1', collapsed: 'abc'});
        compare('socket URL restoration', [
            `window._urlParamsProcessed=false; onSocketConnected()`,
            e => e.fireSocket('notebook_started', {name: 'fernando'}),
            e => e.flush(),
            `onSocketConnected()`, e => e.flush(),
        ], search);
        compare('session-list URL restoration', [
            `window._urlParamsProcessed=false; updateSessionList(['Shell-1','Shell-2'],[],{running_jupyter:[],running_notebooks:[],groups:[],session_groups:{}})`,
            e => e.fireSocket('notebook_started', {name: 'fernando'}),
            e => e.flush(),
        ], search);
    }
}

compare('notebook error', [
    `openNotebook('fernando')`, e => e.fireSocket('notebook_error', {error: 'start failed'}),
]);
compare('chat close retains legacy metadata', [
    `openChatPane('12345678')`, `closeChatSession('12345678')`, `syncUrlParams()`,
]);
compare('Jupyter navigation', [
    `openJupyter('/jupyter/notebooks/folder/Lab.ipynb')`,
    e => e.fireMessage(1, {type: 'jupyter-name', name: 'New', jpath: '/notebooks/folder/New.ipynb'}),
    e => e.fireMessage(1, {type: 'get-pane-context'}),
    `openChatPane('12345678')`, `openJupyter('New')`,
]);

const openings = [
    `attachSession('Shell-1')`, `openChatPane('12345678')`,
    `openJupyter('Lab')`, `openNotebook('fernando')`, `toggleDesktop()`,
];
for (const pane of [1, 2]) for (const source of openings) for (const target of openings) {
    compare('content transitions', [
        ...(pane === 2 ? ['toggleSplit()'] : []), source,
        e => e.fireSocket('notebook_started', {name: 'fernando'}), target,
        e => e.fireSocket('notebook_started', {name: 'fernando'}), e => e.flush(),
    ]);
}

compare('same notebook in both panes', [
    `openNotebook('fernando')`, `toggleSplit()`, `openNotebook('fernando')`,
    e => e.fireSocket('notebook_started', {name: 'fernando'}),
    e => e.fireSocket('notebook_deleted', {name: 'fernando'}),
]);

compare('group context and rename', [
    `_cachedGroups=[{id:'group1',name:'Project',color:'#b8860b'}]; _cachedSessionGroups={'chat:12345678':'group1','notebook:fernando':'group1'}; _cachedChatSessions=[{id:'12345678',loaded:true}]; _cachedData={running_notebooks:['fernando']}`,
    `openNotebook('fernando')`, `toggleSplit()`, `openChatPane('12345678')`,
    e => e.fireMessage(2, {type: 'get-pane-context'}),
    `setActiveTerminal(1,true); attachSession('Shell-1')`,
    e => e.fireSocket('session_renamed', {old_name: 'Shell-1', new_name: 'renamed'}),
    `syncUrlParams()`,
]);

for (const session of ['jupyter:Lab', 'notebook:fernando', 'Shell-1', 'chat:12345678']) {
    compare('sidebar gestures ' + session, [
        `updateSessionList(['Shell-1'],[{id:'12345678',name:'Chat',loaded:true,status:'idle'}],{running_jupyter:['Lab'],running_notebooks:['fernando'],groups:[],session_groups:{}})`,
        e => { e.item = e.elements.get('sessionList').querySelector(`.session-item[data-session="${session}"]`); assert.ok(e.item); e.item.fire('touchstart'); },
        e => e.item.fire('touchmove'), e => e.flush(500), e => e.item.fire('touchend'),
        e => e.item.fire('touchstart'), e => e.flush(500), e => e.item.fire('touchend'),
        e => e.item.fire('touchmove'),
        e => e.item.fire('contextmenu'),
        e => {
            const menu = e.sandbox.document.querySelector('.group-context-menu');
            menu.children.find(child => child.textContent === 'Close').onclick();
        },
    ]);
}

const tabA = environment(true);
const tabB = environment(true);
tabA.run(`openChatPane('12345678'); toggleSplit(); openJupyter('Lab')`);
const beforeB = tabB.snapshot();
tabA.run(`setActiveTerminal(1,true); toggleSplit()`);
assert.deepEqual(tabB.snapshot(), beforeB);
tabB.run(`openChatPane('12345678')`);
assert.equal(tabA.run('isSplit'), false);
assert.equal(tabB.run('isSplit'), false);
assert.notEqual(tabA.run('paneController'), tabB.run('paneController'));

const extension = environment(true);
extension.run(`paneController.register('example', {
    type:'example', matches:key=>key.startsWith('example:'), ownsKey:key=>key.startsWith('example:'),
    open:key=>{const browser=paneController.showBrowser(activeTerminal,key); browser.innerHTML='example'; paneController.finishOpen('example');},
    urlFragment:'/example/', keyFromUrl:url=>url.includes('/example/')?'example:one':null,
    context:key=>({type:'example',name:key.slice(8)})
}); paneController.open('example:one')`);
assert.equal(extension.run('paneController.sessionKey(1)'), 'example:one');
assert.equal(extension.run('paneController.context(1).name'), 'one');
assert.ok(extension.snapshot().search.includes('session=example%3Aone'));
extension.run(`toggleSplit(); paneController.open('example:two'); setActiveTerminal(1,true)`);
assert.equal(extension.run('paneController.sessionKey(2)'), 'example:two');
assert.equal(extension.run('activeTerminal'), 1);

function original(path) {
    return execFileSync('git', ['show', `${baseline}:${path}`], {encoding: 'utf8'});
}

function section(source, start, end) {
    const first = source.indexOf(start);
    const last = source.indexOf(end, first);
    assert.ok(first >= 0 && last > first);
    return source.slice(first, last);
}

const cssPath = 'src/static/css/index.css';
assert.equal(section(fs.readFileSync(cssPath, 'utf8'), '.terminals-wrapper {', '.toast {'),
    section(original(cssPath), '.terminals-wrapper {', '.toast {'));
const htmlPath = 'src/templates/index.html';
assert.equal(section(fs.readFileSync(htmlPath, 'utf8'), '        <div class="main-content">', '    <div class="modal" id="newSessionModal"'),
    section(original(htmlPath), '        <div class="main-content">', '    <div class="modal" id="newSessionModal"'));
assert.equal(current['mobile.js'].slice(current['mobile.js'].indexOf('if (window.visualViewport)')),
    previous['mobile.js'].slice(previous['mobile.js'].indexOf('if (window.visualViewport)')));
const terminalPath = 'src/static/js/terminal.js';
assert.equal(section(fs.readFileSync(terminalPath, 'utf8'), 'function setupFocusScroll(', '// --- Resize ---')
    .replace('setupFocusScroll(entry)', 'setupFocusScroll(wtermEl, pane)')
    .replace('entry.element.querySelector', 'wtermEl.querySelector')
    .replaceAll('entry.pane', 'pane'),
    section(original(terminalPath), 'function setupFocusScroll(', '// --- Resize ---'));

console.log(`${checks} before/after pane comparisons passed; independent tab state verified`);
