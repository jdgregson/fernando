const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/templates/chat.html', 'utf8');
const form = source.match(/<form\b([^>]*\bid="chat-composer"[^>]*)>([\s\S]*?)<\/form>/);
assert.ok(form);
assert.ok(!form[2].includes('id="messages"'));
const textareaTag = form[2].match(/<textarea\b([^>]*)>/)[1];
const attributes = Object.fromEntries([...textareaTag.matchAll(/([\w-]+)="([^"]*)"/g)].map(m => [m[1], m[2]]));
assert.equal(attributes.name, 'chat-message');
assert.equal(attributes.autocomplete, 'off');
assert.equal(attributes.autocorrect, 'on');
assert.equal(attributes.spellcheck, 'true');
assert.ok(form[2].includes(`id="${attributes['aria-labelledby']}" for="${attributes.id}" hidden>Chat message</label>`));
assert.ok(!source.includes("getElementById('input')"));
for (const button of form[2].matchAll(/<button\b([^>]*)>/g)) assert.match(button[1], /type="button"/);

let prevented = false;
vm.runInNewContext(form[1].match(/onsubmit="([^"]*)"/)[1], {event: {preventDefault() { prevented = true; }}});
assert.ok(prevented);

const start = source.indexOf(`\n    const input = document.getElementById('${attributes.id}');`);
const end = source.indexOf('\n    let attachedFiles =', start);
assert.ok(start > 0 && end > start);
const inputCode = source.slice(start, end);

for (const userAgent of ['iPhone', 'Desktop']) {
    for (const saved of ['', 'existing draft']) {
        const listeners = new Map();
        const input = {
            value: '', style: {}, scrollHeight: 30,
            addEventListener(type, handler) { listeners.set(type, handler); },
        };
        const menu = {style: {display: 'none'}};
        const storage = new Map(saved ? [['acp_draft_chat-one', saved]] : []);
        let sent = 0;
        const timers = [];
        const sandbox = vm.createContext({
            document: {getElementById(id) {
                if (id === attributes.id) return input;
                assert.equal(id, 'slashMenu');
                return menu;
            }},
            navigator: {userAgent}, sessionId: 'chat-one', window: {},
            localStorage: {
                getItem: key => storage.get(key),
                setItem: (key, value) => storage.set(key, value),
                removeItem: key => storage.delete(key),
            },
            requestAnimationFrame() { return 1; },
            clearTimeout() {},
            setTimeout(callback) { timers.push(callback); return timers.length; },
            sendMessage() { sent++; },
        });
        vm.runInContext(inputCode, sandbox);
        assert.equal(input.value, saved);
        let enterPrevented = false;
        listeners.get('keydown')({key: 'Enter', shiftKey: false, preventDefault() { enterPrevented = true; }});
        assert.equal(sent, userAgent === 'iPhone' ? 0 : 1);
        assert.equal(enterPrevented, userAgent !== 'iPhone');
        listeners.get('keydown')({key: 'Enter', shiftKey: true, preventDefault() { assert.fail('Shift+Enter must keep newline behavior'); }});
        input.value = 'ordinary chat text';
        listeners.get('input')();
        timers.at(-1)();
        assert.equal(storage.get('acp_draft_chat-one'), 'ordinary chat text');
        input.value = '';
        listeners.get('input')();
        timers.at(-1)();
        assert.equal(storage.has('acp_draft_chat-one'), false);
    }
}

console.log('Composer is independently labeled and scoped; native submission blocked; iOS/desktop Enter and per-session drafts preserved');
