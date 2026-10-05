const assert = require('node:assert/strict');
const fs = require('node:fs');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

const chat = fs.readFileSync('src/templates/chat.html', 'utf8');
const section = (start, end) => {
    const first = chat.indexOf(start);
    const last = chat.indexOf(end, first);
    assert.ok(first >= 0 && last > first);
    return chat.slice(first, last);
};

function storage() {
    const data = new Map();
    return {get length(){return data.size;},key:index=>[...data.keys()][index],getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
}

function workspace(shared) {
    const dom = new JSDOM('<body><div id="messages"></div><form id="chat-composer"><div id="attachedFiles"></div><span id="uploadingIndicator"></span></form></body>', {url:'https://fernando.test/chat/fixture',runScripts:'outside-only'});
    const win = dom.window;
    Object.defineProperty(win,'localStorage',{value:shared});
    win.FernandoPane = {activate() {}};
    win.fetch = () => assert.fail('A reference drop must not upload or fetch file contents');
    win.socket = {emit:()=>assert.fail('A reference drop must not send a message')};
    win.escapeHtml = text => {const element=win.document.createElement('span');element.textContent=text;return element.innerHTML;};
    win.eval(fs.readFileSync('src/static/js/file-drag.js','utf8'));
    win.eval('const CLIP_SVG=""; let _stagedReward=null; ' +
        section('let attachedFiles = [];','function downsampleImage') +
        section('function renderAttached()','function removeAttached') +
        section('function removeAttached(idx)','// iOS: throttle') +
        section('function renderFileChips(paths, container)','async function downloadFile'));
    return {win,doc:win.document,close:()=>win.close()};
}

function transfer() {
    const data = new Map();
    return {types:['application/x-fernando-files'],setData:(key,value)=>data.set(key,value),getData:key=>data.get(key)||''};
}

function fire(w, target, type, payload) {
    const event = new w.win.Event(type,{bubbles:true,cancelable:true});
    Object.defineProperty(event,'dataTransfer',{value:payload});
    target.dispatchEvent(event);
}

test('Files references use existing attachment chips, deduplicate, and can be dragged to another chat', () => {
    const shared = storage(), first = workspace(shared), second = workspace(shared);
    const payload = transfer();
    const entries = [{path:'/home/fernando/projects/code.py',name:'code.py'},{path:'/tmp/<img>.txt',name:'<img>.txt'}];
    payload.setData(first.win.FernandoFileDrag.type, first.win.FernandoFileDrag.create(entries));
    fire(first,first.doc.body,'drop',payload);
    assert.equal(first.doc.querySelectorAll('.attached-file').length,2);
    assert.equal(first.doc.querySelector('#attachedFiles img'),null);
    const next = transfer();
    fire(first,first.doc.querySelector('.attached-file'),'dragstart',next);
    fire(second,second.doc.body,'drop',next);
    assert.equal(second.doc.querySelector('.attached-file .name').textContent,'code.py');
    const duplicate = transfer();
    duplicate.setData(first.win.FernandoFileDrag.type,first.win.FernandoFileDrag.create([entries[0]]));
    fire(second,second.doc.body,'drop',duplicate);
    assert.equal(second.doc.querySelectorAll('.attached-file').length,1);
    second.doc.querySelector('.attached-file .remove').click();
    assert.equal(second.doc.querySelectorAll('.attached-file').length,0);
    first.close(); second.close();
});

test('transcript attachment chips are draggable references and unknown or expired tokens are rejected', () => {
    const shared = storage(), source = workspace(shared), target = workspace(shared);
    source.win.renderFileChips(['/home/fernando/projects/history.txt'],source.doc.getElementById('messages'));
    const payload = transfer();
    fire(source,source.doc.querySelector('.file-chip'),'dragstart',payload);
    fire(target,target.doc.body,'drop',payload);
    assert.equal(target.doc.querySelector('.attached-file .name').textContent,'history.txt');
    fire(target,target.doc.body,'drop',payload);
    assert.match(target.doc.getElementById('uploadingIndicator').textContent,/not from an active Fernando view/);
    const id = source.win.FernandoFileDrag.create([{path:'/tmp/old',name:'old'}]);
    const key = 'fernando-file-drag:' + id;
    const old = JSON.parse(shared.getItem(key)); old.created -= 61000; shared.setItem(key,JSON.stringify(old));
    payload.setData(source.win.FernandoFileDrag.type,id);
    fire(target,target.doc.body,'drop',payload);
    assert.match(target.doc.getElementById('uploadingIndicator').textContent,/expired/);
    assert.equal(target.doc.querySelectorAll('.attached-file').length,1);
    source.close(); target.close();
});
