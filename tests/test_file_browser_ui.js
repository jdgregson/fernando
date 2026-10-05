const assert = require('node:assert/strict');
const fs = require('node:fs');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

async function workspace({touch = false, view = 'list', hidden = false, storage = null, path = '/home/fernando'} = {}) {
    const source = fs.readFileSync('src/templates/file_browser.html', 'utf8').replace('{{ home }}', '/home/fernando').replace('{{ api_key }}', 'fixture-key').replace('{{ default_view }}', view).replace("{{ 'true' if default_hidden else 'false' }}", String(hidden));
    const dom = new JSDOM(source, {url: 'https://fernando.test/files/fixture?api_key=fixture-key&path=' + encodeURIComponent(path), runScripts: 'outside-only', pretendToBeVisual: true});
    const win = dom.window;
    if (storage) Object.defineProperty(win, 'localStorage', {value:storage});
    const requests = [];
    const navigation = [];
    const operations = [];
    const copied = [];
    const style = win.document.createElement('style'); style.textContent = fs.readFileSync('src/static/css/file-browser.css', 'utf8'); win.document.head.appendChild(style);
    win.matchMedia = () => ({matches:touch});
    win.HTMLElement.prototype.scrollIntoView = function() {};
    win.HTMLElement.prototype.setPointerCapture = function(id) { this.capture = id; };
    win.HTMLElement.prototype.hasPointerCapture = function(id) { return this.capture === id; };
    win.HTMLElement.prototype.releasePointerCapture = function() { this.capture = null; };
    Object.defineProperty(win.navigator, 'clipboard', {value:{writeText: async text => copied.push(text)}});
    win.ace = {
        edit: () => ({session: {setUseWorker() {}, on() {}, setMode() {}}, commands: {addCommand() {}}, resize() {}, setValue() {}}),
        config: {set() {}},
    };
    win.FernandoPane = {navigate: data => navigation.push(data.path), activate() {}};
    win.fetch = async (url, options) => {
        assert.equal(options.headers['X-API-Key'], 'fixture-key');
        if (url.endsWith('/operation')) {
            operations.push(JSON.parse(options.body));
            return {ok:true, headers:{get:()=>'application/json'}, json:async()=>({ok:true})};
        }
        const path = new URL(url, win.location.origin).searchParams.get('path');
        requests.push(path);
        return {ok: true, headers: {get: () => 'application/json'}, json: async () => ({path, entries: [
            {name: 'projects', path: path + '/projects', directory: true, mode: 'drwxr-xr-x', size: 0, modified: 1},
            {name: 'notes.txt', path: path + '/notes.txt', directory: false, mode: '-rw-r--r--', size: 42, modified: 1},
            {name: 'paper.txt', path: path + '/paper.txt', directory: false, mode: '-rw-r--r--', size: 21, modified: 1},
            {name: '.hidden', path: path + '/.hidden', directory: false, mode: '-rw-------', size: 1, modified: 1},
        ]})};
    };
    win.eval(fs.readFileSync('src/static/js/file-drag.js', 'utf8'));
    win.eval(fs.readFileSync('src/static/js/file-browser.js', 'utf8'));
    const settle = () => new Promise(resolve => setImmediate(resolve));
    await settle();
    return {win, doc: win.document, requests, navigation, operations, copied, settle, close: () => win.close()};
}

test('properties renders metadata safely and calculates directory contents', async () => {
    const w = await workspace();
    const $ = id => w.doc.getElementById(id);
    w.win.HTMLDialogElement.prototype.showModal = function() { this.open = true; };
    const calls = [];
    w.win.fetch = async (url, options) => {
        assert.equal(options.headers['X-API-Key'], 'fixture-key');
        calls.push(url);
        const data = url.includes('directory-totals?') ? {files:2, directories:1, symlinks:1, other:0, size:123, allocated:4096, complete:true} : {
            name:'<img src=x onerror=alert(1)>', type:'Directory', path:'/home/fernando/projects', location:'/home/fernando', directory:true,
            size:4096, allocated:4096, hidden:false, target:null, created:null, modified:1, accessed:2, changed:3,
            owner:'fernando', uid:1000, group:'fernando', gid:1000, mode:'drwxr-xr-x', permissions:'0755', inode:'123', device:'1', links:2,
        };
        return {ok:true, headers:{get:()=>'application/json'}, json:async()=>data};
    };
    $('rows').firstElementChild.dispatchEvent(new w.win.MouseEvent('contextmenu', {bubbles:true}));
    $('propertiesButton').click();
    await w.settle();
    assert.equal($('propertiesDialog').open, true);
    assert.equal($('propertiesTitle').textContent, '<img src=x onerror=alert(1)> — Properties');
    assert.equal($('propertiesDialog').querySelector('img'), null);
    assert.match($('propertiesContent').textContent, /CreatedNot available/);
    assert.match($('propertiesContent').textContent, /Metadata changed/);
    assert.match($('propertiesContent').textContent, /UID 1000/);
    assert.match($('propertiesContent').textContent, /2 files, 1 folders, 1 links/);
    assert.equal(calls.length, 2);
    assert.match(calls[0], /properties\?path=%2Fhome%2Ffernando%2Fprojects/);
    w.close();
});

test('compact browser puts actions in the menu, filters hidden items, and switches to working tiles', async () => {
    const w = await workspace();
    const $ = id => w.doc.getElementById(id);
    assert.equal(w.doc.querySelector('footer, .toolbar, #upButton'), null);
    assert.ok($('pathBox').contains($('breadcrumbs')));
    assert.equal($('browserMenu').contains($('renameButton')), false);
    assert.ok($('fileContextMenu').contains($('renameButton')));
    assert.equal(w.win.getComputedStyle($('breadcrumbs').querySelector('a')).minHeight, '22px');
    assert.equal($('rows').children.length, 3);
    $('menuButton').click();
    assert.equal(w.doc.body.classList.contains('menu-open'), true);
    $('hiddenButton').click();
    assert.equal($('rows').children.length, 4);
    $('searchButton').click();
    assert.equal(w.doc.activeElement, $('filterInput'));
    $('filterInput').value = 'notes';
    $('filterInput').dispatchEvent(new w.win.Event('input'));
    assert.equal($('rows').children.length, 1);
    assert.equal($('menuButton').classList.contains('filtered'), true);
    $('searchButton').click();
    $('tileButton').click();
    assert.equal(w.doc.body.classList.contains('menu-open'), true);
    assert.equal($('tileButton').hasAttribute('aria-pressed'), false);
    assert.equal($('viewModeLabel').textContent, 'List mode');
    assert.equal($('fileTable').hidden, true);
    assert.equal($('tileGrid').hidden, false);
    assert.equal($('tileGrid').children.length, 4);
    const folder = $('tileGrid').firstElementChild;
    folder.click();
    assert.equal(folder.classList.contains('selected'), true);
    folder.dispatchEvent(new w.win.MouseEvent('dblclick', {bubbles: true}));
    await w.settle();
    assert.equal(w.requests.at(-1), '/home/fernando/projects');
    assert.equal(w.navigation.at(-1), '/home/fernando/projects');
    assert.equal($('breadcrumbs').textContent, '~/projects');
    $('breadcrumbs').querySelector('a').click();
    await w.settle();
    assert.equal(w.requests.at(-1), '/home/fernando');
    w.close();
});

test('defaults, explicit search clear, and navigation resets are applied', async () => {
    const w = await workspace({view:'tile', hidden:true, touch:true});
    const $ = id => w.doc.getElementById(id);
    assert.equal($('tileGrid').children.length, 4);
    assert.equal($('hiddenButton').getAttribute('aria-pressed'), 'true');
    $('menuButton').click();
    $('searchButton').click();
    $('filterInput').value = 'paper'; $('filterInput').dispatchEvent(new w.win.Event('input'));
    assert.equal($('tileGrid').children.length, 1);
    $('clearSearch').click();
    assert.equal($('tileGrid').children.length, 4);
    $('filterInput').value = 'projects'; $('filterInput').dispatchEvent(new w.win.Event('input'));
    $('tileGrid').firstElementChild.dispatchEvent(new w.win.MouseEvent('dblclick', {bubbles:true}));
    await w.settle();
    assert.equal($('filterInput').value, '');
    $('filterInput').value = 'paper'; $('filterInput').dispatchEvent(new w.win.Event('input'));
    $('homeButton').click(); await w.settle();
    assert.equal($('filterInput').value, '');
    assert.equal(w.requests.at(-1), '/home/fernando');
    assert.equal(w.doc.body.classList.contains('menu-open'), false);
    w.close();
});

test('desktop flyout stays open until toggled and heading buttons have vertical breathing room', async () => {
    const w = await workspace();
    const $ = id => w.doc.getElementById(id);
    $('menuButton').click();
    $('pathDisplay').click();
    $('pathInput').dispatchEvent(new w.win.KeyboardEvent('keydown', {key:'Escape',bubbles:true}));
    $('searchButton').click();
    $('filterInput').dispatchEvent(new w.win.KeyboardEvent('keydown', {key:'Enter',bubbles:true,cancelable:true}));
    $('fileList').dispatchEvent(new w.win.KeyboardEvent('keydown', {key:'Escape',bubbles:true}));
    $('homeButton').click(); await w.settle();
    assert.equal(w.doc.body.classList.contains('menu-open'), true);
    assert.equal(w.win.getComputedStyle($('browserMenu')).borderLeftColor, 'rgb(20, 49, 81)');
    const header = w.doc.querySelector('th');
    assert.equal(w.win.getComputedStyle(header).paddingTop, '4px');
    assert.equal(w.win.getComputedStyle(header).paddingBottom, '4px');
    assert.equal(w.win.getComputedStyle(header.querySelector('button')).minHeight, '22px');
    $('menuButton').click();
    assert.equal(w.doc.body.classList.contains('menu-open'), false);
    w.close();
});

test('the same selection menu item turns checkboxes on and off on desktop and mobile', async () => {
    for (const touch of [false,true]) {
        const w = await workspace({touch});
        const row = w.doc.getElementById('rows').firstElementChild;
        const toggle = w.doc.getElementById('selectItemsButton');
        const context = () => row.dispatchEvent(new w.win.MouseEvent('contextmenu', {bubbles:true,cancelable:true}));
        context(); toggle.click();
        assert.equal(toggle.getAttribute('aria-pressed'), 'true');
        assert.equal(w.win.getComputedStyle(row.querySelector('.selection-check')).display, 'inline-flex');
        context(); toggle.click();
        assert.equal(toggle.getAttribute('aria-pressed'), 'false');
        assert.equal(w.win.getComputedStyle(row.querySelector('.selection-check')).display, 'none');
        assert.equal(w.doc.getElementById('selectionModeButton').hidden, true);
        context(); toggle.click();
        w.doc.getElementById('fileList').dispatchEvent(new w.win.MouseEvent('contextmenu', {bubbles:true,cancelable:true}));
        toggle.click();
        assert.equal(w.doc.body.classList.contains('selecting'), false);
        w.close();
    }
});

test('file drags cross independent browser documents using shared origin storage', async () => {
    const storage = new Map();
    const shared = {get length(){return storage.size;},key:index=>[...storage.keys()][index],getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
    const source = await workspace({storage:shared});
    const target = await workspace({storage:shared,path:'/tmp/destination'});
    const payload = new Map();
    const transfer = {types:['application/x-fernando-files'],files:[],items:[],setData:(key,value)=>payload.set(key,value),getData:key=>payload.get(key)||''};
    const from = source.doc.querySelector('[data-path="/home/fernando/notes.txt"]');
    const start = new source.win.Event('dragstart',{bubbles:true,cancelable:true}); Object.defineProperty(start,'dataTransfer',{value:transfer}); from.dispatchEvent(start);
    const drop = new target.win.Event('drop',{bubbles:true,cancelable:true}); Object.defineProperty(drop,'dataTransfer',{value:transfer}); target.doc.getElementById('fileList').dispatchEvent(drop);
    await target.settle();
    assert.deepEqual(target.operations, [{action:'move',path:'/home/fernando/notes.txt',destination:'/tmp/destination/notes.txt'}]);
    assert.equal(source.operations.length, 0);
    assert.equal(storage.size, 0);
    const reference = source.win.FernandoFileDrag.create([{path:'/home/fernando/notes.txt',name:'../../outside'}], 'references');
    payload.set(source.win.FernandoFileDrag.type, reference);
    target.doc.getElementById('fileList').dispatchEvent(drop);
    await target.settle();
    assert.deepEqual(target.operations.at(-1), {action:'copy',path:'/home/fernando/notes.txt',destination:'/tmp/destination/notes.txt'});
    source.close(); target.close();
});

test('desktop letter cycling, modifier selection, context copying and marquee selection work', async () => {
    const w = await workspace();
    const list = w.doc.getElementById('fileList');
    const rows = [...w.doc.getElementById('rows').children];
    const key = value => list.dispatchEvent(new w.win.KeyboardEvent('keydown', {key:value,bubbles:true,cancelable:true}));
    key('p'); assert.ok(rows[0].classList.contains('selected'));
    key('p'); assert.ok(rows[2].classList.contains('selected'));
    key('p'); assert.ok(rows[0].classList.contains('selected'));
    rows[1].dispatchEvent(new w.win.MouseEvent('click', {bubbles:true,ctrlKey:true}));
    assert.equal(w.doc.querySelectorAll('#rows .selected').length, 2);
    rows[1].dispatchEvent(new w.win.MouseEvent('contextmenu', {bubbles:true,cancelable:true,clientX:100,clientY:100}));
    assert.equal(w.doc.getElementById('fileContextMenu').hidden, false);
    w.doc.getElementById('copyNameButton').click(); await w.settle();
    assert.equal(w.copied.at(-1), 'projects\nnotes.txt');
    list.getBoundingClientRect = () => ({left:0,top:0,width:400,height:300,bottom:300});
    rows.forEach((row,index) => { row.getBoundingClientRect = () => ({left:0,top:index*30,width:400,height:30}); });
    for (const [type,x,y] of [['pointerdown',5,200],['pointermove',350,45],['pointerup',350,45]]) {
        const event = new w.win.MouseEvent(type, {bubbles:true,button:0,clientX:x,clientY:y});
        Object.defineProperty(event, 'pointerId', {value:7});
        list.dispatchEvent(event);
    }
    assert.equal(rows[0].classList.contains('selected'), false);
    assert.equal(rows[1].classList.contains('selected'), true);
    assert.equal(rows[2].classList.contains('selected'), true);
    assert.equal(w.doc.getElementById('selectionRectangle').hidden, true);
    w.close();
});

test('mobile long press opens file context and supports multiple selection', async () => {
    const w = await workspace({touch:true});
    const rows = [...w.doc.getElementById('rows').children];
    assert.equal(rows[0].draggable, false);
    const event = new w.win.MouseEvent('pointerdown', {bubbles:true,clientX:50,clientY:80});
    Object.defineProperties(event, {pointerType:{value:'touch'},pointerId:{value:3}});
    rows[0].dispatchEvent(event);
    await new Promise(resolve => setTimeout(resolve, 530));
    assert.equal(w.doc.getElementById('fileContextMenu').hidden, false);
    w.doc.getElementById('selectItemsButton').click();
    rows[1].dispatchEvent(new w.win.MouseEvent('pointerdown', {bubbles:true}));
    rows[1].click();
    assert.equal(w.doc.querySelectorAll('#rows .selected').length, 2);
    assert.equal(w.doc.getElementById('selectionCount').textContent, 'Done · 2');
    w.doc.getElementById('selectionModeButton').click();
    assert.equal(w.doc.querySelectorAll('#rows .selected').length, 0);
    w.close();
});

test('slow second click renames inline in list and tiles without opening the file', async () => {
    for (const view of ['list','tile']) {
        const w = await workspace({view});
        let now = 1000;
        Object.defineProperty(w.win.performance, 'now', {value:()=>now});
        const row = [...w.doc.querySelectorAll('#fileList [data-path]')].find(element => element.dataset.path.endsWith('/notes.txt'));
        row.click(); now += 1000; row.click();
        await new Promise(resolve => setTimeout(resolve, 380));
        const input = w.doc.querySelector('.file-rename');
        assert.ok(input);
        input.value = 'renamed.txt';
        input.dispatchEvent(new w.win.KeyboardEvent('keydown', {key:'Enter',bubbles:true,cancelable:true}));
        await w.settle();
        assert.deepEqual(w.operations.at(-1), {action:'move',path:'/home/fernando/notes.txt',destination:'/home/fernando/renamed.txt'});
        w.close();
    }
});

test('internal drag and drop uses an origin-local capability and moves selected files into folders', async () => {
    const w = await workspace();
    const rows = [...w.doc.getElementById('rows').children];
    const data = new Map();
    const transfer = {types:['application/x-fernando-files'],files:[],items:[],setData:(key,value)=>data.set(key,value),getData:key=>data.get(key)||''};
    const dispatch = (target,type) => {
        const event = new w.win.Event(type,{bubbles:true,cancelable:true}); Object.defineProperty(event,'dataTransfer',{value:transfer}); target.dispatchEvent(event);
    };
    rows[1].click(); rows[2].dispatchEvent(new w.win.MouseEvent('click',{bubbles:true,ctrlKey:true}));
    dispatch(rows[1], 'dragstart');
    const token = data.get('application/x-fernando-files');
    assert.match(token, /^[0-9a-f-]{36}$/);
    assert.ok(w.win.localStorage.getItem('fernando-file-drag:' + token));
    dispatch(rows[0], 'drop'); await w.settle();
    assert.equal(w.operations.length, 2);
    assert.equal(w.operations[0].destination, '/home/fernando/projects/notes.txt');
    assert.equal(w.operations[1].destination, '/home/fernando/projects/paper.txt');
    assert.equal(w.win.localStorage.getItem('fernando-file-drag:' + token), null);
    dispatch(rows[0], 'drop'); await w.settle();
    assert.equal(w.operations.length, 2);
    w.close();
});

test('path editing supports blank space, keyboard and stationary long press without following the held link', async () => {
    const w = await workspace();
    const $ = id => w.doc.getElementById(id);
    $('pathDisplay').click();
    assert.equal($('pathForm').hidden, false);
    assert.equal(w.doc.activeElement, $('pathInput'));
    $('pathInput').value = '~/projects';
    $('pathForm').dispatchEvent(new w.win.Event('submit', {cancelable: true}));
    await w.settle();
    assert.equal(w.requests.at(-1), '/home/fernando/projects');
    assert.equal($('pathForm').hidden, true);
    $('pathDisplay').dispatchEvent(new w.win.KeyboardEvent('keydown', {key: 'F2', bubbles: true}));
    assert.equal($('pathForm').hidden, false);
    $('pathInput').dispatchEvent(new w.win.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    assert.equal($('pathForm').hidden, true);
    let now = 1000;
    Object.defineProperty(w.win.performance, 'now', {value: () => now});
    const link = $('breadcrumbs').querySelector('a');
    const pointer = (type, x = 1) => {
        const event = new w.win.MouseEvent(type, {bubbles: true, cancelable: true, button: 0, clientX: x, clientY: 1});
        Object.defineProperty(event, 'pointerId', {value: 7});
        link.dispatchEvent(event);
    };
    pointer('pointerdown'); now += 600; pointer('pointerup');
    assert.equal($('pathForm').hidden, false);
    link.click();
    await w.settle();
    assert.equal(w.requests.at(-1), '/home/fernando/projects');
    $('pathInput').dispatchEvent(new w.win.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    pointer('pointerdown'); pointer('pointermove', 20); now += 600; pointer('pointerup', 20);
    assert.equal($('pathForm').hidden, true);
    w.close();
});
